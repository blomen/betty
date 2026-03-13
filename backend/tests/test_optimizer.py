"""Tests for ML optimizer training infrastructure."""
import numpy as np
import pytest


def test_walk_forward_split():
    """Test walk-forward cross-validation with embargo."""
    from src.ml.optimizer.trainer import walk_forward_splits

    n_samples = 100
    splits = list(walk_forward_splits(n_samples, n_splits=5, embargo=5))
    assert len(splits) == 5

    for train_idx, test_idx in splits:
        assert max(train_idx) < min(test_idx) - 5  # embargo gap
        assert len(set(train_idx) & set(test_idx)) == 0


def test_train_model_basic():
    """Test basic model training with synthetic data."""
    from src.ml.optimizer.trainer import train_model

    np.random.seed(42)
    X = np.random.randn(100, 5)
    y = X[:, 0] * 2 + X[:, 1] + np.random.randn(100) * 0.1

    result = train_model(X, y, task="regression")
    assert result is not None
    assert "model" in result
    assert "validation_score" in result
    assert result["validation_score"] is not None


def test_train_model_too_few_samples():
    """Too few samples should return None."""
    from src.ml.optimizer.trainer import train_model

    X = np.random.randn(10, 5)
    y = np.random.randn(10)

    result = train_model(X, y, task="regression", min_samples=20)
    assert result is None


def test_schedule_optimizer_not_ready(db_session):
    """Should return None when insufficient data."""
    from src.ml.optimizer.schedule import ScheduleOptimizer
    opt = ScheduleOptimizer()
    result = opt.check_and_train(db_session)
    assert result is None


def test_schedule_optimizer_threshold():
    from src.ml.optimizer.schedule import ScheduleOptimizer
    opt = ScheduleOptimizer()
    assert opt.activation_threshold == 50


def test_provider_priority_not_ready(db_session):
    from src.ml.optimizer.provider_priority import ProviderPriorityScorer
    opt = ProviderPriorityScorer()
    assert opt.check_and_train(db_session) is None
    assert opt.activation_threshold == 100


def test_timeout_tuner_not_ready(db_session):
    from src.ml.optimizer.timeout import TimeoutTuner
    opt = TimeoutTuner()
    assert opt.check_and_train(db_session) is None
    assert opt.activation_threshold == 50


def test_coverage_optimizer_not_ready(db_session):
    from src.ml.optimizer.coverage import CoverageOptimizer
    opt = CoverageOptimizer()
    assert opt.check_and_train(db_session) is None
    assert opt.activation_threshold == 20
