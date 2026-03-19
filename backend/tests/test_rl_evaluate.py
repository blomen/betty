"""Tests for rl.agent.evaluate — compute_metrics and print_evaluation_report."""

import math

import pytest

from src.rl.agent.evaluate import compute_metrics, print_evaluation_report
from src.rl.config import Action

LONG = Action.LONG.value
SHORT = Action.SHORT.value
SKIP = Action.SKIP.value


# ---------------------------------------------------------------------------
# 1. Perfect trader — all wins
# ---------------------------------------------------------------------------

class TestPerfectTrader:
    def setup_method(self):
        self.episodes = [
            {"action": LONG, "reward": 2.0, "level_type": "vwap"},
            {"action": SHORT, "reward": 2.0, "level_type": "vwap"},
            {"action": LONG, "reward": 2.0, "level_type": "pdh"},
        ]
        self.m = compute_metrics(self.episodes)

    def test_win_rate_is_one(self):
        assert self.m["win_rate"] == 1.0

    def test_profit_factor_very_high(self):
        # No losses → profit_factor should be inf
        assert math.isinf(self.m["profit_factor"])

    def test_skip_rate_zero(self):
        assert self.m["skip_rate"] == 0.0

    def test_total_r(self):
        assert self.m["total_r"] == pytest.approx(6.0)

    def test_avg_r(self):
        assert self.m["avg_r"] == pytest.approx(2.0)

    def test_no_drawdown(self):
        # Equity only goes up — max drawdown should be 0
        assert self.m["max_drawdown_r"] == pytest.approx(0.0)

    def test_equity_curve_monotone(self):
        curve = self.m["equity_curve"]
        assert curve == [2.0, 4.0, 6.0]


# ---------------------------------------------------------------------------
# 2. All skip
# ---------------------------------------------------------------------------

class TestAllSkip:
    def setup_method(self):
        self.episodes = [
            {"action": SKIP, "reward": 0.0, "level_type": "vwap"},
            {"action": SKIP, "reward": 0.0, "level_type": "pdh"},
            {"action": SKIP, "reward": 0.0, "level_type": "vwap"},
        ]
        self.m = compute_metrics(self.episodes)

    def test_skip_rate_is_one(self):
        assert self.m["skip_rate"] == pytest.approx(1.0)

    def test_trades_taken_zero(self):
        assert self.m["trades_taken"] == 0

    def test_win_rate_zero(self):
        assert self.m["win_rate"] == 0.0

    def test_avg_r_zero(self):
        assert self.m["avg_r"] == pytest.approx(0.0)

    def test_total_r_zero(self):
        assert self.m["total_r"] == pytest.approx(0.0)

    def test_equity_curve_flat(self):
        assert self.m["equity_curve"] == [0.0, 0.0, 0.0]

    def test_profit_factor_inf_no_losses(self):
        # No trades at all → no losses → inf
        assert math.isinf(self.m["profit_factor"])


# ---------------------------------------------------------------------------
# 3. Mixed results
# ---------------------------------------------------------------------------

