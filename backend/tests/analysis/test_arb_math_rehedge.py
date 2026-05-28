"""Unit tests for rehedge sizing helpers in local.mirror.arb_math.

Pure-function tests — no DB, no I/O. The local.mirror import path works
because the repo root is on sys.path during pytest (see backend/tests/conftest.py).
"""

import pytest
from local.mirror.arb_math import (
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
