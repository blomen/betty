"""Tests for the tick-to-candle aggregator."""

from datetime import datetime, timezone

import pytest

from src.rl.data.candle_aggregator import CandleAggregator


def _tick(ts_str: str, price: float, size: int = 1, side: str = "A") -> dict:
    """Helper: build a tick dict from a compact timestamp string."""
    ts = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
    return {"ts": ts, "price": price, "size": size, "side": side}


class TestNoTicks:
    def test_no_completed_candles(self):
        agg = CandleAggregator()
        assert agg.get_completed_1m() == []
        assert agg.get_completed_30m() == []

    def test_current_1m_is_none(self):
        agg = CandleAggregator()
        assert agg.current_1m is None

    def test_flush_returns_none(self):
        agg = CandleAggregator()
        assert agg.flush() is None


class TestSingleTick:
    def test_opens_candle(self):
        agg = CandleAggregator()
        completed = agg.update(_tick("2024-01-15 09:30:05", price=4800.0))
        assert completed == []
        assert agg.current_1m is not None

    def test_candle_ohlc_matches_tick(self):
        agg = CandleAggregator()
        agg.update(_tick("2024-01-15 09:30:05", price=4800.25, size=3, side="A"))
        c = agg.current_1m
        assert c["open"] == 4800.25
        assert c["high"] == 4800.25
        assert c["low"] == 4800.25
        assert c["close"] == 4800.25
        assert c["volume"] == 3
        assert c["buy_volume"] == 3
        assert c["sell_volume"] == 0
        assert c["delta"] == 3
        assert c["tick_count"] == 1

    def test_candle_ts_is_minute_bucket(self):
        agg = CandleAggregator()
        agg.update(_tick("2024-01-15 09:30:47", price=4800.0))
        expected_ts = datetime(2024, 1, 15, 9, 30, 0, tzinfo=timezone.utc)
        assert agg.current_1m["ts"] == expected_ts

    def test_no_completed_candles_yet(self):
        agg = CandleAggregator()
        agg.update(_tick("2024-01-15 09:30:05", price=4800.0))
        assert agg.get_completed_1m() == []


class TestMinuteBoundary:
    def test_crossing_minute_closes_candle(self):
        agg = CandleAggregator()
        agg.update(_tick("2024-01-15 09:30:05", price=4800.0))
        completed = agg.update(_tick("2024-01-15 09:31:00", price=4801.0))
        assert len(completed) == 1
        assert len(agg.get_completed_1m()) == 1

    def test_closed_candle_ts_is_correct(self):
        agg = CandleAggregator()
        agg.update(_tick("2024-01-15 09:30:05", price=4800.0))
        agg.update(_tick("2024-01-15 09:31:00", price=4801.0))
        closed = agg.get_completed_1m()[0]
        expected = datetime(2024, 1, 15, 9, 30, 0, tzinfo=timezone.utc)
        assert closed["ts"] == expected

    def test_new_candle_starts_after_crossing(self):
        agg = CandleAggregator()
        agg.update(_tick("2024-01-15 09:30:05", price=4800.0))
        agg.update(_tick("2024-01-15 09:31:00", price=4801.0))
        assert agg.current_1m is not None
        assert agg.current_1m["open"] == 4801.0


class TestOHLCV:
    def test_high_low_tracked_correctly(self):
        agg = CandleAggregator()
        prices = [4800.0, 4802.5, 4798.0, 4801.25, 4799.0]
        for i, p in enumerate(prices):
            agg.update(_tick(f"2024-01-15 09:30:{i:02d}", price=p, size=1, side="A"))
        c = agg.current_1m
        assert c["open"] == 4800.0
        assert c["high"] == 4802.5
        assert c["low"] == 4798.0
        assert c["close"] == 4799.0

    def test_volume_accumulates(self):
        agg = CandleAggregator()
        for i in range(5):
            agg.update(_tick(f"2024-01-15 09:30:{i:02d}", price=4800.0, size=10, side="A"))
        assert agg.current_1m["volume"] == 50
        assert agg.current_1m["tick_count"] == 5

    def test_close_is_last_price(self):
        agg = CandleAggregator()
        agg.update(_tick("2024-01-15 09:30:01", price=4800.0))
        agg.update(_tick("2024-01-15 09:30:02", price=4805.0))
        agg.update(_tick("2024-01-15 09:30:03", price=4795.0))
        assert agg.current_1m["close"] == 4795.0

    def test_closed_candle_ohlcv_correct(self):
        agg = CandleAggregator()
        agg.update(_tick("2024-01-15 09:30:01", price=4800.0, size=5, side="A"))
        agg.update(_tick("2024-01-15 09:30:30", price=4810.0, size=3, side="B"))
        agg.update(_tick("2024-01-15 09:30:59", price=4805.0, size=7, side="A"))
        # Cross minute
        agg.update(_tick("2024-01-15 09:31:00", price=4806.0, size=1, side="A"))
        c = agg.get_completed_1m()[0]
        assert c["open"] == 4800.0
        assert c["high"] == 4810.0
        assert c["low"] == 4800.0
        assert c["close"] == 4805.0
        assert c["volume"] == 15
        assert c["tick_count"] == 3


