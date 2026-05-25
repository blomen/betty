"""Local SSE broadcaster for mirror events."""

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class MirrorBroadcaster:
    """Fan-out broadcaster: mirror loops publish, frontend SSE consumes."""

    def __init__(self):
        self._clients: dict[int, asyncio.Queue] = {}
        self._counter = 0

    def subscribe(self) -> tuple[int, asyncio.Queue]:
        self._counter += 1
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._clients[self._counter] = q
        return self._counter, q

    def unsubscribe(self, client_id: int) -> None:
        self._clients.pop(client_id, None)

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        message = {"event": event_type, "data": data}
        dead = []
        for cid, q in self._clients.items():
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                dead.append(cid)
        for cid in dead:
            self._clients.pop(cid, None)
        try:
            from .state_writer import write_event

            write_event(event_type, data)
        except Exception as e:
            logger.debug(f"[broadcaster] state_writer.write_event failed: {e!r}")


mirror_broadcaster = MirrorBroadcaster()
