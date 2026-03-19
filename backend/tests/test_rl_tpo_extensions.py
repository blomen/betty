"""Tests for TPO extension functions: shape classification, rotation factor, excess detection."""
import pytest

from src.market_data.tpo import (
    TPOProfile,
    compute_tpo_profile,
    classify_tpo_shape,
    detect_excess,
)


def _bars_30m(prices: list[tuple[float, float]]) -> list[dict]:
    """Helper: create list of bar dicts from (high, low) tuples."""
    return [{"high": h, "low": l} for h, l in prices]


# ---------------------------------------------------------------------------
# classify_tpo_shape
# ---------------------------------------------------------------------------

class TestClassifyTpoShape:
    def test_balanced_profile(self):
        # Uniform distribution across range → balanced
        bars = _bars_30m([(10.0, 9.0)] * 10)  # same range for each period
        profile = compute_tpo_profile(bars, tick_size=0.25)
        result = classify_tpo_shape(profile)
        assert result == "balanced"

    def test_p_shape_cluster_at_top(self):
        # Many periods at top, few at bottom → p-shape
        # Top cluster: bars with high range in upper region
        top_bars = _bars_30m([(20.0, 18.0)] * 15)   # 15 bars at top
        bottom_bars = _bars_30m([(12.0, 10.0)] * 3)  # 3 bars at bottom
        bars = bottom_bars + top_bars
        profile = compute_tpo_profile(bars, tick_size=0.25)
        result = classify_tpo_shape(profile)
        assert result == "p-shape"

    def test_b_shape_cluster_at_bottom(self):
        # Many periods at bottom, few at top → b-shape
        bottom_bars = _bars_30m([(12.0, 10.0)] * 15)  # 15 bars at bottom
        top_bars = _bars_30m([(20.0, 18.0)] * 3)      # 3 bars at top
        bars = top_bars + bottom_bars
        profile = compute_tpo_profile(bars, tick_size=0.25)
        result = classify_tpo_shape(profile)
        assert result == "b-shape"

    def test_d_shape_elongated_range(self):
        # More than 30 price levels with even distribution → d-shape
        # Create bars that span a wide range with even coverage
        bars = []
        # Each bar covers 2.5 range, stepping up 2.5 each time to cover ~40 tick levels
        for i in range(10):
            bars.append({"high": 10.0 + i * 2.5, "low": 9.0 + i * 2.5})
        profile = compute_tpo_profile(bars, tick_size=0.25)
        sorted_prices = sorted(profile.letters.keys())
        # Verify we actually have >30 price levels for this test to be meaningful
        assert len(sorted_prices) > 30
        result = classify_tpo_shape(profile)
        assert result == "d-shape"

    def test_empty_profile_returns_balanced(self):
        profile = compute_tpo_profile([], tick_size=0.25)
        result = classify_tpo_shape(profile)
        assert result == "balanced"



# ---------------------------------------------------------------------------
# detect_excess
# ---------------------------------------------------------------------------

class TestDetectExcess:
    def test_empty_profile_returns_zero_zero(self):
        profile = compute_tpo_profile([], tick_size=0.25)
        excess_high, excess_low = detect_excess(profile)
        assert excess_high == 0
        assert excess_low == 0

    def test_single_tpo_at_high_excess_high(self):
        # A lone single-print at the top = excess high
        # Build a profile: wide body + single print sticking up at top
        body_bars = _bars_30m([(10.0, 9.0)] * 10)   # creates density at 9.0-10.0
        spike_bar = _bars_30m([(11.0, 10.75)])        # single spike bar at top
        bars = body_bars + spike_bar
        profile = compute_tpo_profile(bars, tick_size=0.25)
        excess_high, excess_low = detect_excess(profile)
        assert excess_high > 0

    def test_single_tpo_at_low_excess_low(self):
        # A lone single-print at the bottom = excess low
        # spike_bar covers 8.75-9.0 (2 tick levels: 8.75, 9.0)
        # body_bars start at 9.25 so there is no overlap with the spike
        spike_bar = _bars_30m([(9.0, 8.75)])           # single spike bar at bottom
        body_bars = _bars_30m([(10.0, 9.25)] * 10)    # density well above spike
        bars = spike_bar + body_bars
        profile = compute_tpo_profile(bars, tick_size=0.25)
        excess_high, excess_low = detect_excess(profile)
        assert excess_low > 0

    def test_multiple_touches_at_high_no_excess(self):
        # Multiple bars touch the high -> more than 1 letter -> no excess
        bars = _bars_30m([(10.0, 9.0)] * 8)
        profile = compute_tpo_profile(bars, tick_size=0.25)
        excess_high, excess_low = detect_excess(profile)
        # Top prices all touched by many letters -> no excess
        assert excess_high == 0

    def test_multiple_touches_at_low_no_excess(self):
        bars = _bars_30m([(10.0, 9.0)] * 8)
        profile = compute_tpo_profile(bars, tick_size=0.25)
        excess_high, excess_low = detect_excess(profile)
        assert excess_low == 0

    def test_both_extremes_excess(self):
        # Spike at top and spike at bottom, dense middle
        bottom_spike = _bars_30m([(9.25, 9.0)])
        middle_bars = _bars_30m([(10.0, 9.5)] * 10)
        top_spike = _bars_30m([(11.0, 10.75)])
        bars = bottom_spike + middle_bars + top_spike
        profile = compute_tpo_profile(bars, tick_size=0.25)
        excess_high, excess_low = detect_excess(profile)
        assert excess_high > 0
        assert excess_low > 0
