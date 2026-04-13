"""Bets API routes."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import tuple_
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ...analysis.devig import get_fair_odds_for_outcome
from ...db.models import Event, Odds, SpecialOdds
from ...repositories import BetRepo, ProfileRepo
from ...services import BetService
from ..deps import get_db, get_db_writer
from ..schemas import BatchBetCreate, BetCreate, BetEdit, BetUpdate
from .providers import load_provider_site_urls

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bets", tags=["bets"])


def _boost_event_str(bet, sp) -> str | None:
    """Get boost event string from bet record or specials fallback."""
    if bet.boost_event:
        return bet.boost_event
    if sp and sp.event:
        return sp.event
    return None


def _boost_home(bet, sp) -> str | None:
    ev_str = _boost_event_str(bet, sp)
    if ev_str and " vs " in ev_str:
        return ev_str.split(" vs ")[0].strip()
    return None


def _boost_away(bet, sp) -> str | None:
    ev_str = _boost_event_str(bet, sp)
    if ev_str and " vs " in ev_str:
        return ev_str.split(" vs ")[1].strip()
    return None


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


def _predict_result(bet, event) -> str | None:
    """Predict bet result from event scores or winner data.

    For BO series sports (esports/tennis), can predict moneyline result
    when the series is clinched (e.g., 2-0 in BO3) even before match is finished.
    """
    import json as _json

    from ...services.results_service import determine_bet_result

    if not event:
        return None

    # For BO series (esports/tennis): predict when series is clinched
    if event.match_status == "live" and event.sport in ("esports", "tennis"):
        if event.home_score is not None and event.away_score is not None:
            bo = _get_bo_format(event)
            wins_needed = (bo + 1) // 2  # BO3→2, BO5→3
            if event.home_score >= wins_needed or event.away_score >= wins_needed:
                # Series clinched — can determine moneyline result
                # (spread/total may still change with remaining maps/sets)
                market = bet.market or ""
                if "_" in market:
                    market = market.split("_", 1)[0]
                if market in ("1x2", "moneyline"):
                    return determine_bet_result(
                        event.home_score,
                        event.away_score,
                        market,
                        bet.outcome,
                        bet.point,
                    )

    if event.match_status != "finished":
        return None

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

    # Path 0: Market resolution (Polymarket total/spread — bypasses stale scores)
    if market in ("total", "spread") and event.stats_json:
        try:
            stats = _json.loads(event.stats_json)
            resolved_markets = stats.get("resolved_markets", {})
            resolution = resolved_markets.get(bet.market)  # e.g., "total_226.5" → "over"
            if resolution:
                return "won" if bet.outcome == resolution else "lost"
        except (ValueError, TypeError):
            pass

    # Path 1: Score-based
    if event.home_score is not None and event.away_score is not None:
        return determine_bet_result(
            event.home_score,
            event.away_score,
            market,
            bet.outcome,
            point,
        )

    # Path 2: Winner-based (from Polymarket outcomePrices)
    if bet.market in ("1x2", "moneyline") and event.stats_json:
        try:
            stats = _json.loads(event.stats_json)
            winner = stats.get("winner")
            if winner:
                from ...matching.matcher import get_team_match_score

                home_match = get_team_match_score(winner, event.home_team)
                away_match = get_team_match_score(winner, event.away_team)
                if home_match > away_match and home_match >= 75:
                    actual_winner = "home"
                elif away_match > home_match and away_match >= 75:
                    actual_winner = "away"
                else:
                    return None
                if bet.outcome == actual_winner:
                    return "won"
                elif bet.market == "moneyline" and actual_winner == "draw":
                    return "void"
                else:
                    return "lost"
        except (ValueError, TypeError):
            pass

    return None


def _get_service(db: Session = Depends(get_db)) -> BetService:
    return BetService(db)


@router.get("")
def list_bets(
    status: str | None = None,
    exclude_bonus: bool = False,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Get bet history for active profile."""
    profile_repo = ProfileRepo(db)
    bet_repo = BetRepo(db)
    profile = profile_repo.get_active()

    bets = bet_repo.list_for_profile(profile.id, status=status, exclude_bonus=exclude_bonus, limit=limit)
    site_urls = load_provider_site_urls()

    # Pre-fetch events for team name resolution
    event_ids = [b.event_id for b in bets if b.event_id]
    events_map = {}
    if event_ids:
        events = db.query(Event).filter(Event.id.in_(event_ids)).all()
        events_map = {e.id: e for e in events}

    # Pre-fetch specials data for boost bets (event name, sport, time)
    boost_titles = [b.outcome for b in bets if b.market == "boost" and b.outcome]
    specials_map: dict[str, SpecialOdds] = {}
    if boost_titles:
        specials = db.query(SpecialOdds).filter(SpecialOdds.title.in_(boost_titles)).all()
        for s in specials:
            specials_map[s.title] = s

    # Pre-fetch Pinnacle odds for de-vigging (compute edge/prob on the fly)
    pinnacle_map: dict[tuple[str, str], dict[str, float]] = {}  # (event_id, market) -> {outcome: odds}
    if event_ids:
        pin_rows = (
            db.query(Odds)
            .filter(
                Odds.event_id.in_(event_ids),
                Odds.provider_id == "pinnacle",
            )
            .all()
        )
        for row in pin_rows:
            key = (row.event_id, row.market)
            if key not in pinnacle_map:
                pinnacle_map[key] = {}
            pinnacle_map[key][row.outcome] = row.odds

    # Pre-fetch current provider odds for pending bets (for live ODDS column)
    pending_lookups = [(b.event_id, b.provider_id) for b in bets if b.result == "pending" and b.event_id]
    # (event_id, provider_id, market, outcome, point) -> current odds
    current_odds_map: dict[tuple, float] = {}
    if pending_lookups:
        # Only fetch odds for the specific (event_id, provider_id) pairs we need
        pending_pairs = list(set(pending_lookups))
        provider_rows = (
            db.query(Odds)
            .filter(
                tuple_(Odds.event_id, Odds.provider_id).in_(pending_pairs),
            )
            .all()
        )
        for row in provider_rows:
            current_odds_map[(row.event_id, row.provider_id, row.market, row.outcome, row.point)] = row.odds

    bet_list = []
    for b in bets:
        ev = events_map.get(b.event_id) if b.event_id else None
        sp = specials_map.get(b.outcome) if b.market == "boost" and b.outcome else None

        # Edge at placement: compute from stored fair_odds_at_placement
        placed_edge_pct = None
        if b.fair_odds_at_placement and b.fair_odds_at_placement > 1.0:
            placed_edge_pct = round((b.odds / b.fair_odds_at_placement - 1) * 100, 2)
        elif b.utility_score:
            placed_edge_pct = round(b.utility_score * 100, 2)

        # Current values from latest Pinnacle odds
        fair_odds = None
        edge_pct = None
        sel_prob = None
        current_odds = None

        if b.event_id and b.market and b.outcome:
            # Current provider odds from Odds table (keyed by point for spread/total)
            current_odds = current_odds_map.get((b.event_id, b.provider_id, b.market, b.outcome, b.point))

            pin_market = pinnacle_map.get((b.event_id, b.market), {})
            if len(pin_market) >= 2 and b.outcome in pin_market:
                fair = get_fair_odds_for_outcome(b.outcome, pin_market, method="multiplicative")
                if fair and fair > 1.0:
                    fair_odds = round(fair, 3)
                    sel_prob = round(1.0 / fair, 4)
                    # Current edge: use current provider odds if available, else placed odds
                    live_odds = current_odds if current_odds else b.odds
                    edge_pct = round((live_odds / fair - 1) * 100, 2)

        # For settled bets, fall back to stored values
        if edge_pct is None and placed_edge_pct is not None:
            edge_pct = placed_edge_pct

        # Fair win probability from stored fair odds at placement
        if sel_prob is None and b.fair_odds_at_placement and b.fair_odds_at_placement > 1.0:
            sel_prob = round(1.0 / b.fair_odds_at_placement, 4)

        bet_list.append(
            {
                "id": b.id,
                "event_id": b.event_id,
                "provider": b.provider_id,
                "market": b.market,
                "outcome": b.outcome,
                "odds": b.odds,
                "stake": b.stake,
                "currency": b.currency or "SEK",
                "is_bonus": b.is_bonus,
                "bonus_type": b.bonus_type,
                "result": b.result,
                "payout": b.payout,
                "profit": b.profit,
                "roi_pct": b.roi_pct,
                "placed_at": b.placed_at.isoformat() if b.placed_at else None,
                "settled_at": b.settled_at.isoformat() if b.settled_at else None,
                "risk_score": b.risk_score_at_bet,
                "clv_pct": b.clv_pct,
                "closing_odds": b.closing_odds,
                "provider_closing_odds": b.provider_closing_odds,
                "provider_clv_pct": b.provider_clv_pct,
                "edge_pct": edge_pct,
                "fair_odds": fair_odds,
                "selection_probability": sel_prob,
                "placed_edge_pct": placed_edge_pct,
                "fair_odds_at_placement": b.fair_odds_at_placement,
                "current_odds": current_odds,
                "point": b.point,
                "settlement_source": b.settlement_source,
                "home_team": ev.home_team if ev else (_boost_home(b, sp)),
                "away_team": ev.away_team if ev else (_boost_away(b, sp)),
                "display_home": ev.display_home if ev else None,
                "display_away": ev.display_away if ev else None,
                "sport": ev.sport if ev else (sp.sport if sp and sp.sport != "unknown" else None),
                "league": ev.league if ev else (sp.league if sp else None),
                "start_time": (b.start_time.isoformat() + "Z")
                if b.start_time
                else ((ev.start_time.isoformat() + "Z") if ev and ev.start_time else None),
                "home_score": ev.home_score if ev else None,
                "away_score": ev.away_score if ev else None,
                "match_status": ev.match_status if ev else None,
                "match_minute": ev.match_minute if ev else None,
                "match_period": ev.match_period if ev else None,
                "predicted_result": _predict_result(b, ev) if ev else None,
                "provider_site_url": site_urls.get(b.provider_id),
                "boost_title": b.boost_title or ((sp.llm_title or sp.title) if sp else None),
                "bet_type": b.bet_type,
            }
        )

    return {
        "profile_id": profile.id,
        "bets": bet_list,
        "count": len(bet_list),
    }


