"""SSE broadcast channel for real-time odds/opportunity updates."""
import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class Broadcaster:
    """Fan-out broadcaster: one producer, many SSE consumers."""

    def __init__(self):
        self._clients: dict[int, asyncio.Queue] = {}
        self._counter = 0

    def subscribe(self) -> tuple[int, asyncio.Queue]:
        """Register a new SSE client. Returns (client_id, queue)."""
        self._counter += 1
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._clients[self._counter] = q
        logger.info(f"SSE client {self._counter} connected ({len(self._clients)} total)")
        return self._counter, q

    def unsubscribe(self, client_id: int) -> None:
        """Remove an SSE client."""
        self._clients.pop(client_id, None)
        logger.info(f"SSE client {client_id} disconnected ({len(self._clients)} remaining)")

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        """Push an event to all connected clients. Non-blocking; drops if queue full."""
        message = {"event": event_type, "data": data}
        dead = []
        for cid, q in self._clients.items():
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                dead.append(cid)
        for cid in dead:
            logger.warning(f"SSE client {cid} queue full, disconnecting")
            self._clients.pop(cid, None)

    @property
    def client_count(self) -> int:
        return len(self._clients)


# Singleton instance — imported by orchestrator (publish) and extraction routes (subscribe)
odds_broadcaster = Broadcaster()
