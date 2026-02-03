"""Opportunities API routes - with arbitrage scan."""

from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from ...db.models import Event, Odds, Opportunity, Provider
from ...analysis import find_best_hedge
from ...analysis.scanner import OpportunityScanner
from ...bankroll.manager import kelly_stake
from ..deps import get_db
from ..schemas import BonusMatchRequest

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
    """Get current arb/value/bonus opportunities with enhanced filtering."""
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
        # Filter by profit_pct for arb or edge_pct for value
        query = query.filter(
            (Opportunity.profit_pct >= min_value) |
            (Opportunity.edge_pct >= min_value)
        )

    # Sort by edge/profit (highest first) instead of detection time
    if type == 'arbitrage':
        opps = query.order_by(Opportunity.profit_pct.desc().nullslast()).limit(50).all()
    else:
        opps = query.order_by(Opportunity.edge_pct.desc().nullslast()).limit(50).all()

    # Build response with event details
    results = []
    for o in opps:
        # Get event for this opportunity (from joined query or separate lookup)
        event = db.query(Event).filter(Event.id == o.event_id).first()
        results.append({
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
        })

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


@router.get("/arbitrage/scan")
async def scan_arbitrage_opportunities(
    min_profit_pct: float = 0.5,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """
    Scan for arbitrage opportunities with full leg details.

    Returns complete 3-leg structure for 1x2 markets (or 2-leg for moneyline).
    """
    scanner = OpportunityScanner(db)
    opportunities = scanner.scan_arbitrage(min_profit_pct=min_profit_pct)

    results = []
    for arb in opportunities[:limit]:
        # Get event details
        event = db.query(Event).filter(Event.id == arb.event_id).first()

        results.append({
            "event_id": arb.event_id,
            "market": arb.market,
            "profit_pct": arb.profit_pct,
            "home_team": event.home_team if event else None,
            "away_team": event.away_team if event else None,
            "sport": event.sport if event else None,
            "start_time": event.start_time.isoformat() if event and event.start_time else None,
            "legs": [
                {
                    "outcome": s["outcome"],
                    "provider": s["provider"],
                    "odds": next((o["odds"] for o in arb.outcomes if o["outcome"] == s["outcome"]), 0),
                    "stake": s["stake"],
                    "return": s["return"],
                }
                for s in arb.stakes
            ],
        })

    return {
        "opportunities": results,
        "count": len(opportunities),
    }


@router.get("/bonus/scan")
async def scan_bonus_opportunities(
    anchor_provider: str,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """
    Scan for bonus arbitrage opportunities at anchor provider vs Pinnacle.

    Returns opportunities where the anchor provider has better odds than
    Pinnacle's de-vigged fair odds (positive edge = true arbitrage).
    """
    scanner = OpportunityScanner(db)
    opportunities = scanner.scan_bonus(
        anchor_provider=anchor_provider,
        counterpart_providers=["pinnacle"],
        devig=True
    )

    # Filter to arbitrage only (positive edge = anchor better than fair)
    arb_opportunities = [o for o in opportunities if o.edge_pct > 0]

    # Get bankroll for Kelly calculation
    providers = db.query(Provider).filter(Provider.is_enabled == True).all()
    total_bankroll = sum(p.balance for p in providers)
    anchor_balance = next(
        (p.balance for p in providers if p.id == anchor_provider), 0
    )

    # Calculate suggested stake for each opportunity
    results = []
    for o in arb_opportunities[:limit]:
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
        "count": len(arb_opportunities),
        "anchor_provider": anchor_provider,
        "total_bankroll": round(total_bankroll, 2),
        "anchor_balance": round(anchor_balance, 2),
    }
