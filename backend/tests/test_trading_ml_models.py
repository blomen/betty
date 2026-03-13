"""Tests for M5-M7, M9 trading ML models."""
import json
import numpy as np
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone


# --- M5: Setup Scorer ---

def test_setup_scorer_encode_features():
    from src.ml.models.setup_scorer import _encode_features, FEATURE_NAMES_PHASE1
    features = {
        "base_score": 75, "delta_pct": 0.089, "cvd_slope_5bar": 45.2,
        "volume_ratio_vs_20bar": 1.45, "volume_ratio_vs_session": 1.12,
        "distance_to_level_ticks": 3, "distance_to_poc_ticks": 15,
        "price_position_in_va": 0.72, "ib_range_vs_avg": 0.85,
        "minutes_since_rth_open": 45, "aspr_percentile": 0.35,
        "passive_active_ratio": 1.8, "absorption_bar_count": 2,
        "vix_level": 18.5, "gex": -500000,
    }
    vec = _encode_features(features, FEATURE_NAMES_PHASE1)
    assert vec is not None
    assert len(vec) == len(FEATURE_NAMES_PHASE1)
    assert vec[0] == 75.0  # base_score


def test_setup_scorer_encode_missing_features():
    from src.ml.models.setup_scorer import _encode_features, FEATURE_NAMES_PHASE1
    features = {"base_score": 60}
    vec = _encode_features(features, FEATURE_NAMES_PHASE1)
    assert vec is not None
    assert len(vec) == len(FEATURE_NAMES_PHASE1)
    assert vec[0] == 60.0
    assert vec[1] == 0.0  # missing -> 0.0


def test_setup_scorer_categorical_encoding():
    from src.ml.models.setup_scorer import _encode_features, SETUP_TYPE_MAP, DIRECTION_MAP
    features = {"setup_type": "spring", "direction": "long"}
    vec = _encode_features(features, ["setup_type", "direction"])
    assert vec[0] == SETUP_TYPE_MAP["spring"]
    assert vec[1] == DIRECTION_MAP["long"]


# --- M6: Temporal Pattern ---

def test_temporal_pattern_encode_sequence():
    from src.ml.models.temporal_pattern import _encode_candle_sequence, CANDLE_FEATURE_NAMES
    candles = [_mock_candle(i) for i in range(20)]
    seq = _encode_candle_sequence(candles)
    assert seq is not None
    assert seq.shape == (20, len(CANDLE_FEATURE_NAMES))


def test_temporal_pattern_get_label():
    from src.ml.models.temporal_pattern import _get_label
    assert _get_label(1.5, 1) == 0  # reversal_long (strong positive)
    assert _get_label(-1.5, 0) == 1  # reversal_short (strong negative)
    assert _get_label(0.3, 1) == 2  # continuation_long (mild positive)
    assert _get_label(-0.3, 0) == 3  # continuation_short (mild negative)
    assert _get_label(0.0, 0) == 4  # chop
    assert _get_label(None, None) is None


def test_temporal_pattern_insufficient_data():
    from src.ml.models.temporal_pattern import TemporalPatternModel
    data = [_mock_trading_feature(i, with_candles=True) for i in range(100)]
    result = TemporalPatternModel().train(data)
    assert result is None  # Need 500


# --- M7: Gate Classifier ---

def test_gate_features_extraction():
    from src.ml.features.gate_features import extract_gate_features
    features = extract_gate_features(
        rf_after_ib=3.0, ib_range=120.0, ib_range_vs_avg=0.85,
        opening_type="OD", first_hour_delta_total=5000.0,
        vix_level=18.5, gex=-500000.0,
    )
    assert features["rf_after_ib"] == 3.0
    assert features["opening_type_encoded"] == 0  # OD=0
    assert features["vix_level"] == 18.5


def test_gate_features_unknown_opening_type():
    from src.ml.features.gate_features import extract_gate_features
    features = extract_gate_features(opening_type="UNKNOWN")
    assert features["opening_type_encoded"] == 4  # default for unknown


def test_gate_classifier_train():
    from src.ml.models.gate_classifier import GateClassifierModel
    data = [_mock_session_feature(i) for i in range(120)]
    result = GateClassifierModel().train(data)
    assert result is not None
    assert result["training_data_count"] == 120


def test_gate_classifier_insufficient_data():
    from src.ml.models.gate_classifier import GateClassifierModel
    data = [_mock_session_feature(i) for i in range(50)]
    result = GateClassifierModel().train(data)
    assert result is None


# --- M9: Macro Engine ---

def test_macro_features_extraction():
    from src.ml.features.macro_features import extract_macro_features
    features = extract_macro_features(
        vix_level=18.5, vix_change_1d=-2.3,
        dxy_level=104.2, us10y_level=4.52,
        yield_curve_spread=0.15,
    )
    assert features["vix_level"] == 18.5
    assert features["yield_curve_spread"] == 0.15


def test_news_impact_features():
    from src.ml.features.macro_features import extract_news_impact_features
    features = extract_news_impact_features(
        event_name="CPI", importance=3, surprise=0.3,
        vix_at_event=22.0, immediate_impact_pct=-0.5,
    )
    assert features["event_type_encoded"] == 2  # CPI=2
    assert features["surprise"] == 0.3


def test_news_impact_features_unknown_event():
    from src.ml.features.macro_features import extract_news_impact_features
    features = extract_news_impact_features(event_name="Unknown Event")
    assert features["event_type_encoded"] == -1


