"""Tests for level touch feature extractor."""

from src.ml.features.level_touch_features import (
    APPROACH_MAP,
    BOOLEAN_FEATURES,
    CATEGORICAL_MAPS,
    CVD_TREND_MAP,
    DEV_POC_MAP,
    FEATURE_NAMES,
    LEVEL_CATEGORY_MAP,
    LEVEL_TYPE_MAP,
    MACRO_BIAS_MAP,
    MARKET_TYPE_MAP,
    OPENING_TYPE_MAP,
    REGIME_MAP,
    STACKED_DIR_MAP,
    VALUE_MIGRATION_MAP,
    extract_level_touch_features,
)


def test_extract_all_features_present():
    features = extract_level_touch_features(
        level_type="vah", level_category="session",
        approach_direction="from_below",
    )
    assert isinstance(features, dict)
    assert "level_type" in features
    assert "delta" in features
    assert "cvd" in features
    assert "vsa_absorption" in features
    assert "delta_slope_5m" in features
    assert "market_type" in features
    assert "vix_level" in features
    assert "last_3_candles_direction" in features
    assert len(features) >= 58


def test_extract_with_orderflow_values():
    features = extract_level_touch_features(
        level_type="poc", approach_direction="from_above",
        delta=-500, delta_aligned=True, cvd=-2000, cvd_trend="falling",
        vsa_absorption=True,
    )
    assert features["delta"] == -500
    assert features["delta_aligned"] is True
    assert features["cvd"] == -2000


def test_extract_with_none_defaults():
    features = extract_level_touch_features(
        level_type="pdh", level_category="prior",
        approach_direction="from_below",
    )
    assert features["delta"] is None
    assert features["vix_level"] is None


def test_feature_names_list_matches_dict_keys():
    features = extract_level_touch_features(level_type="poc")
    for name in FEATURE_NAMES:
        assert name in features, f"FEATURE_NAMES contains '{name}' not in output dict"
    for key in features:
        assert key in FEATURE_NAMES, f"Output dict key '{key}' missing from FEATURE_NAMES"


def test_feature_names_length():
    assert len(FEATURE_NAMES) >= 58


def test_level_type_map_values():
    assert LEVEL_TYPE_MAP["poc"] == 0
    assert LEVEL_TYPE_MAP["vah"] == 1
    assert LEVEL_TYPE_MAP["val"] == 2
    assert LEVEL_TYPE_MAP["vwap"] == 3
    assert LEVEL_TYPE_MAP["pdh"] == 7
    assert LEVEL_TYPE_MAP["pdl"] == 8
    assert LEVEL_TYPE_MAP["naked_poc"] == 13


def test_level_category_map_values():
    assert LEVEL_CATEGORY_MAP["band"] == 0
    assert LEVEL_CATEGORY_MAP["prior"] == 1
    assert LEVEL_CATEGORY_MAP["session"] == 4


def test_approach_map_values():
    assert APPROACH_MAP["from_below"] == 0
    assert APPROACH_MAP["from_above"] == 1


def test_cvd_trend_map_values():
    assert CVD_TREND_MAP["rising"] == 0
    assert CVD_TREND_MAP["falling"] == 1
    assert CVD_TREND_MAP["flat"] == 2


def test_stacked_dir_map_values():
    assert STACKED_DIR_MAP["buy"] == 0
    assert STACKED_DIR_MAP["sell"] == 1
    assert STACKED_DIR_MAP["neutral"] == 2


def test_market_type_map_values():
    assert MARKET_TYPE_MAP["balanced"] == 0
    assert MARKET_TYPE_MAP["trending_up"] == 1
    assert MARKET_TYPE_MAP["trending_down"] == 2


def test_opening_type_map_values():
    assert OPENING_TYPE_MAP["OD"] == 0
    assert OPENING_TYPE_MAP["OTD"] == 1
    assert OPENING_TYPE_MAP["ORR"] == 2
    assert OPENING_TYPE_MAP["OA"] == 3


def test_value_migration_map_values():
    assert VALUE_MIGRATION_MAP["up"] == 0
    assert VALUE_MIGRATION_MAP["down"] == 1
    assert VALUE_MIGRATION_MAP["overlapping"] == 2


