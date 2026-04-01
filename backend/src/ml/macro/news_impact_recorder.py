"""Background recorder that measures NQ price impact after economic events.

Checks for recently-passed economic events and snapshots NQ price at
the event time, then at +1m, +5m, +15m, +30m, +60m intervals.
Writes results to the news_impact table for M9 training data.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Intervals (minutes) at which we record post-event prices
IMPACT_INTERVALS = [1, 5, 15, 30, 60]

# Column mapping: minutes -> NewsImpact column name
_PRICE_COL = {1: "price_1m", 5: "price_5m", 15: "price_15m", 30: "price_30m", 60: "price_60m"}


def _get_nq_price(stream) -> float | None:
    """Get current NQ price from the live Databento stream."""
    try:
        ticks = stream.buffer.ticks
        if ticks:
            return ticks[-1]["price"]
    except Exception:
        pass
    return None


def _get_vix_level() -> float | None:
    """Get current VIX level from yfinance (cached-friendly)."""
    try:
        import yfinance as yf
        from datetime import date

        end = date.today()
        start = end - timedelta(days=5)
        df = yf.download("^VIX", start=start, end=end, progress=False, auto_adjust=True)
        if df.empty:
            return None
        close = df["Close"]
        if hasattr(close, "columns"):
            close = close.iloc[:, 0]
        return float(close.iloc[-1])
    except Exception:
        return None


async def record_news_impacts(db_factory, stream) -> int:
    """Check for recent economic events and record price impacts.

    For each event that passed within the last 65 minutes:
    - If no NewsImpact row exists: create one with price_before + vix_at_event
    - If row exists but has unfilled price columns: fill any that are now due

    Args:
        db_factory: callable returning a new SQLAlchemy Session
        stream: DatabentoLiveStream instance for live NQ price

    Returns:
        Number of impact measurements recorded.
    """
    from src.db.models import EconomicEvent, NewsImpact

    db: Session = db_factory()
    recorded = 0
    try:
        now = datetime.now(timezone.utc)
        # Look at events from 65 minutes ago to 1 minute ago
        # (need at least 1 min elapsed for first measurement)
        window_start = now - timedelta(minutes=65)
        window_end = now - timedelta(minutes=1)

        # Compare as strings for SQLite compatibility (DateTime stored as ISO text)
        events = db.query(EconomicEvent).filter(
            EconomicEvent.event_datetime >= window_start.strftime("%Y-%m-%d %H:%M:%S"),
            EconomicEvent.event_datetime <= window_end.strftime("%Y-%m-%d %H:%M:%S"),
            EconomicEvent.importance >= 2,  # Medium+ importance only
        ).all()

        if not events:
            return 0

        price = _get_nq_price(stream)
        if price is None:
            logger.debug("No NQ price available for news impact recording")
            return 0

        for event in events:
            evt_dt = event.event_datetime
            if evt_dt.tzinfo is None:
                evt_dt = evt_dt.replace(tzinfo=timezone.utc)
            elapsed_minutes = (now - evt_dt).total_seconds() / 60.0

            # Find or create NewsImpact row
            impact = db.query(NewsImpact).filter_by(event_id=event.id).first()

            if impact is None:
                # New event — record baseline
                try:
                    vix = _get_vix_level()
                except Exception:
                    vix = None
                impact = NewsImpact(
                    event_id=event.id,
                    symbol="NQ",
                    price_before=price if elapsed_minutes < 2 else None,
                    vix_at_event=vix,
                )
                db.add(impact)
                db.flush()
                recorded += 1
                logger.info("Created news impact row for '%s' (id=%d)", event.event_name, event.id)

            # Fill price columns for intervals that have elapsed
            updated = False
            for minutes in IMPACT_INTERVALS:
                col = _PRICE_COL[minutes]
                if elapsed_minutes >= minutes and getattr(impact, col) is None:
                    setattr(impact, col, price)
                    updated = True
                    recorded += 1

            # Compute derived metrics once we have 1m and 30m
            if updated and impact.price_before and impact.price_1m:
                impact.immediate_impact_pct = round(
                    (impact.price_1m - impact.price_before) / impact.price_before * 100, 4
                )
                if impact.price_30m:
                    impact.sustained_impact_pct = round(
                        (impact.price_30m - impact.price_before) / impact.price_before * 100, 4
                    )
                    impact.reversal_pct = round(
                        (impact.price_1m - impact.price_30m) / impact.price_before * 100, 4
                    )

            if updated:
                logger.info(
                    "Updated news impact for '%s': elapsed=%.0fm, cols filled=%d",
                    event.event_name, elapsed_minutes,
                    sum(1 for m in IMPACT_INTERVALS if getattr(impact, _PRICE_COL[m]) is not None),
                )

        db.commit()
    except Exception as e:
        logger.error("News impact recording failed: %s", e, exc_info=True)
        db.rollback()
    finally:
        db.close()

    return recorded


async def news_impact_loop(db_factory, stream, interval_seconds: int = 60):
    """Continuously record news impacts on a fixed interval.

    Runs forever — designed to be launched as an asyncio.create_task().
    """
    logger.info("News impact recorder started (interval=%ds)", interval_seconds)
    while True:
        try:
            count = await record_news_impacts(db_factory, stream)
            if count:
                logger.info("News impact recorder: %d measurements recorded", count)
        except Exception as e:
            logger.error("News impact loop error: %s", e)
        await asyncio.sleep(interval_seconds)
