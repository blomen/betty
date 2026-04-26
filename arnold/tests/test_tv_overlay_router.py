"""Smoke tests for the TV overlay router."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent))

from arnold.tv_overlay.router import create_router  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(create_router(), prefix="/stocks")
    return TestClient(app)


def test_status_endpoint_returns_zero_clients(client: TestClient) -> None:
    r = client.get("/stocks/api/tv-overlay/status")
    assert r.status_code == 200
    body = r.json()
    assert body["attached_clients"] == 0
    assert body["userscript_url"] == "/stocks/api/tv-overlay/userscript"


def test_userscript_endpoint_serves_javascript(client: TestClient) -> None:
    r = client.get("/stocks/api/tv-overlay/userscript")
    assert r.status_code == 200
    assert "javascript" in r.headers.get("content-type", "")
    body = r.text
    assert "==UserScript==" in body
    assert "@match" in body and "tradingview.com" in body


def test_websocket_attaches_and_increments_count(client: TestClient) -> None:
    with client.websocket_connect("/stocks/ws/tv-overlay") as ws:
        ws.send_json({"type": "hello", "version": "test"})
        # Server should accept; status reports 1 client.
        r = client.get("/stocks/api/tv-overlay/status")
        assert r.status_code == 200
        assert r.json()["attached_clients"] == 1
    # After disconnect, client count returns to 0.
    r = client.get("/stocks/api/tv-overlay/status")
    assert r.json()["attached_clients"] == 0
