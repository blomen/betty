"""Tests for the mirror router."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from firevsports.mirror.router import create_mirror_router


def _make_browser(running: bool = False, pages: list | None = None):
    """Return a mock MirrorBrowser."""
    browser = MagicMock()
    browser.running = running
    _pages = pages or []
    browser.context = MagicMock()
    browser.context.pages = _pages
    browser.get_status.return_value = {
        "running": running,
        "tabs": len(_pages),
        "pages": [{"url": p.url, "title": ""} for p in _pages],
    }
    browser.start = AsyncMock(return_value=browser.context)
    browser.stop = AsyncMock(return_value=None)
    browser.open_tab = AsyncMock(return_value=MagicMock(url="https://example.com/new"))
    return browser


def _make_app(browser) -> FastAPI:
    from mirror.sse import MirrorBroadcaster
    app = FastAPI()
    app.include_router(create_mirror_router(browser, MirrorBroadcaster(), "http://localhost:18000"))
    return app


# ---------------------------------------------------------------------------
# GET /mirror/status
# ---------------------------------------------------------------------------

def test_status_not_running():
    """GET /mirror/status returns running=False when browser is off."""
    browser = _make_browser(running=False)
    client = TestClient(_make_app(browser))
    resp = client.get("/mirror/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["running"] is False
    assert data["tabs"] == 0
    assert data["pages"] == []


def test_status_running_with_tabs():
    """GET /mirror/status reflects open tabs when browser is running."""
    page = MagicMock()
    page.url = "https://pinnacle.se/betting"
    browser = _make_browser(running=True, pages=[page])
    client = TestClient(_make_app(browser))
    resp = client.get("/mirror/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["running"] is True
    assert data["tabs"] == 1
    assert len(data["pages"]) == 1
    assert data["pages"][0]["url"] == "https://pinnacle.se/betting"


# ---------------------------------------------------------------------------
# POST /mirror/start
# ---------------------------------------------------------------------------

def test_start_returns_status():
    """POST /mirror/start calls browser.start() and returns updated status."""
    browser = _make_browser(running=False)
    # After start, get_status should reflect running=True
    browser.get_status.side_effect = [
        {"running": False, "tabs": 0, "pages": []},  # initial (unused by start)
        {"running": True, "tabs": 0, "pages": []},
    ]
    client = TestClient(_make_app(browser))
    resp = client.post("/mirror/start")
    assert resp.status_code == 200
    browser.start.assert_awaited_once()


# ---------------------------------------------------------------------------
# POST /mirror/stop
# ---------------------------------------------------------------------------

def test_stop_calls_browser_stop():
    """POST /mirror/stop calls browser.stop()."""
    browser = _make_browser(running=True)
    client = TestClient(_make_app(browser))
    resp = client.post("/mirror/stop")
    assert resp.status_code == 200
    browser.stop.assert_awaited_once()


# ---------------------------------------------------------------------------
# POST /mirror/navigate
# ---------------------------------------------------------------------------

def test_navigate_400_when_not_running():
    """POST /mirror/navigate returns 400 if browser is not running."""
    browser = _make_browser(running=False)
    client = TestClient(_make_app(browser))
    resp = client.post("/mirror/navigate", json={
        "provider_id": "pinnacle",
        "event_id": "123",
        "market": "1x2",
        "outcome": "home",
        "odds": 1.85,
        "fair_odds": 1.80,
        "stake": 50.0,
        "display_home": "Arsenal",
        "display_away": "Chelsea",
    })
    assert resp.status_code == 400


def test_navigate_404_when_no_tab_found():
    """POST /mirror/navigate returns 404 when the provider tab is missing."""
    browser = _make_browser(running=True, pages=[])

    mock_workflow = MagicMock()
    mock_workflow.domain = "pinnacle.se"
    mock_workflow.find_tab = AsyncMock(return_value=None)

    with patch("firevsports.mirror.router.get_workflow", return_value=mock_workflow):
        client = TestClient(_make_app(browser))
        resp = client.post("/mirror/navigate", json={
            "provider_id": "pinnacle",
            "event_id": "123",
            "market": "1x2",
            "outcome": "home",
            "odds": 1.85,
            "fair_odds": 1.80,
            "stake": 50.0,
            "display_home": "Arsenal",
            "display_away": "Chelsea",
        })
    assert resp.status_code == 404


def test_navigate_success():
    """POST /mirror/navigate calls navigate_to_event and returns success."""
    page = MagicMock()
    page.url = "https://pinnacle.se/betting/soccer/arsenal-vs-chelsea"
    browser = _make_browser(running=True, pages=[page])

    mock_workflow = MagicMock()
    mock_workflow.domain = "pinnacle.se"
    mock_workflow.find_tab = AsyncMock(return_value=page)
    mock_workflow.navigate_to_event = AsyncMock(return_value=True)

    with patch("firevsports.mirror.router.get_workflow", return_value=mock_workflow):
        client = TestClient(_make_app(browser))
        resp = client.post("/mirror/navigate", json={
            "provider_id": "pinnacle",
            "event_id": "123",
            "market": "1x2",
            "outcome": "home",
            "odds": 1.85,
            "fair_odds": 1.80,
            "stake": 50.0,
            "display_home": "Arsenal",
            "display_away": "Chelsea",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    mock_workflow.navigate_to_event.assert_awaited_once()


# ---------------------------------------------------------------------------
# POST /mirror/place
# ---------------------------------------------------------------------------

def test_place_400_when_not_running():
    """POST /mirror/place returns 400 if browser is not running."""
    browser = _make_browser(running=False)
    client = TestClient(_make_app(browser))
    resp = client.post("/mirror/place", json={"provider_id": "pinnacle", "bet_id": 42})
    assert resp.status_code == 400


def test_place_404_when_no_tab_found():
    """POST /mirror/place returns 404 when the provider tab is missing."""
    browser = _make_browser(running=True, pages=[])

    mock_workflow = MagicMock()
    mock_workflow.domain = "pinnacle.se"
    mock_workflow.find_tab = AsyncMock(return_value=None)

    with patch("firevsports.mirror.router.get_workflow", return_value=mock_workflow):
        client = TestClient(_make_app(browser))
        resp = client.post("/mirror/place", json={"provider_id": "pinnacle", "bet_id": 42})
    assert resp.status_code == 404


def test_place_success():
    """POST /mirror/place calls place_bet and returns placement result."""
    from firevsports.mirror.workflows.base import PlacementResult

    page = MagicMock()
    page.url = "https://pinnacle.se/betting"
    browser = _make_browser(running=True, pages=[page])

    mock_result = PlacementResult(
        status="placed", bet_id=42, actual_odds=1.85, actual_stake=50.0
    )
    mock_workflow = MagicMock()
    mock_workflow.domain = "pinnacle.se"
    mock_workflow.find_tab = AsyncMock(return_value=page)
    mock_workflow.place_bet = AsyncMock(return_value=mock_result)

    with patch("firevsports.mirror.router.get_workflow", return_value=mock_workflow):
        client = TestClient(_make_app(browser))
        resp = client.post("/mirror/place", json={"provider_id": "pinnacle", "bet_id": 42})

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "placed"
    assert data["bet_id"] == 42
    assert data["actual_odds"] == 1.85
    mock_workflow.place_bet.assert_awaited_once()


# ---------------------------------------------------------------------------
# POST /mirror/open-tab
# ---------------------------------------------------------------------------

def test_open_tab_400_when_not_running():
    """POST /mirror/open-tab returns 400 if browser is not running."""
    browser = _make_browser(running=False)
    client = TestClient(_make_app(browser))
    resp = client.post("/mirror/open-tab", json={"url": "https://example.com"})
    assert resp.status_code == 400


def test_open_tab_success():
    """POST /mirror/open-tab calls browser.open_tab and returns url."""
    browser = _make_browser(running=True)
    browser.open_tab = AsyncMock(return_value=MagicMock(url="https://example.com/new"))
    client = TestClient(_make_app(browser))
    resp = client.post("/mirror/open-tab", json={"url": "https://example.com"})
    assert resp.status_code == 200
    assert resp.json()["url"] == "https://example.com/new"
    browser.open_tab.assert_awaited_once_with("https://example.com")
