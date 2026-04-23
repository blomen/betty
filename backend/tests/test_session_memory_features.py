"""Tests for session-memory features (training sim + live extract)."""

from __future__ import annotations

import numpy as np
import pytest

from src.rl.features.session_memory_features import (
    SESSION_MEMORY_DIM,
    extract_session_memory_live,
    simulate_session_memory,
)


class TestDimensionality:
    def test_segment_dim_is_6(self):
        assert SESSION_MEMORY_DIM == 6

    def test_simulate_shape(self):
        n = 25
        te = np.arange(1700000000, 1700000000 + n * 60, 60, dtype=np.float64)
        rc = np.random.default_rng(0).uniform(-1, 2, n).astype(np.float32)
        rr = np.random.default_rng(1).uniform(-1, 2, n).astype(np.float32)
        mem = simulate_session_memory(te, rc, rr)
        assert mem.shape == (n, 6)

    def test_live_shape(self):
        vec = extract_session_memory_live(
            recent_outcomes=[0.5, -0.2, 1.0],
            session_R=1.3,
            peak_session_R=1.5,
            consecutive_losses=1,
            trades_taken=3,
        )
        assert vec.shape == (6,)
        assert vec.dtype == np.float32


class TestNormalisationBounds:
    def test_all_in_expected_ranges(self):
        # Extreme-case inputs → values stay in stated ranges
        vec = extract_session_memory_live(
            recent_outcomes=[1.0, 1.0, 1.0, 1.0, 1.0],
            session_R=1000.0,
            peak_session_R=-1000.0,
            consecutive_losses=0,
            trades_taken=500,
        )
        assert 0.0 <= vec[0] <= 1.0  # win rate
        assert -1.0 <= vec[1] <= 1.0  # avg R
        assert -1.0 <= vec[2] <= 0.0  # DD from peak (must be ≤0)
        assert 0.0 <= vec[3] <= 1.0  # consec loss
        assert 0.0 <= vec[4] <= 1.0  # trade count
        assert 0.0 <= vec[5] <= 1.0  # R volatility

    def test_dd_is_negative_when_below_peak(self):
        vec = extract_session_memory_live(
            recent_outcomes=[-0.5, -0.5],
            session_R=5.0,
            peak_session_R=10.0,
            consecutive_losses=2,
            trades_taken=4,
        )
        # DD from peak = 5 - 10 = -5, normalised = -0.05
        assert vec[2] < 0.0
        assert vec[2] >= -1.0


class TestTrainingVsLiveMatch:
    """Critical: training-time simulation and live extraction must produce the
    same vector for equivalent session states, otherwise the model trains on
    one distribution and sees another at inference.
    """

    def test_action_conditioned_matches_live(self):
        """With explicit actions, the sim takes each trade (including losers)
        and its state at row N should match the live extract of what the policy
        actually saw. This is the action-conditioned path that will be used
        once a TriggerGBT is available."""
        # Trades actually taken: +1, -0.5, +0.8, -1.0, +0.5 → net +0.8 R
        recent = [1.0, -0.5, 0.8, -1.0, 0.5]
        session_R = sum(recent)
        # Peak: 1.0 → 0.5 → 1.3 → 0.3 → 0.8 → max = 1.3
        peak = 1.3
        consec = 0  # last trade was +0.5
        trades = 5

        live = extract_session_memory_live(
            recent_outcomes=recent,
            session_R=session_R,
            peak_session_R=peak,
            consecutive_losses=consec,
            trades_taken=trades,
        )
        te = np.arange(1700000000, 1700000000 + 6 * 60, 60, dtype=np.float64)
        # rewards_cont has our outcomes; rewards_rev is worse so action=0 everywhere
        rc = np.array([1.0, -0.5, 0.8, -1.0, 0.5, 0.0], dtype=np.float32)
        rr = np.full(6, -99.0, dtype=np.float32)
        actions = np.zeros(6, dtype=np.int64)  # all CONT
        mem = simulate_session_memory(te, rc, rr, actions=actions)
        # Row 5 reflects state AFTER trades 0-4 resolved.
        np.testing.assert_array_almost_equal(mem[5], live, decimal=4)

    def test_greedy_fallback_skips_negative_outcomes(self):
        """Without actions, the sim takes only positive outcomes (legacy
        position_state behaviour). Documenting the limitation."""
        te = np.arange(1700000000, 1700000000 + 6 * 60, 60, dtype=np.float64)
        rc = np.array([1.0, -0.5, 0.8, -1.0, 0.5, 0.0], dtype=np.float32)
        rr = np.full(6, -99.0, dtype=np.float32)
        mem = simulate_session_memory(te, rc, rr, actions=None)
        # In greedy mode, trades 1 and 3 are skipped (max with 0 = 0)
        # → row 5 sees only 3 trades, all positive
        # trades_taken = 3 → normalised 0.03
        assert mem[5, 4] == pytest.approx(0.03, abs=0.001)
        # consec_losses = 0 (never records a loss)
        assert mem[5, 3] == pytest.approx(0.0)


class TestSessionBoundary:
    def test_reset_on_new_session(self):
        # 3 trades day 1, then >1h gap, then 3 more
        day1 = np.arange(1700000000, 1700000000 + 3 * 60, 60, dtype=np.float64)
        day2 = np.arange(day1[-1] + 4000, day1[-1] + 4000 + 3 * 60, 60, dtype=np.float64)
        te = np.concatenate([day1, day2])
        rc = np.array([1.0, 1.0, 1.0, 2.0, 2.0, 2.0], dtype=np.float32)
        rr = np.full(6, -99.0, dtype=np.float32)
        mem = simulate_session_memory(te, rc, rr)
        # After the boundary, row 3 should reflect fresh-session state
        # (no accumulated R, 0 trades taken).
        assert mem[3, 3] == 0.0  # consec_loss = 0
        assert mem[3, 4] == 0.0  # trade_count = 0


class TestWarmup:
    def test_first_trade_has_neutral_rolling_state(self):
        te = np.arange(1700000000, 1700000000 + 60, 60, dtype=np.float64)[:1]
        rc = np.array([1.0], dtype=np.float32)
        rr = np.array([-99.0], dtype=np.float32)
        mem = simulate_session_memory(te, rc, rr)
        # win_rate default 0.5, avg_R 0.0, DD 0, consec 0, trades 0, vol 0
        np.testing.assert_array_almost_equal(mem[0], [0.5, 0.0, 0.0, 0.0, 0.0, 0.0])
