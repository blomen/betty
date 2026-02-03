"""Polymarket matched events API routes."""

from typing import Optional
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from ...db.models import Event, Odds, Provider
from ..deps import get_db

router = APIRouter(prefix="/api/polymarket", tags=["polymarket"])


@router.get("/matched")
async def get_polymarket_matched(
    sport: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """Get Polymarket events matched with other providers."""

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
            Odds.provider_id != "polymarket"
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
        odds_query = db.query(Odds).filter(Odds.event_id == event.id)
        all_odds = odds_query.all()

        # Separate Polymarket odds from others
        polymarket_odds = []
        other_providers = {}

        for o in all_odds:
            odds_entry = {
                "outcome": o.outcome,
                "odds": o.odds,
            }

            if o.provider_id == "polymarket":
                polymarket_odds.append(odds_entry)
            else:
                if o.provider_id not in other_providers:
                    other_providers[o.provider_id] = []
                other_providers[o.provider_id].append(odds_entry)

        # Calculate edges for each outcome/provider combination
        edges = []
        best_edge = 0.0

        # Create a lookup for polymarket odds by outcome
        poly_odds_lookup = {o["outcome"]: o["odds"] for o in polymarket_odds}

        for provider_id, provider_odds in other_providers.items():
            for po in provider_odds:
                outcome = po["outcome"]
                provider_odd = po["odds"]

                # Find matching Polymarket odds for this outcome
                poly_odd = poly_odds_lookup.get(outcome)

                if poly_odd and poly_odd > 0:
                    # Edge % = (provider_odds / fair_odds - 1) * 100
                    # Using Polymarket as fair odds reference
                    edge_pct = (provider_odd / poly_odd - 1) * 100

                    if edge_pct > 0:  # Only include positive edges
                        edges.append({
                            "outcome": outcome,
                            "provider": provider_id,
                            "edge_pct": round(edge_pct, 2),
                            "provider_odds": provider_odd,
                            "polymarket_odds": poly_odd,
                        })

                        if edge_pct > best_edge:
                            best_edge = edge_pct

        # Sort edges by edge_pct descending
        edges.sort(key=lambda x: x["edge_pct"], reverse=True)

        result.append({
            "id": event.id,
            "sport": event.sport,
            "league": event.league,
            "home_team": event.home_team,
            "away_team": event.away_team,
            "start_time": event.start_time.isoformat() if event.start_time else None,
            "polymarket_odds": polymarket_odds,
            "other_providers": other_providers,
            "edges": edges[:10],  # Limit to top 10 edges
            "best_edge": round(best_edge, 2),
        })

    # Sort by best_edge descending (best opportunities first)
    result.sort(key=lambda x: x["best_edge"], reverse=True)

    return {
        "events": result,
        "count": len(result),
    }
