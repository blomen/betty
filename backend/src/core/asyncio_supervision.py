"""Helpers for supervised fire-and-forget asyncio tasks.

`asyncio.create_task` returns a Task whose exceptions are silently swallowed
unless someone awaits or attaches a done callback. We have a number of
genuinely fire-and-forget background coroutines (revival workers, news
recorder, recompute jobs) where awaiting at the call site is wrong, but
losing exceptions makes incidents invisible until the next deploy.

`supervise_task` attaches a done callback that logs the exception with full
traceback. It also optionally keeps a strong reference (via a caller-owned
set) to prevent GC of the task before completion — Python may collect a
task that nobody references, even if it's still pending.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)


def supervise_task(
    coro: Coroutine[Any, Any, Any],
    *,
    name: str | None = None,
    keepalive: set[asyncio.Task[Any]] | None = None,
) -> asyncio.Task[Any]:
    """Spawn ``coro`` as a Task with exception logging on completion.

    Cancellation is treated as expected (not logged as an error).
    Pass ``keepalive`` (a long-lived set) to prevent GC of the task; the
    callback will discard from it on completion.
    """
    task = asyncio.create_task(coro, name=name)
    if keepalive is not None:
        keepalive.add(task)

    def _on_done(t: asyncio.Task[Any]) -> None:
        if keepalive is not None:
            keepalive.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error("Supervised task %r failed", t.get_name(), exc_info=exc)

    task.add_done_callback(_on_done)
    return task
