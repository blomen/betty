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
    """Fetch economic events from ForexFactory JSON feed.

    Filters to USD-only high-importance events within the requested window.
    """
    import asyncio
    from src.ml.macro.economic_calendar import fetch_events as ff_fetch

    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # Already in an async context — run in a new thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                raw_events = pool.submit(lambda: asyncio.run(ff_fetch())).result(timeout=20)
        else:
            raw_events = asyncio.run(ff_fetch())
    except Exception as e:
        logger.error("ForexFactory calendar fetch failed: %s", e)
        return []

    from datetime import timedelta
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=days_ahead)

    results = []
    for evt in raw_events:
        # Only USD events
        if evt.get("currency", "").upper() != "USD":
            continue
        dt = evt.get("event_date")
        if dt is None:
            continue
        # Filter to window
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt > cutoff:
            continue
        results.append({
            "event_name": evt["event_name"],
            "event_datetime": dt,
            "importance": evt.get("importance", 1),
            "forecast": evt.get("forecast"),
            "actual": evt.get("actual"),
            "previous": evt.get("previous"),
            "surprise": evt.get("surprise"),
        })

    logger.info("Economic calendar: fetched %d USD events from ForexFactory", len(results))
    return results


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
