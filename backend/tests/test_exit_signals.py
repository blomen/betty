"""Tests for exit_signals — framework reversal-confirmation detection."""

from __future__ import annotations

import numpy as np

from src.rl.exit_signals import count_reversal_signals, should_exit_on_reversal


def _empty_obs() -> np.ndarray:
    return np.zeros(302, dtype=np.float32)


def _set(obs: np.ndarray, idx: int, value: float) -> np.ndarray:
    obs[idx] = value
    return obs


# Index constants mirroring exit_signals._OF_*/_MICRO_*
_OF_CVD_SLOPE = 31 + 4  # orderflow[4]
_OF_STACKED_IMB_SIGNED = 31 + 13  # orderflow[13]
_OF_STACKED_IMB_FLIP = 31 + 15  # orderflow[15]
_OF_ABSORPTION_COUNT = 31 + 16  # orderflow[16]
_OF_ABSORPTION_STRENGTH = 31 + 17  # orderflow[17]
_MICRO_BIG_TRADE_COUNT = 248 + 10  # micro[10]
_MICRO_LAST5_DELTA = 248 + 12  # micro[12]


class TestEmptyInputs:
    def test_zero_direction_no_signals(self):
        obs = _empty_obs()
        sig = count_reversal_signals(obs, trade_direction=0)
        assert sig.fired_count == 0

    def test_none_obs_no_signals(self):
        sig = count_reversal_signals(None, trade_direction=1)
        assert sig.fired_count == 0

    def test_short_obs_no_signals(self):
        sig = count_reversal_signals(np.zeros(50, dtype=np.float32), trade_direction=1)
        assert sig.fired_count == 0

    def test_empty_obs_zero_signals(self):
        sig = count_reversal_signals(_empty_obs(), trade_direction=1)
        assert sig.fired_count == 0


class TestCvdFlip:
    def test_cvd_against_long_strong_fires(self):
        obs = _empty_obs()
        _set(obs, _OF_CVD_SLOPE, -0.5)  # strongly negative — against a long
        sig = count_reversal_signals(obs, trade_direction=1)
        assert sig.cvd_flip is True

    def test_cvd_with_long_does_not_fire(self):
        obs = _empty_obs()
        _set(obs, _OF_CVD_SLOPE, 0.5)  # positive — supports long
        sig = count_reversal_signals(obs, trade_direction=1)
        assert sig.cvd_flip is False

    def test_cvd_weak_does_not_fire(self):
        obs = _empty_obs()
        _set(obs, _OF_CVD_SLOPE, -0.1)  # negative but below 0.3 magnitude
        sig = count_reversal_signals(obs, trade_direction=1)
        assert sig.cvd_flip is False

    def test_cvd_against_short_strong_fires(self):
        obs = _empty_obs()
        _set(obs, _OF_CVD_SLOPE, 0.5)  # positive — against a short
        sig = count_reversal_signals(obs, trade_direction=-1)
        assert sig.cvd_flip is True


class TestAbsorptionAtTarget:
    def test_strong_absorption_fires(self):
        obs = _empty_obs()
        _set(obs, _OF_ABSORPTION_COUNT, 0.6)
        _set(obs, _OF_ABSORPTION_STRENGTH, 0.7)
        sig = count_reversal_signals(obs, trade_direction=1)
        assert sig.absorption_at_target is True

    def test_weak_absorption_does_not_fire(self):
        obs = _empty_obs()
        _set(obs, _OF_ABSORPTION_COUNT, 0.2)
        _set(obs, _OF_ABSORPTION_STRENGTH, 0.2)
        sig = count_reversal_signals(obs, trade_direction=1)
        assert sig.absorption_at_target is False

    def test_count_but_no_strength_does_not_fire(self):
        obs = _empty_obs()
        _set(obs, _OF_ABSORPTION_COUNT, 0.8)
        _set(obs, _OF_ABSORPTION_STRENGTH, 0.1)
        sig = count_reversal_signals(obs, trade_direction=1)
        assert sig.absorption_at_target is False


