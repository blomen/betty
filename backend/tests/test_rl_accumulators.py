"""Tests for IncrementalVWAP and IncrementalVolumeProfile accumulators."""
import math
import pytest

from src.rl.data.accumulators import IncrementalVWAP, IncrementalVolumeProfile
from src.market_data.levels import compute_vwap_bands, compute_volume_profile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trades(*price_size_pairs) -> list[dict]:
    """Build a trade list from (price, size) tuples."""
    return [{"price": p, "size": s} for p, s in price_size_pairs]


def _feed_vwap(acc: IncrementalVWAP, trades: list[dict]) -> None:
    for t in trades:
        acc.update(t["price"], t["size"])


def _feed_vp(acc: IncrementalVolumeProfile, trades: list[dict]) -> None:
    for t in trades:
        acc.update(t["price"], t["size"])


# ---------------------------------------------------------------------------
# IncrementalVWAP — unit tests
# ---------------------------------------------------------------------------

class TestIncrementalVWAPEmpty:
    def test_empty_returns_none(self):
        acc = IncrementalVWAP()
        assert acc.get() is None


class TestIncrementalVWAPSingleTrade:
    def test_vwap_equals_price(self):
        acc = IncrementalVWAP()
        acc.update(100.0, 10)
        result = acc.get()
        assert result is not None
        assert result.vwap == pytest.approx(100.0)

    def test_sd_is_zero_for_single_trade(self):
        acc = IncrementalVWAP()
        acc.update(100.0, 10)
        result = acc.get()
        # With a single price, variance = 0 → all bands collapse to vwap
        assert result.sd1_upper == pytest.approx(100.0)
        assert result.sd1_lower == pytest.approx(100.0)


class TestIncrementalVWAPWeighted:
    def test_weighted_average(self):
        # price 100 × size 1, price 200 × size 3 → vwap = 175
        acc = IncrementalVWAP()
        acc.update(100.0, 1)
        acc.update(200.0, 3)
        result = acc.get()
        assert result is not None
        assert result.vwap == pytest.approx(175.0)

    def test_bands_are_ordered(self):
        acc = IncrementalVWAP()
        for p, s in [(100, 5), (105, 3), (98, 7), (110, 2)]:
            acc.update(p, s)
        r = acc.get()
        assert r.sd3_lower < r.sd2_lower < r.sd1_lower <= r.vwap
        assert r.vwap <= r.sd1_upper < r.sd2_upper < r.sd3_upper


class TestIncrementalVWAPMatchesBatch:
    def test_matches_batch_computation(self):
        trades = _trades(
            (100.25, 10), (100.50, 5), (100.00, 20),
            (101.00, 8), (99.75, 15), (100.25, 12),
        )
        acc = IncrementalVWAP()
        _feed_vwap(acc, trades)
        incremental = acc.get()
        batch = compute_vwap_bands(trades)

        assert incremental is not None
        assert batch is not None
        assert incremental.vwap       == pytest.approx(batch.vwap)
        assert incremental.sd1_upper  == pytest.approx(batch.sd1_upper)
        assert incremental.sd1_lower  == pytest.approx(batch.sd1_lower)
        assert incremental.sd2_upper  == pytest.approx(batch.sd2_upper)
        assert incremental.sd2_lower  == pytest.approx(batch.sd2_lower)
        assert incremental.sd3_upper  == pytest.approx(batch.sd3_upper)
        assert incremental.sd3_lower  == pytest.approx(batch.sd3_lower)


class TestIncrementalVWAPReset:
    def test_reset_returns_none(self):
        acc = IncrementalVWAP()
        acc.update(100.0, 5)
        acc.reset()
        assert acc.get() is None

    def test_reset_then_new_data(self):
        acc = IncrementalVWAP()
        acc.update(100.0, 5)
        acc.reset()
        acc.update(200.0, 1)
        result = acc.get()
        assert result is not None
        assert result.vwap == pytest.approx(200.0)


# ---------------------------------------------------------------------------
# IncrementalVolumeProfile — unit tests
# ---------------------------------------------------------------------------

class TestIncrementalVolumeProfileEmpty:
    def test_empty_returns_none(self):
        acc = IncrementalVolumeProfile()
        assert acc.get() is None


