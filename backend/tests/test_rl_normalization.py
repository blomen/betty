"""
Tests for RunningNormalizer (Welford's online algorithm).
"""

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest

from src.rl.data.normalization import RunningNormalizer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

RNG = np.random.default_rng(42)
DIM = 8
N_SAMPLES = 5_000


def make_data(n: int = N_SAMPLES, dim: int = DIM) -> np.ndarray:
    """Generate samples from a non-trivial distribution (different mean/std per dim)."""
    means = np.arange(dim, dtype=np.float64) * 2.0          # 0, 2, 4, ...
    stds = np.arange(1, dim + 1, dtype=np.float64) * 0.5    # 0.5, 1.0, 1.5, ...
    return RNG.normal(loc=means, scale=stds, size=(n, dim)).astype(np.float32)


# ---------------------------------------------------------------------------
# Test 1: Empty normalizer returns input unchanged (count < 2)
# ---------------------------------------------------------------------------

class TestEmptyNormalizer:
    def test_count_zero_returns_input(self):
        norm = RunningNormalizer(dim=DIM)
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0], dtype=np.float32)
        out = norm.normalize(x)
        np.testing.assert_array_almost_equal(out, x.astype(np.float32))

    def test_count_one_returns_input(self):
        norm = RunningNormalizer(dim=DIM)
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0], dtype=np.float32)
        norm.update(x)
        assert norm.count == 1
        out = norm.normalize(x)
        np.testing.assert_array_almost_equal(out, x.astype(np.float32))

    def test_output_dtype_is_float32(self):
        norm = RunningNormalizer(dim=3)
        x = np.array([1.0, 2.0, 3.0], dtype=np.float64)
        out = norm.normalize(x)
        assert out.dtype == np.float32


# ---------------------------------------------------------------------------
# Test 2: After many updates, normalize produces ~zero mean
# ---------------------------------------------------------------------------

class TestNormalizedMean:
    def test_normalized_mean_near_zero(self):
        data = make_data()
        norm = RunningNormalizer(dim=DIM)
        for row in data:
            norm.update(row)

        # Normalize all samples and compute the mean of the result
        normalized = np.stack([norm.normalize(row) for row in data])
        mean_of_normalized = normalized.mean(axis=0)

        # Each dimension should have mean close to 0
        np.testing.assert_allclose(mean_of_normalized, 0.0, atol=0.05,
                                   err_msg="Normalized mean not near zero")

    def test_mean_tracks_running_mean(self):
        """Welford mean should match numpy mean on the same data."""
        data = make_data(n=200)
        norm = RunningNormalizer(dim=DIM)
        for row in data:
            norm.update(row)

        np.testing.assert_allclose(norm.mean, data.mean(axis=0).astype(np.float64),
                                   rtol=1e-5)


# ---------------------------------------------------------------------------
# Test 3: After many updates, normalize produces ~unit variance
# ---------------------------------------------------------------------------

class TestNormalizedVariance:
    def test_normalized_variance_near_one(self):
        data = make_data()
        norm = RunningNormalizer(dim=DIM)
        for row in data:
            norm.update(row)

        normalized = np.stack([norm.normalize(row) for row in data])
        std_of_normalized = normalized.std(axis=0)

        np.testing.assert_allclose(std_of_normalized, 1.0, atol=0.05,
                                   err_msg="Normalized std not near 1.0")

    def test_std_property(self):
        """std property should match sample std of input data."""
        data = make_data(n=200)
        norm = RunningNormalizer(dim=DIM)
        for row in data:
            norm.update(row)

        expected_std = data.astype(np.float64).std(axis=0, ddof=1)
        np.testing.assert_allclose(norm.std, expected_std, rtol=1e-5)


# ---------------------------------------------------------------------------
# Test 4: Save and load preserves statistics
# ---------------------------------------------------------------------------

