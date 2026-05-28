"""Unit tests for rehedge sizing helpers in src.analysis.middle_math.

Pure-function tests — no DB, no I/O.
"""

import pytest

from src.analysis.middle_math import (
    brackets_key_number,
    equalise_payouts,
    middle_size,
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


class TestMiddleSize:
    def test_target_zero_loss_equals_equalise(self):
        # With target_wing_pct=0, stake_b should equal equalise_payouts —
        # both wings produce identical payout, total stake is just refunded
        # on whichever side wins.
        stake_a, odds_a, odds_b = 100.0, 2.0, 2.0
        stake_b = middle_size(stake_a, odds_a, odds_b, target_wing_pct=0.0)
        assert stake_b == pytest.approx(equalise_payouts(stake_a, odds_a, odds_b))

    def test_target_one_percent_wing_loss(self):
        # Accept 1% loss on wings → smaller stake_b, bigger middle upside.
        # Note: odds_b > odds_a; with these inputs Case 1 (B becomes the
        # smaller-payout side after under-staking) applies.
        stake_a, odds_a, odds_b = 100.0, 2.0, 2.15
        stake_b = middle_size(stake_a, odds_a, odds_b, target_wing_pct=0.01)
        # Equal-payout would be 100. Accepting 1% loss → slightly smaller.
        assert stake_b < 100.0
        # Verify the resulting wing-loss is ~1%.
        total = stake_a + stake_b
        a_wins_payout = stake_a * odds_a
        b_wins_payout = stake_b * odds_b
        wing_loss = total - min(a_wins_payout, b_wins_payout)
        assert wing_loss / total == pytest.approx(0.01, abs=0.001)

    def test_invalid_target_clamps_to_zero(self):
        # Negative target_wing_pct nonsensical — clamp.
        stake_a, odds_a, odds_b = 100.0, 2.0, 2.0
        assert middle_size(stake_a, odds_a, odds_b, target_wing_pct=-0.5) == pytest.approx(
            equalise_payouts(stake_a, odds_a, odds_b)
        )

    @pytest.mark.parametrize("bad", [0.0, -1.0])
    def test_invalid_odds_returns_zero(self, bad):
        assert middle_size(100.0, bad, 2.0, target_wing_pct=0.01) == 0.0
        assert middle_size(100.0, 2.0, bad, target_wing_pct=0.01) == 0.0
