"""Incremental tick-to-candle aggregator.

Processes raw ticks and builds 1m and 30m OHLCV candles in real time.
Ticks have format: {"ts": datetime, "price": float, "size": int, "side": "A"|"B"}
Side "A" = ask (buy aggressor), "B" = bid (sell aggressor).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _minute_bucket(ts: datetime) -> datetime:
    """Truncate datetime to the start of its minute (UTC-aware safe)."""
    return ts.replace(second=0, microsecond=0)


def _thirty_min_bucket(ts: datetime) -> datetime:
    """Truncate datetime to the start of its 30-minute period."""
    minute = (ts.minute // 30) * 30
    return ts.replace(minute=minute, second=0, microsecond=0)


def _new_candle(ts: datetime, price: float, size: int, side: str) -> dict[str, Any]:
    buy_vol = size if side == "A" else 0
    sell_vol = size if side == "B" else 0
    return {
        "ts": ts,
        "open": price,
        "high": price,
        "low": price,
        "close": price,
        "volume": size,
        "buy_volume": buy_vol,
        "sell_volume": sell_vol,
        "delta": buy_vol - sell_vol,
        "tick_count": 1,
    }


def _update_candle(candle: dict[str, Any], price: float, size: int, side: str) -> None:
    candle["high"] = max(candle["high"], price)
    candle["low"] = min(candle["low"], price)
    candle["close"] = price
    candle["volume"] += size
    if side == "A":
        candle["buy_volume"] += size
    else:
        candle["sell_volume"] += size
    candle["delta"] = candle["buy_volume"] - candle["sell_volume"]
    candle["tick_count"] += 1


class CandleAggregator:
    """Incrementally aggregates ticks into 1m and 30m OHLCV candles.

    Usage::

        agg = CandleAggregator()
        completed = agg.update(tick)   # list of newly closed 1m candles
        candle = agg.current_1m        # candle currently building
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Clear all state for a new session."""
        self._current_1m: dict[str, Any] | None = None
        self._current_1m_bucket: datetime | None = None

        self._current_30m: dict[str, Any] | None = None
        self._current_30m_bucket: datetime | None = None

        self._completed_1m: list[dict[str, Any]] = []
        self._completed_30m: list[dict[str, Any]] = []

    @property
    def current_1m(self) -> dict[str, Any] | None:
        """The 1m candle currently being built (not yet closed)."""
        return self._current_1m

    def update(self, tick: dict[str, Any]) -> list[dict[str, Any]]:
        """Process one tick and return a list of newly completed 1m candles.

        Args:
            tick: Dict with keys ``ts`` (datetime), ``price`` (float),
                  ``size`` (int), ``side`` ("A" | "B").

        Returns:
            List of completed 1m candle dicts (usually empty or one element).
        """
        ts: datetime = tick["ts"]
        price: float = float(tick["price"])
        size: int = int(tick["size"])
        side: str = tick["side"]

        bucket = _minute_bucket(ts)
        newly_closed: list[dict[str, Any]] = []

        if self._current_1m_bucket is None:
            # First tick ever — open the first candle
            self._current_1m_bucket = bucket
            self._current_1m = _new_candle(bucket, price, size, side)
        elif bucket > self._current_1m_bucket:
            # Minute boundary crossed — close current candle(s)
            closed = self._current_1m  # type: ignore[assignment]
            self._completed_1m.append(closed)
            newly_closed.append(closed)
            # Pass the new bucket so 30m logic can detect period rollover
            self._try_close_30m(closed, new_1m_bucket=bucket)

            # Open a new candle for the new bucket
            self._current_1m_bucket = bucket
            self._current_1m = _new_candle(bucket, price, size, side)
        else:
            # Same minute — update existing candle
            _update_candle(self._current_1m, price, size, side)  # type: ignore[arg-type]

        return newly_closed

    def _try_close_30m(self, closed_1m: dict[str, Any], new_1m_bucket: datetime | None = None) -> None:
        """Merge a closed 1m candle into the 30m candle, closing it if needed.

        Args:
            closed_1m: The 1m candle that was just completed.
            new_1m_bucket: The minute bucket of the *next* tick (used to detect
                whether the 30m period has rolled over).
        """
        candle_ts: datetime = closed_1m["ts"]
        bucket_30m = _thirty_min_bucket(candle_ts)

        def _open_new(bucket: datetime) -> dict[str, Any]:
            return {
                "ts": bucket,
                "open": closed_1m["open"],
                "high": closed_1m["high"],
                "low": closed_1m["low"],
                "close": closed_1m["close"],
                "volume": closed_1m["volume"],
                "buy_volume": closed_1m["buy_volume"],
                "sell_volume": closed_1m["sell_volume"],
                "delta": closed_1m["delta"],
                "tick_count": closed_1m["tick_count"],
            }

        if self._current_30m_bucket is None:
            # First 1m candle ever — open the first 30m candle
            self._current_30m_bucket = bucket_30m
            self._current_30m = _open_new(bucket_30m)
        else:
            # Merge the closed 1m into the current 30m first
            c = self._current_30m
            assert c is not None
            c["high"] = max(c["high"], closed_1m["high"])
            c["low"] = min(c["low"], closed_1m["low"])
            c["close"] = closed_1m["close"]
            c["volume"] += closed_1m["volume"]
            c["buy_volume"] += closed_1m["buy_volume"]
            c["sell_volume"] += closed_1m["sell_volume"]
            c["delta"] = c["buy_volume"] - c["sell_volume"]
            c["tick_count"] += closed_1m["tick_count"]

        # If the *next* incoming tick is in a new 30m period, close the current one
        if new_1m_bucket is not None:
            next_bucket_30m = _thirty_min_bucket(new_1m_bucket)
            if next_bucket_30m > self._current_30m_bucket:  # type: ignore[operator]
                assert self._current_30m is not None
                self._completed_30m.append(self._current_30m)
                self._current_30m = None
                self._current_30m_bucket = None

    def flush(self) -> dict[str, Any] | None:
        """Force-close the current in-progress 1m candle (end of session).

        Returns the closed candle, or None if there is nothing to flush.
        Also closes any open 30m candle.
        """
        if self._current_1m is None:
            return None

        closed = self._current_1m
        self._completed_1m.append(closed)
        self._try_close_30m(closed)

        # Also close open 30m candle
        if self._current_30m is not None:
            self._completed_30m.append(self._current_30m)
            self._current_30m = None
            self._current_30m_bucket = None

        self._current_1m = None
        self._current_1m_bucket = None
        return closed

    def get_completed_1m(self) -> list[dict[str, Any]]:
        """Return all closed 1m candles."""
        return list(self._completed_1m)

    def get_completed_30m(self) -> list[dict[str, Any]]:
        """Return all closed 30m candles."""
        return list(self._completed_30m)

    def get_recent_1m(self, n: int = 5) -> list[dict[str, Any]]:
        """Return the last N completed 1m candles (oldest first)."""
        return list(self._completed_1m[-n:]) if n > 0 else []
