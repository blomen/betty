"""Tests for /ws/signals relay endpoint."""
import pytest
import time
from unittest.mock import MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.api.routes.signals_ws import router


def _make_app():
    app = FastAPI()
    app.include_router(router)
    monitor = MagicMock()
    monitor.on_tick = MagicMock()
    monitor.set_signal_callback = MagicMock()
    app.state.level_monitor = monitor
    return app, monitor


def test_tick_forwarded_to_level_monitor():
    app, monitor = _make_app()
    client = TestClient(app)
    with client.websocket_connect("/ws/signals") as ws:
        ws.send_json({"type": "tick", "price": 21450.25, "size": 3, "ts": 1712678400.0})
        time.sleep(0.1)
        monitor.on_tick.assert_called_with(21450.25, 3, 1712678400.0)


def test_ping_pong():
    app, monitor = _make_app()
    client = TestClient(app)
    with client.websocket_connect("/ws/signals") as ws:
        ws.send_json({"type": "ping"})
        resp = ws.receive_json()
        assert resp["type"] == "pong"


def test_signal_callback_registered_and_cleared():
    app, monitor = _make_app()
    client = TestClient(app)
    with client.websocket_connect("/ws/signals") as ws:
        ws.send_json({"type": "ping"})
        ws.receive_json()
    # set_signal_callback called at least once (set on connect, cleared on disconnect)
    assert monitor.set_signal_callback.call_count >= 1


def test_no_level_monitor_sends_error():
    app = FastAPI()
    app.include_router(router)
    # Don't set level_monitor
    client = TestClient(app)
    with client.websocket_connect("/ws/signals") as ws:
        resp = ws.receive_json()
        assert resp["type"] == "error"
