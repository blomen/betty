"""
Auto-settlement service — settles bets using scores from Pinnacle and Polymarket.

Score sources:
1. Pinnacle: live scores captured during extraction (home_score, away_score on Event)
2. Polymarket: definitive scores from Gamma API (score field + outcomePrices resolution)

FT detection:
- Pinnacle: when a live event disappears from API, orchestrator marks it "finished"
- Polymarket: events with ended=True or resolved outcomePrices (1/0) are definitively finished
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..db.models import Bet, Event, Odds
from .bet_service import BetService

logger = logging.getLogger(__name__)


# ── Settlement determination (pure function) ──────────────────────────

def determine_bet_result(
    home_score: int,
    away_score: int,
    market: str,
    outcome: str,
    point: Optional[float] = None,
) -> Optional[str]:
    """Determine bet outcome from match score.

    Score semantics vary by sport:
    - Football/basketball/ice_hockey/baseball: goals/points/runs
    - Tennis: sets won
    - Esports: maps won

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
    """Settles pending bets using scores from Pinnacle and Polymarket."""

    def __init__(self, db: Session):
        self.db = db
        self.bet_service = BetService(db)

    @staticmethod
    def _get_bo_format(event) -> int:
        """Get best-of format from stats_json, or sport default (3)."""
        if event.stats_json:
            import json as _json
            try:
                stats = _json.loads(event.stats_json)
                bo = stats.get("bo")
                if bo:
                    return bo
            except (ValueError, TypeError):
                pass
        return 3

    @staticmethod
    def _is_series_clinched(event) -> bool:
        """Check if a BO series is clinched (one side has enough wins)."""
        if event.sport not in ("esports", "tennis"):
            return False
        if event.home_score is None or event.away_score is None:
            return False
        bo = ResultsService._get_bo_format(event)
        wins_needed = (bo + 1) // 2
        return event.home_score >= wins_needed or event.away_score >= wins_needed

    def auto_settle(self, source: str = "auto") -> dict:
        """Auto-settle all eligible pending bets on finished or clinched events.

        Three settlement paths:
        1. Score-based: events with home_score/away_score → determine_bet_result()
        2. Winner-based: moneyline bets on events with winner stored in stats_json
           (from Polymarket outcomePrices resolution — no scores needed)
        3. BO-clinched: esports/tennis moneyline bets where series is decided
           (e.g., 2-0 in BO3) — settled even before match_status="finished"

        Args:
            source: Settlement source label ("auto", "auto_pinnacle", "auto_polymarket")

        Returns: {checked, settled, skipped, results: [...]}
        """
        # Find pending Polymarket bets on finished events OR live BO-series events
        # Only auto-settle Polymarket bets — other providers are settled manually
        from sqlalchemy import or_
        pending_bets = (
            self.db.query(Bet)
            .join(Event, Event.id == Bet.event_id)
            .filter(
                Bet.result == "pending",
                Bet.event_id.isnot(None),
                Bet.provider_id == "polymarket",
                or_(
                    Event.match_status == "finished",
                    # Also include live esports/tennis for BO-clinch check
                    (Event.match_status == "live") & (Event.sport.in_(["esports", "tennis"])),
                ),
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

            result = None
            score_str = "n/a"

            # Normalize market: "total_226.5" → "total", extract embedded point
            market = bet.market or ""
            point = bet.point
            if "_" in market:
                parts = market.split("_", 1)
                market = parts[0]
                if point is None:
                    try:
                        point = float(parts[1])
                    except (ValueError, IndexError):
                        pass

            # For live BO events, only settle moneyline when series is clinched
            if event.match_status == "live":
                if not self._is_series_clinched(event) or market not in ("1x2", "moneyline"):
                    skipped += 1
                    continue

            if event.home_score is not None and event.away_score is not None:
                # Path 1: Score-based settlement
                if point is None and market in ("spread", "total"):
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
                        continue

                result = determine_bet_result(
                    event.home_score, event.away_score,
                    market, bet.outcome, point,
                )
                score_str = f"{event.home_score}-{event.away_score}"

            elif market in ("1x2", "moneyline") and event.stats_json:
                # Path 2: Winner-based settlement (from Polymarket outcomePrices)
                import json as _json
                try:
                    stats = _json.loads(event.stats_json)
                    winner = stats.get("winner")
                    if winner:
                        from ..matching.matcher import get_team_match_score
                        home_match = get_team_match_score(winner, event.home_team)
                        away_match = get_team_match_score(winner, event.away_team)
                        if home_match > away_match and home_match >= 75:
                            actual_winner = "home"
                        elif away_match > home_match and away_match >= 75:
                            actual_winner = "away"
                        else:
                            skipped += 1
                            continue

                        if bet.outcome == actual_winner:
                            result = "won"
                        elif bet.market == "moneyline" and actual_winner == "draw":
                            result = "void"
                        else:
                            result = "lost"
                        score_str = f"winner:{winner}"
                except (ValueError, TypeError):
                    pass

            if result is None:
                skipped += 1
                continue

            # Calculate payout
            if result == "won":
                payout = bet.stake * bet.odds
            elif result == "void":
                payout = bet.stake
            else:
                payout = 0.0

            settle_result = self.bet_service.settle_bet(bet.id, result, payout)

            if settle_result.get("success"):
                bet.settlement_source = source
                settled += 1
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

    def update_scores_from_polymarket(self, resolved_events: list[dict]) -> dict:
        """Update Event scores and winner from Polymarket resolved event data.

        Matches resolved Polymarket events to canonical events and updates
        home_score, away_score, match_status, and winner (via stats_json).

        Args:
            resolved_events: List from PolymarketRetriever.fetch_resolved()

        Returns: {matched, updated, skipped}
        """
        from ..matching.normalizer import generate_canonical_id
        from ..matching.matcher import get_team_match_score

        matched = 0
        updated = 0
        skipped = 0

        for rev in resolved_events:
            home = rev.get("home_team")
            away = rev.get("away_team")
            sport = rev.get("sport")
            home_score = rev.get("home_score")
            away_score = rev.get("away_score")

            if not home or not away or not sport or sport == "unknown":
                skipped += 1
                continue

            # Try to find canonical event
            start_time = rev.get("start_time")
            canonical_id = generate_canonical_id(sport, home, away, start_time)
            swapped_id = generate_canonical_id(sport, away, home, start_time)

            db_event = self.db.query(Event).filter(Event.id == canonical_id).first()
            teams_swapped = False

            if not db_event:
                db_event = self.db.query(Event).filter(Event.id == swapped_id).first()
                if db_event:
                    teams_swapped = True

            if not db_event:
                # Fuzzy match: find events in same sport on same date
                if isinstance(start_time, str):
                    date_str = start_time.split("T")[0].replace("-", "")
                else:
                    skipped += 1
                    continue

                # Query events on that date (within ±1 day)
                from datetime import timedelta
                try:
                    target_date = datetime.strptime(date_str, "%Y%m%d")
                except (ValueError, TypeError):
                    skipped += 1
                    continue

                candidates = (
                    self.db.query(Event)
                    .filter(
                        Event.sport == sport,
                        Event.start_time >= target_date - timedelta(days=1),
                        Event.start_time <= target_date + timedelta(days=2),
                    )
                    .all()
                )

                best_score = 0
                best_event = None
                best_swapped = False

                for cand in candidates:
                    # Direct match
                    h_direct = get_team_match_score(home, cand.home_team)
                    a_direct = get_team_match_score(away, cand.away_team)
                    # Swapped match
                    h_swapped = get_team_match_score(home, cand.away_team)
                    a_swapped = get_team_match_score(away, cand.home_team)

                    direct_avg = (h_direct + a_direct) / 2
                    swapped_avg = (h_swapped + a_swapped) / 2

                    is_swapped = swapped_avg > direct_avg
                    avg = max(direct_avg, swapped_avg)
                    t1, t2 = (h_swapped, a_swapped) if is_swapped else (h_direct, a_direct)

                    if avg >= 85 and min(t1, t2) >= 75 and avg > best_score:
                        best_score = avg
                        best_event = cand
                        best_swapped = is_swapped

                if best_event:
                    db_event = best_event
                    teams_swapped = best_swapped

            if not db_event:
                skipped += 1
                continue

            matched += 1

            # Update scores if available from API (closed events often lack scores,
            # but they may have been captured during regular extraction while live)
            if home_score is not None and away_score is not None:
                if teams_swapped:
                    db_event.home_score = away_score
                    db_event.away_score = home_score
                else:
                    db_event.home_score = home_score
                    db_event.away_score = away_score

            # Store winner from outcomePrices resolution (for scoreless settlement)
            winner_team = rev.get("winner_team")
            if winner_team:
                import json as _json
                try:
                    stats = _json.loads(db_event.stats_json) if db_event.stats_json else {}
                except (ValueError, TypeError):
                    stats = {}
                stats["winner"] = winner_team
                db_event.stats_json = _json.dumps(stats)

            # Always mark as finished — scores were likely already captured during extraction
            if db_event.match_status != "finished":
                db_event.match_status = "finished"
                updated += 1

        if matched > 0:
            self.db.commit()

        logger.info(
            f"[ResultsService] Polymarket scores: {matched} matched, "
            f"{updated} newly finished, {skipped} skipped"
        )

        return {"matched": matched, "updated": updated, "skipped": skipped}
