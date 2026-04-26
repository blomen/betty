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


class TestOppKey:
    def test_opp_key_includes_event_market_point_outcome(self):
        opp = {
            "event_id": "evt-123",
            "market": "spread",
            "point": -2.5,
            "outcome": "home",
        }
        # First-leg outcome is what determines the anchor's selection
        leg = {"outcome": "home", "provider": "betinia", "odds": 2.10}
        key = ArbRunner._compute_opp_key(opp, leg)
        assert key == "evt-123|spread|-2.5|home"

    def test_opp_key_handles_missing_point(self):
        opp = {"event_id": "evt-456", "market": "1x2", "outcome": "draw"}
        leg = {"outcome": "draw", "provider": "betinia", "odds": 3.40}
        key = ArbRunner._compute_opp_key(opp, leg)
        assert key == "evt-456|1x2||draw"


from unittest.mock import MagicMock  # noqa: E402


class TestStopResetsGreenGateState:
    """Per code review on Task 3: stop() must reset opp_key + planned + dethrone + recomputed_profit
    so a restart doesn't see stale state."""

    def _make_runner(self):
        browser = MagicMock()
        browser.context = MagicMock()
        browser.context.pages = []
        browser.provider_data = {}
        broadcaster = MagicMock()
        broadcaster.publish = MagicMock()
        return ArbRunner(
            provider_id="betinia",
            browser=browser,
            broadcaster=broadcaster,
            proxy_url="https://x.test",
            block_event_market=lambda b: None,
            is_blocked=lambda b: False,
            placed_today={},
            active_providers=["betinia", "pinnacle"],
        )

    def test_stop_clears_all_green_gate_state(self):
        runner = self._make_runner()
        # Simulate state that _load_all_legs would have populated
        runner.current_opp_key = "evt-A|1x2||home"
        runner._planned_anchor_odds = 2.10
        runner._dethroned_to = {"event_id": "evt-B"}
        runner._current_recomputed_profit_pct = 1.5

        runner.stop()

        assert runner.current_opp_key is None
        assert runner._planned_anchor_odds == 0.0
        assert runner._dethroned_to is None
        assert runner._current_recomputed_profit_pct is None