class TestMixedResults:
    def setup_method(self):
        # 5 episodes: 1 skip, 2 wins, 2 losses
        self.episodes = [
            {"action": SKIP,  "reward":  0.0, "level_type": "vwap"},
            {"action": LONG,  "reward":  2.0, "level_type": "vwap"},
            {"action": SHORT, "reward": -1.0, "level_type": "pdh"},
            {"action": LONG,  "reward":  2.0, "level_type": "vwap"},
            {"action": SHORT, "reward": -1.0, "level_type": "pdh"},
        ]
        self.m = compute_metrics(self.episodes)

    def test_total_episodes(self):
        assert self.m["total_episodes"] == 5

    def test_trades_taken(self):
        assert self.m["trades_taken"] == 4

    def test_skip_rate(self):
        assert self.m["skip_rate"] == pytest.approx(1 / 5)

    def test_win_rate(self):
        # 2 wins out of 4 trades
        assert self.m["win_rate"] == pytest.approx(0.5)

    def test_profit_factor(self):
        # gross_wins = 4.0, gross_losses = 2.0 → PF = 2.0
        assert self.m["profit_factor"] == pytest.approx(2.0)

    def test_total_r(self):
        assert self.m["total_r"] == pytest.approx(2.0)

    def test_avg_r(self):
        assert self.m["avg_r"] == pytest.approx(0.5)

    def test_equity_curve_length(self):
        assert len(self.m["equity_curve"]) == 5

    def test_equity_curve_values(self):
        # skip (0), win+2 (2), loss-1 (1), win+2 (3), loss-1 (2)
        assert self.m["equity_curve"] == pytest.approx([0.0, 2.0, 1.0, 3.0, 2.0])

    def test_max_drawdown(self):
        # Peak at 3.0, trough at 2.0 → drawdown = 1.0
        assert self.m["max_drawdown_r"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 4. Level-type breakdown
# ---------------------------------------------------------------------------

class TestLevelBreakdown:
    def setup_method(self):
        self.episodes = [
            {"action": LONG,  "reward":  2.0, "level_type": "vwap"},
            {"action": SKIP,  "reward":  0.0, "level_type": "vwap"},
            {"action": SHORT, "reward": -1.0, "level_type": "pdh"},
            {"action": LONG,  "reward":  2.0, "level_type": "pdh"},
            {"action": LONG,  "reward":  2.0, "level_type": "val"},
        ]
        self.m = compute_metrics(self.episodes)
        self.lb = self.m["level_breakdown"]

    def test_level_keys_present(self):
        assert set(self.lb.keys()) == {"vwap", "pdh", "val"}

    # --- vwap: 2 total, 1 trade (win), 1 skip ---
    def test_vwap_total(self):
        assert self.lb["vwap"]["total"] == 2

    def test_vwap_trades(self):
        assert self.lb["vwap"]["trades"] == 1

    def test_vwap_skip_rate(self):
        assert self.lb["vwap"]["skip_rate"] == pytest.approx(0.5)

    def test_vwap_win_rate(self):
        assert self.lb["vwap"]["win_rate"] == pytest.approx(1.0)

    def test_vwap_avg_r(self):
        assert self.lb["vwap"]["avg_r"] == pytest.approx(2.0)

    # --- pdh: 2 total, 2 trades (1 win, 1 loss) ---
    def test_pdh_total(self):
        assert self.lb["pdh"]["total"] == 2

    def test_pdh_trades(self):
        assert self.lb["pdh"]["trades"] == 2

    def test_pdh_skip_rate(self):
        assert self.lb["pdh"]["skip_rate"] == pytest.approx(0.0)

    def test_pdh_win_rate(self):
        assert self.lb["pdh"]["win_rate"] == pytest.approx(0.5)

    def test_pdh_avg_r(self):
        # (2.0 + -1.0) / 2 = 0.5
        assert self.lb["pdh"]["avg_r"] == pytest.approx(0.5)

    # --- val: 1 total, 1 trade (win) ---
    def test_val_total(self):
        assert self.lb["val"]["total"] == 1

    def test_val_trades(self):
        assert self.lb["val"]["trades"] == 1

    def test_val_skip_rate(self):
        assert self.lb["val"]["skip_rate"] == pytest.approx(0.0)

    def test_val_win_rate(self):
        assert self.lb["val"]["win_rate"] == pytest.approx(1.0)

    def test_val_avg_r(self):
        assert self.lb["val"]["avg_r"] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# 5. Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_episodes(self):
        m = compute_metrics([])
        assert m["total_episodes"] == 0
        assert m["trades_taken"] == 0
        assert m["equity_curve"] == []
        assert m["level_breakdown"] == {}
        assert math.isinf(m["profit_factor"])

    def test_single_loss(self):
        m = compute_metrics([{"action": LONG, "reward": -1.0, "level_type": "vwap"}])
        assert m["win_rate"] == 0.0
        assert m["profit_factor"] == pytest.approx(0.0)
        assert m["max_drawdown_r"] == pytest.approx(1.0)

    def test_drawdown_calculation(self):
        # Equity: 2, 4, 2, 4, 1 → peak 4, trough 1 → dd = 3
        episodes = [
            {"action": LONG, "reward":  2.0, "level_type": "vwap"},
            {"action": LONG, "reward":  2.0, "level_type": "vwap"},
            {"action": LONG, "reward": -2.0, "level_type": "vwap"},
            {"action": LONG, "reward":  2.0, "level_type": "vwap"},
            {"action": LONG, "reward": -3.0, "level_type": "vwap"},
        ]
        m = compute_metrics(episodes)
        assert m["equity_curve"] == pytest.approx([2.0, 4.0, 2.0, 4.0, 1.0])
        assert m["max_drawdown_r"] == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# 6. print_evaluation_report smoke test
# ---------------------------------------------------------------------------

def test_print_evaluation_report_runs(capsys):
    episodes = [
        {"action": LONG,  "reward":  2.0, "level_type": "vwap"},
        {"action": SKIP,  "reward":  0.0, "level_type": "pdh"},
        {"action": SHORT, "reward": -1.0, "level_type": "pdh"},
    ]
    m = compute_metrics(episodes)
    print_evaluation_report(m)

    captured = capsys.readouterr()
    assert "EVALUATION REPORT" in captured.out
    assert "vwap" in captured.out
    assert "pdh" in captured.out
