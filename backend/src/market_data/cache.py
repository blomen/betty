"""Caching wrapper for market data providers. Stores completed days as parquet."""

import logging
from datetime import datetime, date, timezone
from pathlib import Path

import pandas as pd

from .base import BarData, MarketDataProvider, TickData

logger = logging.getLogger(__name__)


class CachedMarketDataProvider(MarketDataProvider):
    """Wraps any MarketDataProvider with local parquet caching.

    Completed trading days are cached permanently.
    Current/today's data is always re-fetched.
    """

    def __init__(self, inner: MarketDataProvider, cache_dir: str | Path):
        self.inner = inner
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, symbol: str, schema: str, dt: date) -> Path:
        return self.cache_dir / f"{symbol}_{schema}_{dt.isoformat()}.parquet"

    def _is_complete_day(self, dt: date) -> bool:
        """A day is complete (cacheable) if it's before today."""
        return dt < date.today()

    async def get_bars(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> list[BarData]:
        cache_key = self._cache_path(symbol, f"bars_{interval}", start.date())

        if self._is_complete_day(start.date()) and cache_key.exists():
            logger.debug("Cache hit: %s", cache_key.name)
            return self._read_bars_cache(cache_key)

        bars = await self.inner.get_bars(symbol, interval, start, end)

        if self._is_complete_day(start.date()) and bars:
            self._write_bars_cache(cache_key, bars)
            logger.info("Cached %d bars to %s", len(bars), cache_key.name)

        return bars

    async def get_ticks(
        self, symbol: str, start: datetime, end: datetime
    ) -> list[TickData]:
        cache_key = self._cache_path(symbol, "ticks", start.date())

        if self._is_complete_day(start.date()) and cache_key.exists():
            logger.debug("Cache hit: %s", cache_key.name)
            return self._read_ticks_cache(cache_key)

        ticks = await self.inner.get_ticks(symbol, start, end)

        if self._is_complete_day(start.date()) and ticks:
            self._write_ticks_cache(cache_key, ticks)
            logger.info("Cached %d ticks to %s", len(ticks), cache_key.name)

        return ticks

    async def get_latest_price(self, symbol: str) -> float | None:
        return await self.inner.get_latest_price(symbol)

    # ---- Parquet serialization ----

    def _write_bars_cache(self, path: Path, bars: list[BarData]) -> None:
        df = pd.DataFrame([{
            "timestamp": b.timestamp,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
            "delta": b.delta,
        } for b in bars])
        df.to_parquet(path, index=False)

    @staticmethod
    def _to_datetime(ts) -> datetime:
        """Convert various timestamp formats to timezone-aware datetime."""
        if hasattr(ts, "to_pydatetime"):
            dt = ts.to_pydatetime()
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        if isinstance(ts, (int, float)):
            # Epoch nanoseconds from parquet
            return datetime.fromtimestamp(int(ts) / 1e9, tz=timezone.utc)
        if isinstance(ts, datetime):
            return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        return ts

    def _read_bars_cache(self, path: Path) -> list[BarData]:
        df = pd.read_parquet(path)
        return [
            BarData(
                timestamp=self._to_datetime(row["timestamp"]),
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=int(row["volume"]),
                delta=int(row["delta"]),
            )
            for _, row in df.iterrows()
        ]

    def _write_ticks_cache(self, path: Path, ticks: list[TickData]) -> None:
        df = pd.DataFrame([{
            "timestamp": t.timestamp,
            "price": t.price,
            "size": t.size,
            "side": t.side,
        } for t in ticks])
        df.to_parquet(path, index=False)

    def _read_ticks_cache(self, path: Path) -> list[TickData]:
        df = pd.read_parquet(path)
        return [
            TickData(
                timestamp=self._to_datetime(row["timestamp"]),
                price=row["price"],
                size=int(row["size"]),
                side=row["side"],
            )
            for _, row in df.iterrows()
        ]
