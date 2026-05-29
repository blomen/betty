"""Dispatch contract for POST /mirror/sharp/refresh-event.

Pinnacle  reuses _pinnacle_fetch_markets (mocked here), persists each leg via
the live-update endpoint (mocked).
Polymarket / Kalshi  501 (no per-event endpoint, deferred).
Unknown provider  400.
Missing matchup_id for pinnacle  400.
Missing event_id  400.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from local.mirror.router import create_mirror_router


def _make_browser():
    """Minimal MirrorBrowser mock  the dispatch endpoint itself does not
    touch the browser; the patched _pinnacle_fetch_markets bypasses that.
    """
    browser = MagicMock()
    browser.running = True
    browser.context = MagicMock()
    browser.context.pages = []
    return browser


@pytest.fixture
def client():
    from local.mirror.sse import MirrorBroadcaster

    # create_mirror_router calls PendingLoop.start(), which schedules an
    # asyncio.create_task() — that needs a running event loop. TestClient's
    # sync interface runs the app via a per-request loop, so no loop exists
    # at router-construction time. Stub the loop start to a no-op for tests.
    with patch("local.mirror.router.PendingLoop.start", return_value=None):
        app = FastAPI()
        app.include_router(create_mirror_router(_make_browser(), MirrorBroadcaster(), "http://localhost:18000"))
    return TestClient(app)


FAKE_RESP = {
    "matchup_id": 1234567,
    "requested_id": 1234567,
    "league": "WTA",
    "sport": "Tennis",
    "participants": ["Linette", "Swiatek"],
    "is_live": False,
    "status": "pending",
    "markets": [
        {
            "key": "s;0;m",
            "period": 0,
            "prices": [
                {"designation": "home", "american": 1450, "decimal": 14.51, "points": None},
                {"designation": "away", "american": -2500, "decimal": 1.04, "points": None},
            ],
        },
    ],
}


def test_pinnacle_dispatch_returns_markets(client):
    with (
        patch(
            "local.mirror.router._pinnacle_fetch_markets_for_router",
            new=AsyncMock(return_value=FAKE_RESP),
        ),
        patch(
            "local.mirror.router._persist_sharp_market",
            new=AsyncMock(return_value=None),
        ),
    ):
        resp = client.post(
            "/mirror/sharp/refresh-event",
            json={
                "provider_id": "pinnacle",
                "matchup_id": "1234567",
                "event_id": "evt-linette-1",
                "market": "moneyline",
                "point": None,
            },
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["provider_id"] == "pinnacle"
    assert isinstance(data["markets"], list)
    assert data["markets"][0]["prices"][0]["decimal"] == 14.51


def test_polymarket_returns_501(client):
    resp = client.post(
        "/mirror/sharp/refresh-event",
        json={
            "provider_id": "polymarket",
            "matchup_id": "n/a",
            "event_id": "evt-x",
            "market": "moneyline",
            "point": None,
        },
    )
    assert resp.status_code == 501
    assert "polymarket" in resp.json()["detail"].lower()


def test_kalshi_returns_501(client):
    resp = client.post(
        "/mirror/sharp/refresh-event",
        json={
            "provider_id": "kalshi",
            "matchup_id": "n/a",
            "event_id": "evt-x",
            "market": "moneyline",
            "point": None,
        },
    )
    assert resp.status_code == 501


def test_unknown_provider_returns_400(client):
    resp = client.post(
        "/mirror/sharp/refresh-event",
        json={
            "provider_id": "definitely-not-real",
            "matchup_id": "1",
            "event_id": "e",
            "market": "moneyline",
            "point": None,
        },
    )
    assert resp.status_code == 400


def test_pinnacle_missing_matchup_id_returns_400(client):
    resp = client.post(
        "/mirror/sharp/refresh-event",
        json={
            "provider_id": "pinnacle",
            "matchup_id": "",
            "event_id": "e",
            "market": "moneyline",
            "point": None,
        },
    )
    assert resp.status_code == 400


def test_missing_event_id_returns_400(client):
    resp = client.post(
        "/mirror/sharp/refresh-event",
        json={
            "provider_id": "pinnacle",
            "matchup_id": "1234567",
            "market": "moneyline",
            "point": None,
        },
    )
    assert resp.status_code == 400


def test_pinnacle_persists_each_leg_to_live_update(client):
    posts: list[dict] = []

    async def fake_persist(*, provider_id, event_id, market, point, result):
        posts.append(
            {
                "provider_id": provider_id,
                "event_id": event_id,
                "market": market,
                "point": point,
                "n_outcomes": sum(len(mk.get("prices", [])) for mk in result.get("markets", [])),
            }
        )

    with (
        patch(
            "local.mirror.router._pinnacle_fetch_markets_for_router",
            new=AsyncMock(return_value=FAKE_RESP),
        ),
        patch("local.mirror.router._persist_sharp_market", new=fake_persist),
    ):
        client.post(
            "/mirror/sharp/refresh-event",
            json={
                "provider_id": "pinnacle",
                "matchup_id": "1234567",
                "event_id": "evt-x",
                "market": "moneyline",
                "point": None,
            },
        )
    assert len(posts) == 1
    assert posts[0]["provider_id"] == "pinnacle"
    assert posts[0]["event_id"] == "evt-x"
    assert posts[0]["market"] == "moneyline"
    assert posts[0]["n_outcomes"] == 2
