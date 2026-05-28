"""Open-position re-hedge scanner.

Periodically scans `bets WHERE result='pending' AND start_time > now`
and looks for post-placement middles (Case 1) — NFL spreads/totals where
the line has moved through a key number since we placed.

Distinct from analysis/scanner.py (which scans current market for value
and arb from scratch). This scanner's search space is what we already own.

Phase 1: read-only — emits RehedgeCandidate objects. Upsert into
`opportunities` and placement wiring land in later tasks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.orm import Session

from src.db.models import Bet, Event

RehedgeCase = Literal["post_placement_middle", "clv_inversion_salvage"]


@dataclass(frozen=True)
class RehedgeCandidate:
    """A re-hedge action recommended for an open bet.

    bet_id: the open bet this candidate hedges
    case: which classifier emitted it
    hedge_*: the side we want to take to hedge
    recommended_stake_base: stake in SEK (Betty's base currency)
    metadata: case-specific context — key_number, wing_loss_pct,
        inversion_pct, etc. Surfaced to UI as-is.
    """

    bet_id: int
    case: RehedgeCase
    hedge_provider: str
    hedge_market: str
    hedge_outcome: str
    hedge_point: float | None
    hedge_odds: float
    recommended_stake_base: float
    base_currency: str
    metadata: dict = field(default_factory=dict)


def _query_open_bets(db: Session) -> list[Bet]:
    """Return pending bets on future events that are scannable for rehedge.

    Filters:
    - result == 'pending'
    - event_id IS NOT NULL (excludes boost / free-text bets)
    - Event.start_time > now (excludes started/live events — out of scope)
    """
    now = datetime.now(UTC)
    return (
        db.query(Bet)
        .join(Event, Event.id == Bet.event_id)
        .filter(
            Bet.result == "pending",
            Bet.event_id.isnot(None),
            Event.start_time.isnot(None),
            Event.start_time > now,
        )
        .all()
    )


def scan_open_positions(db: Session) -> list[RehedgeCandidate]:
    """Scan all open pending bets, return emit-able rehedge candidates.

    Stateless — caller owns the session and is responsible for upserting
    the results into `opportunities`.
    """
    return []  # implemented in later tasks
