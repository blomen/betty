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