class TestImbalanceFlip:
    def test_flip_against_long_fires(self):
        obs = _empty_obs()
        _set(obs, _OF_STACKED_IMB_SIGNED, -0.4)  # negative = sellers, against long
        _set(obs, _OF_STACKED_IMB_FLIP, 0.5)  # flip indicator active
        sig = count_reversal_signals(obs, trade_direction=1)
        assert sig.imbalance_flip is True

    def test_no_flip_indicator_does_not_fire(self):
        obs = _empty_obs()
        _set(obs, _OF_STACKED_IMB_SIGNED, -0.4)
        _set(obs, _OF_STACKED_IMB_FLIP, 0.05)  # no flip
        sig = count_reversal_signals(obs, trade_direction=1)
        assert sig.imbalance_flip is False

    def test_weak_imbalance_against_does_not_fire(self):
        obs = _empty_obs()
        _set(obs, _OF_STACKED_IMB_SIGNED, -0.1)  # too weak
        _set(obs, _OF_STACKED_IMB_FLIP, 0.5)
        sig = count_reversal_signals(obs, trade_direction=1)
        assert sig.imbalance_flip is False


class TestBigTradesAgainst:
    def test_aggression_flip_fires(self):
        obs = _empty_obs()
        _set(obs, _MICRO_BIG_TRADE_COUNT, 0.6)  # many big trades
        _set(obs, _MICRO_LAST5_DELTA, -0.4)  # last5 delta against long
        sig = count_reversal_signals(obs, trade_direction=1)
        assert sig.big_trades_against is True

    def test_no_big_trades_does_not_fire(self):
        obs = _empty_obs()
        _set(obs, _MICRO_BIG_TRADE_COUNT, 0.1)
        _set(obs, _MICRO_LAST5_DELTA, -0.8)
        sig = count_reversal_signals(obs, trade_direction=1)
        assert sig.big_trades_against is False


class TestCombinedAndShouldExit:
    def test_all_four_fire(self):
        obs = _empty_obs()
        _set(obs, _OF_CVD_SLOPE, -0.5)
        _set(obs, _OF_ABSORPTION_COUNT, 0.6)
        _set(obs, _OF_ABSORPTION_STRENGTH, 0.7)
        _set(obs, _OF_STACKED_IMB_SIGNED, -0.4)
        _set(obs, _OF_STACKED_IMB_FLIP, 0.5)
        _set(obs, _MICRO_BIG_TRADE_COUNT, 0.6)
        _set(obs, _MICRO_LAST5_DELTA, -0.4)
        sig = count_reversal_signals(obs, trade_direction=1)
        assert sig.fired_count == 4
        assert sig.should_exit_at_threshold["2_signals"] is True
        assert sig.should_exit_at_threshold["4_signals"] is True

    def test_should_exit_default_threshold_2(self):
        obs = _empty_obs()
        _set(obs, _OF_CVD_SLOPE, -0.5)  # 1 signal only
        assert should_exit_on_reversal(obs, trade_direction=1, min_signals=2) is False

        _set(obs, _OF_ABSORPTION_COUNT, 0.6)
        _set(obs, _OF_ABSORPTION_STRENGTH, 0.7)  # 2 signals now
        assert should_exit_on_reversal(obs, trade_direction=1, min_signals=2) is True

    def test_aggressive_exit_fires_on_one(self):
        obs = _empty_obs()
        _set(obs, _OF_CVD_SLOPE, -0.5)
        assert should_exit_on_reversal(obs, trade_direction=1, min_signals=1) is True

    def test_conservative_exit_needs_three(self):
        obs = _empty_obs()
        _set(obs, _OF_CVD_SLOPE, -0.5)
        _set(obs, _OF_ABSORPTION_COUNT, 0.6)
        _set(obs, _OF_ABSORPTION_STRENGTH, 0.7)
        # Only 2 fire
        assert should_exit_on_reversal(obs, trade_direction=1, min_signals=3) is False
