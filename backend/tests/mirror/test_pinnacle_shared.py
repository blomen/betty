"""PinnacleSharedRunner — lend/release semantics."""

from __future__ import annotations

import asyncio

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


class _FakeWorkflow:
    async def find_tab(self, _ctx):
        return _FakePage()


def test_runner_starts_in_idle_state():
    from arnold.mirror.pinnacle_shared import STATE_LENT_TO_ARB

    runner = _build_runner()
    assert runner.state != STATE_LENT_TO_ARB
    assert runner._lent_event.is_set()  # set means "not lent"


@pytest.mark.asyncio
async def test_lend_to_arb_marks_state_and_clears_event(monkeypatch):
    from arnold.mirror.pinnacle_shared import STATE_LENT_TO_ARB

    runner = _build_runner()

    monkeypatch.setattr(
        "arnold.mirror.pinnacle_shared.get_workflow",
        lambda _pid: _FakeWorkflow(),
    )

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

    monkeypatch.setattr(
        "arnold.mirror.pinnacle_shared.get_workflow",
        lambda _pid: _FakeWorkflow(),
    )
    await runner.lend_to_arb("group-abc")
    runner.release_to_value()

    assert runner.state != STATE_LENT_TO_ARB
    assert runner._lent_event.is_set()
    events = [e for e, _ in runner._broadcaster.events]
    assert "pinnacle_released" in events


@pytest.mark.asyncio
async def test_lend_is_idempotent(monkeypatch):
    runner = _build_runner()

    monkeypatch.setattr(
        "arnold.mirror.pinnacle_shared.get_workflow",
        lambda _pid: _FakeWorkflow(),
    )
    p1 = await runner.lend_to_arb("group-abc")
    p2 = await runner.lend_to_arb("group-abc")
    assert p1 is p2
    lent_events = [e for e, _ in runner._broadcaster.events if e == "pinnacle_lent"]
    assert len(lent_events) == 1  # only emit once


@pytest.mark.asyncio
async def test_lend_does_not_mutate_state_when_no_tab_found(monkeypatch):
    """If find_tab returns None, state stays free — no half-lent leak."""
    from arnold.mirror.pinnacle_shared import STATE_LENT_TO_ARB

    runner = _build_runner()

    class _NoTabWorkflow:
        async def find_tab(self, _ctx):
            return None

    monkeypatch.setattr(
        "arnold.mirror.pinnacle_shared.get_workflow",
        lambda _pid: _NoTabWorkflow(),
    )

    page = await runner.lend_to_arb("group-no-tab")

    assert page is None
    assert runner.state != STATE_LENT_TO_ARB
    assert runner._lent_event.is_set()  # still free
    assert runner._lent_to_group_id is None
    lent_events = [e for e, _ in runner._broadcaster.events if e == "pinnacle_lent"]
    assert lent_events == []


@pytest.mark.asyncio
async def test_value_loop_waits_for_lent_event(monkeypatch):
    """When lent, the value loop's pre-bet hook must yield until released."""
    from arnold.mirror.pinnacle_shared import PinnacleSharedRunner  # noqa: F401

    runner = _build_runner()

    class _FakeWorkflow:
        async def find_tab(self, _ctx):
            return _FakePage()

    monkeypatch.setattr(
        "arnold.mirror.pinnacle_shared.get_workflow",
        lambda _pid: _FakeWorkflow(),
    )

    await runner.lend_to_arb("g1")

    # The hook should not return while lent
    waited = asyncio.create_task(runner._await_unlent_or_done())
    await asyncio.sleep(0.05)
    assert not waited.done()

    runner.release_to_value()
    await asyncio.wait_for(waited, timeout=1.0)


async def test_coordinator_spawns_shared_runner_when_soft_anchors_present(monkeypatch):
    """PlayCoordinator must instantiate PinnacleSharedRunner — not ProviderRunner —
    when the active set contains both pinnacle and at least one soft anchor."""
    from arnold.mirror.pinnacle_shared import PinnacleSharedRunner
    from arnold.mirror.play_loop import PlayLoop

    pl = PlayLoop(browser=_FakeBrowser(), broadcaster=_RecordingBroadcaster(), proxy_url="http://x")
    pl._provider_ids = ["betinia", "pinnacle"]
    pl._spawn_runners(["betinia", "pinnacle"])

    assert isinstance(pl._runners["pinnacle"], PinnacleSharedRunner)
    for r in pl._runners.values():
        r.stop()


