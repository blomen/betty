"""Tests for LevelClassifierModel (Task 7).

TDD: tests are written before implementation.
"""
import numpy as np


def test_encode_features_basic():
    from src.ml.models.level_classifier import _encode_features
    features = {
        "level_type": "vah", "level_category": "session",
        "approach_direction": "from_below",
        "delta": 500, "cvd": 2000, "delta_aligned": True,
        "vsa_absorption": False, "regime": "risk_on",
        "market_type": "trending_up",
    }
    vec = _encode_features(features)
    assert isinstance(vec, np.ndarray)
    assert vec.dtype == np.float32


def test_encode_features_with_missing():
    from src.ml.models.level_classifier import _encode_features
    features = {"level_type": "poc", "approach_direction": "from_above"}
    vec = _encode_features(features)
    assert isinstance(vec, np.ndarray)
    assert np.any(np.isnan(vec))  # Missing values -> NaN


def test_encode_categoricals():
    from src.ml.models.level_classifier import _encode_features
    features = {"level_type": "vah", "approach_direction": "from_below", "regime": "risk_on"}
    vec = _encode_features(features)
    # Should NOT be NaN for known categorical values
    # (exact position depends on FEATURE_NAMES order)
    from src.ml.features.level_touch_features import FEATURE_NAMES, CATEGORICAL_MAPS
    idx_level_type = FEATURE_NAMES.index("level_type")
    idx_approach = FEATURE_NAMES.index("approach_direction")
    idx_regime = FEATURE_NAMES.index("regime")
    assert not np.isnan(vec[idx_level_type]), "level_type should not be NaN for known value"
    assert not np.isnan(vec[idx_approach]), "approach_direction should not be NaN for known value"
    assert not np.isnan(vec[idx_regime]), "regime should not be NaN for known value"


def test_encode_unknown_categorical_is_nan():
    from src.ml.models.level_classifier import _encode_features
    features = {"level_type": "unknown_level_xyz"}
    vec = _encode_features(features)
    from src.ml.features.level_touch_features import FEATURE_NAMES
    idx = FEATURE_NAMES.index("level_type")
    assert np.isnan(vec[idx]), "Unknown categorical should encode to NaN"


def test_encode_boolean_features():
    from src.ml.models.level_classifier import _encode_features
    from src.ml.features.level_touch_features import FEATURE_NAMES, BOOLEAN_FEATURES
    bool_feat = next(iter(BOOLEAN_FEATURES))
    features = {bool_feat: True}
    vec = _encode_features(features)
    idx = FEATURE_NAMES.index(bool_feat)
    assert vec[idx] == 1.0

    features2 = {bool_feat: False}
    vec2 = _encode_features(features2)
    assert vec2[idx] == 0.0


def test_encode_feature_length():
    from src.ml.models.level_classifier import _encode_features
    from src.ml.features.level_touch_features import FEATURE_NAMES
    vec = _encode_features({})
    assert len(vec) == len(FEATURE_NAMES)


def test_train_with_synthetic_data():
    from src.ml.models.level_classifier import LevelClassifierModel
    model = LevelClassifierModel()
    rows = []
    for i in range(500):
        outcome_idx = i % 5
        features = {
            "level_type": ["poc", "vah", "val", "vwap", "pdh"][i % 5],
            "level_category": "session",
            "approach_direction": "from_below" if i % 2 == 0 else "from_above",
            "delta": float(100 + i * 10 * (-1 if outcome_idx < 2 else 1)),
            "cvd": float(i * 50 * (-1 if outcome_idx < 2 else 1)),
            "delta_aligned": outcome_idx >= 3,
            "vsa_absorption": outcome_idx < 2,
            "session_elapsed_pct": float(i % 100),
            "vix_level": 15.0 + (i % 10),
        }
        rows.append({
            "features": features,
            "outcome": ["strong_reversal", "weak_reversal", "chop",
                       "weak_continuation", "strong_continuation"][outcome_idx],
        })
    result = model.train(rows)
    assert result is not None
    assert "file_path" in result
    assert result["training_data_count"] > 0
    assert result["validation_score"] >= 0.0


def test_train_saves_classes_in_joblib():
    """The saved joblib artifact must include 'classes' and 'use_fallback'."""
    from src.ml.models.level_classifier import LevelClassifierModel
    model = LevelClassifierModel()
    rows = []
    for i in range(500):
        outcome_idx = i % 5
        features = {
            "level_type": ["poc", "vah", "val", "vwap", "pdh"][i % 5],
            "approach_direction": "from_below" if i % 2 == 0 else "from_above",
            "delta": float(100 + i),
            "cvd": float(i * 50),
            "delta_aligned": outcome_idx >= 3,
            "vsa_absorption": outcome_idx < 2,
        }
        rows.append({
            "features": features,
            "outcome": ["strong_reversal", "weak_reversal", "chop",
                       "weak_continuation", "strong_continuation"][outcome_idx],
        })
    result = model.train(rows)
    assert result is not None
    import joblib
    artifact = joblib.load(result["file_path"])
    assert "classes" in artifact
    assert "use_fallback" in artifact
    assert isinstance(artifact["use_fallback"], bool)
    assert isinstance(artifact["classes"], list)
    assert len(artifact["classes"]) in (3, 5)


def test_train_insufficient_data():
    from src.ml.models.level_classifier import LevelClassifierModel
    model = LevelClassifierModel()
    rows = [{"features": {"delta": 1}, "outcome": "chop"} for _ in range(10)]
    result = model.train(rows)
    assert result is None


def test_train_collapses_to_3_classes_when_sparse():
    """When some classes have fewer than MIN_SAMPLES_PER_CLASS rows, fall back to 3 classes."""
    from src.ml.models.level_classifier import LevelClassifierModel, FALLBACK_CLASSES
    model = LevelClassifierModel()
    # 300+ rows but only chop and strong_reversal represented (imbalanced)
    rows = []
    for i in range(350):
        rows.append({
            "features": {"delta": float(i), "approach_direction": "from_below"},
            "outcome": "chop" if i % 2 == 0 else "strong_reversal",
        })
    result = model.train(rows)
    # Should still train (uses 3-class fallback) and return a result
    assert result is not None
    import joblib
    artifact = joblib.load(result["file_path"])
    assert artifact["use_fallback"] is True
    assert artifact["classes"] == FALLBACK_CLASSES


def test_predict_returns_none():
    """predict() on LevelClassifierModel always returns None (uses Predictor singleton)."""
    from src.ml.models.level_classifier import LevelClassifierModel
    model = LevelClassifierModel()
    result = model.predict({"delta": 100, "level_type": "poc"})
    assert result is None


def test_train_accepts_json_string_features():
    """train() must handle rows where features is a JSON string (matches DB storage pattern)."""
    import json
    from src.ml.models.level_classifier import LevelClassifierModel
    model = LevelClassifierModel()
    rows = []
    for i in range(500):
        outcome_idx = i % 5
        features = {
            "level_type": ["poc", "vah", "val", "vwap", "pdh"][i % 5],
            "approach_direction": "from_below" if i % 2 == 0 else "from_above",
            "delta": float(100 + i),
            "cvd": float(i * 50),
            "delta_aligned": outcome_idx >= 3,
        }
        rows.append({
            "features": json.dumps(features),  # JSON string, not dict
            "outcome": ["strong_reversal", "weak_reversal", "chop",
                       "weak_continuation", "strong_continuation"][outcome_idx],
        })
    result = model.train(rows)
    assert result is not None
