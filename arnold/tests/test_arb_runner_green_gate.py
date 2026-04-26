"""ArbRunner green-gate + dethrone tests (per spec §4.2)."""

from __future__ import annotations

from arnold.mirror.arb_runner import ArbRunner


class TestComputeSlipState:
    def test_green_when_live_matches_planned(self):
        assert ArbRunner._compute_slip_state(planned_odds=2.10, live_odds=2.10) == "green"

    def test_green_when_drift_within_tolerance(self):
        # 1% tolerance → 2.10 * 0.99 = 2.079; live=2.08 is acceptable
        assert ArbRunner._compute_slip_state(planned_odds=2.10, live_odds=2.08) == "green"

    def test_red_when_drift_exceeds_tolerance(self):
        # 2.10 * 0.99 = 2.079; live=2.07 is below threshold
        assert ArbRunner._compute_slip_state(planned_odds=2.10, live_odds=2.07) == "red"

    def test_red_when_live_is_none(self):
        assert ArbRunner._compute_slip_state(planned_odds=2.10, live_odds=None) == "red"

    def test_red_when_live_is_zero(self):
        assert ArbRunner._compute_slip_state(planned_odds=2.10, live_odds=0.0) == "red"

    def test_red_when_live_is_negative(self):
        assert ArbRunner._compute_slip_state(planned_odds=2.10, live_odds=-0.5) == "red"

    def test_green_when_live_is_above_planned(self):
        # Higher odds than planned is always good
        assert ArbRunner._compute_slip_state(planned_odds=2.10, live_odds=2.50) == "green"
