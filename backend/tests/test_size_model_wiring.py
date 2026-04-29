"""Tests for SizeModel multiplier wiring into _execute_entry's sizing math.

Pure logic tests of the size scaling rule. We unit-test the math directly
rather than spinning up the whole adapter — the integration of size into the
order placement path is exercised by the existing broker_adapter tests.
"""

from __future__ import annotations

# Mirrors the rule inside broker_adapter._execute_entry. If the formula in
# the adapter changes, this helper changes alongside; the tests pin the
# behaviour we want.


def scale_size(base_size: int, size_mult: float, max_position: int) -> int | None:
    """Apply SizeModel multiplier to base_size; return None for skip."""
    if size_mult <= 0.0:
        return None
    scaled = base_size * size_mult
    return max(1, min(int(scaled + 0.5), max_position))


def test_skip_tier_returns_none():
    """size_mult == 0.0 → skip the trade entirely."""
    assert scale_size(base_size=1, size_mult=0.0, max_position=2) is None
    assert scale_size(base_size=2, size_mult=0.0, max_position=4) is None


def test_default_multiplier_keeps_base_size():
    """size_mult == 1.0 produces no change relative to legacy behaviour."""
    assert scale_size(base_size=1, size_mult=1.0, max_position=2) == 1
    assert scale_size(base_size=2, size_mult=1.0, max_position=4) == 2


def test_high_tier_doubles_size_within_cap():
    """size_mult == 1.5 with base=1 → 1.5 → rounds to 2."""
    assert scale_size(base_size=1, size_mult=1.5, max_position=2) == 2


def test_high_tier_hits_cap():
    """size_mult == 1.5 with base=2 → 3.0 → cap at max_position."""
    assert scale_size(base_size=2, size_mult=1.5, max_position=2) == 2
    assert scale_size(base_size=2, size_mult=1.5, max_position=4) == 3


def test_low_tiers_floor_to_one():
    """size_mult == 0.3/0.6 with base=1 still floors to 1 contract."""
    assert scale_size(base_size=1, size_mult=0.3, max_position=2) == 1
    assert scale_size(base_size=1, size_mult=0.6, max_position=2) == 1


def test_low_tier_with_larger_base_can_drop_size():
    """size_mult == 0.3 with base=4 → 1.2 → 1; size_mult == 0.6 with base=4 → 2.4 → 2."""
    assert scale_size(base_size=4, size_mult=0.3, max_position=4) == 1
    assert scale_size(base_size=4, size_mult=0.6, max_position=4) == 2


def test_round_half_up():
    """Boundary: 0.5 rounds to 1, 1.5 rounds to 2 (with cap headroom)."""
    assert scale_size(base_size=1, size_mult=0.5, max_position=2) == 1
    assert scale_size(base_size=3, size_mult=0.5, max_position=4) == 2  # 1.5 → 2
