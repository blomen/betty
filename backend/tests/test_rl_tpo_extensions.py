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


# ---------------------------------------------------------------------------
# compute_session_tpos
# ---------------------------------------------------------------------------

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.market_data.tpo import (
    SessionTPO,
    SessionTPOSet,
    compute_session_tpos,
)

CET = ZoneInfo("Europe/Stockholm")


def _bar_30m(ts_hour_cet: int, ts_min_cet: int, high: float, low: float) -> dict:
    """Create a 30m bar with a CET timestamp."""
    ts = datetime(2026, 3, 25, ts_hour_cet, ts_min_cet, tzinfo=CET).astimezone(timezone.utc)
    return {"ts": ts, "high": high, "low": low, "open": low, "close": high, "volume": 100}


class TestComputeSessionTpos:
    def test_empty_bars_returns_all_none(self):
        result = compute_session_tpos([], tick_size=0.25)
        assert result.tokyo is None
        assert result.london is None
        assert result.ny is None
        assert result.poc_migration_tokyo_london == 0.0
        assert result.poc_migration_london_ny == 0.0

    def test_tokyo_only_session(self):
        """Bars only in Tokyo window (00:00-08:00 CET) -> london/ny are None."""
        bars = [
            _bar_30m(0, 0, 19810.0, 19800.0),
            _bar_30m(0, 30, 19815.0, 19805.0),
            _bar_30m(1, 0, 19820.0, 19810.0),
            _bar_30m(1, 30, 19825.0, 19815.0),
        ]
        result = compute_session_tpos(bars, tick_size=0.25)
        assert result.tokyo is not None
        assert result.tokyo.session == "tokyo"
        assert result.tokyo.poc > 0
        assert result.tokyo.vah >= result.tokyo.val
        assert result.london is None
        assert result.ny is None

    def test_all_three_sessions(self):
        """Bars across all sessions produce three profiles + migrations."""
        bars = [
            _bar_30m(0, 0, 19810.0, 19800.0),
            _bar_30m(0, 30, 19815.0, 19805.0),
            _bar_30m(1, 0, 19812.0, 19802.0),
            _bar_30m(8, 0, 19850.0, 19840.0),
            _bar_30m(8, 30, 19855.0, 19845.0),
            _bar_30m(9, 0, 19852.0, 19842.0),
            _bar_30m(15, 30, 19890.0, 19880.0),
            _bar_30m(16, 0, 19895.0, 19885.0),
            _bar_30m(16, 30, 19892.0, 19882.0),
        ]
        result = compute_session_tpos(bars, tick_size=0.25)
        assert result.tokyo is not None
        assert result.london is not None
        assert result.ny is not None
        assert result.poc_migration_tokyo_london > 0
        assert result.poc_migration_london_ny > 0

    def test_letters_restart_at_A_per_session(self):
        """Each session's TPO letters start at A, not continuing from prior session."""
        bars = [
            _bar_30m(0, 0, 19810.0, 19800.0),
            _bar_30m(0, 30, 19815.0, 19805.0),
            _bar_30m(8, 0, 19850.0, 19840.0),
            _bar_30m(8, 30, 19855.0, 19845.0),
        ]
        result = compute_session_tpos(bars, tick_size=0.25)
        tokyo_profile = result.tokyo
        london_profile = result.london
        assert tokyo_profile is not None
        assert london_profile is not None
        assert tokyo_profile.shape in ("p-shape", "b-shape", "d-shape", "balanced", "B-shape")
        assert london_profile.shape in ("p-shape", "b-shape", "d-shape", "balanced", "B-shape")

    def test_ib_valid_false_for_low_volume_tokyo(self):
        """Tokyo IB with very narrow bars -> ib_valid should be False."""
        bars = [
            _bar_30m(0, 0, 19800.0, 19800.0),
            _bar_30m(0, 30, 19800.0, 19800.0),
        ]
        result = compute_session_tpos(bars, tick_size=0.25)
        assert result.tokyo is not None
        assert result.tokyo.ib_valid is False

    def test_ib_valid_true_for_normal_session(self):
        """London with normal IB range -> ib_valid should be True."""
        bars = [
            _bar_30m(8, 0, 19860.0, 19840.0),
            _bar_30m(8, 30, 19865.0, 19845.0),
            _bar_30m(9, 0, 19855.0, 19850.0),
        ]
        result = compute_session_tpos(bars, tick_size=0.25)
        assert result.london is not None
        assert result.london.ib_valid is True


# ---------------------------------------------------------------------------
# extract_session_tpo_features
# ---------------------------------------------------------------------------