# Retry config for SQLite write lock contention during bet placement.
# Extraction bulk-inserts hold write locks for seconds at a time — without retry,
# bet commits fail silently and the bet is lost.
_BET_COMMIT_MAX_RETRIES = 4
_BET_COMMIT_BACKOFF_BASE = 0.3  # seconds (0.3, 0.6, 1.2, 2.4)


@router.post("")
async def create_bet(bet: BetCreate, db: Session = Depends(get_db_writer)):
    """Record a placed bet for active profile.

    Uses get_db_writer (no auto-commit) with manual commit + retry.
    On SQLite lock contention, rolls back and re-executes the full service
    method since rollback expunges pending objects.
    """
    for attempt in range(_BET_COMMIT_MAX_RETRIES):
        service = BetService(db)
        result = service.create_bet(
            event_id=bet.event_id,
            provider_id=bet.provider_id,
            market=bet.market,
            outcome=bet.outcome,
            odds=bet.odds,
            stake=bet.stake,
            point=bet.point,
            is_bonus=bet.is_bonus,
            bonus_type=bet.bonus_type,
            utility_score=bet.utility_score,
            selection_probability=bet.selection_probability,
            stake_noise_applied=bet.stake_noise_applied,
            fair_odds_at_placement=bet.fair_odds_at_placement,
            boost_event=bet.boost_event,
            boost_title=bet.boost_title,
            bet_type=bet.bet_type,
            start_time_str=bet.start_time,
        )

        if "error" in result:
            status_code = 404 if "not found" in result["error"] else 400
            raise HTTPException(status_code, result["error"])

        try:
            db.commit()
            return result
        except OperationalError as e:
            if "database is locked" in str(e) and attempt < _BET_COMMIT_MAX_RETRIES - 1:
                wait = _BET_COMMIT_BACKOFF_BASE * (2**attempt)
                logger.warning(
                    f"[Bets] Commit blocked by SQLite lock (attempt {attempt + 1}/"
                    f"{_BET_COMMIT_MAX_RETRIES}), retrying in {wait:.1f}s"
                )
                db.rollback()
                await asyncio.sleep(wait)
            else:
                logger.error(f"[Bets] Commit failed after {attempt + 1} attempts: {e}")
                raise

    raise HTTPException(503, "Database busy — please try again")


