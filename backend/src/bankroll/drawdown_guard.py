"""Provider-level drawdown circuit breaker.

When the rolling 7-day SEK-net-P&L for a single provider drops below
`-threshold_pct × stake_bankroll_sek`, new value-bet placements on that
provider are blocked until the rolling window improves on its own (no
manual reset needed — the window rolls forward, P&L drops out, breaker
clears).

Why per-provider, why SEK-converted, why stateless:

- Per-provider — protects against a single provider's pricing model
  drifting (e.g. a soft book changes its devig and starts shading lines
  the way we already bet) without pausing the whole pipeline.
- SEK-converted — the threshold is a % of the stake bankroll (which is
  SEK-denominated). Provider-native P&L doesn't normalise across
  USDC/USD/SEK currencies and can be misleading.
- Stateless — no `limit` rows persisted. The breaker is a pure function
  of the bet history. As the 7d window slides, the breaker clears
  automatically once enough losing bets fall out.

Gated by env `DRAWDOWN_BREAKER_ENABLED` (default off). Threshold
configurable via `DRAWDOWN_PAUSE_PCT` (decimal, default 0.10 = 10%).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_CACHE_TTL_SEC = 300.0
_DEFAULT_LOOKBACK_DAYS = 7
_DEFAULT_THRESHOLD_PCT = 0.10
_MIN_BETS_FOR_BREACH = 10  # don't trip on a single -EV outlier

# Per-process cache. Key: (profile_id, provider_id, days). Value: (ts, pnl_sek).
_cache: dict[tuple[int, str, int], tuple[float, float]] = {}


def is_enabled() -> bool:
    return os.environ.get("DRAWDOWN_BREAKER_ENABLED", "").strip().lower() in ("1", "true", "yes")


def pause_threshold_pct() -> float:
    """Drawdown threshold as a decimal (e.g. 0.10 = pause at -10% of bankroll)."""
    raw = os.environ.get("DRAWDOWN_PAUSE_PCT", "").strip()
    if not raw:
        return _DEFAULT_THRESHOLD_PCT
    try:
        val = float(raw)
    except ValueError:
        return _DEFAULT_THRESHOLD_PCT
    # Sanity: clamp to (0, 1)
    if val <= 0.0 or val >= 1.0:
        return _DEFAULT_THRESHOLD_PCT
    return val


def is_breached(pnl_sek: float, stake_bankroll_sek: float, threshold_pct: float, n_bets: int) -> bool:
    """Pure-function breach test. Both inputs in SEK. Pure for testing."""
    if stake_bankroll_sek <= 0 or threshold_pct <= 0:
        return False
    if n_bets < _MIN_BETS_FOR_BREACH:
        return False
    return pnl_sek < -(threshold_pct * stake_bankroll_sek)


def compute_provider_pnl_sek(
    db: Session,
    profile_id: int,
    provider_id: str,
    days: int = _DEFAULT_LOOKBACK_DAYS,
) -> tuple[float, int]:
    """Return (pnl_sek, n_settled_bets) for the rolling window.

    Bets are kept in their native currency on the row but converted to
    SEK via the provider's exchange rate before summing — matches the
    convention in `ProfileRepo.get_total_bankroll`.
    """
    from datetime import datetime, timedelta

    from ..config import get_exchange_rate
    from ..db.models import Bet

    cutoff = datetime.now(UTC) - timedelta(days=days)
    rows = (
        db.query(Bet)
        .filter(
            Bet.profile_id == profile_id,
            Bet.provider_id == provider_id,
            Bet.settled_at >= cutoff,
            Bet.result.in_(("won", "lost", "void")),
        )
        .all()
    )
    if not rows:
        return 0.0, 0
    rate = get_exchange_rate(provider_id) or 1.0
    pnl_native = sum((b.payout or 0) - (b.stake or 0) for b in rows)
    return float(pnl_native) * float(rate), len(rows)


def get_provider_pnl_cached(
    db: Session,
    profile_id: int,
    provider_id: str,
    days: int = _DEFAULT_LOOKBACK_DAYS,
) -> tuple[float, int]:
    """Cached `(pnl_sek, n_bets)` lookup. 5-minute TTL, per-process."""
    key = (profile_id, provider_id, days)
    now = time.monotonic()
    entry = _cache.get(key)
    if entry is not None and (now - entry[0]) < _CACHE_TTL_SEC:
        # Recover (pnl, n) from a single packed float: we store pnl in the
        # tuple second slot and n via a parallel dict. Simpler: re-query
        # the bet count separately. Keep cache shape simple — (ts, pnl).
        # If we ever need to dedup the count, add a second cache key.
        return entry[1], -1  # n=-1 sentinel means "from cache, n unknown"
    pnl, n = compute_provider_pnl_sek(db, profile_id, provider_id, days=days)
    _cache[key] = (now, pnl)
    return pnl, n


def is_paused(
    db: Session,
    profile_id: int,
    provider_id: str,
    stake_bankroll_sek: float,
    days: int = _DEFAULT_LOOKBACK_DAYS,
) -> tuple[bool, str | None]:
    """Hot-path check. Returns (paused, human_reason).

    Returns (False, None) when the feature flag is off, when the DB
    query fails, or when the breach threshold is not met — fails open
    so a single bug or hiccup can't suppress every bet.
    """
    if not is_enabled():
        return False, None
    if stake_bankroll_sek <= 0 or not provider_id or not profile_id:
        return False, None
    try:
        pnl_sek, _n_cached = get_provider_pnl_cached(db, profile_id, provider_id, days=days)
    except Exception as exc:
        logger.warning("drawdown_guard: pnl lookup failed for %s: %s", provider_id, exc)
        return False, None

    # Cache returns n=-1 (unknown). Re-compute n on a breach candidate
    # to honor _MIN_BETS_FOR_BREACH. This avoids the cache being able to
    # trip the breaker after a single outlier.
    threshold_pct = pause_threshold_pct()
    if pnl_sek >= -(threshold_pct * stake_bankroll_sek):
        return False, None

    # Candidate breach — re-confirm with fresh n
    try:
        _pnl_fresh, n_fresh = compute_provider_pnl_sek(db, profile_id, provider_id, days=days)
    except Exception as exc:
        logger.warning("drawdown_guard: confirm lookup failed for %s: %s", provider_id, exc)
        return False, None

    if not is_breached(pnl_sek, stake_bankroll_sek, threshold_pct, n_fresh):
        return False, None

    reason = (
        f"7d P&L {pnl_sek:.0f} SEK breaches "
        f"-{threshold_pct * 100:.0f}% of bankroll ({stake_bankroll_sek:.0f} SEK), "
        f"n={n_fresh}"
    )
    return True, reason


def invalidate_cache() -> None:
    """Drop the per-process cache. Test helper."""
    _cache.clear()