def test_dev_poc_map_values():
    assert DEV_POC_MAP["up"] == 0
    assert DEV_POC_MAP["down"] == 1
    assert DEV_POC_MAP["flat"] == 2


def test_regime_map_values():
    assert REGIME_MAP["risk_on"] == 0
    assert REGIME_MAP["risk_off"] == 1
    assert REGIME_MAP["mixed"] == 2


def test_macro_bias_map_values():
    assert MACRO_BIAS_MAP["bull"] == 0
    assert MACRO_BIAS_MAP["bear"] == 1
    assert MACRO_BIAS_MAP["neutral"] == 2


def test_categorical_maps_contains_all_string_features():
    expected_keys = {
        "level_type", "level_category", "approach_direction", "cvd_trend",
        "stacked_imbalance_direction", "market_type", "opening_type",
        "value_migration", "developing_poc_direction", "regime", "macro_bias",
    }
    for key in expected_keys:
        assert key in CATEGORICAL_MAPS, f"'{key}' missing from CATEGORICAL_MAPS"


def test_boolean_features_set():
    expected_bools = {
        "delta_aligned", "delta_divergence", "delta_unwind", "vsa_absorption",
        "tick_vol_accelerating", "trapped_traders", "stop_run_detected",
        "price_in_value_area", "last_candle_is_doji",
    }
    for feat in expected_bools:
        assert feat in BOOLEAN_FEATURES, f"'{feat}' missing from BOOLEAN_FEATURES"


def test_all_keys_returned_when_no_args():
    features = extract_level_touch_features()
    assert len(features) >= 58
    # All values should be None when no args given
    for key, val in features.items():
        assert val is None, f"Expected None for '{key}', got {val!r}"


def test_level_metadata_group():
    features = extract_level_touch_features(
        level_type="order_block",
        level_category="structure",
        level_strength=3,
        level_confluence=2,
        approach_direction="from_above",
        distance_from_poc=15.5,
        distance_from_vwap=8.0,
    )
    assert features["level_type"] == "order_block"
    assert features["level_category"] == "structure"
    assert features["level_strength"] == 3
    assert features["level_confluence"] == 2
    assert features["approach_direction"] == "from_above"
    assert features["distance_from_poc"] == 15.5
    assert features["distance_from_vwap"] == 8.0


def test_orderflow_group():
    features = extract_level_touch_features(
        delta=1200,
        delta_aligned=False,
        delta_divergence=True,
        delta_unwind=False,
        cvd=3500,
        cvd_trend="rising",
        vsa_absorption=False,
        tick_vol_accelerating=True,
        trapped_traders=False,
        passive_active_ratio=1.8,
        big_trades_count=5,
        big_trades_net_delta=800,
        stop_run_detected=True,
        imbalance_ratio_max=3.2,
        stacked_imbalance_count=4,
        stacked_imbalance_direction="buy",
        last_candle_delta=300,
        last_candle_body_ratio=0.75,
    )
    assert features["delta"] == 1200
    assert features["delta_aligned"] is False
    assert features["delta_divergence"] is True
    assert features["cvd"] == 3500
    assert features["cvd_trend"] == "rising"
    assert features["vsa_absorption"] is False
    assert features["passive_active_ratio"] == 1.8
    assert features["big_trades_count"] == 5
    assert features["stop_run_detected"] is True
    assert features["imbalance_ratio_max"] == 3.2
    assert features["stacked_imbalance_direction"] == "buy"
    assert features["last_candle_body_ratio"] == 0.75


def test_temporal_derivatives_group():
    features = extract_level_touch_features(
        delta_slope_5m=0.5,
        delta_slope_10m=0.3,
        cvd_acceleration=0.1,
        volume_roc_5m=1.2,
        tick_rate_roc=0.8,
        spread_compression=0.4,
        time_to_level_seconds=120,
        price_velocity=2.5,
        absorption_building=True,
        imbalance_trend=0.6,
    )
    assert features["delta_slope_5m"] == 0.5
    assert features["delta_slope_10m"] == 0.3
    assert features["cvd_acceleration"] == 0.1
    assert features["volume_roc_5m"] == 1.2
    assert features["time_to_level_seconds"] == 120
    assert features["price_velocity"] == 2.5
    assert features["absorption_building"] is True
    assert features["imbalance_trend"] == 0.6


