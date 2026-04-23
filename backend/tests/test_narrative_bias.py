"""Tests for the narrative-bias head (rule-based macro/regime risk knobs)."""

from __future__ import annotations

import numpy as np
import pytest

from src.rl.narrative_bias import (
    NarrativeBias,
    apply_bias_to_confidence,
    apply_risk_modulation_to_size,
    compute_bias_score,
    compute_narrative_bias,
    compute_risk_modulation,
)


def _bullish() -> np.ndarray:
    n = np.zeros(18, dtype=np.float32)
    n[0] = 0.8  # regime_score bullish
    n[1] = 0.7  # htf_trend up
    n[2] = -0.5  # low vol
    n[3] = 0.5  # trend day
    n[4] = 1.0  # OD opening
    n[13] = 0.6  # trend_alignment
    n[17] = 0.7  # trend_conviction
    return n


def _bearish() -> np.ndarray:
    n = np.zeros(18, dtype=np.float32)
    n[0] = -0.8
    n[1] = -0.7
    n[2] = 0.6  # high vol
    n[3] = -0.5  # non-trend
    n[4] = -0.5  # ORR
    n[13] = -0.6
    n[17] = -0.6
    return n


def _hostile() -> np.ndarray:
    n = np.zeros(18, dtype=np.float32)
    n[2] = 0.9  # high vol
    n[3] = -0.9  # non-trend balance
    n[4] = -0.5  # ORR
    return n


def _friendly() -> np.ndarray:
    n = np.zeros(18, dtype=np.float32)
    n[2] = -0.7  # low vol
    n[3] = 0.8  # trend
    n[4] = 1.0  # OD
    n[17] = 0.8  # high conviction
    return n


class TestBiasScore:
    def test_neutral_zero_input(self):
        assert compute_bias_score(np.zeros(18, dtype=np.float32)) == pytest.approx(0.0)

    def test_bullish_positive(self):
        assert compute_bias_score(_bullish()) > 0.5

    def test_bearish_negative(self):
        assert compute_bias_score(_bearish()) < -0.5

    def test_clipped_to_unit_interval(self):
        # Build a maxed-out vector
        n = np.ones(18, dtype=np.float32)
        b = compute_bias_score(n)
        assert -1.0 <= b <= 1.0

    def test_handles_short_vector(self):
        # Edge case: callers may pass partial vectors
        assert compute_bias_score(np.zeros(5, dtype=np.float32)) == 0.0
        assert compute_bias_score(None) == 0.0


class TestRiskModulation:
    def test_neutral_returns_one(self):
        assert compute_risk_modulation(np.zeros(18, dtype=np.float32)) == pytest.approx(1.0)

    def test_friendly_above_one(self):
        mod = compute_risk_modulation(_friendly())
        assert mod > 1.0
        assert mod <= 1.5

    def test_hostile_below_one(self):
        mod = compute_risk_modulation(_hostile())
        assert mod < 1.0
        assert mod >= 0.5

    def test_bounds_respected(self):
        # Even extreme values must stay in [0.5, 1.5]
        n_extreme_friendly = np.ones(18, dtype=np.float32)
        n_extreme_hostile = -np.ones(18, dtype=np.float32)
        assert 0.5 <= compute_risk_modulation(n_extreme_friendly) <= 1.5
        assert 0.5 <= compute_risk_modulation(n_extreme_hostile) <= 1.5


class TestComputeNarrativeBias:
    def test_returns_dataclass(self):
        nb = compute_narrative_bias(_bullish(), trade_direction=1)
        assert isinstance(nb, NarrativeBias)
        assert -1.0 <= nb.bias_score <= 1.0
        assert 0.5 <= nb.risk_modulation <= 1.5
        assert -1.0 <= nb.bias_agreement <= 1.0
        assert "regime_score" in nb.components

    def test_long_with_bullish_bias_positive_agreement(self):
        nb = compute_narrative_bias(_bullish(), trade_direction=1)
        assert nb.bias_agreement > 0.0

    def test_long_with_bearish_bias_negative_agreement(self):
        nb = compute_narrative_bias(_bearish(), trade_direction=1)
        assert nb.bias_agreement < 0.0

    def test_short_with_bearish_bias_positive_agreement(self):
        nb = compute_narrative_bias(_bearish(), trade_direction=-1)
        assert nb.bias_agreement > 0.0

    def test_skip_direction_zero_agreement(self):
        nb = compute_narrative_bias(_bullish(), trade_direction=0)
        assert nb.bias_agreement == 0.0


class TestApplyBiasToConfidence:
    def test_neutral_agreement_no_change(self):
        assert apply_bias_to_confidence(0.6, bias_agreement=0.0) == pytest.approx(0.6)

    def test_positive_agreement_boosts(self):
        out = apply_bias_to_confidence(0.6, bias_agreement=1.0, boost_strength=0.15)
        assert out > 0.6
        assert out <= 1.0

    def test_negative_agreement_reduces(self):
        out = apply_bias_to_confidence(0.6, bias_agreement=-1.0, boost_strength=0.15)
        assert out < 0.6
        assert out >= 0.0

    def test_clipped_to_unit_interval(self):
        assert apply_bias_to_confidence(0.95, bias_agreement=1.0, boost_strength=0.5) <= 1.0
        assert apply_bias_to_confidence(0.05, bias_agreement=-1.0, boost_strength=0.5) >= 0.0


class TestApplyRiskModulationToSize:
    def test_neutral_modulation_no_change(self):
        assert apply_risk_modulation_to_size(1.0, 1.0) == pytest.approx(1.0)

    def test_friendly_modulation_increases(self):
        assert apply_risk_modulation_to_size(1.0, 1.5) == pytest.approx(1.5)

    def test_hostile_modulation_decreases(self):
        assert apply_risk_modulation_to_size(1.0, 0.5) == pytest.approx(0.5)

    def test_zero_size_stays_zero(self):
        assert apply_risk_modulation_to_size(0.0, 1.5) == pytest.approx(0.0)
