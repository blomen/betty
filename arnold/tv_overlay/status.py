"""Thread-safe overlay status snapshot."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import asdict, dataclass


@dataclass
class _Status:
    attached_clients: int = 0
    last_paint_at: float | None = None
    draw_count: int = 0
    error: str | None = None


_state = _Status()
_lock = threading.Lock()
# Ring of (epoch, message) tuples — preserves distinct diagnostic tags from
# the userscript (e.g. _groupDiag("first-group-created" / "no-shapesGroupController-method"))
# instead of letting each new error overwrite the previous one. 20 is plenty
# for the failure-mode-per-tag guard the userscript already does.
_recent_errors: deque[tuple[float, str]] = deque(maxlen=20)


def snapshot() -> _Status:
    with _lock:
        return _Status(**asdict(_state))


def get_status() -> dict:
    s = snapshot()
    with _lock:
        recent = [{"ts": ts, "message": m} for ts, m in _recent_errors]
    return {
        "attached_clients": s.attached_clients,
        "last_paint_at": s.last_paint_at,
        "draw_count": s.draw_count,
        "error": s.error,
        "recent_errors": recent,
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
        if err:
            _recent_errors.append((time.time(), err))
