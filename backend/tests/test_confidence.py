"""Tests for composite confidence scoring (backend/src/rl/confidence.py)."""
from __future__ import annotations

import numpy as np
import pytest

from src.rl.confidence import (
    compute_composite_confidence,
    size_multiplier,
    _compute_narrative_alignment,
    _compute_micro_alignment,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _max_signals(trade_direction: int = 1) -> dict:
    """Return all signals at their maximum (most confident) values."""
    # setup_probs: one setup at max probability
    setup_probs = np.zeros(8, dtype=np.float32)
    setup_probs[0] = 1.0

    # narrative: bullish signals (positive values for a long trade)
    narrative = np.ones(15, dtype=np.float32) * 0.9

    # trigger_forecast: high confidence
    trigger_forecast = np.zeros(8, dtype=np.float32)
    trigger_forecast[0] = 0.95

    # q_spread: large spread = very decisive
    q_spread = 3.0

    # zone: high quality
    zone_confluence_weight = 1.0
    zone_member_count = 5

    # micro: smooth acceleration into the level (continuation)
    micro = np.zeros(20, dtype=np.float32)
    micro[1] = 0.8   # approach_accel > 0 (accelerating)
    micro[11] = 0.9  # last5_velocity > 0 (fast)
    micro[9] = 0.1   # reversal_count_norm < 0.3 (smooth)
    micro[19] = 0.5  # last5_acceleration > 0

    return dict(
        setup_probs=setup_probs,
        narrative=narrative,
        trigger_forecast=trigger_forecast,
        q_spread=q_spread,
        zone_confluence_weight=zone_confluence_weight,
        zone_member_count=zone_member_count,
        micro_features=micro,
        trade_direction=trade_direction,
    )


def _min_signals(trade_direction: int = 1) -> dict:
    """Return all signals at their minimum (least confident) values."""
    setup_probs = np.zeros(8, dtype=np.float32)  # all zero
    narrative = np.zeros(15, dtype=np.float32)   # neutral
    trigger_forecast = np.zeros(8, dtype=np.float32)
    q_spread = 0.0
    zone_confluence_weight = 0.0
    zone_member_count = 1

    # micro: decelerating approach but trade is continuation — signals disagree
    micro = np.zeros(20, dtype=np.float32)
    micro[1] = -0.5   # accel < 0 (decelerating)
    micro[11] = 0.0
    micro[9] = 0.5    # reversal_count > 0.3 (choppy)
    micro[19] = -0.3

    return dict(
        setup_probs=setup_probs,
        narrative=narrative,
        trigger_forecast=trigger_forecast,
        q_spread=q_spread,
        zone_confluence_weight=zone_confluence_weight,
        zone_member_count=zone_member_count,
        micro_features=micro,
        trade_direction=trade_direction,
    )


# ---------------------------------------------------------------------------
# Composite confidence tests
# ---------------------------------------------------------------------------

class TestCompositeConfidence:
    def test_max_signals_near_one(self):
        """All signals at maximum should produce a composite near 1.0."""
        signals = _max_signals(trade_direction=1)
        score = compute_composite_confidence(**signals)
        assert score > 0.85, f"Expected >0.85, got {score:.4f}"
        assert score <= 1.0

    def test_min_signals_near_zero(self):
        """All signals at zero should produce a composite near 0."""
        signals = _min_signals(trade_direction=1)
        score = compute_composite_confidence(**signals)
        # With all zeros, only narrative_alignment contributes (neutral ~0.5)
        # and micro_alignment contributes (reversal approach, checking 3 signals)
        # The actual minimum achievable is bounded by the neutral signals
        assert score < 0.35, f"Expected <0.35, got {score:.4f}"

    def test_composite_always_in_unit_interval(self):
        """Composite score must always be in [0, 1]."""
        rng = np.random.default_rng(42)
        for _ in range(100):
            setup = rng.random(8).astype(np.float32)
            setup /= setup.sum() + 1e-9  # normalize to sum ≤ 1
            narrative = rng.uniform(-1, 1, 15).astype(np.float32)
            trigger = rng.random(8).astype(np.float32)
            q_spread = float(rng.uniform(0, 5))
            zone_w = float(rng.uniform(0, 1))
            zone_m = int(rng.integers(1, 10))
            micro = rng.uniform(-1, 1, 20).astype(np.float32)
            direction = int(rng.choice([-1, 0, 1]))

            score = compute_composite_confidence(
                setup_probs=setup,
                narrative=narrative,
                trigger_forecast=trigger,
                q_spread=q_spread,
                zone_confluence_weight=zone_w,
                zone_member_count=zone_m,
                micro_features=micro,
                trade_direction=direction,
            )
            assert 0.0 <= score <= 1.0, f"Out of range: {score}"

    def test_skip_direction_is_neutral(self):
        """trade_direction=0 should give neutral alignment contributions."""
        signals = _max_signals(trade_direction=0)
        score_skip = compute_composite_confidence(**signals)

        signals_long = _max_signals(trade_direction=1)
        score_long = compute_composite_confidence(**signals_long)

        # Skip should be lower than bullish signals with bullish narrative
        assert score_skip < score_long


# ---------------------------------------------------------------------------
# Size multiplier tier tests
# ---------------------------------------------------------------------------

class TestSizeMultiplier:
    def test_a_plus_setup(self):
        assert size_multiplier(0.9) == 1.5
        assert size_multiplier(1.0) == 1.5
        assert size_multiplier(0.85) == 1.5

    def test_a_setup(self):
        assert size_multiplier(0.75) == 1.0
        assert size_multiplier(0.70) == 1.0

    def test_b_setup(self):
        assert size_multiplier(0.6) == 0.6
        assert size_multiplier(0.50) == 0.6

    def test_c_setup(self):
        assert size_multiplier(0.4) == 0.3
        assert size_multiplier(0.30) == 0.3

    def test_skip(self):
        assert size_multiplier(0.1) == 0.0
        assert size_multiplier(0.0) == 0.0
        assert size_multiplier(0.29) == 0.0

    def test_boundary_exactly_at_thresholds(self):
        """Boundaries should go to the upper tier (>=)."""
        assert size_multiplier(0.85) == 1.5  # >= 0.85
        assert size_multiplier(0.70) == 1.0  # >= 0.70
        assert size_multiplier(0.50) == 0.6  # >= 0.50
        assert size_multiplier(0.30) == 0.3  # >= 0.30


# ---------------------------------------------------------------------------
# Narrative alignment tests
# ---------------------------------------------------------------------------

class TestNarrativeAlignment:
    def test_long_with_bullish_signals_high_alignment(self):
        """Long trade with all bullish narrative signals → alignment = 1.0."""
        narrative = np.zeros(15, dtype=np.float32)
        narrative[0] = 0.8   # regime_score bullish
        narrative[1] = 0.7   # htf_trend up
        narrative[8] = 0.6   # initiative_direction buying
        narrative[3] = 0.5   # day_type trend

        alignment = _compute_narrative_alignment(narrative, trade_direction=1)
        assert alignment == 1.0, f"Expected 1.0, got {alignment}"

    def test_long_with_bearish_signals_low_alignment(self):
        """Long trade with all bearish narrative signals → alignment = 0.0."""
        narrative = np.zeros(15, dtype=np.float32)
        narrative[0] = -0.8  # regime_score bearish
        narrative[1] = -0.7  # htf_trend down
        narrative[8] = -0.6  # initiative_direction selling
        narrative[3] = -0.5  # day_type non-trend bearish

        alignment = _compute_narrative_alignment(narrative, trade_direction=1)
        assert alignment == 0.0, f"Expected 0.0, got {alignment}"

    def test_short_with_bearish_signals_high_alignment(self):
        """Short trade with all bearish narrative signals → alignment = 1.0."""
        narrative = np.zeros(15, dtype=np.float32)
        narrative[0] = -0.8
        narrative[1] = -0.7
        narrative[8] = -0.6
        narrative[3] = -0.5

        alignment = _compute_narrative_alignment(narrative, trade_direction=-1)
        assert alignment == 1.0, f"Expected 1.0, got {alignment}"

    def test_skip_direction_neutral(self):
        """trade_direction=0 → neutral alignment = 0.5."""
        narrative = np.ones(15, dtype=np.float32)
        alignment = _compute_narrative_alignment(narrative, trade_direction=0)
        assert alignment == 0.5

    def test_partial_agreement(self):
        """2 out of 4 signals agree → 0.5."""
        narrative = np.zeros(15, dtype=np.float32)
        narrative[0] = 0.5   # agrees with long
        narrative[1] = 0.5   # agrees with long
        narrative[8] = -0.5  # disagrees
        narrative[3] = -0.5  # disagrees

        alignment = _compute_narrative_alignment(narrative, trade_direction=1)
        assert alignment == 0.5, f"Expected 0.5, got {alignment}"


# ---------------------------------------------------------------------------
# Micro alignment tests
# ---------------------------------------------------------------------------

class TestMicroAlignment:
    def test_skip_direction_neutral(self):
        micro = np.zeros(20, dtype=np.float32)
        assert _compute_micro_alignment(micro, trade_direction=0) == 0.5

    def test_reversal_approach_all_confirming(self):
        """Decelerating approach with choppy price → 3/3 reversal signals."""
        micro = np.zeros(20, dtype=np.float32)
        micro[1] = -0.5   # approach_accel < 0 (decelerating, reversal approach)
        micro[19] = -0.3  # last5_acceleration < 0
        micro[9] = 0.7    # reversal_count_norm > 0.5

        score = _compute_micro_alignment(micro, trade_direction=1)
        assert score == 1.0, f"Expected 1.0, got {score}"

    def test_continuation_approach_all_confirming(self):
        """Accelerating approach, fast, smooth → 3/3 continuation signals."""
        micro = np.zeros(20, dtype=np.float32)
        micro[1] = 0.5    # approach_accel > 0 (continuation approach)
        micro[11] = 0.8   # last5_velocity > 0
        micro[9] = 0.1    # reversal_count_norm < 0.3

        score = _compute_micro_alignment(micro, trade_direction=1)
        assert score == 1.0, f"Expected 1.0, got {score}"
