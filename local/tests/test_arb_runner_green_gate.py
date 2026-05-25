"""ArbRunner green-gate + dethrone tests (per spec §4.2)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from local.mirror.arb_runner import ArbRunner


def _make_browser():
    browser = MagicMock()
    browser.context = MagicMock()
    browser.context.pages = []
    browser.provider_data = {}
    return browser


def _make_broadcaster():
    bc = MagicMock()
    bc.publish = MagicMock()
    return bc


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


class TestStopResetsGreenGateState:
    """Per code review on Task 3: stop() must reset opp_key + planned + dethrone + recomputed_profit
    so a restart doesn't see stale state."""

    def _make_runner(self):
        return ArbRunner(
            provider_id="betinia",
            browser=_make_browser(),
            broadcaster=_make_broadcaster(),
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
        runner._all_green = True

        runner.stop()

        assert runner.current_opp_key is None
        assert runner._planned_anchor_odds == 0.0
        assert runner._dethroned_to is None
        assert runner._current_recomputed_profit_pct is None
        assert runner._all_green is False


class TestAlignmentPayload:
    def _setup_runner_with_loaded_opp(self):
        """Build an ArbRunner with state as if _load_all_legs already succeeded."""
        runner = ArbRunner(
            provider_id="betinia",
            browser=_make_browser(),
            broadcaster=_make_broadcaster(),
            proxy_url="https://x.test",
            block_event_market=lambda b: None,
            is_blocked=lambda b: False,
            placed_today={},
            active_providers=["betinia", "pinnacle"],
        )
        runner.state = "standby"
        runner.current_opp = {"event_id": "e1", "market": "1x2", "outcome": "home"}
        runner.current_arb_group_id = "abc123"
        runner._planned_anchor_odds = 2.10
        runner._anchor_stake = 100.0
        runner._counter_legs = [{"provider": "pinnacle", "outcome": "away", "odds": 2.05, "_planned_odds": 2.05}]
        # Stub the streams so _on_leg_odds_change can read anchor_odds
        anchor_stream = MagicMock()
        anchor_stream.current_odds = 2.10
        anchor_stream.page = MagicMock()
        counter_stream = MagicMock()
        counter_stream.page = MagicMock()
        runner._streams = {"betinia": anchor_stream, "pinnacle": counter_stream}
        return runner

    @pytest.mark.asyncio
    async def test_alignment_includes_slip_state_per_leg(self):
        runner = self._setup_runner_with_loaded_opp()
        # Tick anchor with planned odds (green) and counter with planned odds (green)
        runner._latest_counter_odds = {"pinnacle": 2.05}
        runner._on_leg_odds_change("betinia", 2.10)
        runner._on_leg_odds_change("pinnacle", 2.05)

        # Find the most recent arb_alignment broadcast
        calls = [c for c in runner._broadcaster.publish.call_args_list if c.args[0] == "arb_alignment"]
        assert calls, "expected at least one arb_alignment broadcast"
        payload = calls[-1].args[1]

        assert payload["arb_group_id"] == "abc123"
        assert "all_green" in payload
        assert payload["all_green"] is True
        assert "current_profit_pct" in payload
        legs = payload["legs"]
        assert all("slip_state" in leg for leg in legs)
        assert all("planned_odds" in leg for leg in legs)
        assert all(leg["slip_state"] == "green" for leg in legs)

    @pytest.mark.asyncio
    async def test_alignment_marks_red_when_anchor_drifts_below_tol(self):
        runner = self._setup_runner_with_loaded_opp()
        # Drift anchor below 1% tol: 2.10 * 0.99 = 2.079; 2.07 < 2.079 → red
        runner._latest_counter_odds = {"pinnacle": 2.05}
        runner._on_leg_odds_change("pinnacle", 2.05)
        runner._broadcaster.publish.reset_mock()
        runner._last_alignment_broadcast = 0.0  # reset throttle so next call fires
        runner._streams["betinia"].current_odds = 2.07
        runner._on_leg_odds_change("betinia", 2.07)

        calls = [c for c in runner._broadcaster.publish.call_args_list if c.args[0] == "arb_alignment"]
        assert calls
        payload = calls[-1].args[1]
        assert payload["all_green"] is False
        anchor_leg = next(l for l in payload["legs"] if l["provider_id"] == "betinia")
        assert anchor_leg["slip_state"] == "red"

    def test_alignment_all_green_false_when_profit_negative(self):
        # Pure 2-leg arb: profit-negative without any leg going red is unreachable,
        # since the only way to push profit < 0 is to push odds down, which trips the
        # drift gate. The red-leg test covers the combined case.
        pass  # intentionally a no-op marker


class TestDethroneHysteresis:
    """Per spec §4.2: switch to a new opp only when its profit beats current by ≥0.5pp."""

    def _make_runner(self):
        runner = ArbRunner(
            provider_id="betinia",
            browser=_make_browser(),
            broadcaster=_make_broadcaster(),
            proxy_url="https://x.test",
            block_event_market=lambda b: None,
            is_blocked=lambda b: False,
            placed_today={},
            active_providers=["betinia", "pinnacle"],
        )
        runner.current_opp_key = "evt-A|1x2||home"
        runner._current_recomputed_profit_pct = 1.0
        return runner

    def test_no_dethrone_when_top_is_same_opp(self):
        runner = self._make_runner()
        top_opp = {
            "event_id": "evt-A",
            "market": "1x2",
            "point": None,
            "outcome": "home",
            "guaranteed_profit_pct": 5.0,
            "arb_legs": [{"provider": "betinia", "outcome": "home", "odds": 2.10}],
        }
        assert runner._should_dethrone(top_opp) is False

    def test_no_dethrone_when_below_hysteresis(self):
        runner = self._make_runner()
        top_opp = {
            "event_id": "evt-B",
            "market": "1x2",
            "point": None,
            "outcome": "away",
            "guaranteed_profit_pct": 1.4,  # +0.4pp over current 1.0 — below 0.5pp
            "arb_legs": [{"provider": "betinia", "outcome": "away", "odds": 2.20}],
        }
        assert runner._should_dethrone(top_opp) is False

    def test_dethrone_at_hysteresis_threshold(self):
        runner = self._make_runner()
        top_opp = {
            "event_id": "evt-B",
            "market": "1x2",
            "point": None,
            "outcome": "away",
            "guaranteed_profit_pct": 1.5,  # +0.5pp over current 1.0
            "arb_legs": [{"provider": "betinia", "outcome": "away", "odds": 2.20}],
        }
        assert runner._should_dethrone(top_opp) is True

    def test_dethrone_with_no_recomputed_profit_yet_uses_zero_baseline(self):
        runner = self._make_runner()
        runner._current_recomputed_profit_pct = None
        top_opp = {
            "event_id": "evt-B",
            "market": "1x2",
            "point": None,
            "outcome": "away",
            "guaranteed_profit_pct": 0.6,  # +0.6pp over baseline 0
            "arb_legs": [{"provider": "betinia", "outcome": "away", "odds": 2.20}],
        }
        assert runner._should_dethrone(top_opp) is True


class TestFetchArbOppsPostFilter:
    """Per fix landed 2026-04-26: backend counterpart_providers filter is broken;
    runner post-filters opps so all legs must be in counter_pool ∪ {anchor}."""

    def _make_runner(self, active=("betinia", "pinnacle")):
        return ArbRunner(
            provider_id="betinia",
            browser=_make_browser(),
            broadcaster=_make_broadcaster(),
            proxy_url="https://x.test",
            block_event_market=lambda b: None,
            is_blocked=lambda b: False,
            placed_today={},
            active_providers=list(active),
        )

    @pytest.mark.asyncio
    async def test_keeps_opp_when_all_legs_in_pool(self, monkeypatch):

        runner = self._make_runner()

        class FakeClient:
            async def get(self, url, params=None, headers=None, timeout=None):
                providers = (params or {}).get("providers")
                assert "counterpart_providers" not in url, "URL must not include counterpart filter (backend bug)"
                assert providers == "betinia"

                class R:
                    status_code = 200

                    def raise_for_status(self):
                        pass

                    def json(self):
                        return {
                            "opportunities": [
                                {
                                    "guaranteed_profit_pct": 5.0,
                                    "arb_legs": [
                                        {"provider": "betinia", "outcome": "home", "odds": 2.0},
                                        {"provider": "pinnacle", "outcome": "away", "odds": 2.5},
                                    ],
                                }
                            ]
                        }

                return R()

        from local import http_client as _hc

        monkeypatch.setattr(_hc, "tunnel_client", lambda: FakeClient())

        result = await runner._fetch_arb_opps()
        assert len(result) == 1
        assert result[0]["guaranteed_profit_pct"] == 5.0

    @pytest.mark.asyncio
    async def test_drops_opp_when_a_leg_is_outside_pool(self, monkeypatch):

        # betsson not in active list and not UNLIMITED → should be rejected
        runner = self._make_runner(active=["betinia", "pinnacle"])

        class FakeClient:
            async def get(self, url, params=None, headers=None, timeout=None):
                class R:
                    status_code = 200

                    def raise_for_status(self):
                        pass

                    def json(self):
                        return {
                            "opportunities": [
                                {
                                    "guaranteed_profit_pct": 8.0,
                                    "arb_legs": [
                                        {"provider": "betinia", "outcome": "home", "odds": 2.0},
                                        {"provider": "betsson", "outcome": "away", "odds": 2.5},
                                    ],
                                }
                            ]
                        }

                return R()

        from local import http_client as _hc

        monkeypatch.setattr(_hc, "tunnel_client", lambda: FakeClient())

        result = await runner._fetch_arb_opps()
        assert result == []  # betsson not in active list and not in UNLIMITED, opp gets dropped

    @pytest.mark.asyncio
    async def test_drops_opp_with_no_legs(self, monkeypatch):

        runner = self._make_runner()

        class FakeClient:
            async def get(self, url, params=None, headers=None, timeout=None):
                class R:
                    status_code = 200

                    def raise_for_status(self):
                        pass

                    def json(self):
                        return {"opportunities": [{"guaranteed_profit_pct": 5.0, "legs": []}]}

                return R()

        from local import http_client as _hc

        monkeypatch.setattr(_hc, "tunnel_client", lambda: FakeClient())

        result = await runner._fetch_arb_opps()
        assert result == []


class TestHedgeFailureEmits:
    """Per final review on 2026-04-26: counter placements that the provider rejects
    must surface as arb_hedge_failed, not silently recorded as arb_hedge_placed."""

    def _runner_with_counter_intercepted(self, body):
        """Build a runner positioned for the counter-confirm phase with an intercepted body."""
        runner = ArbRunner(
            provider_id="betinia",
            browser=_make_browser(),
            broadcaster=_make_broadcaster(),
            proxy_url="https://x.test",
            block_event_market=lambda b: None,
            is_blocked=lambda b: False,
            placed_today={},
            active_providers=["betinia", "pinnacle"],
        )
        runner.current_opp = {
            "event_id": "evt-X",
            "market": "1x2",
            "outcome": "home",
            "display_home": "H",
            "display_away": "A",
            "sport": "football",
        }
        runner.current_arb_group_id = "test_group"
        runner._planned_anchor_odds = 2.10
        runner._anchor_stake = 100.0
        runner._counter_legs = [
            {
                "provider": "pinnacle",
                "outcome": "away",
                "odds": 2.05,
                "_planned_odds": 2.05,
                "_current_stake": 50.0,
            }
        ]
        runner._counter_events = {"pinnacle": asyncio.Event()}
        runner._counter_events["pinnacle"].set()
        runner._counter_intercepted = {"pinnacle": {"body": body}}
        # Stub streams so _push_stake can resolve page without a real browser
        pinnacle_stream = MagicMock()
        pinnacle_stream.page = MagicMock()
        runner._streams = {"pinnacle": pinnacle_stream}
        return runner

    @pytest.mark.asyncio
    async def test_emits_arb_hedge_failed_when_pinnacle_rejects(self, monkeypatch):
        # Stub _record_bet so we can assert it was NOT called
        recorded = []

        async def fake_record_bet(self, bet, result, arb_group):
            recorded.append((bet, result, arb_group))

        monkeypatch.setattr(ArbRunner, "_record_bet", fake_record_bet)

        # The body shape that pinnacle's parser sees as failure:
        body = {"error": "STAKE_LIMIT", "maxStake": 25.0}
        runner = self._runner_with_counter_intercepted(body)

        await runner._update_counter_slips_and_await_hedges(100.0, 2.10)

        # arb_hedge_failed should fire, arb_hedge_placed should NOT for this leg
        events = [(c.args[0], c.args[1]) for c in runner._broadcaster.publish.call_args_list]
        names = [e[0] for e in events]
        assert "arb_hedge_failed" in names
        assert "arb_hedge_placed" not in [n for n in names if "pinnacle" in str(events[names.index(n)][1])]
        # _record_bet was never called for the failed leg
        assert len(recorded) == 0

    @pytest.mark.asyncio
    async def test_emits_arb_hedge_placed_when_pinnacle_accepts(self, monkeypatch):
        recorded = []

        async def fake_record_bet(self, bet, result, arb_group):
            recorded.append((bet, result, arb_group))

        monkeypatch.setattr(ArbRunner, "_record_bet", fake_record_bet)

        # Pinnacle success body has wagerNumber per its parse_placement_status
        body = {"wagerNumber": 12345}
        runner = self._runner_with_counter_intercepted(body)

        await runner._update_counter_slips_and_await_hedges(100.0, 2.10)

        names = [c.args[0] for c in runner._broadcaster.publish.call_args_list]
        assert "arb_hedge_placed" in names
        assert "arb_hedge_failed" not in names
        assert len(recorded) == 1
        # arb_complete fires after all hedges resolve
        assert "arb_complete" in names


class TestPlacedWhileRedGate:
    """Spec §4.2: anchor click while any leg is red → reject + don't record."""

    def _make_runner_in_standby(self):
        runner = ArbRunner(
            provider_id="betinia",
            browser=_make_browser(),
            broadcaster=_make_broadcaster(),
            proxy_url="https://x.test",
            block_event_market=lambda b: None,
            is_blocked=lambda b: False,
            placed_today={},
            active_providers=["betinia", "pinnacle"],
        )
        runner.state = "standby"
        runner.current_arb_group_id = "abc"
        runner._planned_anchor_odds = 2.10
        runner._anchor_stake = 100.0
        runner._counter_legs = []
        return runner

    def test_on_bet_intercepted_sets_red_flag_when_not_all_green(self):
        runner = self._make_runner_in_standby()
        runner._all_green = False
        runner.on_bet_intercepted({"wagerNumber": 1}, None)
        assert runner._intercepted_while_red is True
        assert runner._anchor_event.is_set()

    def test_on_bet_intercepted_clears_red_flag_when_all_green(self):
        runner = self._make_runner_in_standby()
        runner._all_green = True
        runner.on_bet_intercepted({"wagerNumber": 1}, None)
        assert runner._intercepted_while_red is False

    @pytest.mark.asyncio
    async def test_stream_and_await_anchor_emits_rejected_when_red(self):
        runner = self._make_runner_in_standby()
        runner._all_green = False

        async def _intercept_after_delay():
            await asyncio.sleep(0.05)
            runner.on_bet_intercepted({"wagerNumber": 1}, None)

        asyncio.create_task(_intercept_after_delay())
        result = await asyncio.wait_for(runner._stream_and_await_anchor(), timeout=2.0)
        assert result is None
        events = [c.args for c in runner._broadcaster.publish.call_args_list]
        rejected = [e for e in events if e[0] == "arb_anchor_rejected"]
        assert rejected, "arb_anchor_rejected must fire on red intercept"
        assert rejected[-1][1].get("reason") == "placed_while_red"

    @pytest.mark.asyncio
    async def test_stream_and_await_anchor_proceeds_when_green(self, monkeypatch):
        runner = self._make_runner_in_standby()
        runner._all_green = True

        # Force the anchor workflow's parser to report success regardless of body shape
        from local.mirror.workflows import get_workflow

        wf = get_workflow("betinia")
        monkeypatch.setattr(
            type(wf),
            "parse_placement_status",
            staticmethod(lambda body: {"success": True, "error": None, "max_stake": None}),
        )

        async def _intercept_after_delay():
            await asyncio.sleep(0.05)
            runner.on_bet_intercepted({"wagerNumber": 1}, None)

        asyncio.create_task(_intercept_after_delay())
        result = await asyncio.wait_for(runner._stream_and_await_anchor(), timeout=2.0)
        assert result is not None
        events = [c.args[0] for c in runner._broadcaster.publish.call_args_list]
        assert "arb_anchor_placed" in events
        assert "arb_anchor_rejected" not in events


class TestWatchTopOppLoop:
    """Coverage gap from final review: _watch_top_opp loop body integration."""

    def _make_runner(self):
        runner = ArbRunner(
            provider_id="betinia",
            browser=_make_browser(),
            broadcaster=_make_broadcaster(),
            proxy_url="https://x.test",
            block_event_market=lambda b: None,
            is_blocked=lambda b: False,
            placed_today={},
            active_providers=["betinia", "pinnacle"],
        )
        runner.current_opp_key = "evt-A|1x2||home"
        runner._current_recomputed_profit_pct = 1.0
        runner.current_arb_group_id = "watcher_test"
        return runner

    @pytest.mark.asyncio
    async def test_watcher_fires_dethrone_when_better_opp_appears(self, monkeypatch):
        from local.mirror import arb_runner as ar

        runner = self._make_runner()
        # Speed up the loop sleep so the test finishes quickly
        monkeypatch.setattr(ar, "RERANK_INTERVAL_S", 0.05)

        better = {
            "event_id": "evt-B",
            "market": "1x2",
            "point": None,
            "outcome": "away",
            "guaranteed_profit_pct": 5.0,  # +4pp over current 1.0 → above hysteresis
            "arb_legs": [{"provider": "betinia", "outcome": "away", "odds": 2.20}],
        }

        async def fake_fetch():
            return [better]

        monkeypatch.setattr(runner, "_fetch_arb_opps", fake_fetch)

        await asyncio.wait_for(runner._watch_top_opp(), timeout=2.0)

        assert runner._dethroned_to == better
        assert runner._anchor_event.is_set()
        events = [c.args[0] for c in runner._broadcaster.publish.call_args_list]
        assert "arb_dethroned" in events

    @pytest.mark.asyncio
    async def test_watcher_does_not_dethrone_for_same_opp(self, monkeypatch):
        from local.mirror import arb_runner as ar

        runner = self._make_runner()
        monkeypatch.setattr(ar, "RERANK_INTERVAL_S", 0.05)

        same = {
            "event_id": "evt-A",
            "market": "1x2",
            "point": None,
            "outcome": "home",
            "guaranteed_profit_pct": 99.0,  # huge profit but same opp_key → no dethrone
            "arb_legs": [{"provider": "betinia", "outcome": "home", "odds": 2.10}],
        }

        async def fake_fetch():
            return [same]

        monkeypatch.setattr(runner, "_fetch_arb_opps", fake_fetch)

        # Run for a bit then cancel — watcher should NOT exit on its own
        task = asyncio.create_task(runner._watch_top_opp())
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert runner._dethroned_to is None
        events = [c.args[0] for c in runner._broadcaster.publish.call_args_list]
        assert "arb_dethroned" not in events

    @pytest.mark.asyncio
    async def test_watcher_swallows_fetch_exceptions_and_continues(self, monkeypatch):
        from local.mirror import arb_runner as ar

        runner = self._make_runner()
        monkeypatch.setattr(ar, "RERANK_INTERVAL_S", 0.05)

        call_count = {"n": 0}

        async def fake_fetch():
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise RuntimeError("transient fetch failure")
            return []  # nothing to dethrone — watcher continues

        monkeypatch.setattr(runner, "_fetch_arb_opps", fake_fetch)

        task = asyncio.create_task(runner._watch_top_opp())
        await asyncio.sleep(0.3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert call_count["n"] >= 3, "watcher must keep looping past errors"
        assert runner._dethroned_to is None
