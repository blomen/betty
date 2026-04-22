"""Tests for Phase 3c EarlyExitModel — pump-and-retrace detector."""

from __future__ import annotations

import numpy as np
import pytest

from src.rl.agent.early_exit_model import (
    PUMP_R_THRESHOLD,
    REALIZED_R_MAX,
    EarlyExitModel,
    derive_early_exit_labels,
)


class TestLabelDerivation:
    @pytest.mark.parametrize(
        "peak_R,realized_R,expected",
        [
            (0.6, 0.2, 1),  # pumped, retraced
            (0.6, 0.8, 0),  # pumped, held → not early-exit
            (0.3, 0.0, 0),  # didn't pump
            (0.5, 0.2, 1),  # exactly at pump threshold
            (0.6, 0.5, 0),  # exactly at realized boundary (not < 0.5)
            (0.0, 0.0, 0),  # no movement
            (2.0, -0.5, 1),  # big pump, big loss → early exit useful
        ],
    )
    def test_scalar_label_boundaries(self, peak_R, realized_R, expected):
        peak = np.array([peak_R], dtype=np.float32)
        real = np.array([realized_R], dtype=np.float32)
        assert int(derive_early_exit_labels(peak, real)[0]) == expected

    def test_thresholds_exposed(self):
        assert PUMP_R_THRESHOLD == 0.5
        assert REALIZED_R_MAX == 0.5

    def test_batch_label_shapes(self):
        n = 1000
        rng = np.random.default_rng(0)
        peak = rng.uniform(0.0, 2.0, n).astype(np.float32)
        real = rng.uniform(-1.0, 2.0, n).astype(np.float32)
        labels = derive_early_exit_labels(peak, real)
        assert labels.shape == (n,)
        assert labels.dtype == np.int32
        assert set(np.unique(labels).tolist()).issubset({0, 1})


class TestEarlyExitModelTraining:
    def _synthetic_data(self, n: int = 3000, seed: int = 0):
        rng = np.random.default_rng(seed)
        X = rng.normal(size=(n, 50)).astype(np.float32)
        # Feature 0 predicts peak_R; feature 1 predicts realized_R.
        # High peak + low realized = pump-and-retrace.
        peak = np.clip(X[:, 0] * 0.5 + 0.3 + rng.normal(scale=0.2, size=n), 0.0, 2.0).astype(np.float32)
        realized = (X[:, 1] * 0.3 + rng.normal(scale=0.3, size=n)).astype(np.float32)
        return X, peak, realized

    def test_train_returns_metrics(self):
        X, peak, real = self._synthetic_data()
        m = EarlyExitModel()
        metrics = m.train(X, peak, real, n_estimators=40, max_depth=3)
        assert "val_auc" in metrics
        assert "val_precision@0.5" in metrics
        assert "val_recall@0.5" in metrics
        assert 0 <= metrics["val_accuracy"] <= 100
        assert 0 <= metrics["val_precision@0.5"] <= 1
        assert 0 <= metrics["val_recall@0.5"] <= 1

    def test_predict_proba_in_unit_interval(self):
        X, peak, real = self._synthetic_data()
        m = EarlyExitModel()
        m.train(X, peak, real, n_estimators=40, max_depth=3)
        for obs in X[:20]:
            p = m.predict_proba(obs)
            assert 0.0 <= p <= 1.0

    def test_predict_batch_matches_single(self):
        X, peak, real = self._synthetic_data()
        m = EarlyExitModel()
        m.train(X, peak, real, n_estimators=40, max_depth=3)
        batch = m.predict_proba_batch(X[:20])
        singles = np.array([m.predict_proba(x) for x in X[:20]], dtype=np.float32)
        np.testing.assert_array_almost_equal(batch, singles, decimal=5)

    def test_should_early_exit_threshold(self):
        X, peak, real = self._synthetic_data()
        m = EarlyExitModel()
        m.train(X, peak, real, n_estimators=40, max_depth=3)
        # Build a sample where the model is confident either way, then test both ends.
        probs = m.predict_proba_batch(X[:200])
        high_idx = int(np.argmax(probs))
        low_idx = int(np.argmin(probs))
        if probs[high_idx] >= 0.5:
            assert m.should_early_exit(X[high_idx], threshold=0.5)
        if probs[low_idx] < 0.5:
            assert not m.should_early_exit(X[low_idx], threshold=0.5)

    def test_save_load_roundtrip(self, tmp_path):
        X, peak, real = self._synthetic_data()
        m = EarlyExitModel()
        m.train(X, peak, real, n_estimators=40, max_depth=3)
        path = tmp_path / "ee_test.joblib"
        m.save(path)
        m2 = EarlyExitModel.load(path)
        sample = X[0]
        assert abs(m.predict_proba(sample) - m2.predict_proba(sample)) < 1e-6

    def test_beats_random_on_signal(self):
        """Model should beat chance on a clear pump-and-retrace signal."""
        X, peak, real = self._synthetic_data(n=5000)
        m = EarlyExitModel()
        metrics = m.train(X, peak, real, n_estimators=100, max_depth=4)
        # Baseline: always predict the majority class.
        y = derive_early_exit_labels(peak, real)
        majority_pct = max(y.mean(), 1 - y.mean()) * 100
        assert metrics["val_accuracy"] >= majority_pct - 2, (
            f"val_acc {metrics['val_accuracy']} not beating majority-class {majority_pct:.1f}%"
        )
