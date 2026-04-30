"""PinnacleSharedRunner — lend/release semantics."""

from __future__ import annotations

import pytest


class _FakePage:
    url = "https://www.pinnacle.se/en/"


class _FakeContext:
    pages: list = []


class _FakeBrowser:
    context = _FakeContext()
    provider_data: dict = {"pinnacle": {"balance": 500.0}}


class _RecordingBroadcaster:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def publish(self, ev, payload):
        self.events.append((ev, payload))


def _build_runner():
    from arnold.mirror.pinnacle_shared import PinnacleSharedRunner

    return PinnacleSharedRunner(
        provider_id="pinnacle",
        browser=_FakeBrowser(),
        broadcaster=_RecordingBroadcaster(),
        proxy_url="http://localhost:18000",
        pop_bet=lambda: None,
        block_event_market=lambda _b: None,
        is_blocked=lambda _b: False,
        placed_today={},
    )


def test_runner_starts_in_idle_state():
    from arnold.mirror.pinnacle_shared import STATE_LENT_TO_ARB

    runner = _build_runner()
    assert runner.state != STATE_LENT_TO_ARB
    assert runner._lent_event.is_set()  # set means "not lent"


@pytest.mark.asyncio
async def test_lend_to_arb_marks_state_and_clears_event(monkeypatch):
    from arnold.mirror.pinnacle_shared import STATE_LENT_TO_ARB

    runner = _build_runner()

    # Stub find_tab to return a fake page
    async def _fake_find_tab(_ctx):
        return _FakePage()

    runner._find_tab = _fake_find_tab  # type: ignore

    page = await runner.lend_to_arb("group-abc")
    assert page is not None
    assert runner.state == STATE_LENT_TO_ARB
    assert not runner._lent_event.is_set()
    events = [e for e, _ in runner._broadcaster.events]
    assert "pinnacle_lent" in events


@pytest.mark.asyncio
async def test_release_to_value_sets_event_and_emits(monkeypatch):
    from arnold.mirror.pinnacle_shared import STATE_LENT_TO_ARB

    runner = _build_runner()

    async def _fake_find_tab(_ctx):
        return _FakePage()

    runner._find_tab = _fake_find_tab  # type: ignore
    await runner.lend_to_arb("group-abc")
    runner.release_to_value()

    assert runner.state != STATE_LENT_TO_ARB
    assert runner._lent_event.is_set()
    events = [e for e, _ in runner._broadcaster.events]
    assert "pinnacle_released" in events


@pytest.mark.asyncio
async def test_lend_is_idempotent(monkeypatch):
    runner = _build_runner()

    async def _fake_find_tab(_ctx):
        return _FakePage()

    runner._find_tab = _fake_find_tab  # type: ignore
    p1 = await runner.lend_to_arb("group-abc")
    p2 = await runner.lend_to_arb("group-abc")
    assert p1 is p2
    lent_events = [e for e, _ in runner._broadcaster.events if e == "pinnacle_lent"]
    assert len(lent_events) == 1  # only emit once
