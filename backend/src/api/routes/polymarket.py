"""Polymarket API routes: matched events, value bets, stats."""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from ...db.models import Event, Odds
from ...repositories import ProfileRepo
from ...analysis.scanner import OpportunityScanner
from ...bankroll.stake_calculator import StakeCalculator, BONUS_MIN_ODDS
from ..deps import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/polymarket", tags=["polymarket"])


@router.get("/value")
async def get_polymarket_value(
    min_edge: float = Query(3.0, description="Minimum edge percentage"),
    sport: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Get Polymarket value bets vs de-vigged Pinnacle odds."""
    scanner = OpportunityScanner(db)
    all_values = scanner.scan_value(min_edge_pct=min_edge)

    # Filter to polymarket only
    poly_values = [vb for vb in all_values if vb.provider == "polymarket"]

    # Initialize stake calculator with profile risk settings
    stake_calculator = None
    profile = None
    if poly_values:
        try:
            profile_repo = ProfileRepo(db)
            profile = profile_repo.get_active()
            bankroll = profile_repo.get_total_bankroll(profile.id)
            stake_calculator = StakeCalculator(
                bankroll=bankroll,
                max_kelly=profile.kelly_fraction,
                single_bet_cap_pct=profile.max_stake_pct / 100.0,
                min_edge=profile.min_edge_pct / 100.0,
            )
        except Exception as e:
            logger.warning(f"Could not initialize stake calculator: {e}")

    # Enrich with event context
    results = []
    for vb in poly_values:
        event = db.query(Event).filter(Event.id == vb.event_id).first()
        if not event:
            continue

        if sport and event.sport != sport:
            continue

        result = {
            "event_id": vb.event_id,
            "market": vb.market,
            "outcome": vb.outcome,
            "polymarket_odds": vb.provider_odds,
            "fair_odds": vb.fair_odds,
            "fair_probability": vb.fair_probability,
            "edge_pct": vb.edge_pct,
            "point": vb.point,
            "home_team": event.home_team,
            "away_team": event.away_team,
            "sport": event.sport,
            "league": event.league,
            "start_time": event.start_time.isoformat() if event.start_time else None,
        }

        # Add stake recommendation
        if stake_calculator and profile:
            try:
                edge_raw = (vb.provider_odds / vb.fair_odds - 1) if vb.fair_odds > 1 else 0
                bonus_status = profile_repo.get_bonus_status(profile.id, "polymarket")
                min_odds = 0.0 if bonus_status.get("is_cleared", True) else bonus_status.get("min_odds", BONUS_MIN_ODDS)

                stake_rec = stake_calculator.calculate(
                    edge_raw=edge_raw,
                    odds=vb.provider_odds,
                    event_id=vb.event_id,
                    provider_id="polymarket",
                    min_odds=min_odds,
                )
                result["suggested_stake"] = round(stake_rec.raw_kelly_stake, 2)
                result["final_stake"] = round(stake_rec.stake, 2)
                result["kelly_fraction"] = stake_rec.kelly_fraction
                result["skip_reason"] = stake_rec.skip_reason
                result["bonus_cleared"] = bonus_status.get("is_cleared", True)
            except Exception as e:
                logger.debug(f"Stake calculation failed for {vb.event_id}: {e}")
                result["suggested_stake"] = None
                result["final_stake"] = None
                result["kelly_fraction"] = None
                result["skip_reason"] = None
                result["bonus_cleared"] = None

        results.append(result)

        if len(results) >= limit:
            break

    return {
        "value_bets": results,
        "count": len(results),
        "total_scanned": len(all_values),
    }


@router.get("/stats")
async def get_polymarket_stats(
    db: Session = Depends(get_db),
):
    """Get Polymarket extraction statistics and data quality metrics."""
    # Total Polymarket odds and events
    poly_odds_count = (
        db.query(func.count(Odds.id))
        .filter(Odds.provider_id == "polymarket")
        .scalar()
    ) or 0

    poly_event_ids = (
        db.query(Odds.event_id)
        .filter(Odds.provider_id == "polymarket")
        .distinct()
        .subquery()
    )
    poly_event_count = db.query(func.count()).select_from(poly_event_ids).scalar() or 0

    # Matched events (have both Polymarket + at least one other provider)
    matched_count = 0
    if poly_event_count > 0:
        matched_subq = (
            db.query(Odds.event_id)
            .filter(
                Odds.event_id.in_(
                    db.query(Odds.event_id)
                    .filter(Odds.provider_id == "polymarket")
                    .distinct()
                ),
                Odds.provider_id != "polymarket",
            )
            .distinct()
            .subquery()
        )
        matched_count = db.query(func.count()).select_from(matched_subq).scalar() or 0

    match_rate = round(matched_count / poly_event_count * 100, 1) if poly_event_count > 0 else 0

    # Outcome normalization rate
    normalized_count = (
        db.query(func.count(Odds.id))
        .filter(
            Odds.provider_id == "polymarket",
            Odds.outcome.in_(["home", "away", "draw"]),
        )
        .scalar()
    ) or 0
    normalization_rate = round(normalized_count / poly_odds_count * 100, 1) if poly_odds_count > 0 else 0

    # Sport breakdown
    sport_rows = (
        db.query(Event.sport, func.count(Event.id))
        .join(Odds, Event.id == Odds.event_id)
        .filter(Odds.provider_id == "polymarket")
        .group_by(Event.sport)
        .order_by(func.count(Event.id).desc())
        .all()
    )
    sports = [{"sport": row[0], "count": row[1]} for row in sport_rows]

    return {
        "total_odds": poly_odds_count,
        "total_events": poly_event_count,
        "matched_events": matched_count,
        "match_rate": match_rate,
        "normalization_rate": normalization_rate,
        "sports": sports,
    }


@router.get("/matched")
async def get_polymarket_matched(
    sport: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Get Polymarket events matched with other providers, including Pinnacle fair odds."""

    # Find events that have Polymarket odds
    polymarket_events_subq = (
        db.query(Odds.event_id)
        .filter(Odds.provider_id == "polymarket")
        .distinct()
        .subquery()
    )

    # Find events that also have odds from other providers
    matched_events_subq = (
        db.query(Odds.event_id)
        .filter(
            Odds.event_id.in_(polymarket_events_subq),
            Odds.provider_id != "polymarket",
        )
        .distinct()
        .subquery()
    )

    # Query events with sport filter
    query = db.query(Event).filter(Event.id.in_(matched_events_subq))
    if sport:
        query = query.filter(Event.sport == sport)

    events = query.order_by(Event.start_time).limit(limit).all()

    result = []
    for event in events:
        # Get all odds for this event
        all_odds = db.query(Odds).filter(Odds.event_id == event.id).all()

        # Separate Polymarket, Pinnacle, and other provider odds
        polymarket_odds = []
        pinnacle_odds_lookup = {}
        other_providers = {}

        for o in all_odds:
            odds_entry = {
                "outcome": o.outcome,
                "odds": o.odds,
            }

            if o.provider_id == "polymarket":
                polymarket_odds.append(odds_entry)
            elif o.provider_id == "pinnacle":
                pinnacle_odds_lookup[o.outcome] = o.odds
            else:
                if o.provider_id not in other_providers:
                    other_providers[o.provider_id] = []
                other_providers[o.provider_id].append(odds_entry)

        # Create Polymarket odds lookup
        poly_odds_lookup = {o["outcome"]: o["odds"] for o in polymarket_odds}

        # Calculate edges for other providers vs Polymarket
        edges = []
        best_edge = 0.0

        for provider_id, provider_odds in other_providers.items():
            for po in provider_odds:
                outcome = po["outcome"]
                provider_odd = po["odds"]
                poly_odd = poly_odds_lookup.get(outcome)

                if poly_odd and poly_odd > 0:
                    edge_pct = (provider_odd / poly_odd - 1) * 100
                    if edge_pct > 0:
                        edges.append({
                            "outcome": outcome,
                            "provider": provider_id,
                            "edge_pct": round(edge_pct, 2),
                            "provider_odds": provider_odd,
                            "polymarket_odds": poly_odd,
                        })
                        if edge_pct > best_edge:
                            best_edge = edge_pct

        edges.sort(key=lambda x: x["edge_pct"], reverse=True)

        # Calculate Polymarket edges vs Pinnacle fair odds
        polymarket_edges = []
        for outcome, poly_odd in poly_odds_lookup.items():
            pinnacle_odd = pinnacle_odds_lookup.get(outcome)
            if pinnacle_odd and pinnacle_odd > 0 and poly_odd > 0:
                edge_pct = (poly_odd / pinnacle_odd - 1) * 100
                polymarket_edges.append({
                    "outcome": outcome,
                    "polymarket_odds": poly_odd,
                    "pinnacle_odds": pinnacle_odd,
                    "edge_pct": round(edge_pct, 2),
                })

        polymarket_edges.sort(key=lambda x: x["edge_pct"], reverse=True)

        result.append({
            "id": event.id,
            "sport": event.sport,
            "league": event.league,
            "home_team": event.home_team,
            "away_team": event.away_team,
            "start_time": event.start_time.isoformat() if event.start_time else None,
            "polymarket_odds": polymarket_odds,
            "other_providers": other_providers,
            "edges": edges[:10],
            "best_edge": round(best_edge, 2),
            "polymarket_edges": polymarket_edges,
        })

    # Sort by best_edge descending
    result.sort(key=lambda x: x["best_edge"], reverse=True)

    return {
        "events": result,
        "count": len(result),
    }
