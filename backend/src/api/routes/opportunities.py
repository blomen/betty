"""Opportunities API routes - with arbitrage scan."""

from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
import yaml

from ...db.models import Event, Odds, Opportunity, Provider, Profile, ProfileProviderBonus
from ...analysis import find_best_hedge
from ...analysis.scanner import OpportunityScanner, MAX_ARB_PROFIT_PCT
from ...bankroll.manager import kelly_stake
from ..deps import get_db
from ..schemas import BonusMatchRequest


def load_provider_bonuses() -> dict[str, dict]:
    """Load bonus info from providers.yaml config."""
    config_path = Path(__file__).parent.parent.parent / "config" / "providers.yaml"
    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)
        return {
            pid: p['bonus']
            for pid, p in config.get('providers', {}).items()
            if 'bonus' in p
        }
    except Exception:
        return {}

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
    min_profit_pct: float = 2.0,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """
    Scan for arbitrage opportunities with full leg details.

    Returns complete 3-leg structure for 1x2 markets (or 2-leg for moneyline).
    Opportunities with quality="suspect" (profit >7%) should be verified before betting.
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
            "quality": arb.quality,  # "verified" or "suspect"
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
    limit: int = 10,
    include_negative: bool = True,
    db: Session = Depends(get_db)
):
    """
    Scan for bonus arbitrage opportunities at anchor provider vs Pinnacle.

    Returns opportunities sorted by edge_pct (best first).
    With include_negative=True, shows all opportunities including qualifying losses.
    """
    scanner = OpportunityScanner(db)
    opportunities = scanner.scan_bonus(
        anchor_provider=anchor_provider,
        counterpart_providers=["pinnacle"],
        devig=True
    )

    # Include all opportunities (positive and negative edge) for bonus extraction
    # Positive edge = profit, negative edge = qualifying loss
    if include_negative:
        arb_opportunities = opportunities
    else:
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


@router.get("/bonus/arbitrage")
async def scan_bonus_arbitrage(
    anchor_provider: str,
    limit: int = 50,
    min_anchor_odds: float = 1.8,
    db: Session = Depends(get_db)
):
    """
    Bonus arbitrage opportunities for clearing wagering requirements.

    For each event, calculates opportunities where:
    1. Anchor bets FULL bonus amount on ONE outcome
    2. ALL other outcomes are hedged at counterpart providers
    3. Returns guaranteed profit after hedging

    Valid counterparts: Pinnacle + providers with bonus_status = 'completed'
    """
    from collections import defaultdict

    # Get valid counterpart providers (no active bonus for current profile)
    # Pinnacle/Polymarket are always valid (no bonus)
    # Soft providers are valid if their profile bonus_status is 'completed'
    active_profile = db.query(Profile).filter(Profile.is_active == True).first()

    # Get all providers
    all_providers = db.query(Provider).all()
    valid_counterpart_ids = set()

    for p in all_providers:
        # Sharp providers (no bonus) are always valid counterparts
        if p.id in ('pinnacle', 'polymarket'):
            valid_counterpart_ids.add(p.id)
            continue

        # For soft providers, check profile-specific bonus status
        if active_profile:
            bonus_record = db.query(ProfileProviderBonus).filter(
                ProfileProviderBonus.profile_id == active_profile.id,
                ProfileProviderBonus.provider_id == p.id
            ).first()
            # Valid if completed for this profile
            if bonus_record and bonus_record.bonus_status == 'completed':
                valid_counterpart_ids.add(p.id)

    bonus_info = load_provider_bonuses()
    anchor_bonus = bonus_info.get(anchor_provider, {})
    bonus_amount = anchor_bonus.get('amount', 0)

    if bonus_amount <= 0:
        return {
            'opportunities': [],
            'count': 0,
            'anchor_provider': anchor_provider,
            'anchor_bonus': anchor_bonus,
            'anchor_balance': 0,
            'total_bankroll': 0,
            'valid_counterparts': list(valid_counterpart_ids),
            'error': 'No bonus configured for this provider',
        }

    # Get anchor provider info
    anchor_prov = db.query(Provider).filter(Provider.id == anchor_provider).first()
    anchor_balance = anchor_prov.balance if anchor_prov else 0

    # Get total bankroll
    all_providers = db.query(Provider).filter(Provider.is_enabled == True).all()
    total_bankroll = sum(p.balance for p in all_providers)

    # Get all odds for anchor provider
    anchor_odds = db.query(Odds).filter(Odds.provider_id == anchor_provider).all()

    # Group by event_id -> market -> outcome
    anchor_by_event = defaultdict(lambda: defaultdict(dict))
    for o in anchor_odds:
        anchor_by_event[o.event_id][o.market][o.outcome] = o.odds

    # Get all counterpart odds
    counterpart_odds = db.query(Odds).filter(
        Odds.provider_id.in_(valid_counterpart_ids)
    ).all()

    # Group by event_id -> market -> outcome -> [(provider, odds)]
    counterpart_by_event = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for o in counterpart_odds:
        counterpart_by_event[o.event_id][o.market][o.outcome].append({
            'provider': o.provider_id,
            'odds': o.odds,
        })

    # Expected outcomes for complete markets
    EXPECTED_OUTCOMES = {
        '1x2': {'home', 'away', 'draw'},
        'moneyline': {'home', 'away'},
    }

    results = []

    # For each event where anchor has odds
    for event_id, markets in anchor_by_event.items():
        event = db.query(Event).filter(Event.id == event_id).first()
        if not event:
            continue

        for market, anchor_outcomes in markets.items():
            # Validate market completeness - must have ALL expected outcomes
            expected = EXPECTED_OUTCOMES.get(market)
            if not expected or set(anchor_outcomes.keys()) != expected:
                continue  # Skip incomplete/invalid market

            counterpart_market = counterpart_by_event.get(event_id, {}).get(market, {})

            # For each outcome anchor could bet on
            for anchor_outcome, anchor_odds_value in anchor_outcomes.items():
                # Filter: skip low anchor odds (poor retention for bonus extraction)
                if anchor_odds_value < min_anchor_odds:
                    continue

                # Find best counterpart for ALL other outcomes
                other_outcomes = [o for o in anchor_outcomes.keys() if o != anchor_outcome]

                # Check if ALL other outcomes have counterpart coverage
                hedges = []
                all_covered = True
                for other_outcome in other_outcomes:
                    counterpart_list = counterpart_market.get(other_outcome, [])
                    if not counterpart_list:
                        all_covered = False
                        break
                    # Pick best counterpart odds
                    best = max(counterpart_list, key=lambda x: x['odds'])
                    hedges.append({
                        'outcome': other_outcome,
                        'provider': best['provider'],
                        'odds': best['odds'],
                    })

                if not all_covered:
                    continue

                # Validate hedge completeness - must cover ALL non-anchor outcomes
                expected_hedges = len(expected) - 1  # All outcomes except anchor
                if len(hedges) != expected_hedges:
                    continue  # Missing hedge coverage

                # Calculate stakes and profit
                # Anchor stake = bonus amount (wagering requirement)
                anchor_stake = bonus_amount
                target_return = anchor_stake * anchor_odds_value

                # Hedge stakes to guarantee same return
                total_hedge_cost = 0
                for h in hedges:
                    h['stake'] = target_return / h['odds']
                    h['return'] = target_return
                    total_hedge_cost += h['stake']

                total_investment = anchor_stake + total_hedge_cost
                profit = target_return - total_investment
                profit_pct = (profit / total_investment) * 100 if total_investment > 0 else 0

                # Include all opportunities (including negative profit for bonus conversion)
                legs = [
                    {
                        'outcome': anchor_outcome,
                        'provider': anchor_provider,
                        'odds': anchor_odds_value,
                        'stake': anchor_stake,
                        'return': target_return,
                        'is_anchor': True,
                        'bonus_type': anchor_bonus.get('type'),
                        'bonus_amount': bonus_amount,
                    }
                ]
                for h in hedges:
                    legs.append({
                        'outcome': h['outcome'],
                        'provider': h['provider'],
                        'odds': h['odds'],
                        'stake': h['stake'],
                        'return': h['return'],
                        'is_anchor': False,
                        'bonus_type': None,
                        'bonus_amount': None,
                    })

                # Flag high-profit arbs as suspect (likely data errors)
                quality = 'suspect' if profit_pct > MAX_ARB_PROFIT_PCT else 'verified'

                results.append({
                    'event_id': event_id,
                    'market': market,
                    'profit_pct': round(profit_pct, 2),
                    'profit_amount': round(profit, 2),
                    'quality': quality,  # "verified" or "suspect"
                    'home_team': event.home_team,
                    'away_team': event.away_team,
                    'sport': event.sport,
                    'start_time': event.start_time.isoformat() if event.start_time else None,
                    'anchor_outcome': anchor_outcome,
                    'legs': legs,
                })

    # Sort by profit percentage (highest first)
    results.sort(key=lambda x: x['profit_pct'], reverse=True)

    return {
        'opportunities': results[:limit],
        'count': len(results),
        'anchor_provider': anchor_provider,
        'anchor_bonus': anchor_bonus,
        'anchor_balance': round(anchor_balance, 2),
        'total_bankroll': round(total_bankroll, 2),
        'valid_counterparts': list(valid_counterpart_ids),
    }
