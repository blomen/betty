"""Opportunities API routes - value betting opportunities."""

import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from ...db.models import Event, Odds, Opportunity, Provider, Profile, get_active_profile, get_total_profile_bankroll, get_bonus_status
from ...analysis import find_best_hedge
from ...analysis.scanner import OpportunityScanner
from ...bankroll.manager import kelly_stake
from ...bankroll.stake_calculator import StakeCalculator, BONUS_MIN_ODDS
from ..deps import get_db
from ..schemas import BonusMatchRequest

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/opportunities", tags=["opportunities"])


@router.get("")
async def list_opportunities(
    type: Optional[str] = None,
    active_only: bool = True,
    provider1: Optional[str] = None,
    provider2: Optional[str] = None,
    providers: Optional[str] = None,
    market: Optional[str] = None,
    sport: Optional[str] = None,
    min_value: Optional[float] = None,
    db: Session = Depends(get_db)
):
    """Get current value/bonus opportunities with enhanced filtering and stake recommendations."""
    query = db.query(Opportunity)

    if type:
        query = query.filter(Opportunity.type == type)
    if active_only:
        query = query.filter(Opportunity.is_active == True)
    if provider1:
        query = query.filter(Opportunity.provider1_id == provider1)
    if provider2:
        query = query.filter(Opportunity.provider2_id == provider2)
    if providers:
        provider_list = [p.strip() for p in providers.split(',')]
        query = query.filter(
            (Opportunity.provider1_id.in_(provider_list)) |
            (Opportunity.provider2_id.in_(provider_list))
        )
    if market:
        query = query.filter(Opportunity.market == market)
    # Join with Event table to get event details (sport, start_time, teams)
    # Use outer join to include opportunities even if event was deleted
    if not sport:
        query = query.join(Event, Event.id == Opportunity.event_id, isouter=True)
    else:
        # Already joined above for sport filter
        query = query.join(Event, Event.id == Opportunity.event_id).filter(Event.sport == sport)

    if min_value is not None:
        # Filter by edge_pct for value
        query = query.filter(Opportunity.edge_pct >= min_value)

    # Sort by edge (highest first)
    opps = query.order_by(Opportunity.edge_pct.desc().nullslast()).limit(50).all()

    # Initialize stake calculator for value bets
    stake_calculator = None
    profile = None
    if type == 'value' and opps:
        try:
            profile = get_active_profile(db)
            bankroll = get_total_profile_bankroll(db, profile.id)
            stake_calculator = StakeCalculator(bankroll=bankroll)
        except Exception as e:
            logger.warning(f"Could not initialize stake calculator: {e}")

    # Build response with event details and stake recommendations
    results = []
    for o in opps:
        # Get event for this opportunity (from joined query or separate lookup)
        event = db.query(Event).filter(Event.id == o.event_id).first()

        result = {
            "id": o.id,
            "type": o.type,
            "event_id": o.event_id,
            "market": o.market,
            "provider1": o.provider1_id,
            "provider2": o.provider2_id,
            "odds1": o.odds1,
            "odds2": o.odds2,
            "outcome1": o.outcome1,
            "outcome2": o.outcome2,
            "profit_pct": o.profit_pct,
            "edge_pct": o.edge_pct,
            "fair_odds": o.odds2,  # odds2 stores fair odds for value bets
            "detected_at": o.detected_at.isoformat() if o.detected_at else None,
            # Event details
            "sport": event.sport if event else None,
            "league": event.league if event else None,
            "home_team": event.home_team if event else None,
            "away_team": event.away_team if event else None,
            "starts_at": event.start_time.isoformat() if event and event.start_time else None,
        }

        # Add stake recommendations for value bets
        if type == 'value' and stake_calculator and profile and o.odds1 and o.odds2:
            try:
                # Calculate edge
                edge_raw = (o.odds1 / o.odds2 - 1) if o.odds2 > 1 else 0

                # Check bonus status for min odds
                bonus_status = get_bonus_status(db, profile.id, o.provider1_id)
                min_odds = 0.0 if bonus_status.get("is_cleared", True) else BONUS_MIN_ODDS

                stake_rec = stake_calculator.calculate(
                    edge_raw=edge_raw,
                    odds=o.odds1,
                    event_id=o.event_id,
                    provider_id=o.provider1_id,
                    min_odds=min_odds,
                )
                result["suggested_stake"] = round(stake_rec.raw_kelly_stake, 2)
                result["final_stake"] = round(stake_rec.stake, 2)
                result["kelly_fraction"] = stake_rec.kelly_fraction
                result["skip_reason"] = stake_rec.skip_reason
                result["bonus_cleared"] = bonus_status.get("is_cleared", True)
            except Exception as e:
                logger.debug(f"Stake calculation failed for opp {o.id}: {e}")
                result["suggested_stake"] = None
                result["final_stake"] = None
                result["kelly_fraction"] = None
                result["skip_reason"] = None
                result["bonus_cleared"] = None

        results.append(result)

    return {
        "opportunities": results,
        "count": len(results),
    }


