"""Bets API routes."""

import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from ...services import BetService
from ...repositories import BetRepo, ProfileRepo
from ...db.models import Odds, Event, SpecialOdds
from ...analysis.devig import get_fair_odds_for_outcome
from ..deps import get_db
from ..schemas import BetCreate, BetUpdate, BetEdit, BatchBetCreate
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


def _get_service(db: Session = Depends(get_db)) -> BetService:
    return BetService(db)


@router.get("")
async def list_bets(
    status: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Get bet history for active profile."""
    profile_repo = ProfileRepo(db)
    bet_repo = BetRepo(db)
    profile = profile_repo.get_active()

    bets = bet_repo.list_for_profile(profile.id, status=status, limit=limit)
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
    pending_lookups = [
        (b.event_id, b.provider_id, b.market, b.outcome)
        for b in bets
        if b.result == "pending" and b.event_id and b.market and b.outcome
    ]
    # (event_id, provider_id, market, outcome, point) -> current odds
    current_odds_map: dict[tuple, float] = {}
    if pending_lookups:
        provider_ids = list({t[1] for t in pending_lookups})
        provider_rows = (
            db.query(Odds)
            .filter(
                Odds.event_id.in_(event_ids),
                Odds.provider_id.in_(provider_ids),
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
            current_odds = current_odds_map.get(
                (b.event_id, b.provider_id, b.market, b.outcome, b.point)
            )

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
        if sel_prob is None and b.selection_probability:
            sel_prob = b.selection_probability

        # Selection probability from stored fair odds if not already set
        if sel_prob is None and b.fair_odds_at_placement and b.fair_odds_at_placement > 1.0:
            sel_prob = round(1.0 / b.fair_odds_at_placement, 4)

        bet_list.append({
            "id": b.id,
            "event_id": b.event_id,
            "provider": b.provider_id,
            "market": b.market,
            "outcome": b.outcome,
            "odds": b.odds,
            "stake": b.stake,
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
            "start_time": (ev.start_time.isoformat() + "Z") if ev and ev.start_time else (sp.event_time if sp else None),
            "home_score": ev.home_score if ev else None,
            "away_score": ev.away_score if ev else None,
            "match_status": ev.match_status if ev else None,
            "provider_site_url": site_urls.get(b.provider_id),
            "boost_title": b.boost_title or ((sp.llm_title or sp.title) if sp else None),
        })

    return {
        "profile_id": profile.id,
        "bets": bet_list,
        "count": len(bet_list),
    }


@router.post("")
async def create_bet(bet: BetCreate, service: BetService = Depends(_get_service)):
    """Record a placed bet for active profile."""
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
    )

    if "error" in result:
        status_code = 404 if "not found" in result["error"] else 400
        raise HTTPException(status_code, result["error"])

    return result


@router.post("/close-started")
async def close_started_bets(service: BetService = Depends(_get_service)):
    """
    Snapshot closing Pinnacle odds for pending bets on events that have started.
    Call this to capture CLV before settling. Safe to call repeatedly —
    only processes bets where closing_odds is not yet set.
    """
    result = service.snapshot_closing_odds()
    return {"success": True, **result}


@router.post("/auto-settle")
async def auto_settle_bets(db: Session = Depends(get_db)):
    """Disabled — Pinnacle scores are unreliable (mid-game/pre-OT snapshots)."""
    return {"success": False, "message": "Auto-settle disabled — use manual settlement via Settle tab"}


@router.post("/batch")
async def create_batch_bets(data: BatchBetCreate, service: BetService = Depends(_get_service)):
    """
    Place multiple legs at once (dutch bet).
    Each leg is placed independently — if one fails, already-placed legs remain.
    Returns results per leg so the frontend knows which succeeded.
    """
    if not data.legs:
        raise HTTPException(400, "No legs provided")

    results = []
    placed_count = 0
    total_staked = 0.0

    for i, leg in enumerate(data.legs):
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
        )

        if "error" in result:
            results.append({
                "leg_index": i,
                "provider_id": leg.provider_id,
                "outcome": leg.outcome,
                "success": False,
                "error": result["error"],
            })
        else:
            placed_count += 1
            total_staked += leg.stake
            results.append({
                "leg_index": i,
                "provider_id": leg.provider_id,
                "outcome": leg.outcome,
                "success": True,
                "bet_id": result["bet_id"],
                "stake": leg.stake,
                "odds": leg.odds,
            })

    return {
        "success": placed_count > 0,
        "placed_count": placed_count,
        "total_legs": len(data.legs),
        "total_staked": round(total_staked, 2),
        "results": results,
    }


@router.put("/{bet_id}")
async def settle_bet(bet_id: int, data: BetUpdate, service: BetService = Depends(_get_service)):
    """Settle a bet with result."""
    result = service.settle_bet(bet_id, data.result, data.payout)

    if "error" in result:
        raise HTTPException(404, result["error"])

    return result


@router.patch("/{bet_id}")
async def edit_bet(bet_id: int, data: BetEdit, service: BetService = Depends(_get_service)):
    """Edit a bet's stake, odds, or result. Recalculates payout and adjusts balance."""
    result = service.edit_bet(
        bet_id,
        stake=data.stake,
        odds=data.odds,
        result=data.result,
    )

    if "error" in result:
        raise HTTPException(404, result["error"])

    return result


