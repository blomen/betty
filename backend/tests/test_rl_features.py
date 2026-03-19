"""Tests for RL observation vector feature extractors."""
import math
import numpy as np
import pytest

from src.rl.config import LevelType, TICK_SIZE
from src.rl.features.level_features import encode_level_type, encode_confluence
from src.rl.features.orderflow_features import extract_orderflow_features
from src.rl.features.tpo_features import extract_tpo_features
from src.rl.features.structure_features import extract_structure_features
from src.rl.features.macro_features import extract_macro_features
from src.rl.features.observation import build_observation, OBSERVATION_DIM
from src.market_data.levels import VWAPBands, VolumeProfile, VolumeProfileLevel, SessionLevels
from src.market_data.orderflow import CandleFlow, OrderflowSignals, PriceLevelFlow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candle(close: float = 100.0, delta: int = 50, volume: int = 200) -> CandleFlow:
    return CandleFlow(
        ts=__import__("datetime").datetime(2026, 1, 1, 9, 30),
        open=close - 1,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=volume,
        buy_volume=(volume + delta) // 2,
        sell_volume=(volume - delta) // 2,
        delta=delta,
        tick_count=10,
        spread=2.0,
    )


def _minimal_state(level_type: LevelType = LevelType.VWAP, price: float = 19000.0) -> dict:
    return {
        "level_type": level_type,
        "price": price,
        "candles": [],
        "vwap_bands": None,
        "volume_profile": None,
        "tpo_profile": None,
        "session_levels": None,
        "all_levels": [],
        "orderflow_signals": None,
        "macro": None,
        "session_context": None,
    }


# ---------------------------------------------------------------------------
# Level one-hot tests
# ---------------------------------------------------------------------------

class TestLevelOneHot:
    def test_correct_length(self):
        vec = encode_level_type(LevelType.VWAP)
        assert len(vec) == len(LevelType)

    def test_sums_to_one(self):
        for lt in LevelType:
            vec = encode_level_type(lt)
            assert sum(vec) == pytest.approx(1.0), f"Failed for {lt}"

    def test_only_floats(self):
        vec = encode_level_type(LevelType.POC_DAILY)
        assert all(isinstance(v, float) for v in vec)

    def test_different_types_give_different_vectors(self):
        vec_vwap = encode_level_type(LevelType.VWAP)
        vec_pdh  = encode_level_type(LevelType.PDH)
        assert vec_vwap != vec_pdh

    def test_hot_bit_position_consistent(self):
        members = list(LevelType)
        for idx, lt in enumerate(members):
            vec = encode_level_type(lt)
            assert vec[idx] == 1.0
            assert sum(vec[i] for i in range(len(vec)) if i != idx) == 0.0

    def test_26_members(self):
        assert len(LevelType) == 26


# ---------------------------------------------------------------------------
# Confluence tests
# ---------------------------------------------------------------------------

class TestConfluence:
    def test_no_nearby_levels(self):
        result = encode_confluence(
            touched_price=100.0,
            all_levels=[50.0, 150.0],
            tick_size=0.25,
            proximity_ticks=5,
        )
        assert result["levels_within_5_ticks"] == 0.0

    def test_counts_nearby_levels(self):
        nearby = [100.25, 100.5, 100.75]  # all within 5 ticks of 100.0
        result = encode_confluence(
            touched_price=100.0,
            all_levels=nearby + [200.0],  # 200.0 far away
            tick_size=0.25,
            proximity_ticks=5,
        )
        assert result["levels_within_5_ticks"] == 3.0

    def test_nearest_higher_lower(self):
        result = encode_confluence(
            touched_price=100.0,
            all_levels=[99.0, 101.0, 102.0],
            tick_size=0.25,
        )
        assert result["nearest_higher_level_dist"] == pytest.approx(4.0, abs=0.01)  # 1.0 / 0.25
        assert result["nearest_lower_level_dist"] == pytest.approx(4.0, abs=0.01)

    def test_all_keys_present(self):
        result = encode_confluence(100.0, [], tick_size=0.25)
        expected_keys = {
            "levels_within_5_ticks",
            "strongest_cluster_score",
            "nearest_higher_level_dist",
            "nearest_lower_level_dist",
            "touched_level_hierarchy_rank",
        }
        assert set(result.keys()) == expected_keys

    def test_no_levels_returns_defaults(self):
        result = encode_confluence(100.0, [], tick_size=0.25)
        assert result["levels_within_5_ticks"] == 0.0
        assert result["nearest_higher_level_dist"] == 50.0
        assert result["nearest_lower_level_dist"] == 50.0


# ---------------------------------------------------------------------------
# Orderflow features
# ---------------------------------------------------------------------------