def test_macro_engine_insufficient_data():
    from src.ml.models.macro_engine import MacroEngineModel
    data = [_mock_news_feature(i) for i in range(20)]
    result = MacroEngineModel().train(data)
    assert result is None


# --- Economic Calendar ---

def test_get_upcoming_events():
    from src.data.economic_calendar import get_upcoming_events
    mock_session = MagicMock()
    mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    result = get_upcoming_events(mock_session, minutes_ahead=120)
    assert result == []


def test_get_recent_events():
    from src.data.economic_calendar import get_recent_events
    mock_session = MagicMock()
    mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    result = get_recent_events(mock_session, minutes_ago=60)
    assert result == []


# --- Training Orchestrator ---

def test_training_orchestrator_has_trading_configs():
    from src.ml.training.train_all import MODEL_CONFIGS
    assert "setup_scorer" in MODEL_CONFIGS
    assert "temporal_pattern" in MODEL_CONFIGS
    assert "gate_classifier" in MODEL_CONFIGS
    assert "macro_engine" in MODEL_CONFIGS
    assert MODEL_CONFIGS["setup_scorer"]["domain"] == "trading"
    assert MODEL_CONFIGS["gate_classifier"]["task"] == "multiclass"


# --- Migration ---

def test_market_sessions_migration():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    from src.ml.migrations import _create_market_sessions, _table_exists
    _create_market_sessions(conn)
    assert _table_exists(conn, "market_sessions")
    # Calling again should be idempotent
    _create_market_sessions(conn)
    conn.close()


# --- Feature Store ---

def test_resolve_trading_outcomes_empty():
    """Test that resolve_trading_outcomes handles empty set gracefully."""
    from src.ml.feature_store import resolve_trading_outcomes
    mock_session = MagicMock()
    mock_session.query.return_value.filter.return_value.all.return_value = []
    result = resolve_trading_outcomes(mock_session)
    assert result == 0


# --- Helpers ---

def _mock_trading_feature(idx, with_candles=False):
    """Create mock MlFeature for trading signal."""
    features = {
        "base_score": 65 + (idx % 20), "delta_pct": 0.05 + idx * 0.001,
        "cvd_slope_5bar": 30 + idx * 0.5, "volume_ratio_vs_20bar": 1.0 + idx * 0.01,
        "volume_ratio_vs_session": 1.0, "distance_to_level_ticks": 3 + idx % 10,
        "distance_to_poc_ticks": 10 + idx % 20, "price_position_in_va": 0.5,
        "ib_range_vs_avg": 0.8 + idx * 0.002, "minutes_since_rth_open": 30 + idx,
        "aspr_percentile": 0.4, "passive_active_ratio": 1.5,
        "absorption_bar_count": idx % 4, "vix_level": 18.0, "gex": -500000,
    }
    if with_candles:
        features["candle_sequence"] = [_mock_candle(j) for j in range(20)]
    mock = MagicMock()
    mock.features = features
    mock.outcome = (idx % 5 - 2) * 0.5  # -1.0 to 1.0
    mock.outcome_binary = 1 if mock.outcome > 0 else 0
    return mock


def _mock_candle(idx):
    """Create mock candle dict."""
    return {
        "delta": 100 + idx * 10, "delta_pct": 0.05 + idx * 0.01,
        "cvd": idx * 100, "volume": 5000 + idx * 100,
        "volume_ratio": 1.0 + idx * 0.05, "spread_ticks": 20 + idx,
        "body_ratio": 0.5, "close_position": 0.6,
        "tick_count": 1000 + idx * 50, "passive_active_ratio": 1.5,
        "vwap_distance_ticks": idx - 10, "poc_distance_ticks": idx - 5,
        "imbalance_ratio_max": 2.0, "stacked_imbalance_count": 1,
        "big_trades_count": 5, "big_trades_net_delta": 50,
    }


def _mock_session_feature(idx):
    """Create mock MlFeature for market session (M7)."""
    features = {
        "rf_after_ib": 2 + idx % 5, "ib_range": 100 + idx,
        "ib_range_vs_avg": 0.8 + idx * 0.005, "opening_type_encoded": idx % 4,
        "first_hour_delta_total": 3000 + idx * 100,
        "first_hour_volume_vs_avg": 1.0 + idx * 0.01,
        "overnight_range_pct": 0.5 + idx * 0.01,
        "gap_filled_pct": 0.3 + idx * 0.005,
        "yesterday_market_type_encoded": idx % 3,
        "poor_high_or_low_in_ib": idx % 2,
        "first_hour_big_trades_count": 10 + idx % 20,
        "session_volume_first_hour": 500000 + idx * 10000,
        "vix_level": 18.0, "gex": -500000,
        "value_migration_encoded": idx % 3,
        "ib_tpo_count": 3 + idx % 5,
        "day_type_label": idx % 5,
    }
    mock = MagicMock()
    mock.features = features
    mock.outcome = idx % 5  # day type class
    mock.outcome_binary = None
    return mock


def _mock_news_feature(idx):
    """Create mock MlFeature for news event (M9)."""
    features = {
        "event_type_encoded": idx % 10, "importance": (idx % 3) + 1,
        "surprise": (idx % 10 - 5) * 0.1, "vix_at_event": 18 + idx % 10,
        "delta_1m_after": (idx % 20 - 10) * 100, "volume_1m_after": 5000 + idx * 100,
    }
    mock = MagicMock()
    mock.features = features
    mock.outcome = (idx % 10 - 5) * 0.1  # NQ impact %
    mock.outcome_binary = 1 if mock.outcome > 0 else 0
    return mock