class TestDeltaTracking:
    def test_buy_side_a(self):
        agg = CandleAggregator()
        agg.update(_tick("2024-01-15 09:30:01", price=4800.0, size=10, side="A"))
        c = agg.current_1m
        assert c["buy_volume"] == 10
        assert c["sell_volume"] == 0
        assert c["delta"] == 10

    def test_sell_side_b(self):
        agg = CandleAggregator()
        agg.update(_tick("2024-01-15 09:30:01", price=4800.0, size=8, side="B"))
        c = agg.current_1m
        assert c["buy_volume"] == 0
        assert c["sell_volume"] == 8
        assert c["delta"] == -8

    def test_mixed_sides_delta(self):
        agg = CandleAggregator()
        agg.update(_tick("2024-01-15 09:30:01", price=4800.0, size=15, side="A"))
        agg.update(_tick("2024-01-15 09:30:02", price=4801.0, size=6, side="B"))
        agg.update(_tick("2024-01-15 09:30:03", price=4800.5, size=4, side="A"))
        c = agg.current_1m
        assert c["buy_volume"] == 19
        assert c["sell_volume"] == 6
        assert c["delta"] == 13

    def test_delta_preserved_in_closed_candle(self):
        agg = CandleAggregator()
        agg.update(_tick("2024-01-15 09:30:01", price=4800.0, size=20, side="A"))
        agg.update(_tick("2024-01-15 09:30:02", price=4800.0, size=5, side="B"))
        agg.update(_tick("2024-01-15 09:31:00", price=4800.0, size=1, side="A"))
        closed = agg.get_completed_1m()[0]
        assert closed["delta"] == 15
        assert closed["buy_volume"] == 20
        assert closed["sell_volume"] == 5


class TestThirtyMinCandles:
    def _build_minutes(self, agg: CandleAggregator, start_hour: int, start_min: int, count: int, base_price: float = 4800.0):
        """Inject `count` 1-minute candles starting at start_hour:start_min."""
        from datetime import timedelta
        base = datetime(2024, 1, 15, start_hour, start_min, 0, tzinfo=timezone.utc)
        for i in range(count):
            ts = base + timedelta(minutes=i)
            price = base_price + i * 0.25
            agg.update({"ts": ts, "price": price, "size": 2, "side": "A"})
        # Cross into the next minute to close the last one
        ts = base + timedelta(minutes=count)
        agg.update({"ts": ts, "price": base_price, "size": 1, "side": "B"})

    def test_no_30m_candle_before_boundary(self):
        agg = CandleAggregator()
        self._build_minutes(agg, 9, 30, 10)
        # 10 minutes inside 09:30 period — no 30m candle closed yet
        assert agg.get_completed_30m() == []

    def test_30m_candle_closed_after_boundary(self):
        agg = CandleAggregator()
        # Fill 09:30–09:59 (30 minutes) then push one tick into 10:00
        self._build_minutes(agg, 9, 30, 30)
        # After 30 complete 1m candles and a tick at 10:00+, 30m should close
        assert len(agg.get_completed_30m()) == 1

    def test_30m_candle_ts_is_period_start(self):
        agg = CandleAggregator()
        self._build_minutes(agg, 9, 30, 30)
        c30 = agg.get_completed_30m()[0]
        expected = datetime(2024, 1, 15, 9, 30, 0, tzinfo=timezone.utc)
        assert c30["ts"] == expected

    def test_30m_candle_aggregates_volume(self):
        agg = CandleAggregator()
        # 30 minutes, 2 units per tick, 1 tick per minute
        self._build_minutes(agg, 9, 30, 30, base_price=4800.0)
        c30 = agg.get_completed_30m()[0]
        assert c30["volume"] == 30 * 2  # 30 candles × 2 units

    def test_two_30m_candles(self):
        agg = CandleAggregator()
        self._build_minutes(agg, 9, 0, 30)   # 09:00–09:29
        self._build_minutes(agg, 9, 30, 30)  # 09:30–09:59
        assert len(agg.get_completed_30m()) == 2

    def test_30m_ohlc_spans_first_and_last_1m(self):
        agg = CandleAggregator()
        # Use predictable prices: minute i → price 4800 + i * 0.25
        self._build_minutes(agg, 9, 30, 30, base_price=4800.0)
        c30 = agg.get_completed_30m()[0]
        assert c30["open"] == 4800.0                  # first candle open
        assert c30["high"] == 4800.0 + 29 * 0.25      # highest price in last candle
        assert c30["low"] == 4800.0                    # lowest price in first candle
        # close = close of last 1m candle = last tick price (4800 + 29*0.25)
        assert c30["close"] == 4800.0 + 29 * 0.25