class TestSaveLoad:
    def test_save_load_round_trip(self, tmp_path):
        data = make_data(n=100)
        norm = RunningNormalizer(dim=DIM)
        for row in data:
            norm.update(row)

        save_path = tmp_path / "normalizer.json"
        norm.save(save_path)

        # Load into a fresh normalizer
        norm2 = RunningNormalizer(dim=DIM)
        norm2.load(save_path)

        assert norm2.count == norm.count
        np.testing.assert_array_equal(norm2.mean, norm.mean)
        np.testing.assert_array_equal(norm2.M2, norm.M2)

    def test_loaded_normalizer_produces_same_output(self, tmp_path):
        data = make_data(n=100)
        norm = RunningNormalizer(dim=DIM)
        for row in data:
            norm.update(row)

        save_path = tmp_path / "normalizer.json"
        norm.save(save_path)

        norm2 = RunningNormalizer(dim=DIM)
        norm2.load(save_path)

        test_vec = data[0]
        np.testing.assert_array_equal(norm.normalize(test_vec),
                                      norm2.normalize(test_vec))

    def test_save_creates_parent_dirs(self, tmp_path):
        norm = RunningNormalizer(dim=3)
        norm.update(np.array([1.0, 2.0, 3.0]))
        norm.update(np.array([4.0, 5.0, 6.0]))

        nested_path = tmp_path / "a" / "b" / "c" / "norm.json"
        norm.save(nested_path)
        assert nested_path.exists()

    def test_load_dim_mismatch_raises(self, tmp_path):
        norm = RunningNormalizer(dim=4)
        norm.update(np.ones(4))
        norm.update(np.zeros(4))
        save_path = tmp_path / "norm.json"
        norm.save(save_path)

        wrong_dim = RunningNormalizer(dim=8)
        with pytest.raises(ValueError, match="dim"):
            wrong_dim.load(save_path)

    def test_json_is_human_readable(self, tmp_path):
        norm = RunningNormalizer(dim=2)
        norm.update(np.array([1.0, 2.0]))
        norm.update(np.array([3.0, 4.0]))
        save_path = tmp_path / "norm.json"
        norm.save(save_path)

        payload = json.loads(save_path.read_text())
        assert set(payload.keys()) == {"dim", "count", "mean", "M2"}
        assert payload["count"] == 2


# ---------------------------------------------------------------------------
# Test 5: Welford accuracy vs numpy
# ---------------------------------------------------------------------------

class TestWelfordAccuracy:
    """Verify Welford's algorithm matches numpy batch computations."""

    @pytest.mark.parametrize("n", [10, 100, 1_000, 10_000])
    def test_mean_matches_numpy(self, n):
        data = make_data(n=n)
        norm = RunningNormalizer(dim=DIM)
        for row in data:
            norm.update(row)

        np_mean = data.astype(np.float64).mean(axis=0)
        np.testing.assert_allclose(norm.mean, np_mean, rtol=1e-6,
                                   err_msg=f"Mean mismatch at n={n}")

    @pytest.mark.parametrize("n", [10, 100, 1_000, 10_000])
    def test_variance_matches_numpy(self, n):
        data = make_data(n=n)
        norm = RunningNormalizer(dim=DIM)
        for row in data:
            norm.update(row)

        np_var = data.astype(np.float64).var(axis=0, ddof=1)
        np.testing.assert_allclose(norm.variance, np_var, rtol=1e-5,
                                   err_msg=f"Variance mismatch at n={n}")

    def test_numerical_stability_large_offset(self):
        """Welford stays stable even when data has a large constant offset."""
        offset = 1e8
        data = make_data(n=500)
        shifted = data.astype(np.float64) + offset

        norm = RunningNormalizer(dim=DIM)
        for row in shifted:
            norm.update(row)

        np_mean = shifted.mean(axis=0)
        np_var = shifted.var(axis=0, ddof=1)

        np.testing.assert_allclose(norm.mean, np_mean, rtol=1e-6)
        np.testing.assert_allclose(norm.variance, np_var, rtol=1e-5)

    def test_constant_dimension_handled_by_eps(self):
        """A dimension with zero variance should not cause division by zero."""
        norm = RunningNormalizer(dim=3)
        for _ in range(50):
            norm.update(np.array([5.0, RNG.random(), RNG.random()]))

        # dim 0 is constant; normalize should return 0 (not NaN/inf)
        out = norm.normalize(np.array([5.0, 0.5, 0.5]))
        assert np.isfinite(out).all(), "Non-finite values in normalized output"
        assert abs(float(out[0])) < 1.0  # clamped by eps, not exploding
