"""Tests for SessionState — circuit breaker + per-zone cooldown."""

from __future__ import annotations

from src.rl.session_state import (
    MAX_CONSECUTIVE_LOSSES,
    MIN_ROLLING_WIN_RATE,
    ROLLING_WINDOW,
    SessionState,
)


class TestPerZoneCooldown:
    def test_no_skip_on_first_touch(self):
        s = SessionState()
        assert s.should_skip(zone_key=4500.0, now_ts=1000.0) is None

    def test_skip_within_cooldown_window(self):
        s = SessionState()
        s.record_trade(zone_key=4500.0, now_ts=1000.0, realized_R=+0.5)
        # 60s later — well inside the 300s cooldown
        sr = s.should_skip(zone_key=4500.0, now_ts=1060.0)
        assert sr is not None
        assert sr.code == "cooldown"
        assert "4500" in sr.detail

    def test_no_skip_after_cooldown_passes(self):
        s = SessionState()
        s.record_trade(zone_key=4500.0, now_ts=1000.0, realized_R=+0.5)
        # 301s later — past cooldown
        assert s.should_skip(zone_key=4500.0, now_ts=1301.0) is None

    def test_different_zones_not_blocked_by_cooldown(self):
        s = SessionState()
        s.record_trade(zone_key=4500.0, now_ts=1000.0, realized_R=+0.5)
        # Different price → different zone bucket
        assert s.should_skip(zone_key=4600.0, now_ts=1010.0) is None

    def test_zone_key_rounded_to_quarter_tick(self):
        s = SessionState()
        s.record_trade(zone_key=4500.10, now_ts=1000.0, realized_R=+0.5)
        # 4500.10 rounds to 4500.00 (quarter-tick = 0.25 grid).
        # 4500.05 also rounds to 4500.0 → same bucket → cooldown applies.
        sr = s.should_skip(zone_key=4500.05, now_ts=1100.0)
        assert sr is not None and sr.code == "cooldown"


class TestCircuitBreakerConsecutiveLosses:
    def test_no_circuit_below_threshold(self):
        s = SessionState()
        for i in range(MAX_CONSECUTIVE_LOSSES - 1):
            s.record_trade(zone_key=4500.0 + i * 100, now_ts=1000.0 + i * 1000, realized_R=-0.5)
        # One short of threshold — circuit not active yet
        assert not s.circuit_active

    def test_trips_after_max_consecutive(self):
        s = SessionState()
        for i in range(MAX_CONSECUTIVE_LOSSES):
            s.record_trade(zone_key=4500.0 + i * 100, now_ts=1000.0 + i * 1000, realized_R=-0.5)
        # Next entry attempt should trip the circuit
        sr = s.should_skip(zone_key=5500.0, now_ts=10000.0)
        assert sr is not None
        assert sr.code == "circuit_consec"
        assert s.circuit_active

    def test_resume_after_two_wins(self):
        s = SessionState()
        for i in range(MAX_CONSECUTIVE_LOSSES):
            s.record_trade(zone_key=4500.0 + i * 100, now_ts=1000.0 + i * 1000, realized_R=-0.5)
        s.should_skip(zone_key=5500.0, now_ts=10000.0)  # trips circuit
        assert s.circuit_active

        # One win is not enough
        s.record_trade(zone_key=5500.0, now_ts=10000.0, realized_R=+1.0)
        assert s.circuit_active

        # Second win resumes
        s.record_trade(zone_key=5600.0, now_ts=11000.0, realized_R=+1.0)
        assert not s.circuit_active


class TestCircuitBreakerRollingWinRate:
    def test_no_circuit_with_few_trades(self):
        s = SessionState()
        # Mix of wins/losses below the rolling window threshold
        s.record_trade(zone_key=4500.0, now_ts=1000.0, realized_R=-0.5)
        s.record_trade(zone_key=4600.0, now_ts=2000.0, realized_R=-0.5)
        # Only 2 trades — rolling win rate isn't yet meaningful
        assert s.should_skip(zone_key=4700.0, now_ts=3000.0) is None

    def test_trips_when_rolling_win_rate_low(self):
        # Need full window of bad trades AND not enough consecutive to trip
        # the consec-loss circuit first. Use alternating pattern: 1 win, 9 losses.
        s = SessionState(max_consecutive_losses=999)  # disable consec gate
        s.record_trade(zone_key=4500.0, now_ts=0, realized_R=+0.5)
        for i in range(ROLLING_WINDOW - 1):
            s.record_trade(zone_key=4500.0 + (i + 1) * 100, now_ts=(i + 1) * 1000, realized_R=-0.5)
        # rolling win rate = 1/10 = 10% < MIN_ROLLING_WIN_RATE (15%)
        assert s.rolling_win_rate < MIN_ROLLING_WIN_RATE
        sr = s.should_skip(zone_key=10000.0, now_ts=100000.0)
        assert sr is not None
        assert sr.code == "circuit_winrate"


class TestCircuitBreakerSessionDrawdown:
    def test_trips_at_dd_threshold(self):
        # Force a session DD by alternating: build peak, then drop
        s = SessionState(max_consecutive_losses=999, min_rolling_win_rate=-1.0)
        s.record_trade(zone_key=4500.0, now_ts=1000.0, realized_R=+50.0)  # peak = +50
        s.record_trade(zone_key=4600.0, now_ts=2000.0, realized_R=-260.0)  # dd = -260
        sr = s.should_skip(zone_key=4700.0, now_ts=3000.0)
        assert sr is not None
        assert sr.code == "circuit_dd"
        assert s.session_drawdown_r <= -200


class TestSnapshot:
    def test_snapshot_contains_expected_keys(self):
        s = SessionState()
        s.record_trade(zone_key=4500.0, now_ts=1000.0, realized_R=+0.5)
        snap = s.snapshot()
        assert {
            "trades_taken",
            "trades_skipped_circuit",
            "trades_skipped_cooldown",
            "consecutive_losses",
            "consecutive_wins",
            "rolling_win_rate",
            "session_R",
            "peak_session_R",
            "session_drawdown_R",
            "circuit_active",
            "circuit_reason",
        }.issubset(snap.keys())

    def test_session_state_resets_cleanly(self):
        s = SessionState()
        s.record_trade(zone_key=4500.0, now_ts=1000.0, realized_R=+0.5)
        s.record_trade(zone_key=4600.0, now_ts=2000.0, realized_R=-0.5)
        s.reset_for_new_session()
        assert s.trades_taken == 0
        assert s.session_R == 0.0
        assert not s.circuit_active
        assert s.should_skip(zone_key=4500.0, now_ts=10000.0) is None  # cooldown cleared


class TestSkipCounters:
    def test_circuit_skips_increment_counter(self):
        s = SessionState()
        for i in range(MAX_CONSECUTIVE_LOSSES):
            s.record_trade(zone_key=4500.0 + i * 100, now_ts=1000.0 + i * 1000, realized_R=-0.5)
        for i in range(3):
            s.should_skip(zone_key=10000.0, now_ts=100000.0)
        assert s.trades_skipped_circuit == 3

    def test_cooldown_skips_increment_counter(self):
        s = SessionState()
        s.record_trade(zone_key=4500.0, now_ts=1000.0, realized_R=+0.5)
        s.should_skip(zone_key=4500.0, now_ts=1100.0)
        s.should_skip(zone_key=4500.0, now_ts=1200.0)
        assert s.trades_skipped_cooldown == 2
