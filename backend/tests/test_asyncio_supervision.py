"""Audit #36: supervise_task helper logs orphan-task exceptions.

The bare `asyncio.create_task(coro)` pattern silently swallows unhandled
exceptions. supervise_task adds a done callback that logs them so failed
revival workers / news recorders / etc. become visible in production logs.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from src.core.asyncio_supervision import supervise_task


async def _raises(msg: str = "boom") -> None:
    raise RuntimeError(msg)


async def _ok() -> None:
    return None


async def _slow() -> None:
    await asyncio.sleep(60)


@pytest.mark.asyncio
async def test_supervise_logs_unhandled_exception(caplog):
    caplog.set_level(logging.ERROR, logger="src.core.asyncio_supervision")
    task = supervise_task(_raises("kaboom"), name="oops")
    # Wait for the task + the callback to fire
    with pytest.raises(RuntimeError, match="kaboom"):
        await task
    # Done callbacks fire after the task transitions to done; let the loop spin
    await asyncio.sleep(0)

    matching = [r for r in caplog.records if "Supervised task 'oops' failed" in r.getMessage()]
    assert matching, f"expected error log, got: {[r.getMessage() for r in caplog.records]}"


@pytest.mark.asyncio
async def test_supervise_does_not_log_on_success(caplog):
    caplog.set_level(logging.ERROR, logger="src.core.asyncio_supervision")
    await supervise_task(_ok(), name="happy")
    await asyncio.sleep(0)
    assert not any("Supervised task" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_supervise_does_not_log_on_cancel(caplog):
    caplog.set_level(logging.ERROR, logger="src.core.asyncio_supervision")
    task = supervise_task(_slow(), name="cancelme")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert not any("Supervised task" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_supervise_keepalive_set_discards_on_done():
    keepalive: set[asyncio.Task] = set()
    task = supervise_task(_ok(), name="kept", keepalive=keepalive)
    assert task in keepalive
    await task
    await asyncio.sleep(0)
    assert task not in keepalive
