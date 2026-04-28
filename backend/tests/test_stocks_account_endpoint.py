"""Tests for GET /api/stocks/account endpoint."""

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.stocks import router
from src.stocks.config import TopstepXConfig


def _make_app(
    *,
    runtime_present: bool = True,
    account_search_payload=None,
    account_search_raises: Exception | None = None,
    active_account_id: int = 21795795,
):
    app = FastAPI()
    app.include_router(router)

    if runtime_present:
        cfg = TopstepXConfig(max_trailing_dd=5000.0, max_daily_loss=1500.0)
        client = MagicMock()
        client._config = cfg
        client._account_id = active_account_id
        if account_search_raises is not None:
            client._post = AsyncMock(side_effect=account_search_raises)
        else:
            client._post = AsyncMock(return_value=account_search_payload)

        runtime = MagicMock()
        runtime.client = client
        app.state.stocks_runtime = runtime

    return app


_LIVE_PAYLOAD = {
    "accounts": [
        {
            "id": 21480650,
            "name": "50KTC-V2-574123-24319286",
            "balance": 50000.0,
            "canTrade": True,
            "isVisible": True,
            "simulated": True,
        },
        {
            "id": 21795795,
            "name": "PRAC-V2-574123-23514304",
            "balance": 163792.5,
            "canTrade": True,
            "isVisible": True,
            "simulated": True,
        },
    ],
    "success": True,
    "errorCode": 0,
    "errorMessage": None,
}


def test_account_endpoint_returns_nested_prop_firm_shape():
    app = _make_app(account_search_payload=_LIVE_PAYLOAD)
    client = TestClient(app)
    resp = client.get("/api/stocks/account")
    assert resp.status_code == 200
    body = resp.json()
    assert "prop_firms" in body
    assert len(body["prop_firms"]) == 1
    firm = body["prop_firms"][0]
    assert firm["id"] == "topstepx"
    assert firm["name"] == "TopstepX"
    assert len(firm["accounts"]) == 2


def test_active_account_has_limits_and_inactive_does_not():
    app = _make_app(account_search_payload=_LIVE_PAYLOAD, active_account_id=21795795)
    client = TestClient(app)
    body = client.get("/api/stocks/account").json()
    accounts = body["prop_firms"][0]["accounts"]
    by_id = {a["id"]: a for a in accounts}

    active = by_id[21795795]
    inactive = by_id[21480650]

    assert active["active"] is True
    assert inactive["active"] is False
    assert active["limits"] == {"max_trailing_dd": 5000.0, "max_daily_loss": 1500.0}
    assert inactive["limits"] is None


def test_account_fields_use_snake_case():
    app = _make_app(account_search_payload=_LIVE_PAYLOAD)
    client = TestClient(app)
    body = client.get("/api/stocks/account").json()
    a = body["prop_firms"][0]["accounts"][0]

    assert "can_trade" in a
    assert "canTrade" not in a
    assert isinstance(a["can_trade"], bool)


def test_product_derived_from_account_name_prefix():
    app = _make_app(account_search_payload=_LIVE_PAYLOAD)
    client = TestClient(app)
    body = client.get("/api/stocks/account").json()
    by_id = {a["id"]: a for a in body["prop_firms"][0]["accounts"]}

    assert by_id[21795795]["product"] == "PRAC"
    assert by_id[21480650]["product"] == "50KTC"


def test_returns_empty_when_runtime_missing():
    app = _make_app(runtime_present=False)
    client = TestClient(app)
    resp = client.get("/api/stocks/account")
    assert resp.status_code == 200
    assert resp.json() == {"prop_firms": []}


def test_topstepx_failure_with_no_cache_returns_empty():
    app = _make_app(account_search_raises=RuntimeError("boom"))
    client = TestClient(app)
    resp = client.get("/api/stocks/account")
    assert resp.status_code == 200
    assert resp.json() == {"prop_firms": []}


def test_topstepx_failure_with_cache_returns_cached_payload():
    app = _make_app(account_search_payload=_LIVE_PAYLOAD)
    client = TestClient(app)
    first = client.get("/api/stocks/account").json()
    assert first["prop_firms"][0]["accounts"]

    # Now make TopstepX fail; cache should be served
    runtime = app.state.stocks_runtime
    runtime.client._post = AsyncMock(side_effect=RuntimeError("transient"))
    second = client.get("/api/stocks/account").json()
    assert second == first