@router.post("/bonus/match")
async def match_bonus_bet(
    data: BonusMatchRequest,
    db: Session = Depends(get_db)
):
    """Find the best hedge for a bonus bet."""
    # Query all odds for the event/market
    query = db.query(Odds).filter(
        Odds.event_id == data.event_id,
        Odds.market == data.market,
        Odds.outcome != data.anchor_outcome,
        Odds.provider_id != data.anchor_provider
    )

    # Filter by counterpart providers if specified
    if data.counterpart_providers:
        query = query.filter(Odds.provider_id.in_(data.counterpart_providers))

    opposing_odds = query.all()

    if not opposing_odds:
        raise HTTPException(
            404,
            "No opposing odds found for the specified event/market/outcome combination"
        )

    # Format for find_best_hedge
    opposing_list = [
        {
            "provider": o.provider_id,
            "outcome": o.outcome,
            "odds": o.odds
        }
        for o in opposing_odds
    ]

    # Find best hedge
    result = find_best_hedge(
        event_id=data.event_id,
        market=data.market,
        anchor_provider=data.anchor_provider,
        anchor_outcome=data.anchor_outcome,
        anchor_odds=data.anchor_odds,
        anchor_stake=data.anchor_stake,
        opposing_odds_list=opposing_list,
        is_free_bet=data.is_free_bet
    )

    if not result:
        raise HTTPException(
            404,
            "No suitable hedge found (all hedges are same-provider or no valid options)"
        )

    return {
        "event_id": result.event_id,
        "market": result.market,
        "anchor_provider": result.anchor_provider,
        "anchor_outcome": result.anchor_outcome,
        "anchor_odds": result.anchor_odds,
        "anchor_stake": result.anchor_stake,
        "hedge_provider": result.hedge_provider,
        "hedge_outcome": result.hedge_outcome,
        "hedge_odds": result.hedge_odds,
        "hedge_stake": result.hedge_stake,
        "qualifying_loss": result.qualifying_loss,
        "retention_pct": result.retention_pct,
    }


@router.get("/bonus/scan")
async def scan_bonus_opportunities(
    anchor_provider: str,
    limit: int = 10,
    include_negative: bool = True,
    db: Session = Depends(get_db)
):
    """
    Scan for bonus opportunities at anchor provider vs Pinnacle.

    Returns opportunities sorted by edge_pct (best first).
    With include_negative=True, shows all opportunities including qualifying losses.
    """
    scanner = OpportunityScanner(db)
    opportunities = scanner.scan_bonus(
        anchor_provider=anchor_provider,
        devig=True
    )

    # Include all opportunities (positive and negative edge) for bonus extraction
    # Positive edge = profit, negative edge = qualifying loss
    if include_negative:
        filtered_opportunities = opportunities
    else:
        filtered_opportunities = [o for o in opportunities if o.edge_pct > 0]

    # Get bankroll for Kelly calculation
    providers = db.query(Provider).filter(Provider.is_enabled == True).all()
    total_bankroll = sum(p.balance for p in providers)
    anchor_balance = next(
        (p.balance for p in providers if p.id == anchor_provider), 0
    )

    # Calculate suggested stake for each opportunity
    results = []
    for o in filtered_opportunities[:limit]:
        # Win probability from fair odds
        win_prob = 1 / o.fair_odds if o.fair_odds > 0 else 0

        if win_prob > 0 and total_bankroll > 0:
            rec = kelly_stake(
                odds=o.anchor_odds,
                win_probability=win_prob,
                bankroll=total_bankroll,
                kelly_fraction=0.25,  # Quarter Kelly
                max_stake_pct=5.0,
            )
            # Limit to provider balance
            suggested = min(rec.stake, anchor_balance) if rec.stake > 0 else 0
            kelly_amount = rec.kelly_stake
            max_amount = rec.max_stake
        else:
            suggested = 0
            kelly_amount = 0
            max_amount = total_bankroll * 0.05 if total_bankroll > 0 else 0

        results.append({
            "event_id": o.event_id,
            "market": o.market,
            "outcome": o.outcome,
            "anchor_provider": o.anchor_provider,
            "anchor_odds": o.anchor_odds,
            "fair_odds": o.fair_odds,
            "edge_pct": o.edge_pct,
            "home_team": o.home_team,
            "away_team": o.away_team,
            "sport": o.sport,
            "suggested_stake": round(suggested, 2),
            "kelly_stake": round(kelly_amount, 2),
            "max_stake": round(max_amount, 2),
        })

    return {
        "opportunities": results,
        "count": len(filtered_opportunities),
        "anchor_provider": anchor_provider,
        "total_bankroll": round(total_bankroll, 2),
        "anchor_balance": round(anchor_balance, 2),
    }
