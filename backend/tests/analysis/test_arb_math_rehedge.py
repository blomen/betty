"""Unit tests for rehedge sizing helpers in local.mirror.arb_math.

Pure-function tests — no DB, no I/O. The local.mirror import path works
because the repo root is on sys.path during pytest (see backend/tests/conftest.py).
"""

import pytest
from local.mirror.arb_math import (
    brackets_key_number,
    equalise_payouts,
)


class TestEqualisePayouts:
    def test_equal_odds_equal_stakes(self):
        # Same odds → same stake on each side to equalise payout.
        assert equalise_payouts(stake_a_base=100.0, odds_a=2.0, odds_b=2.0) == pytest.approx(100.0)

    def test_higher_b_odds_needs_smaller_b_stake(self):
        # Side A: 100 * 2.0 = 200 payout. Side B at 4.0 needs 50 to also pay 200.
        assert equalise_payouts(stake_a_base=100.0, odds_a=2.0, odds_b=4.0) == pytest.approx(50.0)

    def test_lower_b_odds_needs_larger_b_stake(self):
        # Side A: 100 * 3.0 = 300 payout. Side B at 1.5 needs 200 to also pay 300.
        assert equalise_payouts(stake_a_base=100.0, odds_a=3.0, odds_b=1.5) == pytest.approx(200.0)

    @pytest.mark.parametrize("bad", [0.0, -1.0])
    def test_invalid_odds_returns_zero(self, bad):
        # Defensive: never crash the scanner on a junk odds value.
        assert equalise_payouts(stake_a_base=100.0, odds_a=bad, odds_b=2.0) == 0.0
        assert equalise_payouts(stake_a_base=100.0, odds_a=2.0, odds_b=bad) == 0.0


class TestBracketsKeyNumber:
    def test_brackets_three(self):
        # We bet home -2.5; opposite side now offers away +3.5 → brackets 3.
        assert brackets_key_number(point_a=-2.5, point_b=3.5, keys=(3, 7, 6, 10, 14)) == 3

    def test_brackets_seven(self):
        # Bet home -6.5; opposite offers away +7.5 → brackets 7.
        assert brackets_key_number(point_a=-6.5, point_b=7.5, keys=(3, 7, 6, 10, 14)) == 7

    def test_total_brackets_44(self):
        # Bet over 43.5; opposite now under 44.5 → brackets 44.
        assert brackets_key_number(point_a=43.5, point_b=44.5, keys=(37, 41, 44, 47, 51)) == 44

    def test_no_bracket_same_side(self):
        # Both lines on same side of 3 — no key bracketed.
        assert brackets_key_number(point_a=-1.5, point_b=2.5, keys=(3, 7, 6, 10, 14)) is None

    def test_multiple_brackets_picks_closest_to_midpoint(self):
        # -2.5 and +10.5 brackets 3 AND 7 AND 10 — we return the closest key
        # to the midpoint (4.0) → 3.
        assert brackets_key_number(point_a=-2.5, point_b=10.5, keys=(3, 7, 6, 10, 14)) == 3

    def test_equal_points_no_bracket(self):
        # Identical lines = no straddle.
        assert brackets_key_number(point_a=-3.0, point_b=3.0, keys=(3, 7, 6, 10, 14)) is None

    def test_handles_none(self):
        # Missing points (boost bets, moneylines) → no bracket.
        assert brackets_key_number(point_a=None, point_b=3.5, keys=(3, 7)) is None
        assert brackets_key_number(point_a=-2.5, point_b=None, keys=(3, 7)) is None
