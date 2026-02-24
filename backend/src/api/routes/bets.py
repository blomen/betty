"""Bets API routes."""

import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from ...services import BetService, BankrollService
from ...repositories import BetRepo, ProfileRepo
from ...db.models import Odds, Event
from ...analysis.devig import get_fair_odds_for_outcome
from ...risk.calculator import RiskCalculator
from ...risk.stake_noise import StakeNoiseInjector
from ..deps import get_db
from ..schemas import BetCreate, BetUpdate, BetEdit, AutoPlaceBetRequest, BatchBetCreate
from .providers import load_provider_site_urls

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bets", tags=["bets"])


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

    bet_list = []
    for b in bets:
        ev = events_map.get(b.event_id) if b.event_id else None

        # Compute edge_pct and selection_probability on-the-fly if not stored
        edge_pct = round(b.utility_score * 100, 2) if b.utility_score else None
        sel_prob = b.selection_probability

        if (edge_pct is None or sel_prob is None) and b.event_id and b.market and b.outcome:
            pin_market = pinnacle_map.get((b.event_id, b.market), {})
            if len(pin_market) >= 2 and b.outcome in pin_market:
                fair = get_fair_odds_for_outcome(b.outcome, pin_market, method="multiplicative")
                if fair and fair > 1.0:
                    if edge_pct is None:
                        edge_pct = round((b.odds / fair - 1) * 100, 2)
                    if sel_prob is None:
                        sel_prob = round(1.0 / fair, 4)

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
            "selection_probability": sel_prob,
            "point": b.point,
            "settlement_source": b.settlement_source,
            "home_team": ev.home_team if ev else None,
            "away_team": ev.away_team if ev else None,
            "sport": ev.sport if ev else None,
            "league": ev.league if ev else None,
            "start_time": ev.start_time.isoformat() if ev and ev.start_time else None,
            "provider_site_url": site_urls.get(b.provider_id),
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


@router.post("/auto-place")
async def auto_place_bet(
    request: AutoPlaceBetRequest,
    db: Session = Depends(get_db),
):
    """
    Auto-place a bet with full pipeline:
    1. Calculate edge vs Pinnacle fair odds (de-vigged)
    2. Compute Kelly stake (with dynamic scaling)
    3. Apply risk assessment + stake noise
    4. Record bet + update bankroll

    Returns the complete bet result with stake breakdown.
    """
    # --- Step 1: Calculate edge vs Pinnacle fair odds ---
    pinnacle_odds_rows = (
        db.query(Odds)
        .filter(
            Odds.event_id == request.event_id,
            Odds.provider_id == "pinnacle",
            Odds.market == request.market,
        )
        .all()
    )

    if not pinnacle_odds_rows:
        raise HTTPException(400, f"No Pinnacle odds found for event {request.event_id} market {request.market}")

    # Build Pinnacle market dict for de-vigging
    pinnacle_market = {row.outcome: row.odds for row in pinnacle_odds_rows}

    if request.outcome not in pinnacle_market and len(pinnacle_market) < 2:
        raise HTTPException(400, f"Incomplete Pinnacle market for de-vigging")

    # De-vig to get fair odds
    fair_odds = get_fair_odds_for_outcome(request.outcome, pinnacle_market, method="multiplicative")
    if not fair_odds or fair_odds <= 1.0:
        raise HTTPException(400, "Could not calculate fair odds for this outcome")

    # Edge = (provider_odds / fair_odds - 1)
    edge_raw = (request.odds / fair_odds) - 1
    edge_pct = round(edge_raw * 100, 2)

    if edge_raw <= 0:
        raise HTTPException(
            400,
            f"Negative edge: {edge_pct:.2f}% (odds {request.odds:.3f} vs fair {fair_odds:.3f}). Not +EV."
        )

    # --- Step 2: Calculate Kelly stake ---
    bankroll_service = BankrollService(db)
    profile = bankroll_service.profile_repo.get_active()
    calc = bankroll_service.get_stake_calculator(profile.id)

    stake_result = calc.calculate(
        edge_raw=edge_raw,
        odds=request.odds,
        event_id=request.event_id,
        provider_id=request.provider_id,
        high_confidence=edge_pct <= 25,  # High edge = low confidence (likely data issue)
    )

    if stake_result.stake <= 0:
        return {
            "action": "skip",
            "reason": stake_result.skip_reason,
            "edge_pct": edge_pct,
            "fair_odds": round(fair_odds, 3),
            "kelly_fraction": stake_result.kelly_fraction,
            "raw_kelly_stake": stake_result.raw_kelly_stake,
            "bankroll": stake_result.bankroll,
        }

    # --- Step 3: Apply risk assessment + stake noise ---
    risk_calc = RiskCalculator(db)
    assessment = risk_calc.assess_provider(request.provider_id)
    risk_score = assessment.risk_score

    noise_injector = StakeNoiseInjector(db)
    noisy = noise_injector.inject_noise(
        stake=stake_result.stake,
        risk_score=risk_score,
    )
    final_stake = noisy.final_stake

    # --- Step 4: Record bet via BetService ---
    # Compute selection probability for the stochastic selector audit trail
    selection_probability = 1.0 / fair_odds

    bet_service = BetService(db)
    bet_result = bet_service.create_bet(
        event_id=request.event_id,
        provider_id=request.provider_id,
        market=request.market,
        outcome=request.outcome,
        odds=request.odds,
        stake=final_stake,
        point=request.point,
        is_bonus=request.is_bonus,
        bonus_type=request.bonus_type,
        utility_score=edge_raw,  # Use edge as utility proxy
        selection_probability=selection_probability,
        stake_noise_applied=noisy.noise_pct,
    )

    if "error" in bet_result:
        status_code = 404 if "not found" in bet_result["error"] else 400
        raise HTTPException(status_code, bet_result["error"])

    # Record in stake calculator for exposure tracking
    calc.record_bet(
        event_id=request.event_id,
        provider_id=request.provider_id,
        stake=final_stake,
        odds=request.odds,
    )

    # Get event info for response
    event = db.query(Event).filter(Event.id == request.event_id).first()

    return {
        "action": "placed",
        "bet_id": bet_result["bet_id"],
        "profile_id": bet_result["profile_id"],
        # Edge analysis
        "edge_pct": edge_pct,
        "fair_odds": round(fair_odds, 3),
        "provider_odds": request.odds,
        "ev_per_unit": round(request.odds * selection_probability - 1, 4),
        # Stake breakdown
        "kelly_fraction": stake_result.kelly_fraction,
        "edge_used": round(stake_result.edge_used * 100, 2),
        "raw_kelly_stake": stake_result.raw_kelly_stake,
        "kelly_stake": stake_result.stake,
        "final_stake": final_stake,
        "bankroll": stake_result.bankroll,
        # Caps
        "was_capped_single": stake_result.was_capped_single,
        "was_capped_event": stake_result.was_capped_event,
        # Risk
        "risk_score": bet_result.get("risk_score", 0.0),
        "noise_pct": round(noisy.noise_pct, 2),
        "noise_reason": noisy.reason,
        # Bonus
        "bonus_wagering": bet_result.get("bonus_wagering"),
        # Event context
        "event": {
            "home_team": event.home_team if event else None,
            "away_team": event.away_team if event else None,
            "sport": event.sport if event else None,
            "start_time": event.start_time.isoformat() if event and event.start_time else None,
        } if event else None,
    }
