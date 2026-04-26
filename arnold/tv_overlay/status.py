"""Thread-safe overlay status snapshot."""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass


@dataclass
class _Status:
    attached_clients: int = 0
    last_paint_at: float | None = None
    draw_count: int = 0
    error: str | None = None


_state = _Status()
_lock = threading.Lock()


def snapshot() -> _Status:
    with _lock:
        return _Status(**asdict(_state))


def get_status() -> dict:
    s = snapshot()
    return {
        "attached_clients": s.attached_clients,
        "last_paint_at": s.last_paint_at,
        "draw_count": s.draw_count,
        "error": s.error,
        "userscript_url": "/stocks/api/tv-overlay/userscript",
    }


def client_attached() -> None:
    with _lock:
        _state.attached_clients += 1


def client_detached() -> None:
    with _lock:
        _state.attached_clients = max(0, _state.attached_clients - 1)


def record_paint(count: int = 1) -> None:
    with _lock:
        _state.draw_count += count
        _state.last_paint_at = time.time()


def set_error(err: str | None) -> None:
    with _lock:
        _state.error = err