class TestGetRecentCandles:
    def _fill(self, agg: CandleAggregator, n: int):
        """Close n 1m candles."""
        from datetime import timedelta
        base = datetime(2024, 1, 15, 9, 30, 0, tzinfo=timezone.utc)
        for i in range(n + 1):
            ts = base + timedelta(minutes=i)
            agg.update({"ts": ts, "price": 4800.0 + i, "size": 1, "side": "A"})

    def test_returns_last_n(self):
        agg = CandleAggregator()
        self._fill(agg, 10)
        recent = agg.get_recent_1m(5)
        assert len(recent) == 5

    def test_returns_oldest_first(self):
        agg = CandleAggregator()
        self._fill(agg, 5)
        all_c = agg.get_completed_1m()
        recent = agg.get_recent_1m(3)
        assert recent == all_c[-3:]

    def test_n_larger_than_available(self):
        agg = CandleAggregator()
        self._fill(agg, 3)
        recent = agg.get_recent_1m(10)
        assert len(recent) == 3

    def test_n_zero_returns_empty(self):
        agg = CandleAggregator()
        self._fill(agg, 5)
        assert agg.get_recent_1m(0) == []


class TestFlush:
    def test_flush_closes_open_candle(self):
        agg = CandleAggregator()
        agg.update(_tick("2024-01-15 09:30:05", price=4800.0, size=5, side="A"))
        assert agg.current_1m is not None
        flushed = agg.flush()
        assert flushed is not None
        assert flushed["volume"] == 5
        assert agg.current_1m is None

    def test_flush_appends_to_completed(self):
        agg = CandleAggregator()
        agg.update(_tick("2024-01-15 09:30:05", price=4800.0))
        agg.flush()
        assert len(agg.get_completed_1m()) == 1

    def test_flush_returns_none_when_empty(self):
        agg = CandleAggregator()
        assert agg.flush() is None

    def test_double_flush_returns_none(self):
        agg = CandleAggregator()
        agg.update(_tick("2024-01-15 09:30:05", price=4800.0))
        agg.flush()
        assert agg.flush() is None

    def test_flush_closes_open_30m_candle(self):
        agg = CandleAggregator()
        agg.update(_tick("2024-01-15 09:30:05", price=4800.0, size=2, side="A"))
        agg.flush()
        # flush should push the partial 30m candle into completed
        assert len(agg.get_completed_30m()) == 1


class TestReset:
    def test_reset_clears_all_state(self):
        agg = CandleAggregator()
        agg.update(_tick("2024-01-15 09:30:05", price=4800.0))
        agg.update(_tick("2024-01-15 09:31:00", price=4801.0))
        agg.reset()
        assert agg.current_1m is None
        assert agg.get_completed_1m() == []
        assert agg.get_completed_30m() == []

    def test_can_use_after_reset(self):
        agg = CandleAggregator()
        agg.update(_tick("2024-01-15 09:30:05", price=4800.0))
        agg.reset()
        completed = agg.update(_tick("2024-01-15 10:00:00", price=4900.0))
        assert completed == []
        assert agg.current_1m is not None
        assert agg.current_1m["open"] == 4900.0
