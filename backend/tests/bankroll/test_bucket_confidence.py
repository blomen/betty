"""Unit tests for sport×market CLV confidence multiplier.

Pure-function tests on `get_multiplier` (no DB) plus a smoke test that
`lookup_multiplier` returns 1.0 when the feature flag is unset, so adding
the wiring is a safe no-op until the user enables it explicitly.
"""

import pytest

from src.bankroll.bucket_confidence import (
    get_multiplier,
    invalidate_cache,
    is_enabled,
    lookup_multiplier,
)


class TestGetMultiplier:
    """Pure-function tests on the CLV → multiplier mapping."""

    def test_below_min_sample_returns_one_regardless_of_clv(self):
        # < 100 bets: we trust the existing edge model, no de-weighting.
        assert get_multiplier(-5.0, 0) == 1.0
        assert get_multiplier(-5.0, 50) == 1.0
        assert get_multiplier(-5.0, 99) == 1.0

    def test_at_min_sample_threshold_applies_multiplier(self):
        # 100 bets is the floor. Bad CLV → deflate.
        assert get_multiplier(-3.0, 100) == 0.0
        assert get_multiplier(-1.0, 100) == 0.5
        assert get_multiplier(-0.2, 100) == 0.75
        assert get_multiplier(0.6, 100) == 1.0

    def test_positive_clv_returns_one(self):
        assert get_multiplier(0.5, 500) == 1.0
        assert get_multiplier(2.0, 500) == 1.0
        assert get_multiplier(10.0, 500) == 1.0

    def test_near_zero_clv_mild_deflation(self):
        # -0.5 <= mean_clv < 0.5: 0.75
        assert get_multiplier(0.0, 500) == 0.75
        assert get_multiplier(0.4, 500) == 0.75
        assert get_multiplier(-0.4, 500) == 0.75

    def test_clearly_negative_clv_half_kelly(self):
        # -2.0 <= mean_clv < -0.5: 0.5
        assert get_multiplier(-0.6, 500) == 0.5
        assert get_multiplier(-1.5, 500) == 0.5
        assert get_multiplier(-1.9, 500) == 0.5

    def test_strongly_negative_clv_skip(self):
        # mean_clv < -2.0: 0.0 (skip)
        assert get_multiplier(-2.5, 500) == 0.0
        assert get_multiplier(-5.0, 500) == 0.0
        assert get_multiplier(-100.0, 500) == 0.0

    def test_none_clv_returns_one(self):
        # No CLV data → don't deflate.
        assert get_multiplier(None, 500) == 1.0
        assert get_multiplier(None, 0) == 1.0

    def test_monotonic_in_mean_clv(self):
        # As CLV improves, multiplier should be non-decreasing.
        clvs = [-3.0, -2.0, -1.5, -0.5, 0.0, 0.4, 0.5, 1.0, 2.0]
        mults = [get_multiplier(c, 200) for c in clvs]
        for a, b in zip(mults, mults[1:], strict=False):
            assert a <= b, f"multiplier decreased: {mults}"

    def test_returns_value_in_unit_interval(self):
        for clv in [-10.0, -2.5, -1.0, 0.0, 1.0, 10.0]:
            for n in [0, 100, 1000]:
                m = get_multiplier(clv, n)
                assert 0.0 <= m <= 1.0


class TestEnvFlag:
    """The feature flag must default to off and accept common truthy values."""

    @pytest.fixture(autouse=True)
    def clear_env(self, monkeypatch):
        monkeypatch.delenv("BUCKET_CONFIDENCE_ENABLED", raising=False)
        invalidate_cache()

    def test_default_disabled(self):
        assert is_enabled() is False

    def test_explicit_off_disabled(self, monkeypatch):
        monkeypatch.setenv("BUCKET_CONFIDENCE_ENABLED", "false")
        assert is_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "True", "TRUE", "yes", "YES"])
    def test_truthy_values_enabled(self, monkeypatch, val):
        monkeypatch.setenv("BUCKET_CONFIDENCE_ENABLED", val)
        assert is_enabled() is True


class TestLookupMultiplierSafety:
    """Lookup must never throw and must default to 1.0 when disabled or
    missing inputs — preserves current behavior on every value-bet hot path."""

    @pytest.fixture(autouse=True)
    def clear_env(self, monkeypatch):
        monkeypatch.delenv("BUCKET_CONFIDENCE_ENABLED", raising=False)
        invalidate_cache()

    def test_disabled_returns_one_with_none_session(self):
        # Even if someone passes None, we shouldn't crash — multiplier=1.0.
        assert lookup_multiplier(None, "soccer", "moneyline") == 1.0

    def test_missing_sport_returns_one(self, monkeypatch):
        monkeypatch.setenv("BUCKET_CONFIDENCE_ENABLED", "1")
        assert lookup_multiplier(None, None, "moneyline") == 1.0
        assert lookup_multiplier(None, "", "moneyline") == 1.0

    def test_missing_market_returns_one(self, monkeypatch):
        monkeypatch.setenv("BUCKET_CONFIDENCE_ENABLED", "1")
        assert lookup_multiplier(None, "soccer", None) == 1.0
        assert lookup_multiplier(None, "soccer", "") == 1.0

    def test_swallowed_db_error_returns_one(self, monkeypatch):
        # If the DB query blows up, fail open (full Kelly), don't crash the
        # bet pipeline. This protects us from a transient DB hiccup or a
        # schema-drift bug accidentally suppressing every value bet.
        monkeypatch.setenv("BUCKET_CONFIDENCE_ENABLED", "1")

        class _Bomb:
            def query(self, *_a, **_kw):
                raise RuntimeError("simulated DB failure")

        assert lookup_multiplier(_Bomb(), "soccer", "moneyline") == 1.0
