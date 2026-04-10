"""Local firevstocks dashboard — serves UI + provides live data endpoints."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

log = logging.getLogger(__name__)

# Shared state — populated by the pipeline
_state = {
    "ticks": deque(maxlen=2000),        # last 2000 ticks for chart
    "signals": deque(maxlen=100),       # last 100 signals
    "quotes": deque(maxlen=1),          # latest quote
    "zones": [],                        # current zones from server
    "account": {},                      # TopstepX account info
    "positions": [],                    # open positions
    "stats": {                          # session stats
        "tick_count": 0,
        "signal_count": 0,
        "trade_count": 0,
        "session_start": None,
        "relay_connected": False,
        "stream_running": False,
    },
}

_dashboard_clients: list[WebSocket] = []


def create_dashboard_app() -> FastAPI:
    app = FastAPI(title="firevstocks Dashboard")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        html_path = Path(__file__).parent / "dashboard.html"
        return HTMLResponse(html_path.read_text(encoding="utf-8"))

    @app.get("/api/state")
    async def get_state():
        return {
            "ticks": list(_state["ticks"])[-200:],  # last 200 for initial chart
            "signals": list(_state["signals"]),
            "quote": list(_state["quotes"])[-1] if _state["quotes"] else None,
            "zones": _state["zones"],
            "account": _state["account"],
            "positions": _state["positions"],
            "stats": _state["stats"],
        }

    @app.websocket("/ws/dashboard")
    async def dashboard_ws(ws: WebSocket):
        await ws.accept()
        _dashboard_clients.append(ws)
        try:
            while True:
                await ws.receive_text()  # keep alive / ping-pong
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            if ws in _dashboard_clients:
                _dashboard_clients.remove(ws)

    return app


async def broadcast(event: dict) -> None:
    """Push event to all connected dashboard clients."""
    if not _dashboard_clients:
        return
    msg = json.dumps(event, default=str)
    dead = []
    for ws in _dashboard_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in _dashboard_clients:
            _dashboard_clients.remove(ws)


def record_tick(price: float, size: int, ts: float, side: str = "B") -> None:
    """Called from pipeline on each tick. Throttles dashboard broadcasts to every 10th tick."""
    _state["ticks"].append({"p": price, "s": size, "t": ts, "d": side})
    _state["stats"]["tick_count"] += 1
    if _state["stats"]["tick_count"] % 10 == 0:
        asyncio.create_task(broadcast({
            "type": "tick",
            "price": price,
            "ts": ts,
            "tick_count": _state["stats"]["tick_count"],
        }))


def record_quote(quote: dict) -> None:
    """Called from pipeline on each quote update."""
    _state["quotes"].append(quote)
    asyncio.create_task(broadcast({"type": "quote", **quote}))


def record_signal(signal: dict) -> None:
    """Called from pipeline when the server sends a trading signal."""
    _state["signals"].append(signal)
    _state["stats"]["signal_count"] += 1
    asyncio.create_task(broadcast({"type": "signal", **signal}))


def update_zones(zones: list) -> None:
    """Called from pipeline when zone data is received from server."""
    _state["zones"] = zones
    asyncio.create_task(broadcast({"type": "zones", "zones": zones}))


def update_account(account: dict) -> None:
    """Called from pipeline with TopstepX account info."""
    _state["account"] = account
    asyncio.create_task(broadcast({"type": "account", **account}))


def update_positions(positions: list) -> None:
    """Called from pipeline with open position data."""
    _state["positions"] = positions
    asyncio.create_task(broadcast({"type": "positions", "positions": positions}))


def update_status(relay_connected: bool, stream_running: bool) -> None:
    """Called from health-check loop to update connection status."""
    _state["stats"]["relay_connected"] = relay_connected
    _state["stats"]["stream_running"] = stream_running
    asyncio.create_task(broadcast({
        "type": "status",
        "relay_connected": relay_connected,
        "stream_running": stream_running,
    }))
