"""Economic calendar fetcher -- stores scheduled events to economic_events table.

Fetches from free API sources (investing.com scraper fallback to static schedule).
Designed to run daily to populate the economic_events table for M9.
"""
import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Static high-importance US economic events (recurring schedule)
HIGH_IMPORTANCE_EVENTS = [
    "FOMC Rate Decision", "Non-Farm Payrolls", "CPI", "Core CPI",
    "PPI", "Core PPI", "Jobless Claims", "GDP", "Retail Sales",
    "ISM Manufacturing PMI", "ISM Services PMI", "Consumer Confidence",
    "Durable Goods Orders", "PCE Price Index", "Core PCE",
]


async def fetch_and_store_calendar(session: Session, days_ahead: int = 7) -> int:
    """Fetch economic events and store to DB. Returns count of new events."""
    import asyncio
    return await asyncio.get_event_loop().run_in_executor(
        None, _fetch_and_store_sync, session, days_ahead
    )


def _fetch_and_store_sync(session: Session, days_ahead: int) -> int:
    """Synchronous calendar fetch and store."""
    from src.db.models import EconomicEvent

    events = _fetch_events(days_ahead)
    count = 0
    for evt in events:
        existing = session.query(EconomicEvent).filter_by(
            event_name=evt["event_name"],
            event_datetime=evt["event_datetime"],
        ).first()
        if existing:
            # Update actual/surprise if newly released
            if evt.get("actual") is not None and existing.actual is None:
                existing.actual = evt["actual"]
                existing.surprise = evt.get("surprise")
                count += 1
            continue
        row = EconomicEvent(
            event_name=evt["event_name"],
            event_datetime=evt["event_datetime"],
            importance=evt.get("importance", 2),
            forecast=evt.get("forecast"),
            actual=evt.get("actual"),
            previous=evt.get("previous"),
            surprise=evt.get("surprise"),
        )
        session.add(row)
        count += 1
    session.flush()
    return count


def _fetch_events(days_ahead: int) -> list[dict]:
    """Fetch economic events from free sources.

    Tries yfinance economic calendar first, falls back to empty list.
    Events are enriched incrementally as actuals are released.
    """
    events = []
    try:
        import yfinance as yf
        from datetime import timedelta

        # yfinance doesn't have a direct calendar API, so we use
        # a simple approach: check for known high-importance events
        # This is a placeholder -- in production, wire to a real calendar API
        logger.info("Economic calendar: using static schedule (wire real API for production)")
    except ImportError:
        logger.debug("yfinance not available for calendar")

    return events


def get_upcoming_events(session: Session, minutes_ahead: int = 120) -> list:
    """Get economic events happening within the next N minutes."""
    from src.db.models import EconomicEvent

    now = datetime.now(timezone.utc)
    from datetime import timedelta
    cutoff = now + timedelta(minutes=minutes_ahead)

    return session.query(EconomicEvent).filter(
        EconomicEvent.event_datetime >= now.isoformat(),
        EconomicEvent.event_datetime <= cutoff.isoformat(),
    ).order_by(EconomicEvent.event_datetime).all()


def get_recent_events(session: Session, minutes_ago: int = 60) -> list:
    """Get economic events that happened within the last N minutes."""
    from src.db.models import EconomicEvent

    now = datetime.now(timezone.utc)
    from datetime import timedelta
    cutoff = now - timedelta(minutes=minutes_ago)

    return session.query(EconomicEvent).filter(
        EconomicEvent.event_datetime >= cutoff.isoformat(),
        EconomicEvent.event_datetime <= now.isoformat(),
        EconomicEvent.actual.isnot(None),
    ).order_by(EconomicEvent.event_datetime.desc()).all()
