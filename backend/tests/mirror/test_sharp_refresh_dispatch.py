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
            "local.mirror.router._persist_sharp_outcomes",
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

    async def fake_persist(*, provider_id, event_id, market, point, result, outcome=None):
        posts.append(
            {
                "provider_id": provider_id,
                "event_id": event_id,
                "market": market,
                "point": point,
                "outcome": outcome,
                "n_outcomes": sum(len(mk.get("prices", [])) for mk in result.get("markets", [])),
            }
        )

    with (
        patch(
            "local.mirror.router._pinnacle_fetch_markets_for_router",
            new=AsyncMock(return_value=FAKE_RESP),
        ),
        patch("local.mirror.router._persist_sharp_outcomes", new=fake_persist),
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


def test_select_pinnacle_market_moneyline():
    from local.mirror.router import _select_pinnacle_market

    markets = [
        {"key": "s;0;m", "period": 0, "prices": [{"designation": "home"}]},
        {"key": "s;1;m", "period": 1, "prices": []},
    ]
    assert _select_pinnacle_market(markets, "moneyline", None) is not None
    assert _select_pinnacle_market(markets, "moneyline", None)["key"] == "s;0;m"


def test_select_pinnacle_market_spread_home():
    from local.mirror.router import _select_pinnacle_market

    markets = [
        {
            "key": "s;0;s;1.5",
            "period": 0,
            "prices": [
                {"designation": "home", "points": 1.5},
                {"designation": "away", "points": -1.5},
            ],
        },
    ]
    m = _select_pinnacle_market(markets, "spread", 1.5, outcome="home")
    assert m is not None
    assert m["key"] == "s;0;s;1.5"


def test_select_pinnacle_market_spread_away_sign_flip():
    """Betty stores team-perspective: away@-1.5. Pinnacle keys home-perspective.
    Refresh of an away row with point=-1.5 must find the home-perspective market `s;0;s;1.5`."""
    from local.mirror.router import _select_pinnacle_market

    markets = [
        {
            "key": "s;0;s;1.5",
            "period": 0,
            "prices": [
                {"designation": "home", "points": 1.5},
                {"designation": "away", "points": -1.5},
            ],
        },
    ]
    m = _select_pinnacle_market(markets, "spread", -1.5, outcome="away")
    assert m is not None, "away-spread sign-flip not handled"
    assert m["key"] == "s;0;s;1.5"


def test_select_pinnacle_market_total():
    from local.mirror.router import _select_pinnacle_market

    markets = [
        {
            "key": "s;0;ou;2.5",
            "period": 0,
            "prices": [
                {"designation": "over", "points": 2.5},
                {"designation": "under", "points": 2.5},
            ],
        },
    ]
    m = _select_pinnacle_market(markets, "total", 2.5)
    assert m is not None
    assert m["key"] == "s;0;ou;2.5"


def test_select_pinnacle_market_float_tolerance():
    """Pinnacle may key as 's;0;s;-2.0' while betty stores -2.0 as int -2 in JSON."""
    from local.mirror.router import _select_pinnacle_market

    markets = [
        {"key": "s;0;s;-2.0", "period": 0, "prices": [{"designation": "home", "points": -2.0}]},
    ]
    m = _select_pinnacle_market(markets, "spread", -2, outcome="home")
    assert m is not None, "float-tolerant matching failed (-2 vs -2.0)"


def test_pinnacle_persist_spread_uses_per_price_points(client):
    """Spread refresh: each persisted row uses the price's own team-perspective
    point, not the request body's point. Catches the away-spread bug where
    the home update was being POSTed with the away point and silently no-op'd."""
    fake_response = {
        "matchup_id": 1234567,
        "requested_id": 1234567,
        "league": "NFL",
        "sport": "Football",
        "participants": ["A", "B"],
        "is_live": False,
        "status": "pending",
        "markets": [
            {
                "key": "s;0;s;1.5",
                "period": 0,
                "prices": [
                    {"designation": "home", "american": -150, "decimal": 1.67, "points": 1.5},
                    {"designation": "away", "american": +130, "decimal": 2.30, "points": -1.5},
                ],
            },
        ],
    }
    posts: list[dict] = []

    class FakeResp:
        status_code = 200

    async def fake_post(url, *, json=None, **kw):
        posts.append({"url": url, "json": json})
        return FakeResp()

    class FakeClient:
        async def post(self, url, *, json=None, **kw):
            return await fake_post(url, json=json, **kw)

    def fake_tunnel_client():
        return FakeClient()

    with (
        patch("local.mirror.router._pinnacle_fetch_markets_for_router", new=AsyncMock(return_value=fake_response)),
        patch("local.http_client.tunnel_client", new=fake_tunnel_client),
    ):
        # Away-spread refresh request: point=-1.5, outcome=away
        client.post(
            "/mirror/sharp/refresh-event",
            json={
                "provider_id": "pinnacle",
                "matchup_id": "1234567",
                "event_id": "evt-spread",
                "market": "spread",
                "point": -1.5,
                "outcome": "away",
            },
        )

    persist_posts = [p for p in posts if "odds/live-update" in p["url"]]
    assert len(persist_posts) == 2, f"expected 2 persist calls, got {len(persist_posts)}"
    by_outcome = {p["json"]["outcome"]: p["json"] for p in persist_posts}
    assert by_outcome["home"]["point"] == 1.5, "home row must persist with team-perspective +1.5, not -1.5"
    assert by_outcome["away"]["point"] == -1.5, "away row must persist with team-perspective -1.5"
    assert by_outcome["home"]["odds"] == 1.67
    assert by_outcome["away"]["odds"] == 2.30