class TestOrderflowFeatures:
    def test_empty_candles_returns_zeros(self):
        out = extract_orderflow_features([])
        assert out.shape == (15,)
        assert np.all(out == 0.0)

    def test_correct_shape(self):
        candles = [_make_candle(delta=10, volume=100) for _ in range(5)]
        out = extract_orderflow_features(candles)
        assert out.shape == (15,)

    def test_dtype_float32(self):
        candles = [_make_candle() for _ in range(3)]
        out = extract_orderflow_features(candles)
        assert out.dtype == np.float32

    def test_no_nans(self):
        candles = [_make_candle(delta=-20, volume=200) for _ in range(10)]
        out = extract_orderflow_features(candles)
        assert not np.any(np.isnan(out))

    def test_with_signals(self):
        candles = [_make_candle() for _ in range(5)]
        signals = OrderflowSignals(
            delta=100, delta_aligned=True, delta_divergence=False,
            delta_unwind=False, cvd=500, cvd_trend="rising",
            vsa_absorption=True, tick_vol_accelerating=False,
            trapped_traders=False, passive_active_ratio=1.5,
            big_trades_count=2, big_trades_net_delta=80,
            stop_run_detected=True,
        )
        out = extract_orderflow_features(candles, signals=signals)
        assert out.shape == (15,)
        assert out[13] == pytest.approx(1.0)  # vsa_absorption
        assert out[14] == pytest.approx(1.0)  # stop_run_detected


# ---------------------------------------------------------------------------
# TPO features
# ---------------------------------------------------------------------------

class TestTPOFeatures:
    def test_none_returns_zeros(self):
        out = extract_tpo_features(None, current_price=19000.0)
        assert out.shape == (13,)
        assert np.all(out == 0.0)

    def test_correct_shape(self):
        profile = {"poc": 19000.0, "vah": 19050.0, "val": 18950.0, "shape": "p"}
        out = extract_tpo_features(profile, current_price=19000.0)
        assert out.shape == (13,)

    def test_price_in_va(self):
        profile = {"poc": 100.0, "vah": 110.0, "val": 90.0, "shape": "balanced"}
        out = extract_tpo_features(profile, current_price=100.0)
        assert out[2] == pytest.approx(1.0)  # price_in_va

    def test_price_outside_va(self):
        profile = {"poc": 100.0, "vah": 105.0, "val": 95.0, "shape": "balanced"}
        out = extract_tpo_features(profile, current_price=120.0)
        assert out[2] == pytest.approx(0.0)

    def test_shape_one_hot_p(self):
        profile = {"poc": 100.0, "shape": "p"}
        out = extract_tpo_features(profile, current_price=100.0)
        # shape_p at index 8, shape_b=9, shape_d=10, shape_balanced=11
        assert out[8] == pytest.approx(1.0)
        assert out[9] == pytest.approx(0.0)
        assert out[10] == pytest.approx(0.0)
        assert out[11] == pytest.approx(0.0)

    def test_shape_one_hot_balanced(self):
        profile = {"poc": 100.0, "shape": "balanced"}
        out = extract_tpo_features(profile, current_price=100.0)
        assert out[11] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Structure features
# ---------------------------------------------------------------------------

class TestStructureFeatures:
    def test_all_none_returns_zeros_mostly(self):
        out = extract_structure_features(19000.0, None, None, None, None)
        assert out.shape == (23,)
        assert out.dtype == np.float32

    def test_price_vs_vwap(self):
        bands = VWAPBands(
            vwap=100.0, sd1_upper=102.0, sd1_lower=98.0,
            sd2_upper=104.0, sd2_lower=96.0,
            sd3_upper=106.0, sd3_lower=94.0,
        )
        # price at vwap + 1 sd → 1.0 SD above
        out = extract_structure_features(102.0, bands, None, None, None)
        assert out[0] == pytest.approx(1.0, abs=0.01)

    def test_price_in_va(self):
        vp = VolumeProfile(
            poc=100.0, vah=105.0, val=95.0,
            levels=[VolumeProfileLevel(price=100.0, volume=1000)],
        )
        out = extract_structure_features(100.0, None, vp, None, None)
        assert out[1] == pytest.approx(1.0)

    def test_session_context_session_type_one_hot(self):
        ctx = {"session_type": "globex", "ib_broken": "none", "minute_of_day": 0}
        out = extract_structure_features(100.0, None, None, None, ctx)
        assert out[17] == 0.0  # rth
        assert out[18] == 1.0  # globex
        assert out[19] == 0.0  # london

    def test_ib_broken_flags(self):
        ctx = {"ib_broken": "up", "minute_of_day": 0}
        out = extract_structure_features(100.0, None, None, None, ctx)
        assert out[20] == 1.0
        assert out[21] == 0.0
        assert out[22] == 0.0

    def test_correct_shape(self):
        out = extract_structure_features(100.0, None, None, None, {})
        assert out.shape == (23,)


