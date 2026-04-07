"""Tests for the narrative feature extractor (15 slow-layer signals)."""
from __future__ import annotations

import numpy as np
import pytest

from src.rl.features.narrative_features import (
    extract_narrative_features,
    NARRATIVE_DIM,
    NARRATIVE_NAMES,
)
from src.market_data.levels import (
    SessionLevels,
    VolumeProfile,
    VWAPBands,
    SwingStructure,
    TimeframeSwings,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_swing_structure(
    daily_struct: str = "uptrend",
    weekly_struct: str = "uptrend",
    monthly_struct: str = "ranging",
    alignment: float = 0.5,
) -> SwingStructure:
    """Return a minimal SwingStructure with no swing points."""
    daily = TimeframeSwings(timeframe="daily", structure=daily_struct)
    weekly = TimeframeSwings(timeframe="weekly", structure=weekly_struct)
    monthly = TimeframeSwings(timeframe="monthly", structure=monthly_struct)
    return SwingStructure(daily=daily, weekly=weekly, monthly=monthly, trend_alignment=alignment)


def _make_vp(poc: float = 19990.0, vah: float = 20010.0, val: float = 19970.0) -> VolumeProfile:
    return VolumeProfile(poc=poc, vah=vah, val=val)


def _make_vwap(vwap: float = 19995.0, sd: float = 20.0) -> VWAPBands:
    return VWAPBands(
        vwap=vwap,
        sd1_upper=vwap + sd,
        sd1_lower=vwap - sd,
        sd2_upper=vwap + 2 * sd,
        sd2_lower=vwap - 2 * sd,
        sd3_upper=vwap + 3 * sd,
        sd3_lower=vwap - 3 * sd,
    )


def _make_session_levels(
    ib_high: float = 20005.0,
    ib_low: float = 19985.0,
    pdh: float = 20050.0,
    pdl: float = 19950.0,
) -> SessionLevels:
    return SessionLevels(ib_high=ib_high, ib_low=ib_low, pdh=pdh, pdl=pdl)


def _make_full_state(price: float = 19990.0) -> dict:
    """Return a well-populated state dict for happy-path tests."""
    return {
        "price": price,
        "macro": {
            "regime_score": 0.6,
            "vix": 18.0,
            "vix_change": 1.0,
            "dxy_change": 0.2,
            "us10y_change": 2.0,
            "us2y_change": 1.5,
            "yield_curve_spread": 0.5,
            "cot_net_position": 50_000,
            "cot_net_change": 5_000,
            "news_proximity": 0.2,
            "news_importance": 1.0,
        },
        "swing_structure": _make_swing_structure(),
        "session_context": {
            "opening_type": "OD",
            "ib_range_percentile": 0.7,
            "minutes_since_rth": 60.0,
            "session_type": "rth",
            "ib_broken": "up",
        },
        "session_levels": _make_session_levels(),
        "volume_profile": _make_vp(),
        "vwap_bands": _make_vwap(),
        "amt_dynamics": {
            "developing_day_type": 0.75,
            "initiative_ratio": 0.6,
            "responsive_ratio": 0.4,
            "balance_width": 30.0,
            "single_print_proximity": 0.1,
            "poc_migration_speed": 0.5,
        },
        "single_print_zones": [],
    }


# ---------------------------------------------------------------------------
# Core shape and type tests
# ---------------------------------------------------------------------------

class TestOutputShape:
    def test_shape_is_15(self):
        state = _make_full_state()
        result = extract_narrative_features(state)
        assert result.shape == (NARRATIVE_DIM,), f"Expected ({NARRATIVE_DIM},), got {result.shape}"

    def test_dtype_is_float32(self):
        state = _make_full_state()
        result = extract_narrative_features(state)
        assert result.dtype == np.float32, f"Expected float32, got {result.dtype}"

    def test_narrative_names_length(self):
        assert len(NARRATIVE_NAMES) == NARRATIVE_DIM, (
            f"NARRATIVE_NAMES has {len(NARRATIVE_NAMES)} entries, expected {NARRATIVE_DIM}"
        )

    def test_narrative_names_are_strings(self):
        for name in NARRATIVE_NAMES:
            assert isinstance(name, str) and name, f"Invalid name: {name!r}"

    def test_narrative_names_are_unique(self):
        assert len(NARRATIVE_NAMES) == len(set(NARRATIVE_NAMES)), "Duplicate names in NARRATIVE_NAMES"


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

class TestBounds:
    def test_all_signals_in_range_full_state(self):
        state = _make_full_state()
        result = extract_narrative_features(state)
        assert np.all(result >= -1.0), f"Values below -1: {result[result < -1.0]}"
        assert np.all(result <= 1.0), f"Values above +1: {result[result > 1.0]}"

    def test_all_signals_in_range_minimal_state(self):
        result = extract_narrative_features({"price": 19900.0})
        assert np.all(result >= -1.0)
        assert np.all(result <= 1.0)

    def test_all_signals_finite(self):
        state = _make_full_state()
        result = extract_narrative_features(state)
        assert np.all(np.isfinite(result)), f"Non-finite values: {result}"

    @pytest.mark.parametrize("vix", [0.0, 15.0, 25.0, 40.0, 80.0, 200.0])
    def test_bounds_with_extreme_vix(self, vix):
        state = _make_full_state()
        state["macro"]["vix"] = vix
        result = extract_narrative_features(state)
        assert np.all(result >= -1.0) and np.all(result <= 1.0)

    @pytest.mark.parametrize("price", [1.0, 10000.0, 20000.0, 100000.0])
    def test_bounds_with_extreme_prices(self, price):
        state = _make_full_state(price=price)
        result = extract_narrative_features(state)
        assert np.all(result >= -1.0) and np.all(result <= 1.0)

    @pytest.mark.parametrize("regime", [0.0, 0.5, 1.0])
    def test_bounds_with_extreme_regime_scores(self, regime):
        state = _make_full_state()
        state["macro"]["regime_score"] = regime
        result = extract_narrative_features(state)
        assert np.all(result >= -1.0) and np.all(result <= 1.0)


# ---------------------------------------------------------------------------
# Empty / None state produces zeros
# ---------------------------------------------------------------------------

class TestEmptyState:
    def test_empty_dict_produces_zeros(self):
        """Empty state should produce zeros for all signals except session_phase.

        session_phase (index 7) is derived from minutes_since_rth which defaults to 0,
        yielding -1.0 (start of session).  All other signals have no data and must be 0.
        """
        result = extract_narrative_features({})
        assert result.shape == (NARRATIVE_DIM,)
        # session_phase defaults to -1 (minutes=0 → start of session)
        # breakout_score defaults to -1 (0/4 signals)
        # ib_extension_ready defaults to -1 (no conditions)
        expected = np.zeros(NARRATIVE_DIM, dtype=np.float32)
        expected[7] = -1.0
        expected[15] = -1.0
        expected[16] = -1.0
        np.testing.assert_array_equal(result, expected), f"Unexpected values: {result}"

    def test_none_values_produce_zeros(self):
        state = {
            "price": 0.0,
            "macro": None,
            "swing_structure": None,
            "session_context": None,
            "session_levels": None,
            "volume_profile": None,
            "vwap_bands": None,
            "amt_dynamics": None,
            "single_print_zones": None,
        }
        result = extract_narrative_features(state)
        assert result.shape == (NARRATIVE_DIM,)
        # session_phase (index 7) is computed from time-of-day → -1.0, not 0
        # breakout_score (15) and ib_extension_ready (16) default to -1.0 (no signals)
        # everything else should be 0
        mask = np.ones(NARRATIVE_DIM, dtype=bool)
        mask[7] = False   # session_phase depends on time-of-day
        mask[15] = False  # breakout_score: 0/4 signals → -1.0
        mask[16] = False  # ib_extension_ready: no conditions met → -1.0
        assert np.all(result[mask] == 0.0), (
            f"Expected zeros for masked signals: {result}"
        )

    def test_price_only_state(self):
        result = extract_narrative_features({"price": 20000.0})
        assert result.shape == (NARRATIVE_DIM,)
        assert np.all(np.isfinite(result))
        assert np.all(result >= -1.0) and np.all(result <= 1.0)


# ---------------------------------------------------------------------------
# Signal-level correctness
# ---------------------------------------------------------------------------

class TestSignalValues:
    def test_regime_score_midpoint(self):
        """regime_score=0.5 → mapped to 0.0"""
        state = {"macro": {"regime_score": 0.5, "vix": 25.0}}
        result = extract_narrative_features(state)
        assert result[0] == pytest.approx(0.0, abs=1e-5), f"regime_score midpoint: {result[0]}"

    def test_regime_score_full_bull(self):
        """regime_score=1.0 → +1.0"""
        state = {"macro": {"regime_score": 1.0, "vix": 25.0}}
        result = extract_narrative_features(state)
        assert result[0] == pytest.approx(1.0, abs=1e-5)

    def test_regime_score_full_bear(self):
        """regime_score=0.0 → -1.0"""
        state = {"macro": {"regime_score": 0.0, "vix": 25.0}}
        result = extract_narrative_features(state)
        assert result[0] == pytest.approx(-1.0, abs=1e-5)

    def test_htf_trend_all_uptrend(self):
        """All timeframes uptrend → htf_trend close to +1"""
        state = {"swing_structure": _make_swing_structure("uptrend", "uptrend", "uptrend", 1.0)}
        result = extract_narrative_features(state)
        assert result[1] > 0.5, f"All-uptrend should yield positive htf_trend, got {result[1]}"

    def test_htf_trend_all_downtrend(self):
        """All timeframes downtrend → htf_trend close to -1"""
        state = {"swing_structure": _make_swing_structure("downtrend", "downtrend", "downtrend", -1.0)}
        result = extract_narrative_features(state)
        assert result[1] < -0.5, f"All-downtrend should yield negative htf_trend, got {result[1]}"

    def test_trend_alignment_propagated(self):
        """trend_alignment (index 13) should equal swing.trend_alignment"""
        for alignment in [-1.0, -0.5, 0.0, 0.5, 1.0]:
            state = {"swing_structure": _make_swing_structure(alignment=alignment)}
            result = extract_narrative_features(state)
            assert result[13] == pytest.approx(alignment, abs=1e-5), (
                f"trend_alignment mismatch for {alignment}: got {result[13]}"
            )

    def test_value_migration_above_prior_va(self):
        """POC above prior VAH → value_migration = +1"""
        state = {
            "price": 20100.0,
            "volume_profile": _make_vp(poc=20060.0, vah=20080.0, val=20020.0),
            "session_levels": _make_session_levels(pdh=20050.0, pdl=19950.0),
        }
        result = extract_narrative_features(state)
        assert result[6] == pytest.approx(1.0, abs=1e-5), f"value_migration above VA: {result[6]}"

    def test_value_migration_below_prior_va(self):
        """POC below prior VAL → value_migration = -1"""
        state = {
            "price": 19900.0,
            "volume_profile": _make_vp(poc=19920.0, vah=19960.0, val=19940.0),
            "session_levels": _make_session_levels(pdh=20050.0, pdl=19950.0),
        }
        result = extract_narrative_features(state)
        assert result[6] == pytest.approx(-1.0, abs=1e-5), f"value_migration below VA: {result[6]}"

    def test_opening_type_od(self):
        """OD opening type → opening_type = +1"""
        state = {"session_context": {"opening_type": "OD"}}
        result = extract_narrative_features(state)
        assert result[4] == pytest.approx(1.0, abs=1e-5), f"OD opening: {result[4]}"

    def test_opening_type_orr(self):
        """ORR → -0.5"""
        state = {"session_context": {"opening_type": "ORR"}}
        result = extract_narrative_features(state)
        assert result[4] == pytest.approx(-0.5, abs=1e-5)

    def test_price_at_poc_vs_value(self):
        """Price exactly at POC → price_vs_poc ≈ 0"""
        poc = 19990.0
        vwap = _make_vwap(vwap=poc, sd=20.0)
        state = {
            "price": poc,
            "volume_profile": _make_vp(poc=poc),
            "vwap_bands": vwap,
        }
        result = extract_narrative_features(state)
        assert result[11] == pytest.approx(0.0, abs=1e-5), f"price_vs_poc at POC: {result[11]}"

    def test_price_at_ib_mid(self):
        """Price at IB midpoint → price_vs_ib = 0"""
        ib_high, ib_low = 20010.0, 19990.0
        price = (ib_high + ib_low) / 2.0
        state = {
            "price": price,
            "session_levels": _make_session_levels(ib_high=ib_high, ib_low=ib_low),
        }
        result = extract_narrative_features(state)
        assert result[12] == pytest.approx(0.0, abs=1e-5), f"price_vs_ib at mid: {result[12]}"

    def test_session_phase_at_open(self):
        """minutes_since_rth=0 → session_phase = -1"""
        state = {"session_context": {"minutes_since_rth": 0.0}}
        result = extract_narrative_features(state)
        assert result[7] == pytest.approx(-1.0, abs=1e-5), f"session_phase at open: {result[7]}"

    def test_session_phase_at_mid(self):
        """minutes_since_rth=195 → session_phase = 0"""
        state = {"session_context": {"minutes_since_rth": 195.0}}
        result = extract_narrative_features(state)
        assert result[7] == pytest.approx(0.0, abs=1e-5), f"session_phase at mid: {result[7]}"

    def test_initiative_direction_all_initiative(self):
        """initiative_ratio=1, responsive_ratio=0 → +1"""
        state = {"amt_dynamics": {"initiative_ratio": 1.0, "responsive_ratio": 0.0}}
        result = extract_narrative_features(state)
        assert result[8] == pytest.approx(1.0, abs=1e-5)

    def test_initiative_direction_all_responsive(self):
        """initiative_ratio=0, responsive_ratio=1 → -1"""
        state = {"amt_dynamics": {"initiative_ratio": 0.0, "responsive_ratio": 1.0}}
        result = extract_narrative_features(state)
        assert result[8] == pytest.approx(-1.0, abs=1e-5)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_zero_ib_range_does_not_crash(self):
        """IB high == IB low → no division by zero"""
        state = {
            "price": 20000.0,
            "session_levels": _make_session_levels(ib_high=20000.0, ib_low=20000.0),
        }
        result = extract_narrative_features(state)
        assert np.all(np.isfinite(result))

    def test_zero_va_width_does_not_crash(self):
        """VAH == VAL → no division by zero"""
        state = {
            "price": 20000.0,
            "volume_profile": _make_vp(poc=20000.0, vah=20000.0, val=20000.0),
        }
        result = extract_narrative_features(state)
        assert np.all(np.isfinite(result))

    def test_single_print_zones_list(self):
        """Non-empty single_print_zones list is handled without crashing"""
        state = {
            "price": 20000.0,
            "single_print_zones": [(19990.0, 19995.0), (20005.0, 20010.0)],
        }
        result = extract_narrative_features(state)
        assert np.all(np.isfinite(result))
        assert np.all(result >= -1.0) and np.all(result <= 1.0)

    def test_invalid_single_print_zone_skipped(self):
        """Malformed zone entries are silently skipped"""
        state = {
            "price": 20000.0,
            "single_print_zones": [None, "bad", (20001.0, 20003.0)],
        }
        result = extract_narrative_features(state)
        assert np.all(np.isfinite(result))

    def test_reproducible(self):
        """Calling twice with the same state gives identical results"""
        state = _make_full_state()
        r1 = extract_narrative_features(state)
        r2 = extract_narrative_features(state)
        np.testing.assert_array_equal(r1, r2)