async def test_coordinator_uses_plain_provider_runner_when_only_unlimited(monkeypatch):
    from arnold.mirror.pinnacle_shared import PinnacleSharedRunner
    from arnold.mirror.play_loop import PlayLoop
    from arnold.mirror.provider_runner import ProviderRunner

    pl = PlayLoop(browser=_FakeBrowser(), broadcaster=_RecordingBroadcaster(), proxy_url="http://x")
    pl._provider_ids = ["pinnacle", "polymarket"]
    pl._spawn_runners(["pinnacle", "polymarket"])

    runner = pl._runners["pinnacle"]
    assert isinstance(runner, ProviderRunner)
    assert not isinstance(runner, PinnacleSharedRunner)
    for r in pl._runners.values():
        r.stop()


@pytest.mark.asyncio
async def test_arb_runner_calls_lend_then_release(monkeypatch):
    """ArbRunner must lend the Pinnacle tab when it loads counters and release on cleanup."""
    from arnold.mirror.arb_runner import ArbRunner
    from arnold.mirror.pinnacle_shared import STATE_LENT_TO_ARB, PinnacleSharedRunner

    bc = _RecordingBroadcaster()
    shared = PinnacleSharedRunner(
        provider_id="pinnacle",
        browser=_FakeBrowser(),
        broadcaster=bc,
        proxy_url="http://x",
        pop_bet=lambda: None,
        block_event_market=lambda _b: None,
        is_blocked=lambda _b: False,
        placed_today={},
    )

    class _FakeWorkflow:
        async def find_tab(self, _ctx):
            return _FakePage()

    monkeypatch.setattr(
        "arnold.mirror.pinnacle_shared.get_workflow",
        lambda _pid: _FakeWorkflow(),
    )

    arb = ArbRunner(
        provider_id="betinia",
        browser=_FakeBrowser(),
        broadcaster=bc,
        proxy_url="http://x",
        block_event_market=lambda _b: None,
        is_blocked=lambda _b: False,
        placed_today={},
        active_providers=["betinia", "pinnacle"],
        stake_caps={},
        pinnacle_shared=shared,
    )

    page = await arb._lend_pinnacle_if_needed("group-xyz")
    assert page is not None
    assert shared.state == STATE_LENT_TO_ARB

    arb._release_pinnacle_if_held()
    assert shared.state != STATE_LENT_TO_ARB


@pytest.mark.asyncio
async def test_lend_pinnacle_if_needed_recovers_on_lend_exception(monkeypatch):
    """If lend_to_arb raises, the helper must call release_to_value to clean up."""
    from arnold.mirror.arb_runner import ArbRunner
    from arnold.mirror.pinnacle_shared import PinnacleSharedRunner

    bc = _RecordingBroadcaster()
    shared = PinnacleSharedRunner(
        provider_id="pinnacle",
        browser=_FakeBrowser(),
        broadcaster=bc,
        proxy_url="http://x",
        pop_bet=lambda: None,
        block_event_market=lambda _b: None,
        is_blocked=lambda _b: False,
        placed_today={},
    )

    release_calls: list[bool] = []
    original_release = shared.release_to_value

    def _spy_release():
        release_calls.append(True)
        original_release()

    shared.release_to_value = _spy_release  # type: ignore

    # Force lend_to_arb to raise after state mutation: stub get_workflow with a
    # workflow whose find_tab returns a real-looking page (so state mutates),
    # but make the broadcaster.publish raise.
    class _FakeWorkflow:
        async def find_tab(self, _ctx):
            class _Page:
                url = "https://www.pinnacle.se/en/"

            return _Page()

    monkeypatch.setattr(
        "arnold.mirror.pinnacle_shared.get_workflow",
        lambda _pid: _FakeWorkflow(),
    )

    raise_count = {"n": 0}

    def _exploding_publish(event, payload):
        if event == "pinnacle_lent":
            raise_count["n"] += 1
            raise RuntimeError("simulated broadcaster failure")
        bc.events.append((event, payload))

    shared._broadcaster.publish = _exploding_publish  # type: ignore

    arb = ArbRunner(
        provider_id="betinia",
        browser=_FakeBrowser(),
        broadcaster=bc,
        proxy_url="http://x",
        block_event_market=lambda _b: None,
        is_blocked=lambda _b: False,
        placed_today={},
        active_providers=["betinia", "pinnacle"],
        stake_caps={},
        pinnacle_shared=shared,
    )

    page = await arb._lend_pinnacle_if_needed("group-explode")

    # Lend raised → helper returns None
    assert page is None
    # Recovery release was invoked
    assert release_calls == [True]
    # State is back to free (not lent)
    assert shared.state != "lent_to_arb"
    assert shared._lent_to_group_id is None
