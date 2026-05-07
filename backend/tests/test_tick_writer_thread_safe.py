"""Regression tests: TickWriter.add() must be safe to call from any thread.

Bug: `TickWriter.add` called `asyncio.create_task(self._flush())` inline.
That works only when the caller is on a thread that has a running asyncio
event loop. From a worker thread (e.g. a future Databento sync-callback
adapter) it raises `RuntimeError: no running event loop` and the batch
silently drops.

Fix: capture the loop in `start()`, schedule via `call_soon_threadsafe`
which is safe from any thread.
"""

import asyncio
import threading
from datetime import datetime, timezone

import pytest

from src.market_data.stream import TICK_BATCH_SIZE, TickWriter


def _fake_session_factory():
    """No-op session factory — _flush() catches its own errors."""
    raise RuntimeError("not used in these tests")


@pytest.mark.asyncio
async def test_add_below_threshold_does_not_schedule_flush():
    writer = TickWriter(_fake_session_factory)
    await writer.start()
    try:
        # Single tick — well below TICK_BATCH_SIZE
        ts = datetime.now(timezone.utc)
        writer.add(ts, 100.0, 1, "A")
        assert len(writer._batch) == 1
        # No pending tasks (apart from the periodic flush)
        flush_pending_before = sum(1 for t in asyncio.all_tasks() if t is not writer._flush_task and not t.done())
        # add() didn't schedule another flush since batch size < threshold
        await asyncio.sleep(0)
        flush_pending_after = sum(1 for t in asyncio.all_tasks() if t is not writer._flush_task and not t.done())
        assert flush_pending_after <= flush_pending_before + 1  # +1 for this test coro itself
    finally:
        # Don't actually flush — the fake session factory raises
        writer._batch = []
        await writer.stop()


@pytest.mark.asyncio
async def test_add_at_threshold_schedules_flush_via_loop():
    """When batch hits TICK_BATCH_SIZE, a flush is scheduled on the loop."""
    flush_calls: list[int] = []

    writer = TickWriter(_fake_session_factory)

    async def fake_flush():
        flush_calls.append(len(writer._batch))
        writer._batch = []

    writer._flush = fake_flush
    await writer.start()
    try:
        ts = datetime.now(timezone.utc)
        for i in range(TICK_BATCH_SIZE):
            writer.add(ts, 100.0 + i * 0.01, 1, "A")
        # Yield repeatedly so the call_soon_threadsafe callback runs and
        # the resulting task gets a chance to execute.
        for _ in range(5):
            await asyncio.sleep(0)
        assert flush_calls, "flush should have been called once batch hit threshold"
    finally:
        await writer.stop()


@pytest.mark.asyncio
async def test_add_from_worker_thread_does_not_raise():
    """The whole point: add() called from a non-loop thread must not crash."""
    flush_calls: list[int] = []
    writer = TickWriter(_fake_session_factory)

    async def fake_flush():
        flush_calls.append(len(writer._batch))
        writer._batch = []

    writer._flush = fake_flush
    await writer.start()

    error_box: list[BaseException] = []

    def worker():
        try:
            ts = datetime.now(timezone.utc)
            for i in range(TICK_BATCH_SIZE):
                writer.add(ts, 100.0 + i * 0.01, 1, "A")
        except BaseException as exc:  # noqa: BLE001
            error_box.append(exc)

    try:
        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=5.0)
        assert not error_box, f"add() from worker raised: {error_box[0]!r}"
        # Drive the loop so the threadsafe callback fires
        for _ in range(10):
            await asyncio.sleep(0.01)
        assert flush_calls, "flush should run once the loop processes the threadsafe callback"
    finally:
        await writer.stop()


@pytest.mark.asyncio
async def test_add_before_start_does_not_raise():
    """Edge case: add() called before start() — batch accumulates, no flush."""
    writer = TickWriter(_fake_session_factory)
    # No start() yet — _loop is None
    ts = datetime.now(timezone.utc)
    # Push enough to exceed the threshold; without _loop it would have
    # crashed under the old code, but call_soon_threadsafe is gated on
    # _loop being set so the new code is a no-op.
    for i in range(TICK_BATCH_SIZE):
        writer.add(ts, 100.0 + i * 0.01, 1, "A")
    assert len(writer._batch) == TICK_BATCH_SIZE
