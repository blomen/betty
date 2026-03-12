"""Databento market data provider for CME futures."""

import logging
import os
from datetime import datetime

import databento as db

from .base import BarData, MarketDataProvider, TickData

logger = logging.getLogger(__name__)


class DabentoProvider(MarketDataProvider):
    """Fetches NQ futures data from Databento (GLBX.MDP3 dataset)."""

    def __init__(self, config: dict):
        api_key = os.environ.get(config.get("api_key_env", "DATABENTO_API_KEY"), "")
        if not api_key:
            raise ValueError("DATABENTO_API_KEY not set in environment")

        self.client = db.Historical(key=api_key)
        self.dataset = config.get("dataset", "GLBX.MDP3")
        self.symbol = config.get("symbol", "NQ.FUT")

    async def get_bars(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> list[BarData]:
        """Fetch OHLCV bars from Databento. Uses ohlcv-1m schema."""
        schema_map = {
            "1m": "ohlcv-1m",
            "5m": "ohlcv-1m",  # We'll resample 1m bars for larger intervals
            "15m": "ohlcv-1m",
            "1h": "ohlcv-1h",
            "1d": "ohlcv-1d",
        }
        schema = schema_map.get(interval, "ohlcv-1m")

        data = self.client.timeseries.get_range(
            dataset=self.dataset,
            symbols=[symbol],
            stype_in="parent",
            schema=schema,
            start=start.isoformat(),
            end=end.isoformat(),
        )

        bars = []
        for rec in data:
            bars.append(BarData(
                timestamp=rec.ts_event if hasattr(rec, "ts_event") else rec.hd.ts_event,
                open=rec.open / 1e9,  # Databento fixed-point prices
                high=rec.high / 1e9,
                low=rec.low / 1e9,
                close=rec.close / 1e9,
                volume=rec.volume,
                delta=0,  # Delta computed separately from ticks
            ))

        # Resample if needed
        if interval in ("5m", "15m") and bars:
            bars = self._resample_bars(bars, interval)

        logger.info("Fetched %d %s bars for %s (%s to %s)", len(bars), interval, symbol, start, end)
        return bars

    async def get_ticks(
        self, symbol: str, start: datetime, end: datetime
    ) -> list[TickData]:
        """Fetch tick trades with aggressor side from Databento TBBO/trades schema."""
        data = self.client.timeseries.get_range(
            dataset=self.dataset,
            symbols=[symbol],
            stype_in="parent",
            schema="trades",
            start=start.isoformat(),
            end=end.isoformat(),
        )

        ticks = []
        for rec in data:
            # Databento side: 'A' = ask side (buy aggressor), 'B' = bid side (sell aggressor)
            side_char = getattr(rec, "side", "")
            side = "buy" if side_char == "A" else "sell" if side_char == "B" else "unknown"

            ticks.append(TickData(
                timestamp=rec.ts_event if hasattr(rec, "ts_event") else rec.hd.ts_event,
                price=rec.price / 1e9,
                size=rec.size,
                side=side,
            ))

        logger.info("Fetched %d ticks for %s (%s to %s)", len(ticks), symbol, start, end)
        return ticks

    async def get_latest_price(self, symbol: str) -> float | None:
        """Get latest price from Databento live snapshot."""
        try:
            data = self.client.timeseries.get_range(
                dataset=self.dataset,
                symbols=[symbol],
                stype_in="parent",
                schema="trades",
                start=datetime.utcnow().replace(hour=0, minute=0, second=0).isoformat(),
                limit=1,
            )
            for rec in data:
                return rec.price / 1e9
        except Exception as e:
            logger.warning("Failed to get latest price: %s", e)
        return None

    def _resample_bars(self, bars: list[BarData], interval: str) -> list[BarData]:
        """Resample 1-min bars to larger intervals."""
        import pandas as pd

        df = pd.DataFrame([{
            "timestamp": b.timestamp,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
            "delta": b.delta,
        } for b in bars])
        df.set_index("timestamp", inplace=True)

        freq = "5min" if interval == "5m" else "15min"
        resampled = df.resample(freq).agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "delta": "sum",
        }).dropna()

        return [
            BarData(
                timestamp=ts.to_pydatetime(),
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=int(row["volume"]),
                delta=int(row["delta"]),
            )
            for ts, row in resampled.iterrows()
        ]
