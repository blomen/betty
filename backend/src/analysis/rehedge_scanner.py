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

from local.mirror.arb_math import brackets_key_number, middle_size
from sqlalchemy.orm import Session

from src.analysis.key_numbers import (
    NFL_SPREAD_KEY_NUMBERS,
    NFL_TOTAL_KEY_NUMBERS,
    is_nfl,
)
from src.config.loader import get_exchange_rate
from src.db.models import Bet, Event, Odds

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


_SPREAD_MARKETS = {"spread", "handicap", "runline", "puckline"}
_TOTAL_MARKETS = {"total", "totals", "over_under", "ou"}

# Tuning knobs — see survey §3b "What kills it"
MAX_WING_LOSS_PCT = 0.025  # 2.5% of total stake — anything bigger means
# the middle bet costs more than its expected value.
TARGET_WING_LOSS_PCT = 0.01  # 1% — what we aim for when sizing


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


def _opposite_outcome(market: str | None, outcome: str | None) -> str | None:
    """Return the symmetric opposite outcome for spread/total markets.

    Returns None for markets where no symmetric opposite exists
    (1x2 has a draw, moneyline has no point). The scanner Case 1
    requires a point, so those markets are dropped here.
    """
    if not market or not outcome:
        return None
    m = market.lower()
    o = outcome.lower()
    if m in _SPREAD_MARKETS:
        return {"home": "away", "away": "home"}.get(o)
    if m in _TOTAL_MARKETS:
        return {"over": "under", "under": "over"}.get(o)
    return None


def _opposite_point(market: str | None, point: float | None) -> float | None:
    """Return the point value used to query the opposite side.

    For spreads, the opposite side has the negated point (home -2.5
    corresponds to away +2.5; Betty stores both as separate Odds rows
    with opposite-signed points). For totals, the same point line
    applies to both over and under.
    """
    if point is None or not market:
        return None
    if market.lower() in _SPREAD_MARKETS:
        return -point
    if market.lower() in _TOTAL_MARKETS:
        return point
    return None


def _bet_stake_sek(bet: Bet) -> float:
    """Convert a bet's native-currency stake to SEK via the provider's rate.

    Currency conversion is the #1 hidden source of off-by-5×-10× sizing
    bugs in Betty (CLAUDE.md "first hypothesis when sizing looks off").
    `get_exchange_rate(provider_id)` returns 1.0 for SEK-denominated
    providers (Swedish softs + this user's Pinnacle account) and the
    correct multiplier (≈10) for USD/USDC providers.
    """
    rate = get_exchange_rate(bet.provider_id)
    return bet.stake * rate


def _keys_for_market(market: str) -> tuple[int, ...]:
    m = market.lower()
    if m in _SPREAD_MARKETS:
        return NFL_SPREAD_KEY_NUMBERS
    if m in _TOTAL_MARKETS:
        return NFL_TOTAL_KEY_NUMBERS
    return ()


def _classify_case1(db: Session, bet: Bet) -> RehedgeCandidate | None:
    """Case 1: post-placement middle on NFL spreads/totals.

    Skip if: bet's event isn't NFL, bet isn't spread/total, bet has no
    point. Otherwise look across providers for an opposite-side quote at
    a point that brackets a key number. Emit the best (lowest wing-loss)
    candidate.
    """
    if not is_nfl(bet.event.sport):
        return None
    keys = _keys_for_market(bet.market or "")
    if not keys or bet.point is None:
        return None

    opp_outcome = _opposite_outcome(bet.market, bet.outcome)
    if opp_outcome is None:
        return None

    # Query all opposite-side quotes on this event/market/scope.
    candidates_q = (
        db.query(Odds)
        .filter(
            Odds.event_id == bet.event_id,
            Odds.market == bet.market,
            Odds.outcome == opp_outcome,
            Odds.scope == "ft",
            Odds.provider_id != bet.provider_id,  # never hedge at the same book
        )
        .all()
    )

    best: RehedgeCandidate | None = None
    best_wing: float = float("inf")
    bet_stake_sek = _bet_stake_sek(bet)

    for opp in candidates_q:
        key = brackets_key_number(bet.point, _opposite_point(bet.market, opp.point), keys)
        if key is None:
            continue

        # Size at our target wing-loss; verify the achieved wing-loss
        # is within the absolute cap.
        stake_b = middle_size(bet_stake_sek, bet.odds, opp.odds, TARGET_WING_LOSS_PCT)
        if stake_b <= 0:
            continue
        total = bet_stake_sek + stake_b
        wing_loss = total - min(bet_stake_sek * bet.odds, stake_b * opp.odds)
        wing_pct = wing_loss / total if total > 0 else float("inf")
        if wing_pct > MAX_WING_LOSS_PCT:
            continue

        if wing_pct < best_wing:
            best_wing = wing_pct
            best = RehedgeCandidate(
                bet_id=bet.id,
                case="post_placement_middle",
                hedge_provider=opp.provider_id,
                hedge_market=bet.market,
                hedge_outcome=opp_outcome,
                hedge_point=opp.point,
                hedge_odds=opp.odds,
                recommended_stake_base=round(stake_b, 2),
                base_currency="SEK",
                metadata={
                    "key_number": key,
                    "wing_loss_pct": round(wing_pct, 4),
                    "original_bet_provider": bet.provider_id,
                    "original_bet_odds": bet.odds,
                    "original_bet_point": bet.point,
                    "original_bet_stake_sek": round(bet_stake_sek, 2),
                },
            )
    return best


def scan_open_positions(db: Session) -> list[RehedgeCandidate]:
    """Scan all open pending bets, return emit-able rehedge candidates.

    Stateless — caller owns the session and is responsible for upserting
    the results into `opportunities` (see persist_rehedge_candidates).
    """
    out: list[RehedgeCandidate] = []
    for bet in _query_open_bets(db):
        c = _classify_case1(db, bet)
        if c is not None:
            out.append(c)
    return out
