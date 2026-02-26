"""Extraction state management."""

from threading import Lock
from fastapi import WebSocket

# Extraction state (thread-safe)
extraction_state_lock = Lock()
extraction_state = {
    "running": False,
    "last_run": None,
    "start_time": None,
    "total_events": 0,
    "total_odds": 0,
    "providers": {},
    "current_provider": None,
    "completed_providers": 0,
    "total_providers": 0,
    "elapsed_seconds": 0,
}

# Per-tier extraction state (thread-safe)
tier_state_lock = Lock()
tier_states: dict[str, dict] = {}


def update_extraction_state(**kwargs):
    """Thread-safe update to extraction state."""
    with extraction_state_lock:
        extraction_state.update(kwargs)


def get_extraction_state():
    """Thread-safe read of extraction state."""
    with extraction_state_lock:
        return extraction_state.copy()


def update_tier_state(tier_name: str, **kwargs):
    """Thread-safe update to per-tier extraction state."""
    with tier_state_lock:
        if tier_name not in tier_states:
            tier_states[tier_name] = {
                "running": False,
                "last_run": None,
                "start_time": None,
                "total_events": 0,
                "total_odds": 0,
                "providers": {},
                "current_provider": None,
                "completed_providers": 0,
                "total_providers": 0,
                "elapsed_seconds": 0,
            }
        tier_states[tier_name].update(kwargs)


def get_tier_states() -> dict[str, dict]:
    """Thread-safe read of all tier states."""
    with tier_state_lock:
        return {k: v.copy() for k, v in tier_states.items()}


# WebSocket connection manager for real-time progress
class ConnectionManager:
    """Manages WebSocket connections for real-time updates."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        """Accept and store new connection."""
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        """Remove disconnected client."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients."""
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)

        # Clean up disconnected clients
        for conn in disconnected:
            self.disconnect(conn)


ws_manager = ConnectionManager()
recorder_ws_manager = ConnectionManager()
