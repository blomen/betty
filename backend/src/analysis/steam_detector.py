"""Steam-move detector.

A steam move is a near-simultaneous line move across multiple sportsbooks
in the same direction — the fingerprint of syndicate or sharp money
hitting the market. Arnold is uniquely positioned to detect this because
the pipeline extracts from 40+ books; most retail tools see only 2-3.

Signal definition (v1, conservative):

  • Window: the last `STEAM_WINDOW_MIN` minutes (default 5).
  • Per-outcome key: (event_id, market, outcome, point, scope).
  • Per-direction: count how many *distinct* providers logged a
    movement in the SAME direction within the window.
  • Threshold: at least `STEAM_MIN_PROVIDERS` (default 3) providers
    moving in the same direction.

Why per-outcome (not per-event): a soccer match can see a steam move
on the away ML while the totals market stays cold. Aggregating across
markets would dilute the signal.

Why per-direction: a market where 5 books drift in random directions
isn't steam — it's noise. Steam is *aligned* flow.

Storage: the detector reads from `odds_movements`, which is written
by `OddsBatchProcessor._log_movements` only when this feature is
enabled. The module's `is_enabled()` flag also gates the writes — so
turning the env flag off zeroes both sides of the cost.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_DEFAULT_WINDOW_MIN = 5
_DEFAULT_MIN_PROVIDERS = 3
_DEFAULT_DELTA_PP_MIN = 0.5


def is_enabled() -> bool:
    return os.environ.get("STEAM_DETECTOR_ENABLED", "").strip().lower() in ("1", "true", "yes")


def window_minutes() -> int:
    raw = os.environ.get("STEAM_WINDOW_MIN", "").strip()
    if not raw:
        return _DEFAULT_WINDOW_MIN
    try:
        val = int(raw)
    except ValueError:
        return _DEFAULT_WINDOW_MIN
    if val <= 0 or val > 120:
        return _DEFAULT_WINDOW_MIN
    return val


def min_providers() -> int:
    raw = os.environ.get("STEAM_MIN_PROVIDERS", "").strip()
    if not raw:
        return _DEFAULT_MIN_PROVIDERS
    try:
        val = int(raw)
    except ValueError:
        return _DEFAULT_MIN_PROVIDERS
    if val < 2 or val > 40:
        return _DEFAULT_MIN_PROVIDERS
    return val


def delta_pp_threshold() -> float:
    """Minimum implied-probability delta (percentage points) to log a movement.

    Sub-threshold deltas are filtered at write time so the table doesn't
    fill with noise. 0.5pp on an odds of 2.0 ≈ a price tick from 2.00 to
    2.02 — a real move, not just a flicker.
    """
    raw = os.environ.get("STEAM_DELTA_PP_MIN", "").strip()
    if not raw:
        return _DEFAULT_DELTA_PP_MIN
    try:
        val = float(raw)
    except ValueError:
        return _DEFAULT_DELTA_PP_MIN
    if val <= 0 or val > 50:
        return _DEFAULT_DELTA_PP_MIN
    return val


@dataclass(frozen=True)
class SteamSignal:
    """A detected steam move on one outcome."""

    event_id: str
    market: str
    outcome: str
    point: float | None
    scope: str
    direction: str  # 'up' or 'down'
    provider_count: int
    providers: tuple[str, ...]
    total_delta_pp: float  # sum of absolute deltas across providers
    first_seen: datetime
    last_seen: datetime

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "market": self.market,
            "outcome": self.outcome,
            "point": self.point,
            "scope": self.scope,
            "direction": self.direction,
            "provider_count": self.provider_count,
            "providers": list(self.providers),
            "total_delta_pp": round(self.total_delta_pp, 2),
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
        }


def detect_steam_moves(
    db: Session,
    window_min: int | None = None,
    min_provider_count: int | None = None,
) -> list[SteamSignal]:
    """Query `odds_movements` for the last N minutes and return signals.

    Pure read-only — safe to call from API routes or scanner. Empty list
    when the feature is disabled (no rows are being written).
    """
    if not is_enabled():
        return []

    window = window_min if window_min is not None else window_minutes()
    threshold = min_provider_count if min_provider_count is not None else min_providers()
    cutoff = datetime.now(UTC) - timedelta(minutes=window)

    from ..db.models import OddsMovement

    rows = (
        db.query(OddsMovement).filter(OddsMovement.recorded_at >= cutoff).order_by(OddsMovement.recorded_at.asc()).all()
    )

    # Group by (event, market, outcome, point, scope, direction) and
    # collect distinct providers within each group.
    grouped: dict[tuple, dict] = {}
    for r in rows:
        key = (r.event_id, r.market, r.outcome, r.point, r.scope, r.direction)
        entry = grouped.setdefault(
            key,
            {
                "providers": set(),
                "total_delta_pp": 0.0,
                "first_seen": r.recorded_at,
                "last_seen": r.recorded_at,
            },
        )
        entry["providers"].add(r.provider_id)
        entry["total_delta_pp"] += abs(r.delta_implied_pp or 0.0)
        if r.recorded_at < entry["first_seen"]:
            entry["first_seen"] = r.recorded_at
        if r.recorded_at > entry["last_seen"]:
            entry["last_seen"] = r.recorded_at

    signals: list[SteamSignal] = []
    for key, entry in grouped.items():
        if len(entry["providers"]) < threshold:
            continue
        event_id, market, outcome, point, scope, direction = key
        signals.append(
            SteamSignal(
                event_id=event_id,
                market=market,
                outcome=outcome,
                point=point,
                scope=scope,
                direction=direction,
                provider_count=len(entry["providers"]),
                providers=tuple(sorted(entry["providers"])),
                total_delta_pp=entry["total_delta_pp"],
                first_seen=entry["first_seen"],
                last_seen=entry["last_seen"],
            )
        )

    # Strongest signals first (most providers, then most movement)
    signals.sort(key=lambda s: (s.provider_count, s.total_delta_pp), reverse=True)
    return signals


def lookup_signal_for_outcome(
    db: Session,
    event_id: str | None,
    market: str | None,
    outcome: str | None,
    point: float | None = None,
    scope: str = "ft",
) -> dict | None:
    """Hot-path lookup for a single value bet.

    Returns the steam signal dict for the (event, market, outcome,
    point, scope) key if one was detected within the recent window —
    used by the scanner to annotate `ValueBet.steam_signal`.

    Fails open: any error returns None so a guard bug never blocks
    placements.
    """
    if not is_enabled() or not event_id or not market or not outcome:
        return None
    try:
        signals = detect_steam_moves(db)
    except Exception:
        return None
    for s in signals:
        if (
            s.event_id == event_id
            and s.market == market
            and s.outcome == outcome
            and s.point == point
            and s.scope == scope
        ):
            return s.to_dict()
    return None


def purge_old_movements(db: Session, retention_hours: int = 24) -> int:
    """Delete movement rows older than `retention_hours`. Safe to call
    from a scheduled cleanup; returns deleted-row count.

    `odds_movements` is append-only and only useful in the 5-min steam
    window — anything past a day is dead weight that just bloats the
    table and slows down the recent-window query.
    """
    from ..db.models import OddsMovement

    cutoff = datetime.now(UTC) - timedelta(hours=retention_hours)
    deleted = db.query(OddsMovement).filter(OddsMovement.recorded_at < cutoff).delete(synchronize_session=False)
    return int(deleted or 0)
