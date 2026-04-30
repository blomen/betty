"""Hedge wait timeout — ArbRunner must give up on un-clicked counters after N seconds."""

from __future__ import annotations

import asyncio

import pytest


class _RecordingBroadcaster:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def publish(self, event: str, payload: dict):
        self.events.append((event, payload))


@pytest.fixture
def runner_with_counter_legs():
    from arnold.mirror.arb_runner import ArbRunner

    class _FakeBrowser:
        provider_data = {"betinia": {"balance": 100.0}}
        context = None

    bc = _RecordingBroadcaster()
    runner = ArbRunner(
        provider_id="betinia",
        browser=_FakeBrowser(),
        broadcaster=bc,
        proxy_url="http://localhost:18000",
        block_event_market=lambda _b: None,
        is_blocked=lambda _b: False,
        placed_today={},
        active_providers=["betinia", "pinnacle"],
        stake_caps={},
    )
    runner.current_arb_group_id = "abcdef123456"
    runner.current_opp = {"event_id": "e1", "market": "moneyline"}
    runner._counter_legs = [{"provider": "pinnacle", "outcome": "away", "odds": 2.10}]
    runner._counter_events = {"pinnacle": asyncio.Event()}
    runner._counter_intercepted = {}  # never fires — simulates user not clicking

    # Stub the slip-stake push so we don't hit Playwright
    async def _no_op(*_a, **_k):
        return True

    class _StubWf:
        provider_id = "pinnacle"
        update_slip_stake = staticmethod(_no_op)
        parse_placement_status = staticmethod(lambda _b: {"success": True})

    def _get_wf(_pid):
        return _StubWf()

    runner._streams = {"pinnacle": type("S", (), {"page": None})()}

    return runner, bc, _get_wf


@pytest.mark.asyncio
async def test_hedge_timeout_emits_failure_for_unclicked_counters(monkeypatch, runner_with_counter_legs):
    runner, bc, get_wf_stub = runner_with_counter_legs
    from arnold.mirror import arb_runner as _ar

    monkeypatch.setattr(_ar, "get_workflow", get_wf_stub)
    monkeypatch.setattr(_ar, "COUNTER_HEDGE_TIMEOUT_S", 0.2)

    # Stub _record_bet to a no-op so we don't hit httpx
    async def _stub_record(*_a, **_k):
        return None

    runner._record_bet = _stub_record  # type: ignore

    await runner._update_counter_slips_and_await_hedges(anchor_actual_stake=50.0, anchor_actual_odds=2.0)

    failed = [p for e, p in bc.events if e == "arb_hedge_failed"]
    assert len(failed) == 1
    assert failed[0]["counter_provider"] == "pinnacle"
    assert failed[0]["reason"] == "user_timeout"
