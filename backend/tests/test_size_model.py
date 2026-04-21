"""Tests for Phase 3c SizeModel — trained position-sizing head."""

from __future__ import annotations

import numpy as np
import pytest

from src.rl.agent.size_model import (
    NUM_SIZE_TIERS,
    SIZE_TIERS,
    SizeModel,
    realized_R_to_tier,
    realized_R_to_tier_batch,
)


class TestTierLabelMapping:
    def test_size_tiers_match_heuristic(self):
        assert SIZE_TIERS == (0.0, 0.3, 0.6, 1.0, 1.5)
        assert NUM_SIZE_TIERS == 5

    @pytest.mark.parametrize(
        "r,expected",
        [
            (-1.0, 0),
            (-0.5, 0),
            (-0.31, 0),
            (-0.3, 1),
            (-0.1, 1),
            (0.0, 2),
            (0.3, 2),
            (0.49, 2),
            (0.5, 3),
            (0.99, 3),
            (1.0, 4),
            (2.5, 4),
        ],
    )
    def test_realized_R_to_tier(self, r, expected):
        assert realized_R_to_tier(r) == expected

    def test_batch_matches_scalar(self):
        rs = np.array([-1.0, -0.3, 0.0, 0.5, 1.0, 2.0], dtype=np.float32)
        batch = realized_R_to_tier_batch(rs)
        scalar = np.array([realized_R_to_tier(float(r)) for r in rs], dtype=np.int32)
        np.testing.assert_array_equal(batch, scalar)


class TestSizeModelTraining:
    def _synthetic_data(self, n: int = 2000, seed: int = 0):
        rng = np.random.default_rng(seed)
        X = rng.normal(size=(n, 50)).astype(np.float32)
        realized_R = (X[:, 0] * 0.5 + rng.normal(scale=0.3, size=n)).astype(np.float32)
        return X, realized_R

    def test_train_returns_metrics(self):
        X, R = self._synthetic_data()
        m = SizeModel()
        metrics = m.train(X, R, n_estimators=40, max_depth=3)
        assert "val_accuracy" in metrics
        assert "val_mean_size_multiplier" in metrics
        assert 0 <= metrics["val_accuracy"] <= 100

    def test_predict_size_returns_valid_tier(self):
        X, R = self._synthetic_data()
        m = SizeModel()
        m.train(X, R, n_estimators=40, max_depth=3)
        for obs in X[:10]:
            size = m.predict_size(obs)
            assert size in SIZE_TIERS

    def test_predict_size_batch_same_as_single(self):
        X, R = self._synthetic_data()
        m = SizeModel()
        m.train(X, R, n_estimators=40, max_depth=3)
        batch_sizes = m.predict_size_batch(X[:20])
        single_sizes = np.array([m.predict_size(x) for x in X[:20]], dtype=np.float32)
        np.testing.assert_array_equal(batch_sizes, single_sizes)

    def test_save_load_roundtrip(self, tmp_path):
        X, R = self._synthetic_data()
        m = SizeModel()
        m.train(X, R, n_estimators=40, max_depth=3)
        path = tmp_path / "size_model_test.joblib"
        m.save(path)
        m2 = SizeModel.load(path)
        sample = X[0]
        assert m.predict_size(sample) == m2.predict_size(sample)

    def test_monotone_signal_beats_random(self):
        """Model should beat 20% random baseline on a clear monotone signal."""
        X, R = self._synthetic_data(n=5000)
        m = SizeModel()
        metrics = m.train(X, R, n_estimators=100, max_depth=4)
        assert metrics["val_accuracy"] > 30, f"accuracy {metrics['val_accuracy']} ≤ 30 on clear signal"
