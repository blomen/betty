"""Sport×market CLV-based Kelly confidence multiplier.

Auto-throttles stakes in (sport, market) buckets where historical CLV is
poor — Arnold's edge model is wrong (or the bookmaker model is sharper)
there. CLV is the leading indicator because it converges much faster than
realized ROI (no outcome variance baked in).

The multiplier scales the Kelly fraction after `get_kelly_fraction`:

    final_kelly = base_kelly × confidence_multiplier

Conservative thresholds: we only deflate (never boost), require a minimum
sample size before applying, and only fully skip on strong negative
evidence. All edge-quality scaling stays in `get_kelly_fraction` — this
layer is orthogonal.

Gated by env var `BUCKET_CONFIDENCE_ENABLED` (default off) so the data
can be inspected via the analytics endpoint before going live.
"""

from __future__ import annotations

import os
import time
from datetime import UTC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


_CACHE_TTL_SEC = 300.0
_DEFAULT_LOOKBACK_DAYS = 90

_cache: dict[str, tuple[float, dict[tuple[str, str], dict]]] = {}


def is_enabled() -> bool:
    return os.environ.get("BUCKET_CONFIDENCE_ENABLED", "").strip().lower() in ("1", "true", "yes")


def get_multiplier(mean_clv_pct: float | None, n: int) -> float:
    """Map (mean_clv_pct, sample_size) → Kelly multiplier ∈ [0.0, 1.0].

    Conservative thresholds. Requires ≥100 bets in the bucket before
    applying any deflation; below that the user's existing edge model is
    trusted at full Kelly. CLV is in percent (e.g. 1.5 = +1.5% CLV).

    Never boosts above 1.0 — the existing Kelly schedule already encodes
    the optimal aggressiveness; this layer can only pull back.
    """
    if mean_clv_pct is None or n < 100:
        return 1.0
    if mean_clv_pct >= 0.5:
        return 1.0
    if mean_clv_pct >= -0.5:
        return 0.75
    if mean_clv_pct >= -2.0:
        return 0.5
    return 0.0


def compute_bucket_stats(
    db: Session,
    days: int = _DEFAULT_LOOKBACK_DAYS,
) -> dict[tuple[str, str], dict]:
    """Compute mean CLV and Kelly multiplier per (sport, market) bucket.

    Reads from `bets` joined to `events` to derive sport, filters to
    settled bets within the lookback window that have a populated
    `clv_pct`. Returns a dict keyed on (sport, market) with `n`,
    `mean_clv_pct`, and `multiplier`.
    """
    from datetime import datetime, timedelta

    from sqlalchemy import func

    from ..db.models import Bet, Event

    cutoff = datetime.now(UTC) - timedelta(days=days)
    rows = (
        db.query(
            Event.sport.label("sport"),
            Bet.market.label("market"),
            func.count(Bet.id).label("n"),
            func.avg(Bet.clv_pct).label("mean_clv"),
        )
        .join(Event, Bet.event_id == Event.id)
        .filter(
            Bet.placed_at >= cutoff,
            Bet.clv_pct.isnot(None),
            Bet.result.in_(("won", "lost", "void")),
        )
        .group_by(Event.sport, Bet.market)
        .all()
    )

    out: dict[tuple[str, str], dict] = {}
    for row in rows:
        sport = row.sport or "unknown"
        market = row.market or "unknown"
        n = int(row.n or 0)
        mean_clv = float(row.mean_clv) if row.mean_clv is not None else None
        out[(sport, market)] = {
            "n": n,
            "mean_clv_pct": round(mean_clv, 3) if mean_clv is not None else None,
            "multiplier": get_multiplier(mean_clv, n),
        }
    return out


def get_bucket_stats_cached(db: Session, days: int = _DEFAULT_LOOKBACK_DAYS) -> dict[tuple[str, str], dict]:
    """Cached wrapper around `compute_bucket_stats`. Per-process, 5 min TTL."""
    key = f"days={days}"
    now = time.monotonic()
    entry = _cache.get(key)
    if entry is not None and (now - entry[0]) < _CACHE_TTL_SEC:
        return entry[1]
    stats = compute_bucket_stats(db, days=days)
    _cache[key] = (now, stats)
    return stats


def lookup_multiplier(
    db: Session,
    sport: str | None,
    market: str | None,
    days: int = _DEFAULT_LOOKBACK_DAYS,
) -> float:
    """Return the Kelly multiplier for (sport, market).

    Returns 1.0 (no-op) when the feature is disabled, when sport or
    market is missing, or when the bucket has no entry. Safe to call
    from the value-bet hot path — looks up an in-memory cache.
    """
    if not is_enabled():
        return 1.0
    if not sport or not market:
        return 1.0
    try:
        stats = get_bucket_stats_cached(db, days=days)
    except Exception:
        return 1.0
    entry = stats.get((sport, market))
    if entry is None:
        return 1.0
    mult = entry.get("multiplier")
    return float(mult) if mult is not None else 1.0


def invalidate_cache() -> None:
    """Drop the per-process cache. Test helper; also useful after backfills."""
    _cache.clear()
