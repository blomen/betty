"""Tests for volume profile accuracy and consistency between implementations.

Verifies:
1. POC is the price with highest volume
2. Value area contains ~70% of total volume
3. VA expansion direction is consistent (UP first on tie)
4. levels.py and amt.py produce matching results for the same input
5. Edge cases: single bar, degenerate bar, uniform distribution
"""
import pytest
from src.market_data.levels import (
    compute_volume_profile,
    compute_volume_profile_from_bars,
    _accumulate_bars_into_buckets,
)


def _make_trades(prices_volumes: list[tuple[float, int]]) -> list[dict]:
    return [{"price": p, "size": v} for p, v in prices_volumes]


def _make_bars(bar_specs: list[tuple[float, float, float, int]]) -> list[dict]:
    """Create bar dicts: (low, high, close, volume)."""
    return [{"low": lo, "high": hi, "close": cl, "volume": vol} for lo, hi, cl, vol in bar_specs]


class TestPOCAccuracy:
    def test_poc_is_highest_volume_price(self):
        """POC must be the price with the most volume."""
        trades = _make_trades([
            (100.0, 50), (100.25, 200), (100.50, 150), (100.75, 80),
        ])
        vp = compute_volume_profile(trades)
        assert vp.poc == 100.25

    def test_poc_from_bars_matches_tick_poc(self):
        """Bar-based VP should find same POC as tick-based when bar has single price."""
        bars = _make_bars([
            (100.0, 100.0, 100.0, 50),   # degenerate bar at 100.0
            (100.25, 100.25, 100.25, 200),
            (100.50, 100.50, 100.50, 150),
        ])
        vp = compute_volume_profile_from_bars(bars, tick_size=0.25)
        assert vp.poc == 100.25

    def test_poc_with_spread_bars(self):
        """VP from bars with range should distribute volume and find correct POC."""
        # Bar centered on 100.25 with most volume
        bars = _make_bars([
            (100.0, 100.50, 100.25, 300),  # 3 levels: 100.0, 100.25, 100.50 → 100 each
            (100.0, 100.0, 100.0, 50),     # all at 100.0
        ])
        vp = compute_volume_profile_from_bars(bars, tick_size=0.25)
        # 100.0 gets 100 (from first bar) + 50 (from second) = 150
        # 100.25 gets 100 (from first bar)
        # 100.50 gets 100 (from first bar)
        assert vp.poc == 100.0  # 150 > 100


class TestValueArea:
    def test_value_area_contains_70_percent(self):
        """Value area should contain approximately 70% of total volume."""
        trades = _make_trades([
            (99.0, 10), (99.25, 20), (99.50, 40), (99.75, 60),
            (100.0, 200), (100.25, 180), (100.50, 60),
            (100.75, 30), (101.0, 15), (101.25, 5),
        ])
        vp = compute_volume_profile(trades)
        total = sum(t["size"] for t in trades)

        # Sum volume within VA
        va_vol = sum(
            t["size"] for t in trades
            if vp.val <= t["price"] <= vp.vah
        )
        pct = va_vol / total
        assert pct >= 0.70, f"VA only contains {pct:.1%} of volume"
        # Should not be wildly over (≤85% in most cases)
        assert pct <= 0.90, f"VA contains {pct:.1%} — overexpanded"

    def test_va_from_bars_also_70_percent(self):
        """Bar-based VP value area should also hit ~70%."""
        bars = _make_bars([
            (99.0, 101.0, 100.0, 500),
            (99.5, 100.5, 100.0, 800),
            (100.0, 101.5, 100.5, 300),
        ])
        vp = compute_volume_profile_from_bars(bars, tick_size=0.25)
        buckets = _accumulate_bars_into_buckets(bars, tick_size=0.25)
        total = sum(buckets.values())
        va_vol = sum(v for p, v in buckets.items() if vp.val <= p <= vp.vah)
        pct = va_vol / total
        assert pct >= 0.70, f"Bar VP VA only contains {pct:.1%}"


