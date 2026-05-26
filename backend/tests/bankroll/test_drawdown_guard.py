"""Unit tests for the provider drawdown circuit breaker.

Pure-function tests on `is_breached` + `pause_threshold_pct` (no DB),
plus safety tests on `is_paused` (must fail-open on any error).
"""

import pytest

from src.bankroll.drawdown_guard import (
    _DEFAULT_THRESHOLD_PCT,
    _MIN_BETS_FOR_BREACH,
    invalidate_cache,
    is_breached,
    is_enabled,
    is_paused,
    pause_threshold_pct,
)


class TestIsBreached:
    """Pure-function breach math."""

    def test_negligible_loss_not_breached(self):
        # -1% of bankroll, way under the 10% threshold.
        assert is_breached(pnl_sek=-100, stake_bankroll_sek=10000, threshold_pct=0.10, n_bets=50) is False

    def test_loss_at_threshold_not_yet_breached(self):
        # Exactly -10% is the boundary; we breach only when strictly worse.
        assert is_breached(pnl_sek=-1000, stake_bankroll_sek=10000, threshold_pct=0.10, n_bets=50) is False

    def test_loss_below_threshold_is_breached(self):
        # -10.01% of bankroll trips the wire.
        assert is_breached(pnl_sek=-1001, stake_bankroll_sek=10000, threshold_pct=0.10, n_bets=50) is True

    def test_winning_pnl_never_breached(self):
        assert is_breached(pnl_sek=5000, stake_bankroll_sek=10000, threshold_pct=0.10, n_bets=100) is False
        assert is_breached(pnl_sek=0, stake_bankroll_sek=10000, threshold_pct=0.10, n_bets=100) is False

    def test_below_min_bets_never_breaches(self):
        # 50% loss but only 5 bets → variance, not signal. Don't trip.
        assert is_breached(pnl_sek=-5000, stake_bankroll_sek=10000, threshold_pct=0.10, n_bets=5) is False
        assert (
            is_breached(pnl_sek=-5000, stake_bankroll_sek=10000, threshold_pct=0.10, n_bets=_MIN_BETS_FOR_BREACH - 1)
            is False
        )

    def test_at_min_bets_threshold_can_breach(self):
        assert (
            is_breached(pnl_sek=-5000, stake_bankroll_sek=10000, threshold_pct=0.10, n_bets=_MIN_BETS_FOR_BREACH)
            is True
        )

    def test_zero_bankroll_safe(self):
        # Don't divide by anything, don't trip on bogus inputs.
        assert is_breached(pnl_sek=-1000, stake_bankroll_sek=0, threshold_pct=0.10, n_bets=100) is False

    def test_zero_threshold_safe(self):
        # threshold_pct=0 must NOT mean "any loss breaches" — fail safe.
        assert is_breached(pnl_sek=-1, stake_bankroll_sek=10000, threshold_pct=0.0, n_bets=100) is False


class TestEnvFlag:
    """Feature flag must default off; threshold must be configurable."""

    @pytest.fixture(autouse=True)
    def clear_env(self, monkeypatch):
        monkeypatch.delenv("DRAWDOWN_BREAKER_ENABLED", raising=False)
        monkeypatch.delenv("DRAWDOWN_PAUSE_PCT", raising=False)
        invalidate_cache()

    def test_flag_default_disabled(self):
        assert is_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "True", "TRUE", "yes", "YES"])
    def test_flag_truthy_enabled(self, monkeypatch, val):
        monkeypatch.setenv("DRAWDOWN_BREAKER_ENABLED", val)
        assert is_enabled() is True

    def test_threshold_default(self):
        assert pause_threshold_pct() == _DEFAULT_THRESHOLD_PCT

    def test_threshold_override(self, monkeypatch):
        monkeypatch.setenv("DRAWDOWN_PAUSE_PCT", "0.25")
        assert pause_threshold_pct() == 0.25

    def test_threshold_garbage_falls_back(self, monkeypatch):
        monkeypatch.setenv("DRAWDOWN_PAUSE_PCT", "not-a-number")
        assert pause_threshold_pct() == _DEFAULT_THRESHOLD_PCT

    def test_threshold_out_of_range_falls_back(self, monkeypatch):
        # 1.5 (150%) and -0.5 are nonsense — must clamp back to default.
        monkeypatch.setenv("DRAWDOWN_PAUSE_PCT", "1.5")
        assert pause_threshold_pct() == _DEFAULT_THRESHOLD_PCT
        monkeypatch.setenv("DRAWDOWN_PAUSE_PCT", "-0.5")
        assert pause_threshold_pct() == _DEFAULT_THRESHOLD_PCT
        monkeypatch.setenv("DRAWDOWN_PAUSE_PCT", "0")
        assert pause_threshold_pct() == _DEFAULT_THRESHOLD_PCT


