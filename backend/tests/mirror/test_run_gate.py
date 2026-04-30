"""Tests for the Run-gate state machine added to ProviderRunner / ArbRunner.

We don't instantiate the real runners (they need a live Playwright browser).
Instead we replicate the gate primitive — an asyncio.Event + a small loop
that re-checks it — and verify the contract:

  1. Gate starts cleared.
  2. set_run(True) flips False→True idempotently (returns False on no-op).
  3. set_run(False) flips True→False idempotently.
  4. A waiter awaiting the event unblocks when set.
  5. A loop that re-checks the event drops back to ready when cleared.

Plus state-constant assertions to lock in the public surface.
"""

from __future__ import annotations

import asyncio

import pytest

from arnold.mirror.play_loop import STATE_READY_TO_RUN


def test_state_constant_value():
    """STATE_READY_TO_RUN is a stable string the frontend depends on."""
    assert STATE_READY_TO_RUN == "ready_to_run"


class _GateHarness:
    """Minimal mirror of the gate primitive used by both runners."""

    def __init__(self):
        self._run_event = asyncio.Event()

    def set_run(self, run: bool) -> bool:
        if run:
            if self._run_event.is_set():
                return False
            self._run_event.set()
            return True
        else:
            if not self._run_event.is_set():
                return False
            self._run_event.clear()
            return True


def test_gate_starts_cleared():
    h = _GateHarness()
    assert not h._run_event.is_set()


def test_set_run_true_flips_and_is_idempotent():
    h = _GateHarness()
    assert h.set_run(True) is True
    assert h._run_event.is_set()
    assert h.set_run(True) is False  # already set


def test_set_run_false_clears_and_is_idempotent():
    h = _GateHarness()
    h.set_run(True)
    assert h.set_run(False) is True
    assert not h._run_event.is_set()
    assert h.set_run(False) is False  # already cleared


@pytest.mark.asyncio
async def test_waiter_unblocks_on_set():
    h = _GateHarness()

    async def waiter():
        await h._run_event.wait()
        return "released"

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.01)  # let the waiter park
    assert not task.done()
    h.set_run(True)
    result = await asyncio.wait_for(task, timeout=1.0)
    assert result == "released"


@pytest.mark.asyncio
async def test_loop_drops_back_to_ready_when_cleared():
    """A bet-loop that re-checks the gate at iteration start should park
    on the event when cleared, and resume on set.

    We verify that when the gate is clear, waiting on it blocks; when set,
    it unblocks. The loop's behavior flows from these primitives."""
    h = _GateHarness()

    # Scenario 1: loop runs when gate is set
    h.set_run(True)
    ran_count = 0

    async def loop_with_gate_set():
        nonlocal ran_count
        for _ in range(2):
            if not h._run_event.is_set():
                await h._run_event.wait()
            ran_count += 1

    task = asyncio.create_task(loop_with_gate_set())
    await asyncio.wait_for(task, timeout=0.5)
    assert ran_count == 2  # both iterations ran without waiting

    # Scenario 2: loop waits when gate is cleared, resumes when set
    h.set_run(False)
    ran_count = 0
    paused = False

    async def loop_that_pauses():
        nonlocal ran_count, paused
        for i in range(3):
            if not h._run_event.is_set():
                paused = True
                await h._run_event.wait()
                paused = False
            ran_count += 1

    task = asyncio.create_task(loop_that_pauses())
    await asyncio.sleep(0.05)  # let it pause at the first gate check
    assert paused is True  # it's waiting
    assert ran_count == 0  # it hasn't incremented yet
    h.set_run(True)
    await asyncio.wait_for(task, timeout=0.5)
    assert ran_count == 3  # all iterations completed


def test_play_loop_set_run_no_runner_returns_false():
    """PlayLoop.set_run(pid) on an unknown provider returns False, never raises."""
    from arnold.mirror.play_loop import PlayLoop

    pl = object.__new__(PlayLoop)  # bypass __init__
    pl._runners = {}  # only field set_run touches
    assert pl.set_run("nonexistent", True) is False
    assert pl.set_run("nonexistent", False) is False
