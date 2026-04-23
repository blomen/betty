"""Tests for stop_policy — confidence + regime + structural-anchor adjustments."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.rl.config import LevelType
from src.rl.stop_policy import (
    apply_stop_adjustments,
    compute_confidence_scale,
    compute_regime_scale,
    compute_structural_anchor_ticks,
)


@dataclass
class _FakeMember:
    level_type: LevelType
    price: float


class TestConfidenceScale:
    def test_high_confidence_tightens(self):
        assert compute_confidence_scale(1.0) < compute_confidence_scale(0.5)

    def test_low_confidence_widens(self):
        assert compute_confidence_scale(0.0) > compute_confidence_scale(0.5)

    def test_bounds_at_zero_and_one(self):
        assert compute_confidence_scale(0.0) == pytest.approx(1.4)
        assert compute_confidence_scale(1.0) == pytest.approx(0.8)

    def test_mid_is_linear(self):
        assert compute_confidence_scale(0.5) == pytest.approx(1.1)

    def test_clips_out_of_range(self):
        assert compute_confidence_scale(-1.0) == pytest.approx(1.4)
        assert compute_confidence_scale(2.0) == pytest.approx(0.8)


class TestRegimeScale:
    def test_defensive_widens(self):
        # Low risk_modulation → defensive regime → wider stop
        assert compute_regime_scale(0.5) > compute_regime_scale(1.0)

    def test_aggressive_tightens(self):
        assert compute_regime_scale(1.5) < compute_regime_scale(1.0)

    def test_neutral_is_between(self):
        neutral = compute_regime_scale(1.0)
        assert 0.9 <= neutral <= 1.2

    def test_bounds(self):
        assert compute_regime_scale(0.5) == pytest.approx(1.2)
        assert compute_regime_scale(1.5) == pytest.approx(0.9)


class TestStructuralAnchor:
    def test_no_structure_returns_none(self):
        members = [_FakeMember(LevelType.VWAP, 4500.0)]  # VWAP is not structural
        assert compute_structural_anchor_ticks(members, 1, 4505.0) is None

    def test_long_uses_support_below(self):
        members = [
            _FakeMember(LevelType.DAILY_SWING_LOW, 4495.0),  # 10pt below entry
            _FakeMember(LevelType.DAILY_SWING_HIGH, 4520.0),  # above entry — ignored
        ]
        # Entry 4505, long: stop goes below. Nearest support = 4495 → distance 10pt = 40 ticks + 2 buffer = 42
        ticks = compute_structural_anchor_ticks(members, 1, 4505.0, buffer_ticks=2.0)
        assert ticks == pytest.approx(42.0)

    def test_short_uses_resistance_above(self):
        members = [
            _FakeMember(LevelType.WEEKLY_SWING_HIGH, 4510.0),
            _FakeMember(LevelType.DAILY_SWING_LOW, 4495.0),
        ]
        # Entry 4500, short: stop goes above. Nearest resistance = 4510 → 10pt = 40t + 2 = 42
        ticks = compute_structural_anchor_ticks(members, -1, 4500.0, buffer_ticks=2.0)
        assert ticks == pytest.approx(42.0)

    def test_picks_closest_among_multiple(self):
        members = [
            _FakeMember(LevelType.DAILY_SWING_LOW, 4480.0),  # 20pt below — further
            _FakeMember(LevelType.PDL, 4498.0),  # 2pt below — closest
            _FakeMember(LevelType.WEEKLY_SWING_LOW, 4470.0),  # further
        ]
        ticks = compute_structural_anchor_ticks(members, 1, 4500.0, buffer_ticks=2.0)
        # Closest support = 4498 → distance 2pt = 8 ticks + 2 buffer = 10
        assert ticks == pytest.approx(10.0)

    def test_no_trade_direction(self):
        members = [_FakeMember(LevelType.DAILY_SWING_LOW, 4495.0)]
        assert compute_structural_anchor_ticks(members, 0, 4500.0) is None


class TestApplyStopAdjustments:
    def test_returns_all_components(self):
        out = apply_stop_adjustments(
            base_stop_ticks=20.0,
            composite_confidence=0.5,
            risk_modulation=1.0,
        )
        assert {
            "base_ticks",
            "conf_scale",
            "regime_scale",
            "scaled_ticks",
            "structural_anchor_ticks",
            "final_ticks",
        }.issubset(out)
        assert out["base_ticks"] == 20.0

    def test_high_conf_high_regime_tightens(self):
        out = apply_stop_adjustments(
            base_stop_ticks=20.0,
            composite_confidence=1.0,  # 0.8x
            risk_modulation=1.5,  # 0.9x
        )
        # 20 * 0.8 * 0.9 = 14.4 ticks
        assert out["scaled_ticks"] == pytest.approx(14.4, abs=0.01)
        assert out["final_ticks"] == pytest.approx(14.4, abs=0.01)

    def test_low_conf_defensive_widens(self):
        out = apply_stop_adjustments(
            base_stop_ticks=20.0,
            composite_confidence=0.0,  # 1.4x
            risk_modulation=0.5,  # 1.2x
        )
        # 20 * 1.4 * 1.2 = 33.6 ticks
        assert out["scaled_ticks"] == pytest.approx(33.6, abs=0.01)

    def test_structural_anchor_wider_takes_precedence(self):
        members = [_FakeMember(LevelType.DAILY_SWING_LOW, 4480.0)]
        # Entry 4500, long, 20pt below = 80 ticks + 2 buffer = 82
        out = apply_stop_adjustments(
            base_stop_ticks=20.0,
            composite_confidence=1.0,  # 0.8 × 0.9 = 14.4
            risk_modulation=1.5,
            zone_members=members,
            trade_direction=1,
            entry_price=4500.0,
        )
        assert out["structural_anchor_ticks"] == pytest.approx(82.0, abs=0.01)
        # final = max(scaled=14.4, anchor=82) = 82 but clipped to 50 ceiling
        assert out["final_ticks"] == pytest.approx(50.0, abs=0.01)

    def test_ceiling_and_floor_clip(self):
        # Huge base stop → clipped to 50
        huge = apply_stop_adjustments(
            base_stop_ticks=100.0,
            composite_confidence=0.0,
            risk_modulation=0.5,
        )
        assert huge["final_ticks"] == 50.0

        # Tiny base stop → floor at 6
        tiny = apply_stop_adjustments(
            base_stop_ticks=2.0,
            composite_confidence=1.0,
            risk_modulation=1.5,
        )
        assert tiny["final_ticks"] == 6.0
