"""Local firevstocks dashboard — serves UI + provides live data endpoints."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from pathlib import Path

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger(__name__)

SERVER_URL = "http://127.0.0.1:18000"
_SERVER_API_KEY = os.environ.get("FIREV_API_KEY", "aqxorczyd8rLzomW94nBjHWaa6tUh6NZ8aMktDbKMgI")


async def _proxy(path: str, params: dict | None = None):
    """Proxy GET to server via SSH tunnel. Returns {} on connection failure."""
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.get(f"{SERVER_URL}{path}", params=params,
                                 headers={"X-API-Key": _SERVER_API_KEY})
            return r.json()
    except Exception as exc:
        log.warning("Proxy %s failed: %s: %s", path, type(exc).__name__, exc)
        return {}

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

    dist_path = Path(__file__).parent.parent.parent.parent / "firevstocks" / "frontend" / "dist"
    if dist_path.exists() and (dist_path / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist_path / "assets"), name="assets")

        @app.get("/", response_class=HTMLResponse)
        async def index():
            return HTMLResponse((dist_path / "index.html").read_text(encoding="utf-8"))
    else:
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

    @app.get("/api/candles")
    async def proxy_candles(interval: str = "5m", days: int = 3, date: str | None = None):
        params = {"symbol": "NQ", "interval": interval, "days": str(days)}
        if date:
            params["date"] = date
        return await _proxy("/api/trading/market/candles", params)

    @app.get("/api/session")
    async def proxy_session():
        return await _proxy("/api/trading/market/session")

    @app.get("/api/session-levels")
    async def proxy_session_levels(days: int = 5):
        return await _proxy("/api/trading/market/session-levels",
                            {"symbol": "NQ", "days": str(days)})

    @app.get("/api/vp/{tf}")
    async def proxy_vp(tf: str):
        return await _proxy("/api/trading/market/volume-profile",
                            {"symbol": "NQ", "timeframe": tf})

    @app.get("/api/vwap")
    async def proxy_vwap():
        return await _proxy("/api/trading/market/vwap",
                            {"symbol": "NQ", "interval": "1m"})

    @app.get("/api/session-tpo")
    async def proxy_session_tpo():
        return await _proxy("/api/trading/market/tpo/sessions",
                            {"symbol": "NQ"})

    @app.get("/api/trades")
    async def get_trades():
        client = _state.get("topstepx_client")
        if not client:
            return {"trades": []}
        try:
            return await client._post("/api/Trade/search", {
                "accountId": client._account_id,
            })
        except Exception:
            return {"trades": []}

    @app.get("/api/account-info")
    async def get_account_info():
        client = _state.get("topstepx_client")
        if not client:
            return {}
        try:
            accounts = await client._post("/api/Account/search", {
                "onlyActiveAccounts": True,
            })
            return accounts[0] if accounts else {}
        except Exception:
            return {}

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


def record_fill(fill: dict) -> None:
    """Called from pipeline when a trade fill occurs."""
    _state["stats"]["trade_count"] += 1
    asyncio.create_task(broadcast({"type": "fill", **fill}))


def record_exit(exit_info: dict) -> None:
    """Called from pipeline when a trade exit occurs."""
    asyncio.create_task(broadcast({"type": "exit", **exit_info}))


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