class TestIncrementalVolumeProfileSingleTrade:
    def test_single_trade_poc(self):
        acc = IncrementalVolumeProfile(tick_size=0.25)
        acc.update(100.25, 50)
        result = acc.get()
        assert result is not None
        assert result.poc == pytest.approx(100.25)
        assert result.vah == pytest.approx(result.poc)
        assert result.val == pytest.approx(result.poc)


class TestIncrementalVolumeProfilePOC:
    def test_poc_is_highest_volume(self):
        acc = IncrementalVolumeProfile(tick_size=0.25)
        acc.update(100.00, 10)
        acc.update(100.25, 50)  # highest
        acc.update(100.50, 30)
        result = acc.get()
        assert result is not None
        assert result.poc == pytest.approx(100.25)


class TestIncrementalVolumeProfileValueArea:
    def test_value_area_contains_poc(self):
        acc = IncrementalVolumeProfile(tick_size=0.25)
        for p, s in [(100.00, 20), (100.25, 100), (100.50, 40), (100.75, 15)]:
            acc.update(p, s)
        result = acc.get()
        assert result is not None
        assert result.val <= result.poc <= result.vah

    def test_value_area_spans_70_percent(self):
        # Build a symmetric profile so we can verify the VA covers >= 70 %
        acc = IncrementalVolumeProfile(tick_size=1.0)
        levels = [(95, 5), (96, 10), (97, 20), (98, 40), (99, 100),
                  (100, 200), (101, 100), (102, 40), (103, 20), (104, 10), (105, 5)]
        for p, s in levels:
            acc.update(p, s)
        result = acc.get()
        total = sum(s for _, s in levels)
        buckets = {float(p): s for p, s in levels}
        va_vol = sum(
            v for p, v in buckets.items()
            if result.val <= p <= result.vah
        )
        assert va_vol / total >= 0.70


class TestIncrementalVolumeProfileSinglePrints:
    def test_single_prints_detected(self):
        # Level at 101.00 with tiny volume → single print
        acc = IncrementalVolumeProfile(tick_size=0.25)
        acc.update(100.00, 200)  # POC candidate
        acc.update(100.25, 50)
        acc.update(100.50, 60)
        acc.update(101.00, 1)    # < 5 % of 200 → single print
        result = acc.get()
        assert result is not None
        single_prices = [sp[0] for sp in result.single_prints]
        assert 101.0 in single_prices

    def test_no_false_single_prints(self):
        # All levels have substantial volume → no single prints
        acc = IncrementalVolumeProfile(tick_size=0.25)
        for p in [100.00, 100.25, 100.50, 100.75]:
            acc.update(p, 100)
        result = acc.get()
        assert result is not None
        assert result.single_prints == []


class TestIncrementalVolumeProfileMatchesBatch:
    def test_matches_batch_computation(self):
        trades = _trades(
            (100.00, 20), (100.25, 50), (100.50, 30),
            (100.75, 10), (101.00, 5),  (99.75, 8),
            (100.25, 40), (100.50, 25), (100.00, 15),
        )
        tick = 0.25
        acc = IncrementalVolumeProfile(tick_size=tick)
        _feed_vp(acc, trades)
        incremental = acc.get()
        batch = compute_volume_profile(trades, tick_size=tick)

        assert incremental is not None
        assert incremental.poc == pytest.approx(batch.poc)
        assert incremental.vah == pytest.approx(batch.vah)
        assert incremental.val == pytest.approx(batch.val)
        # Level counts and prices should match
        assert len(incremental.levels) == len(batch.levels)
        for il, bl in zip(incremental.levels, batch.levels):
            assert il.price  == pytest.approx(bl.price)
            assert il.volume == bl.volume


class TestIncrementalVolumeProfileReset:
    def test_reset_returns_none(self):
        acc = IncrementalVolumeProfile()
        acc.update(100.0, 10)
        acc.reset()
        assert acc.get() is None

    def test_reset_then_new_data(self):
        acc = IncrementalVolumeProfile(tick_size=0.25)
        acc.update(100.0, 100)
        acc.reset()
        acc.update(200.0, 50)
        result = acc.get()
        assert result is not None
        assert result.poc == pytest.approx(200.0)