# ---------------------------------------------------------------------------
# Macro features
# ---------------------------------------------------------------------------

class TestMacroFeatures:
    def test_none_returns_zeros(self):
        out = extract_macro_features(None)
        assert out.shape == (10,)
        assert np.all(out == 0.0)

    def test_vix_normalisation(self):
        out = extract_macro_features({"vix": 25.0})
        assert out[0] == pytest.approx(0.5, abs=0.01)

    def test_vix_clipped(self):
        out = extract_macro_features({"vix": 100.0})
        assert out[0] == pytest.approx(1.0)

    def test_dtype(self):
        out = extract_macro_features({"vix": 20.0, "vix_change": 1.0})
        assert out.dtype == np.float32

    def test_placeholders_are_zero(self):
        out = extract_macro_features({"vix": 20.0})
        assert out[4] == 0.0   # GEX placeholder
        assert out[8] == 0.0   # news placeholder
        assert out[9] == 0.0   # news severity


# ---------------------------------------------------------------------------
# Full observation vector
# ---------------------------------------------------------------------------

class TestBuildObservation:
    def test_returns_numpy_array(self):
        obs = build_observation(_minimal_state())
        assert isinstance(obs, np.ndarray)

    def test_correct_dtype(self):
        obs = build_observation(_minimal_state())
        assert obs.dtype == np.float32

    def test_correct_dim(self):
        obs = build_observation(_minimal_state())
        assert obs.shape == (OBSERVATION_DIM,)

    def test_observation_dim_is_107(self):
        # 26 + 15 + 23 + 13 + 15 + 5 + 10 = 107
        assert OBSERVATION_DIM == 107

    def test_no_nans(self):
        obs = build_observation(_minimal_state())
        assert not np.any(np.isnan(obs))

    def test_no_infs(self):
        obs = build_observation(_minimal_state())
        assert not np.any(np.isinf(obs))

    def test_values_bounded(self):
        obs = build_observation(_minimal_state())
        assert float(np.max(np.abs(obs))) <= 5.0, "Extreme outlier detected"

    def test_different_level_types_differ(self):
        obs_vwap = build_observation(_minimal_state(LevelType.VWAP))
        obs_pdh  = build_observation(_minimal_state(LevelType.PDH))
        assert not np.allclose(obs_vwap, obs_pdh)

    def test_full_state_with_real_objects(self):
        from datetime import datetime
        candles = [_make_candle(close=19000.0 + i, delta=10 * i, volume=100 + i * 5)
                   for i in range(10)]
        bands = VWAPBands(
            vwap=19000.0, sd1_upper=19020.0, sd1_lower=18980.0,
            sd2_upper=19040.0, sd2_lower=18960.0,
            sd3_upper=19060.0, sd3_lower=18940.0,
        )
        vp = VolumeProfile(
            poc=19000.0, vah=19050.0, val=18950.0,
            levels=[VolumeProfileLevel(price=19000.0, volume=5000)],
        )
        tpo = {"poc": 19000.0, "vah": 19040.0, "val": 18960.0,
               "shape": "balanced", "rotation_factor": 5.0}
        sl = SessionLevels(ib_high=19020.0, ib_low=18980.0, pdh=19100.0, pdl=18900.0)
        signals = OrderflowSignals(
            delta=200, delta_aligned=True, delta_divergence=False,
            delta_unwind=False, cvd=1000, cvd_trend="rising",
            vsa_absorption=False, tick_vol_accelerating=True,
            trapped_traders=False, passive_active_ratio=1.2,
        )
        macro = {"vix": 18.0, "vix_change": -0.5, "regime_score": 0.6,
                 "dxy_change": 0.2, "us10y_change": 0.05, "us2y_change": 0.03,
                 "us10y": 4.5, "us2y": 4.8}
        ctx = {
            "minutes_since_rth": 60,
            "session_volume_pct": 0.3,
            "daily_range_pct": 0.5,
            "minute_of_day": 630,
            "session_type": "rth",
            "ib_broken": "none",
        }
        state = {
            "level_type": LevelType.POC_SESSION,
            "price": 19005.0,
            "candles": candles,
            "vwap_bands": bands,
            "volume_profile": vp,
            "tpo_profile": tpo,
            "session_levels": sl,
            "all_levels": [18980.0, 19000.0, 19020.0, 19040.0, 19060.0],
            "orderflow_signals": signals,
            "macro": macro,
            "session_context": ctx,
        }
        obs = build_observation(state)
        assert obs.shape == (OBSERVATION_DIM,)
        assert obs.dtype == np.float32
        assert not np.any(np.isnan(obs))
        assert not np.any(np.isinf(obs))

    def test_observation_dim_constant_matches_build(self):
        obs = build_observation(_minimal_state())
        assert OBSERVATION_DIM == obs.shape[0]
