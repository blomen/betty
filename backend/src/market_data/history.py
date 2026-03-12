"""Databento REST historical data fetch utilities."""
import logging
from datetime import datetime, date, timezone
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class OHLCVBar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


async def fetch_ohlcv_1d(
    api_key: str,
    symbol: str = "NQ.FUT",
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
    symbol: str = "NQ.FUT",
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