class TestIsPausedSafety:
    """`is_paused` must fail open — a bug or DB error never blocks bets."""

    @pytest.fixture(autouse=True)
    def clear_env(self, monkeypatch):
        monkeypatch.delenv("DRAWDOWN_BREAKER_ENABLED", raising=False)
        monkeypatch.delenv("DRAWDOWN_PAUSE_PCT", raising=False)
        invalidate_cache()

    def test_disabled_returns_false(self):
        # No env flag → always (False, None).
        assert is_paused(None, profile_id=1, provider_id="pinnacle", stake_bankroll_sek=10000) == (False, None)

    def test_missing_provider_returns_false(self, monkeypatch):
        monkeypatch.setenv("DRAWDOWN_BREAKER_ENABLED", "1")
        assert is_paused(None, profile_id=1, provider_id="", stake_bankroll_sek=10000) == (False, None)

    def test_missing_profile_returns_false(self, monkeypatch):
        monkeypatch.setenv("DRAWDOWN_BREAKER_ENABLED", "1")
        assert is_paused(None, profile_id=0, provider_id="pinnacle", stake_bankroll_sek=10000) == (False, None)

    def test_zero_bankroll_returns_false(self, monkeypatch):
        monkeypatch.setenv("DRAWDOWN_BREAKER_ENABLED", "1")
        assert is_paused(object(), profile_id=1, provider_id="pinnacle", stake_bankroll_sek=0) == (False, None)

    def test_swallowed_db_error_returns_false(self, monkeypatch):
        # If anything in the lookup raises, fail open. We must never let
        # a guard bug suppress every bet placement.
        monkeypatch.setenv("DRAWDOWN_BREAKER_ENABLED", "1")

        class _Bomb:
            def query(self, *_a, **_kw):
                raise RuntimeError("simulated DB failure")

        result = is_paused(_Bomb(), profile_id=1, provider_id="pinnacle", stake_bankroll_sek=10000)
        assert result == (False, None)


class TestStakeCalculatorIntegration:
    """Drawdown skip flows through StakeCalculator.calculate()."""

    @pytest.fixture(autouse=True)
    def clear_env(self, monkeypatch):
        monkeypatch.delenv("DRAWDOWN_BREAKER_ENABLED", raising=False)
        monkeypatch.delenv("DRAWDOWN_PAUSE_PCT", raising=False)
        invalidate_cache()

    def test_no_db_no_profile_means_no_check(self):
        # Default construction → never checks drawdown → identical to before.
        from src.bankroll.stake_calculator import StakeCalculator

        calc = StakeCalculator(bankroll=10000)
        result = calc.calculate(edge_raw=0.05, odds=2.0, provider_id="pinnacle", min_odds=0.0)
        assert result.stake > 0

    def test_paused_provider_returns_skip(self, monkeypatch):
        # Force is_paused to return True; ensure StakeCalculator returns
        # stake=0 with a clear skip_reason. Patch on the binding the
        # caller imports inside .calculate(), not the module original.
        from src.bankroll import stake_calculator as sc

        monkeypatch.setenv("DRAWDOWN_BREAKER_ENABLED", "1")

        def fake_is_paused(_db, _profile_id, provider_id, **_kw):
            return True, f"forced paused: {provider_id}"

        # The function is imported inside .calculate(); patch via the
        # drawdown_guard module so the inline import picks up the fake.
        from src.bankroll import drawdown_guard

        monkeypatch.setattr(drawdown_guard, "is_paused", fake_is_paused)

        calc = sc.StakeCalculator(bankroll=10000, db_session=object(), profile_id=1)
        result = calc.calculate(edge_raw=0.05, odds=2.0, provider_id="pinnacle", min_odds=0.0)
        assert result.stake == 0.0
        assert "drawdown" in (result.skip_reason or "").lower()