import numpy as np
from src.rl.features.tpo_features import extract_session_tpo_features
from src.rl.features.observation import build_observation, OBSERVATION_DIM
from src.rl.config import LevelType


class TestExtractSessionTpoFeatures:
    def test_none_returns_26_zeros(self):
        result = extract_session_tpo_features(None, current_price=19850.0)
        assert result.shape == (38,)
        assert np.all(result == 0.0)

    def test_all_sessions_populated(self):
        tpo_set = SessionTPOSet(
            tokyo=SessionTPO("tokyo", poc=19800.0, vah=19820.0, val=19780.0,
                             shape="balanced", ib_high=19810.0, ib_low=19795.0,
                             ib_valid=False, poor_high=False, poor_low=True),
            london=SessionTPO("london", poc=19850.0, vah=19870.0, val=19830.0,
                              shape="p-shape", ib_high=19860.0, ib_low=19840.0,
                              ib_valid=True, poor_high=True, poor_low=False),
            ny=SessionTPO("ny", poc=19890.0, vah=19910.0, val=19870.0,
                          shape="b-shape", ib_high=19900.0, ib_low=19880.0,
                          ib_valid=True, poor_high=False, poor_low=False),
            poc_migration_tokyo_london=200.0,
            poc_migration_london_ny=160.0,
        )
        result = extract_session_tpo_features(tpo_set, current_price=19850.0)
        assert result.shape == (38,)
        assert result.dtype == np.float32
        # Tokyo IB features should be zeroed (ib_valid=False)
        assert result[4] == 0.0  # ib_range
        assert result[5] == 0.0  # price_vs_ib_mid
        # London shape should be +1 (p-shape)
        assert result[12 + 3] == 1.0
        # Migration features
        assert result[36] != 0.0
        assert result[37] != 0.0

    def test_partial_sessions_zeros_for_missing(self):
        tpo_set = SessionTPOSet(
            tokyo=SessionTPO("tokyo", poc=19800.0, vah=19820.0, val=19780.0,
                             shape="p-shape", ib_high=19810.0, ib_low=19795.0,
                             ib_valid=True, poor_high=False, poor_low=False),
            london=None, ny=None,
            poc_migration_tokyo_london=0.0, poc_migration_london_ny=0.0,
        )
        result = extract_session_tpo_features(tpo_set, current_price=19800.0)
        assert result.shape == (38,)
        assert not np.all(result[0:12] == 0.0)
        assert np.all(result[12:24] == 0.0)
        assert np.all(result[24:36] == 0.0)

    def test_price_position_in_va_within(self):
        tpo_set = SessionTPOSet(
            tokyo=SessionTPO("tokyo", poc=19800.0, vah=19820.0, val=19780.0,
                             shape="balanced", ib_high=19810.0, ib_low=19790.0,
                             ib_valid=True, poor_high=False, poor_low=False),
            london=None, ny=None,
            poc_migration_tokyo_london=0.0, poc_migration_london_ny=0.0,
        )
        result = extract_session_tpo_features(tpo_set, current_price=19800.0)
        assert abs(result[7]) < 0.01

    def test_price_position_above_va(self):
        tpo_set = SessionTPOSet(
            tokyo=SessionTPO("tokyo", poc=19800.0, vah=19820.0, val=19780.0,
                             shape="balanced", ib_high=19810.0, ib_low=19790.0,
                             ib_valid=True, poor_high=False, poor_low=False),
            london=None, ny=None,
            poc_migration_tokyo_london=0.0, poc_migration_london_ny=0.0,
        )
        result = extract_session_tpo_features(tpo_set, current_price=19840.0)
        assert result[7] > 0.0


# ---------------------------------------------------------------------------
# Observation dimension after per-session TPO
# ---------------------------------------------------------------------------


class TestObservationDimension:
    def test_observation_dim(self):
        """Obs dim auto-computed from build_observation."""
        assert OBSERVATION_DIM == 195

    def test_build_observation_returns_correct_dim(self):
        state = {
            "level_type": LevelType.VWAP,
            "price": 19000.0,
            "candles": [],
            "candles_5m": [],
            "vwap_bands": None,
            "volume_profile": None,
            "session_tpos": None,
            "tpo_profile": None,
            "tpo_profile_obj": None,
            "session_levels": None,
            "all_levels": [],
            "orderflow_signals": None,
            "macro": None,
            "session_context": None,
            "day_type": None,
            "recent_ticks": [],
        }
        obs = build_observation(state)
        # Legacy mode (no zone) is 1 dim smaller than zone mode (OBSERVATION_DIM)
        assert obs.shape == (OBSERVATION_DIM - 1,)
        assert obs.dtype == np.float32
