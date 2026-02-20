"""
Auto-settlement service — settles bets using live scores captured from Pinnacle.

Pinnacle's matchup API provides live scores (participants[].state.score) during extraction.
These are stored on the Event model (home_score, away_score, match_status).

FT detection: when a live event disappears from Pinnacle's API, the orchestrator marks it
as match_status="finished". This service then settles all pending bets on finished events.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..db.models import Bet, Event, Odds
from .bet_service import BetService

logger = logging.getLogger(__name__)


# ── Settlement determination (pure function, unchanged) ───────────────

def determine_bet_result(
    home_score: int,
    away_score: int,
    market: str,
    outcome: str,
    point: Optional[float] = None,
) -> Optional[str]:
    """Determine bet outcome from match score.

    Returns: "won", "lost", "void", or None if cannot determine.
    """
    if market in ("1x2", "moneyline"):
        if home_score > away_score:
            winner = "home"
        elif away_score > home_score:
            winner = "away"
        else:
            winner = "draw"

        if outcome == winner:
            return "won"

        # Moneyline: draw = void (push)
        if market == "moneyline" and winner == "draw":
            return "void"

        return "lost"

    elif market == "spread":
        if point is None:
            return None

        margin = home_score - away_score
        if outcome == "home":
            adjusted = margin + point
        elif outcome == "away":
            # Away point is stored as their handicap (e.g., +9.5),
            # so: away_margin + point = -margin + point
            adjusted = -margin + point
        else:
            return None

        if adjusted > 0:
            return "won"
        elif adjusted == 0:
            return "void"
        else:
            return "lost"

    elif market == "total":
        if point is None:
            return None

        total = home_score + away_score
        if outcome == "over":
            if total > point:
                return "won"
            elif total == point:
                return "void"
            else:
                return "lost"
        elif outcome == "under":
            if total < point:
                return "won"
            elif total == point:
                return "void"
            else:
                return "lost"

    return None


# ── Main service ──────────────────────────────────────────────────────

class ResultsService:
    """Settles pending bets using Pinnacle live scores stored on Event model."""

    def __init__(self, db: Session):
        self.db = db
        self.bet_service = BetService(db)

    def auto_settle(self) -> dict:
        """Auto-settle all eligible pending bets on finished events.

        Flow:
        1. Find pending bets on events with match_status="finished"
        2. Use stored home_score/away_score to determine outcome
        3. Settle via BetService.settle_bet()

        Returns: {checked, settled, skipped, results: [...]}
        """
        # Find pending bets on finished events with scores
        pending_bets = (
            self.db.query(Bet)
            .join(Event, Event.id == Bet.event_id)
            .filter(
                Bet.result == "pending",
                Bet.event_id.isnot(None),
                Event.match_status == "finished",
                Event.home_score.isnot(None),
                Event.away_score.isnot(None),
            )
            .all()
        )

        if not pending_bets:
            return {"checked": 0, "settled": 0, "skipped": 0, "results": []}

        checked = 0
        settled = 0
        skipped = 0
        settlement_results = []

        for bet in pending_bets:
            checked += 1

            event = self.db.query(Event).filter(Event.id == bet.event_id).first()
            if not event:
                skipped += 1
                continue

            # Resolve point for spread/total
            point = bet.point
            if point is None and bet.market in ("spread", "total"):
                odds_row = self.db.query(Odds).filter(
                    Odds.event_id == bet.event_id,
                    Odds.provider_id == bet.provider_id,
                    Odds.market == bet.market,
                    Odds.outcome == bet.outcome,
                ).first()
                if odds_row and odds_row.point is not None:
                    point = odds_row.point
                else:
                    skipped += 1
                    logger.debug(
                        f"[ResultsService] Skipping bet #{bet.id}: "
                        f"no point for {bet.market} {bet.outcome}"
                    )
                    continue

            # Determine result from stored scores
            result = determine_bet_result(
                event.home_score,
                event.away_score,
                bet.market,
                bet.outcome,
                point,
            )

            if result is None:
                skipped += 1
                continue

            # Calculate payout
            if result == "won":
                payout = bet.stake * bet.odds
            elif result == "void":
                payout = bet.stake  # Return stake on push
            else:
                payout = 0.0

            # Settle via existing BetService (handles CLV, balance, bonus advance)
            settle_result = self.bet_service.settle_bet(bet.id, result, payout)

            if settle_result.get("success"):
                bet.settlement_source = "auto_pinnacle"
                settled += 1
                score_str = f"{event.home_score}-{event.away_score}"
                settlement_results.append({
                    "bet_id": bet.id,
                    "event": f"{event.home_team} vs {event.away_team}",
                    "result": result,
                    "payout": round(payout, 2),
                    "score": score_str,
                })
                logger.info(
                    f"[ResultsService] Auto-settled bet #{bet.id}: "
                    f"{result} ({score_str}) — {event.home_team} vs {event.away_team}"
                )

        if settled > 0:
            self.db.commit()

        logger.info(f"[ResultsService] Auto-settled {settled}/{checked} bets ({skipped} skipped)")

        return {
            "checked": checked,
            "settled": settled,
            "skipped": skipped,
            "results": settlement_results,
        }