@router.post("/close-started")
def close_started_bets(service: BetService = Depends(_get_service)):
    """
    Snapshot closing Pinnacle odds for pending bets on events that have started.
    Call this to capture CLV before settling. Safe to call repeatedly —
    only processes bets where closing_odds is not yet set.
    """
    result = service.snapshot_closing_odds()
    return {"success": True, **result}


@router.post("/batch")
async def create_batch_bets(data: BatchBetCreate, db: Session = Depends(get_db_writer)):
    """
    Place multiple legs at once (dutch bet).
    Each leg is placed independently — if one fails, already-placed legs remain.
    Commits per-leg with retry to minimize lock contention impact.
    """
    if not data.legs:
        raise HTTPException(400, "No legs provided")

    results = []
    placed_count = 0
    total_staked = 0.0

    for i, leg in enumerate(data.legs):
        leg_placed = False
        for attempt in range(_BET_COMMIT_MAX_RETRIES):
            service = BetService(db)
            result = service.create_bet(
                event_id=leg.event_id,
                provider_id=leg.provider_id,
                market=leg.market,
                outcome=leg.outcome,
                odds=leg.odds,
                stake=leg.stake,
                point=leg.point,
                is_bonus=leg.is_bonus,
                bonus_type=leg.bonus_type,
                utility_score=leg.utility_score,
                selection_probability=leg.selection_probability,
                bet_type=leg.bet_type,
            )

            if "error" in result:
                results.append(
                    {
                        "leg_index": i,
                        "provider_id": leg.provider_id,
                        "outcome": leg.outcome,
                        "success": False,
                        "error": result["error"],
                    }
                )
                leg_placed = True  # Not placed, but handled
                break

            try:
                db.commit()
                placed_count += 1
                total_staked += leg.stake
                results.append(
                    {
                        "leg_index": i,
                        "provider_id": leg.provider_id,
                        "outcome": leg.outcome,
                        "success": True,
                        "bet_id": result["bet_id"],
                        "stake": leg.stake,
                        "odds": leg.odds,
                    }
                )
                leg_placed = True
                break
            except OperationalError as e:
                if "database is locked" in str(e) and attempt < _BET_COMMIT_MAX_RETRIES - 1:
                    wait = _BET_COMMIT_BACKOFF_BASE * (2**attempt)
                    logger.warning(f"[Bets:batch] Leg {i} commit blocked (attempt {attempt + 1})")
                    db.rollback()
                    await asyncio.sleep(wait)
                else:
                    results.append(
                        {
                            "leg_index": i,
                            "provider_id": leg.provider_id,
                            "outcome": leg.outcome,
                            "success": False,
                            "error": "Database busy",
                        }
                    )
                    db.rollback()
                    leg_placed = True
                    break

        if not leg_placed:
            results.append(
                {
                    "leg_index": i,
                    "provider_id": leg.provider_id,
                    "outcome": leg.outcome,
                    "success": False,
                    "error": "Database busy after retries",
                }
            )

    return {
        "success": placed_count > 0,
        "placed_count": placed_count,
        "total_legs": len(data.legs),
        "total_staked": round(total_staked, 2),
        "results": results,
    }


@router.put("/{bet_id}")
def settle_bet(bet_id: int, data: BetUpdate, service: BetService = Depends(_get_service)):
    """Settle a bet with result."""
    result = service.settle_bet(bet_id, data.result, data.payout)

    if "error" in result:
        raise HTTPException(404, result["error"])

    return result


@router.patch("/{bet_id}")
def edit_bet(bet_id: int, data: BetEdit, service: BetService = Depends(_get_service)):
    """Edit a bet's stake, odds, or result. Recalculates payout and adjusts balance."""
    result = service.edit_bet(
        bet_id,
        stake=data.stake,
        odds=data.odds,
        result=data.result,
        payout=data.payout,
    )

    if "error" in result:
        raise HTTPException(404, result["error"])

    return result


@router.delete("/{bet_id}")
def delete_bet(bet_id: int, service: BetService = Depends(_get_service)):
    """Delete a pending bet that was incorrectly recorded.

    Only pending bets can be deleted. Settled bets must be voided via PATCH.
    """
    result = service.delete_bet(bet_id)

    if "error" in result:
        status_code = 404 if "not found" in result["error"] else 400
        raise HTTPException(status_code, result["error"])

    return result
