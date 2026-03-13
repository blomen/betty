"""Tests for ML model serving infrastructure."""
import pytest
import numpy as np
from unittest.mock import MagicMock, patch
from src.ml.serving.predictor import Predictor


def test_predictor_init_no_models():
    predictor = Predictor()
    assert predictor.models == {}


def test_predictor_predict_no_model():
    predictor = Predictor()
    result = predictor.predict("edge_quality", {"edge_pct": 5.0})
    assert result is None


def test_predictor_predict_with_model():
    predictor = Predictor()
    mock_model = MagicMock()
    mock_model.predict_proba = MagicMock(return_value=np.array([[0.3, 0.7]]))
    predictor.models["edge_quality"] = {
        "model": mock_model,
        "feature_names": ["edge_pct", "prob_sum"],
        "task": "classification",
    }
    result = predictor.predict("edge_quality", {"edge_pct": 5.0, "prob_sum": 0.98})
    assert result is not None
    assert abs(result - 0.7) < 0.01


def test_predictor_predict_regression():
    predictor = Predictor()
    mock_model = MagicMock()
    mock_model.predict = MagicMock(return_value=np.array([0.35]))
    predictor.models["adaptive_kelly"] = {
        "model": mock_model,
        "feature_names": ["edge_pct"],
        "task": "regression",
    }
    result = predictor.predict("adaptive_kelly", {"edge_pct": 5.0})
    assert abs(result - 0.35) < 0.01


def test_predictor_predict_multiclass():
    predictor = Predictor()
    mock_model = MagicMock()
    mock_model.predict_proba = MagicMock(return_value=np.array([[0.1, 0.7, 0.2]]))
    predictor.models["devig_selector"] = {
        "model": mock_model,
        "feature_names": ["sport", "market_type"],
        "task": "multiclass",
    }
    result = predictor.predict("devig_selector", {"sport": 0, "market_type": 1})
    assert isinstance(result, dict)
    assert result["class"] == 1
    assert len(result["probabilities"]) == 3


def test_predictor_load_model():
    predictor = Predictor()
    with patch("joblib.load") as mock_load:
        mock_load.return_value = {
            "model": MagicMock(),
            "feature_names": ["f1"],
            "task": "classification",
        }
        predictor.load_model("edge_quality", "/fake/path.joblib")
        assert "edge_quality" in predictor.models


def test_predictor_is_loaded():
    predictor = Predictor()
    assert not predictor.is_loaded("edge_quality")
    predictor.models["edge_quality"] = {"model": MagicMock()}
    assert predictor.is_loaded("edge_quality")


from src.ml.training.train_all import TrainingOrchestrator


def test_training_orchestrator_init():
    orch = TrainingOrchestrator()
    assert orch.model_configs is not None
    assert "edge_quality" in orch.model_configs


def test_training_orchestrator_check_thresholds(db_session):
    orch = TrainingOrchestrator()
    ready = orch.check_thresholds(db_session)
    assert isinstance(ready, dict)
    assert all(v is False for v in ready.values())
