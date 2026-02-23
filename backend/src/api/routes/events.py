"""Events API routes."""

import json
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from ...db.models import Event
from ..deps import get_db

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("")
async def list_events(
    sport: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """Get extracted events with odds."""
    query = db.query(Event)
    if sport:
        query = query.filter(Event.sport == sport)

    events = query.order_by(Event.start_time).limit(limit).all()

    return {
        "events": [
            {
                "id": e.id,
                "sport": e.sport,
                "league": e.league,
                "home_team": e.home_team,
                "away_team": e.away_team,
                "start_time": e.start_time.isoformat() if e.start_time else None,
                "odds_count": len(e.odds),
            }
            for e in events
        ],
        "count": len(events),
    }


@router.get("/live")
def get_live_events(db: Session = Depends(get_db)):
    """Get events currently live with scores from Pinnacle."""
    events = (
        db.query(Event)
        .filter(Event.match_status.in_(["live", "finished"]))
        .order_by(Event.start_time)
        .all()
    )

    return {
        "events": [
            {
                "id": e.id,
                "sport": e.sport,
                "league": e.league,
                "home_team": e.home_team,
                "away_team": e.away_team,
                "start_time": e.start_time.isoformat() if e.start_time else None,
                "home_score": e.home_score,
                "away_score": e.away_score,
                "match_status": e.match_status,
                "match_minute": e.match_minute,
                "match_period": e.match_period,
                "stats": json.loads(e.stats_json) if e.stats_json else None,
            }
            for e in events
        ],
        "count": len(events),
    }


@router.get("/{event_id}")
async def get_event(event_id: str, db: Session = Depends(get_db)):
    """Get event details with all odds."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(404, f"Event {event_id} not found")

    # Group odds by market
    odds_by_market = {}
    for o in event.odds:
        if o.market not in odds_by_market:
            odds_by_market[o.market] = []
        odds_by_market[o.market].append({
            "provider": o.provider_id,
            "outcome": o.outcome,
            "odds": o.odds,
        })

    return {
        "id": event.id,
        "sport": event.sport,
        "league": event.league,
        "home_team": event.home_team,
        "away_team": event.away_team,
        "start_time": event.start_time.isoformat() if event.start_time else None,
        "odds": odds_by_market,
    }
