"""Tests for add_policy — pyramid / compound-into-winners rule."""

from __future__ import annotations

import pytest

from src.rl.add_policy import (
    ADD_FRACTION,
    MAX_POSITION_MULT,
    MIN_CONFIDENCE,
    MIN_UNREALIZED_R,
    check_pyramid,
)


# Helper — returns a "ready to pyramid" baseline decision context
def _ready_kwargs(**overrides):
    kwargs = dict(
        pos_side="long",
        pos_size=1.0,
        unrealized_R=0.8,  # clearly in profit
        action_direction=1,  # aligned with long
        base_size_mult=1.0,
        composite_confidence=0.8,
    )
    kwargs.update(overrides)
    return kwargs


class TestNoPyramidScenarios:
    def test_flat_position_no_pyramid(self):
        d = check_pyramid(**_ready_kwargs(pos_side="flat"))
        assert d.should_add is False
        assert d.reason == "no_position"
        assert d.add_size == 0.0

    def test_opposite_direction_no_pyramid(self):
        # Long position, but signal says short → flip, not pyramid
        d = check_pyramid(**_ready_kwargs(pos_side="long", action_direction=-1))
        assert d.should_add is False
        assert d.reason == "opposite_direction"

    def test_low_confidence_no_pyramid(self):
        d = check_pyramid(**_ready_kwargs(composite_confidence=0.5))
        assert d.should_add is False
        assert d.reason == "low_confidence"

    def test_losing_position_no_pyramid(self):
        d = check_pyramid(**_ready_kwargs(unrealized_R=-0.5))
        assert d.should_add is False
        assert d.reason == "no_profit_cushion"

    def test_thin_profit_no_pyramid(self):
        # Barely in profit — below MIN_UNREALIZED_R threshold
        d = check_pyramid(**_ready_kwargs(unrealized_R=0.1))
        assert d.should_add is False
        assert d.reason == "no_profit_cushion"

    def test_size_at_cap_no_pyramid(self):
        d = check_pyramid(**_ready_kwargs(pos_size=MAX_POSITION_MULT))
        assert d.should_add is False
        assert d.reason == "size_cap"


class TestPyramidFires:
    def test_all_conditions_met_fires(self):
        d = check_pyramid(**_ready_kwargs())
        assert d.should_add is True
        assert d.reason == "pyramid_add"
        assert d.add_size == pytest.approx(1.0 * ADD_FRACTION)

    def test_short_aligned_fires(self):
        d = check_pyramid(**_ready_kwargs(pos_side="short", action_direction=-1))
        assert d.should_add is True
        assert d.reason == "pyramid_add"

    def test_high_confidence_larger_base_larger_add(self):
        small = check_pyramid(**_ready_kwargs(base_size_mult=0.6))
        large = check_pyramid(**_ready_kwargs(base_size_mult=1.5))
        assert small.add_size < large.add_size
        assert small.add_size == pytest.approx(0.6 * ADD_FRACTION)
        assert large.add_size == pytest.approx(1.5 * ADD_FRACTION)


class TestHeadroomClipping:
    def test_add_clipped_to_cap(self):
        # Position already near the cap — add should be clipped
        d = check_pyramid(**_ready_kwargs(pos_size=MAX_POSITION_MULT - 0.2, base_size_mult=1.0))
        # Raw add would be 0.5, but headroom is only 0.2
        assert d.should_add is True
        assert d.add_size == pytest.approx(0.2)

    def test_zero_headroom_no_add(self):
        d = check_pyramid(**_ready_kwargs(pos_size=MAX_POSITION_MULT - 0.01))
        # Headroom is 0.01, raw add would be 0.5 → clipped to 0.01 (still fires)
        assert d.should_add is True
        assert d.add_size == pytest.approx(0.01)


class TestBoundaries:
    def test_exactly_at_min_confidence_fires(self):
        d = check_pyramid(**_ready_kwargs(composite_confidence=MIN_CONFIDENCE))
        assert d.should_add is True

    def test_just_below_min_confidence_no_fire(self):
        d = check_pyramid(**_ready_kwargs(composite_confidence=MIN_CONFIDENCE - 0.001))
        assert d.should_add is False
        assert d.reason == "low_confidence"

    def test_exactly_at_min_unrealized_R_fires(self):
        d = check_pyramid(**_ready_kwargs(unrealized_R=MIN_UNREALIZED_R))
        assert d.should_add is True


class TestCustomThresholds:
    def test_looser_min_confidence(self):
        d = check_pyramid(**_ready_kwargs(composite_confidence=0.45), min_confidence=0.4)
        assert d.should_add is True

    def test_tighter_min_unrealized_R(self):
        d = check_pyramid(**_ready_kwargs(unrealized_R=0.4), min_unrealized_r=0.5)
        assert d.should_add is False

    def test_smaller_add_fraction_gives_smaller_add(self):
        d = check_pyramid(**_ready_kwargs(), add_fraction=0.25)
        assert d.add_size == pytest.approx(1.0 * 0.25)