class TestTieBreaking:
    def test_equal_volume_expands_up_first(self):
        """When volume above and below POC are equal, expand UP first."""
        # Symmetric distribution: POC at 100.0
        trades = _make_trades([
            (99.50, 50), (99.75, 50),
            (100.0, 200),
            (100.25, 50), (100.50, 50),
        ])
        vp = compute_volume_profile(trades)
        assert vp.poc == 100.0
        # With equal volumes on both sides, VAH should expand first
        assert vp.vah >= 100.25

    def test_bar_vp_tie_breaking_matches_tick_vp(self):
        """Both implementations should expand the same direction on ties."""
        # Create bars that produce a symmetric profile
        bars = _make_bars([
            (99.75, 100.25, 100.0, 300),  # 3 levels, 100 each
        ])
        vp_bars = compute_volume_profile_from_bars(bars, tick_size=0.25)

        trades = _make_trades([
            (99.75, 100), (100.0, 100), (100.25, 100),
        ])
        vp_ticks = compute_volume_profile(trades)

        assert vp_bars.poc == vp_ticks.poc
        assert vp_bars.vah == vp_ticks.vah
        assert vp_bars.val == vp_ticks.val


class TestConsistency:
    """Verify levels.py bar VP matches amt.py bar VP for same input."""

    def test_amt_vp_matches_levels_vp(self):
        """amt.py compute_volume_profile and levels.py compute_volume_profile_from_bars
        should produce the same POC/VAH/VAL for the same bar data."""
        try:
            from src.market_data.amt import compute_volume_profile as amt_vp, BarData
        except ImportError:
            pytest.skip("amt.py not importable")

        # Create BarData objects for amt.py
        class FakeBar:
            def __init__(self, low, high, close, volume):
                self.low = low
                self.high = high
                self.close = close
                self.volume = volume
                self.open = low
                self.timestamp = None

        bars_raw = [
            (99.0, 101.0, 100.0, 500),
            (99.5, 100.5, 100.0, 800),
            (100.0, 101.5, 100.5, 300),
            (98.5, 99.5, 99.0, 200),
        ]

        # levels.py path
        bars_dict = [{"low": lo, "high": hi, "close": cl, "volume": vol} for lo, hi, cl, vol in bars_raw]
        vp_levels = compute_volume_profile_from_bars(bars_dict, tick_size=0.25)

        # amt.py path
        fake_bars = [FakeBar(lo, hi, cl, vol) for lo, hi, cl, vol in bars_raw]
        vp_amt = amt_vp(fake_bars, tick_size=0.25)

        # POC should match (both use max volume bucket)
        assert vp_levels.poc == vp_amt.poc, f"POC mismatch: levels={vp_levels.poc} vs amt={vp_amt.poc}"
        # VAH/VAL should be very close (same expansion logic after fix)
        assert abs(vp_levels.vah - vp_amt.vah) <= 0.50, f"VAH mismatch: levels={vp_levels.vah} vs amt={vp_amt.vah}"
        assert abs(vp_levels.val - vp_amt.val) <= 0.50, f"VAL mismatch: levels={vp_levels.val} vs amt={vp_amt.val}"


class TestEdgeCases:
    def test_empty_input(self):
        vp = compute_volume_profile([])
        assert vp.poc == 0
        assert vp.vah == 0
        assert vp.val == 0

    def test_single_trade(self):
        vp = compute_volume_profile([{"price": 100.0, "size": 500}])
        assert vp.poc == 100.0
        assert vp.vah == 100.0
        assert vp.val == 100.0

    def test_single_bar(self):
        bars = _make_bars([(100.0, 100.50, 100.25, 100)])
        vp = compute_volume_profile_from_bars(bars, tick_size=0.25)
        assert vp.poc > 0
        assert vp.vah >= vp.val

    def test_degenerate_bar_zero_range(self):
        """Bar with high == low should place all volume at close."""
        bars = [{"high": 100.0, "low": 100.0, "close": 100.0, "volume": 500}]
        vp = compute_volume_profile_from_bars(bars, tick_size=0.25)
        assert vp.poc == 100.0

    def test_vah_gte_val(self):
        """VAH must always be >= VAL."""
        import random
        random.seed(42)
        for _ in range(20):
            n = random.randint(5, 50)
            trades = [{"price": round(random.uniform(99, 101) / 0.25) * 0.25, "size": random.randint(1, 500)} for _ in range(n)]
            vp = compute_volume_profile(trades)
            assert vp.vah >= vp.val, f"VAH={vp.vah} < VAL={vp.val}"
