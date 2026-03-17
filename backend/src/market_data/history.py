"""Databento REST historical data fetch utilities."""
import logging
from datetime import datetime, date, timezone
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class OHLCVBar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class HistoricalTick:
    ts: datetime
    price: float
    size: int
    side: str  # "A" (ask aggressor / buy) | "B" (bid aggressor / sell)


async def fetch_ohlcv_1d(
    api_key: str,
    symbol: str = "NQ.c.0",
    start: date | None = None,
    end: date | None = None,
    dataset: str = "GLBX.MDP3",
) -> list[OHLCVBar]:
    """Fetch daily OHLCV bars from Databento historical API."""
    import databento as db

    client = db.Historical(key=api_key)
    data = client.timeseries.get_range(
        dataset=dataset,
        symbols=[symbol],
        schema="ohlcv-1d",
        start=start.isoformat() if start else "2020-01-01",
        end=end.isoformat() if end else datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    )
    bars = []
    for record in data:
        bars.append(OHLCVBar(
            ts=datetime.fromtimestamp(record.ts_event / 1e9),
            open=record.open / 1e9,
            high=record.high / 1e9,
            low=record.low / 1e9,
            close=record.close / 1e9,
            volume=record.volume,
        ))
    return bars


async def fetch_ohlcv_1m(
    api_key: str,
    symbol: str = "NQ.c.0",
    start: date | None = None,
    end: date | None = None,
    dataset: str = "GLBX.MDP3",
) -> list[OHLCVBar]:
    """Fetch 1-minute OHLCV bars from Databento historical API."""
    import databento as db

    client = db.Historical(key=api_key)
    data = client.timeseries.get_range(
        dataset=dataset,
        symbols=[symbol],
        schema="ohlcv-1m",
        start=start.isoformat() if start else "2025-01-01",
        end=end.isoformat() if end else datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    )
    bars = []
    for record in data:
        bars.append(OHLCVBar(
            ts=datetime.fromtimestamp(record.ts_event / 1e9),
            open=record.open / 1e9,
            high=record.high / 1e9,
            low=record.low / 1e9,
            close=record.close / 1e9,
            volume=record.volume,
        ))
    return bars


async def fetch_trades_historical(
    api_key: str,
    symbol: str = "NQ.c.0",
    start: date | None = None,
    end: date | None = None,
    dataset: str = "GLBX.MDP3",
) -> list[HistoricalTick]:
    """Fetch historical tick-level trades from Databento (L1 Trades schema).

    Used for leg VP computation and historical session replay.
    Warning: large date ranges produce millions of ticks — keep ranges short (1-5 days).
    """
    import databento as db

    client = db.Historical(key=api_key)
    data = client.timeseries.get_range(
        dataset=dataset,
        symbols=[symbol],
        stype_in="continuous",
        schema="trades",
        start=start.isoformat() if start else "2026-01-01",
        end=end.isoformat() if end else datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    )
    ticks = []
    for rec in data:
        ts = datetime.fromtimestamp(rec.ts_event / 1e9, tz=timezone.utc)
        side_char = getattr(rec, "side", "")
        side = "A" if side_char == "A" else "B"
        ticks.append(HistoricalTick(
            ts=ts,
            price=rec.price / 1e9,
            size=rec.size,
            side=side,
        ))
    logger.info("Fetched %d historical ticks for %s (%s to %s)", len(ticks), symbol, start, end)
    return ticks


async def backfill_trades_to_db(
    api_key: str,
    db_session_factory: Callable,
    symbol: str = "NQ.c.0",
    start: date | None = None,
    end: date | None = None,
    batch_size: int = 1000,
) -> int:
    """Fetch historical trades and insert into market_trades table in batches.

    Returns total number of ticks inserted.
    """
    ticks = await fetch_trades_historical(api_key, symbol, start, end)
    if not ticks:
        return 0

    db_symbol = symbol.split(".")[0]  # "NQ.FUT" → "NQ"
    total = 0

    for i in range(0, len(ticks), batch_size):
        batch = [
            {
                "symbol": db_symbol,
                "ts": t.ts,
                "price": t.price,
                "size": t.size,
                "side": t.side,
            }
            for t in ticks[i : i + batch_size]
        ]
        session = db_session_factory()
        try:
            from ..repositories.market_repo import MarketRepo
            repo = MarketRepo(session)
            repo.bulk_insert_trades(batch)
            total += len(batch)
        finally:
            session.close()

    logger.info("Backfilled %d ticks to market_trades for %s", total, db_symbol)
    return total
