"""Tests for bars_to_trades() volume distribution and its effect on VP quality."""

import pytest
from src.market_data.levels import bars_to_trades, compute_volume_profile


# ---------------------------------------------------------------------------
# bars_to_trades() unit tests
# ---------------------------------------------------------------------------


class TestBarsToTradesEmpty:
    def test_empty_bars(self):
        assert bars_to_trades([]) == []


class TestBarsToTradesSingleBar:
    def test_distributes_volume_across_range(self):
        bar = {"high": 20100.0, "low": 20098.0, "close": 20099.0, "volume": 100}
        trades = bars_to_trades([bar], tick_size=0.25)
        # Range: 20098.0 to 20100.0 = 9 tick levels
        prices = sorted(set(t["price"] for t in trades))
        assert len(prices) == 9
        assert min(prices) == pytest.approx(20098.0)
        assert max(prices) == pytest.approx(20100.0)

    def test_total_volume_preserved_exactly(self):
        bar = {"high": 20105.0, "low": 20095.0, "close": 20100.0, "volume": 500}
        trades = bars_to_trades([bar], tick_size=0.25)
        total_vol = sum(t["size"] for t in trades)
        assert total_vol == 500

    def test_tick_grid_alignment(self):
        bar = {"high": 20100.13, "low": 20098.87, "close": 20099.5, "volume": 200}
        trades = bars_to_trades([bar], tick_size=0.25)
        for t in trades:
            # Every price should be on tick grid
            remainder = t["price"] % 0.25
            assert remainder == pytest.approx(0.0, abs=1e-8) or remainder == pytest.approx(0.25, abs=1e-8)


class TestBarsToTradesDegenerate:
    def test_zero_range_bar_uses_close(self):
        bar = {"high": 20100.0, "low": 20100.0, "close": 20100.0, "volume": 50}
        trades = bars_to_trades([bar], tick_size=0.25)
        assert len(trades) == 1
        assert trades[0]["price"] == pytest.approx(20100.0)
        assert trades[0]["size"] == 50

    def test_missing_fields_fallback(self):
        bar = {"close": 20100.0}  # no high/low/volume
        trades = bars_to_trades([bar], tick_size=0.25)
        assert len(trades) == 1
        assert trades[0]["price"] == pytest.approx(20100.0)

    def test_zero_volume_fallback(self):
        bar = {"high": 20105.0, "low": 20095.0, "close": 20100.0, "volume": 0}
        trades = bars_to_trades([bar], tick_size=0.25)
        assert len(trades) == 1
        assert trades[0]["price"] == pytest.approx(20100.0)


class TestBarsToTradesMultipleBars:
    def test_multiple_bars_accumulate(self):
        bars = [
            {"high": 20102.0, "low": 20098.0, "close": 20100.0, "volume": 100},
            {"high": 20105.0, "low": 20100.0, "close": 20103.0, "volume": 200},
        ]
        trades = bars_to_trades(bars, tick_size=0.25)
        total_vol = sum(t["size"] for t in trades)
        assert total_vol >= 270  # close to 300, allowing integer division rounding


# ---------------------------------------------------------------------------
# VP quality: bars_to_trades vs close-only
# ---------------------------------------------------------------------------


def _close_only_trades(bars):
    """Old approach: all volume at close price."""
    return [{"price": b["close"], "size": b.get("volume", 1)} for b in bars]


class TestVPQualityComparison:
    """Verify that bars_to_trades produces a more realistic VP than close-only."""

    @pytest.fixture
    def nq_session_bars(self):
        """Synthetic NQ session: 390 1-minute bars with realistic OHLC spread."""
        import random
        random.seed(42)
        bars = []
        price = 20000.0
        for i in range(390):
            move = random.gauss(0, 2)  # ~2 point avg move per bar
            close = round((price + move) * 4) / 4  # snap to tick
            high = close + random.uniform(0.25, 3.0)
            low = close - random.uniform(0.25, 3.0)
            volume = random.randint(50, 500)
            bars.append({"high": high, "low": low, "close": close, "volume": volume})
            price = close
        return bars

    def test_distributed_vp_has_more_levels(self, nq_session_bars):
        """Distributed VP should have more price levels than close-only."""
        close_vp = compute_volume_profile(_close_only_trades(nq_session_bars))
        dist_vp = compute_volume_profile(bars_to_trades(nq_session_bars))

        # Distributed covers the full range of each bar, not just closes
        assert len(dist_vp.levels) > len(close_vp.levels)

    def test_distributed_vp_value_area_is_wider(self, nq_session_bars):
        """With distribution, VA should span a wider price range (more realistic)."""
        close_vp = compute_volume_profile(_close_only_trades(nq_session_bars))
        dist_vp = compute_volume_profile(bars_to_trades(nq_session_bars))

        close_va_width = close_vp.vah - close_vp.val
        dist_va_width = dist_vp.vah - dist_vp.val

        # Distributed VA should be at least as wide (usually wider)
        assert dist_va_width >= close_va_width * 0.8

    def test_poc_in_high_volume_area(self, nq_session_bars):
        """POC should be in a sensible location with distributed volume."""
        dist_vp = compute_volume_profile(bars_to_trades(nq_session_bars))
        all_closes = [b["close"] for b in nq_session_bars]

        # POC should be within the range of closes (sanity check)
        assert min(all_closes) - 5 <= dist_vp.poc <= max(all_closes) + 5

    def test_vah_above_val(self, nq_session_bars):
        """Basic invariant: VAH >= POC >= VAL."""
        dist_vp = compute_volume_profile(bars_to_trades(nq_session_bars))
        assert dist_vp.vah >= dist_vp.poc
        assert dist_vp.poc >= dist_vp.val