def test_session_context_group():
    features = extract_level_touch_features(
        market_type="balanced",
        opening_type="OTD",
        ib_range=40,
        ib_range_vs_aspr=0.9,
        aspr_percentile=65.0,
        rotation_factor=1.4,
        value_migration="up",
        price_vs_vah=-5.0,
        price_vs_val=35.0,
        price_vs_poc=10.0,
        price_in_value_area=True,
        session_elapsed_pct=0.45,
        minutes_since_open=55,
        developing_poc_direction="up",
        prior_touch_count=2,
    )
    assert features["market_type"] == "balanced"
    assert features["opening_type"] == "OTD"
    assert features["ib_range"] == 40
    assert features["price_in_value_area"] is True
    assert features["session_elapsed_pct"] == 0.45
    assert features["developing_poc_direction"] == "up"
    assert features["prior_touch_count"] == 2


def test_macro_context_group():
    features = extract_level_touch_features(
        vix_level=18.5,
        vix_change=-0.5,
        regime="risk_on",
        regime_score=0.75,
        macro_bias="bull",
    )
    assert features["vix_level"] == 18.5
    assert features["vix_change"] == -0.5
    assert features["regime"] == "risk_on"
    assert features["regime_score"] == 0.75
    assert features["macro_bias"] == "bull"


def test_candle_pattern_group():
    features = extract_level_touch_features(
        last_3_candles_direction=-1,
        last_candle_is_doji=False,
        consecutive_same_direction=3,
        highest_volume_candle_position=2,
        range_expansion=1.5,
    )
    assert features["last_3_candles_direction"] == -1
    assert features["last_candle_is_doji"] is False
    assert features["consecutive_same_direction"] == 3
    assert features["highest_volume_candle_position"] == 2
    assert features["range_expansion"] == 1.5


# ---------------------------------------------------------------------------
# Tests for compute.py — temporal derivatives and candle pattern computation
# ---------------------------------------------------------------------------

from src.ml.level_touch.compute import compute_temporal_derivatives, compute_candle_pattern_features


def test_compute_temporal_derivatives_basic():
    candles = []
    for i in range(10):
        candles.append({
            "delta": 100 + i * 20,
            "volume": 500 + i * 50,
            "tick_count": 80 + i * 5,
            "spread": 2.0 - i * 0.1,
            "body_ratio": 0.5 - i * 0.02,
            "stacked_imbalance_count": i // 3,
        })
    result = compute_temporal_derivatives(candles)
    assert result["delta_slope_5m"] is not None
    assert result["delta_slope_10m"] is not None
    assert result["cvd_acceleration"] is not None
    assert result["volume_roc_5m"] is not None
    assert result["spread_compression"] is not None
    assert result["absorption_building"] is not None
    assert result["delta_slope_5m"] > 0  # delta increasing
    assert result["spread_compression"] < 1.0  # spread decreasing


def test_compute_temporal_derivatives_insufficient_candles():
    candles = [{"delta": 100, "volume": 500, "tick_count": 80,
                "spread": 2.0, "body_ratio": 0.5, "stacked_imbalance_count": 0}]
    result = compute_temporal_derivatives(candles)
    assert result["delta_slope_5m"] is None
    assert result["cvd_acceleration"] is None


def test_compute_candle_pattern_features():
    candles = [
        {"open": 100, "close": 101, "volume": 500, "spread": 1.0, "body_ratio": 0.8},
        {"open": 101, "close": 102, "volume": 600, "spread": 1.5, "body_ratio": 0.7},
        {"open": 102, "close": 101.5, "volume": 700, "spread": 2.0, "body_ratio": 0.05},
    ]
    result = compute_candle_pattern_features(candles)
    assert result["last_3_candles_direction"] == 2  # 2 up candles
    assert result["last_candle_is_doji"] is True  # body_ratio < 0.1
    assert result["consecutive_same_direction"] == 1  # last is down, only 1
    assert result["highest_volume_candle_position"] is not None
    assert result["range_expansion"] is not None


def test_compute_candle_pattern_empty():
    result = compute_candle_pattern_features([])
    assert result["last_3_candles_direction"] is None
