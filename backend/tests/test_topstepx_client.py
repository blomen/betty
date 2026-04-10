"""Tests for TopstepXConfig and TopstepXClient."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from src.stocks.config import TopstepXConfig
from src.stocks.topstepx_client import TopstepXClient, SIDE_BUY, SIDE_SELL


# ---------------------------------------------------------------------------
# TopstepXConfig tests
# ---------------------------------------------------------------------------


class TestTopstepXConfig:
    def test_defaults(self):
        cfg = TopstepXConfig()
        assert cfg.username == ""
        assert cfg.api_key == ""
        assert cfg.contract_id == "CON.F.US.ENQ.M26"
        assert cfg.base_url == "https://api.topstepx.com"
        assert cfg.market_hub_url == "wss://rtc.topstepx.com/hubs/market"
        assert cfg.user_hub_url == "wss://rtc.topstepx.com/hubs/user"
        assert cfg.server_ws_url == "ws://127.0.0.1:18000/ws/signals"
        assert cfg.max_position == 2
        assert cfg.max_daily_loss == 1000.0
        assert cfg.max_trailing_dd == 2000.0
        assert cfg.flatten_et == "15:55"

    def test_is_configured_false_when_empty(self):
        cfg = TopstepXConfig()
        assert cfg.is_configured is False

    def test_is_configured_false_when_only_username(self):
        cfg = TopstepXConfig(username="user")
        assert cfg.is_configured is False

    def test_is_configured_false_when_only_api_key(self):
        cfg = TopstepXConfig(api_key="key")
        assert cfg.is_configured is False

    def test_is_configured_true_when_both_set(self):
        cfg = TopstepXConfig(username="user", api_key="key")
        assert cfg.is_configured is True

    def test_from_env_defaults(self):
        """from_env falls back to defaults when env vars are absent."""
        env_keys = [
            "TOPSTEPX_USERNAME", "TOPSTEPX_API_KEY", "TOPSTEPX_CONTRACT",
            "TOPSTEPX_BASE_URL", "TOPSTEPX_MARKET_HUB_URL", "TOPSTEPX_USER_HUB_URL",
            "TOPSTEPX_SERVER_WS_URL", "TOPSTEPX_MAX_POSITION",
            "TOPSTEPX_MAX_DAILY_LOSS", "TOPSTEPX_MAX_TRAILING_DD", "TOPSTEPX_FLATTEN_ET",
        ]
        clean_env = {k: v for k, v in os.environ.items() if k not in env_keys}
        with patch.dict(os.environ, clean_env, clear=True):
            cfg = TopstepXConfig.from_env()
        assert cfg.username == ""
        assert cfg.api_key == ""
        assert cfg.contract_id == "CON.F.US.ENQ.M26"
        assert cfg.max_position == 2
        assert cfg.max_daily_loss == 1000.0
        assert cfg.flatten_et == "15:55"

    def test_from_env_reads_env_vars(self):
        env = {
            "TOPSTEPX_USERNAME": "testuser",
            "TOPSTEPX_API_KEY": "testkey",
            "TOPSTEPX_CONTRACT": "CON.F.US.ES.M25",
            "TOPSTEPX_MAX_POSITION": "5",
            "TOPSTEPX_MAX_DAILY_LOSS": "500.0",
            "TOPSTEPX_MAX_TRAILING_DD": "1500.0",
            "TOPSTEPX_FLATTEN_ET": "16:00",
        }
        with patch.dict(os.environ, env):
            cfg = TopstepXConfig.from_env()
        assert cfg.username == "testuser"
        assert cfg.api_key == "testkey"
        assert cfg.contract_id == "CON.F.US.ES.M25"
        assert cfg.max_position == 5
        assert cfg.max_daily_loss == 500.0
        assert cfg.max_trailing_dd == 1500.0
        assert cfg.flatten_et == "16:00"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client() -> TopstepXClient:
    cfg = TopstepXConfig(username="user", api_key="key")
    client = TopstepXClient(cfg)
    # Pre-populate auth state so _ensure_token is a no-op
    client._token = "tok"
    client._token_expiry = float("inf")
    client._account_id = 42
    client._account_name = "TestAccount"
    return client


def _mock_response(body: dict, status: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = body
    resp.text = str(body)
    resp.raise_for_status = MagicMock()  # no-op for 200
    return resp


# ---------------------------------------------------------------------------
# TopstepXClient tests
# ---------------------------------------------------------------------------


class TestTopstepXClientConnect:
    @pytest.mark.asyncio
    async def test_connect_success(self):
        cfg = TopstepXConfig(username="user", api_key="key")
        client = TopstepXClient(cfg)

        auth_resp = _mock_response({"success": True, "token": "tok123"})
        acct_resp = _mock_response({"accounts": [{"id": 42, "name": "Acct1"}]})

        client._http.post = AsyncMock(side_effect=[auth_resp, acct_resp])

        result = await client.connect()
        assert result is True
        assert client._token == "tok123"
        assert client._account_id == 42
        assert client._account_name == "Acct1"
        await client.close()

    @pytest.mark.asyncio
    async def test_connect_auth_failure(self):
        cfg = TopstepXConfig(username="user", api_key="bad")
        client = TopstepXClient(cfg)

        auth_resp = _mock_response({"success": False, "errorMessage": "Invalid key"})
        client._http.post = AsyncMock(return_value=auth_resp)

        result = await client.connect()
        assert result is False
        await client.close()

    @pytest.mark.asyncio
    async def test_connect_no_accounts(self):
        cfg = TopstepXConfig(username="user", api_key="key")
        client = TopstepXClient(cfg)

        auth_resp = _mock_response({"success": True, "token": "tok"})
        acct_resp = _mock_response({"accounts": []})
        client._http.post = AsyncMock(side_effect=[auth_resp, acct_resp])

        result = await client.connect()
        assert result is False
        await client.close()

    @pytest.mark.asyncio
    async def test_connect_http_exception(self):
        cfg = TopstepXConfig(username="user", api_key="key")
        client = TopstepXClient(cfg)
        client._http.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

        result = await client.connect()
        assert result is False
        await client.close()


class TestTopstepXClientOrders:
    @pytest.mark.asyncio
    async def test_place_market_order_buy(self):
        client = _make_client()
        resp = _mock_response({"success": True, "orderId": 1001})
        client._http.post = AsyncMock(return_value=resp)

        result = await client.place_market_order("Buy", 1)
        assert result["orderId"] == 1001

        call_payload = client._http.post.call_args
        sent = call_payload.kwargs.get("json") or call_payload.args[1] if len(call_payload.args) > 1 else call_payload.kwargs["json"]
        assert sent["side"] == SIDE_BUY
        assert sent["type"] == 2
        assert sent["size"] == 1
        assert sent["contractId"] == "CON.F.US.ENQ.M26"
        await client.close()

    @pytest.mark.asyncio
    async def test_place_market_order_sell(self):
        client = _make_client()
        resp = _mock_response({"success": True, "orderId": 1002})
        client._http.post = AsyncMock(return_value=resp)

        result = await client.place_market_order("Sell", 2)
        sent = client._http.post.call_args.kwargs["json"]
        assert sent["side"] == SIDE_SELL
        assert sent["size"] == 2
        await client.close()

    @pytest.mark.asyncio
    async def test_place_stop_order(self):
        client = _make_client()
        resp = _mock_response({"success": True, "orderId": 2001})
        client._http.post = AsyncMock(return_value=resp)

        result = await client.place_stop_order("Sell", 1, 21500.0)
        assert result["orderId"] == 2001

        sent = client._http.post.call_args.kwargs["json"]
        assert sent["type"] == 3
        assert sent["side"] == SIDE_SELL
        assert sent["stopPrice"] == 21500.0
        await client.close()

    @pytest.mark.asyncio
    async def test_modify_order(self):
        client = _make_client()
        resp = _mock_response({"success": True})
        client._http.post = AsyncMock(return_value=resp)

        await client.modify_order(999, 21600.0)

        sent = client._http.post.call_args.kwargs["json"]
        assert sent["orderId"] == 999
        assert sent["stopPrice"] == 21600.0
        assert sent["accountId"] == 42
        await client.close()

    @pytest.mark.asyncio
    async def test_cancel_order(self):
        client = _make_client()
        resp = _mock_response({"success": True})
        client._http.post = AsyncMock(return_value=resp)

        await client.cancel_order(888)

        sent = client._http.post.call_args.kwargs["json"]
        assert sent["orderId"] == 888
        assert sent["accountId"] == 42
        await client.close()

    @pytest.mark.asyncio
    async def test_liquidate_position(self):
        client = _make_client()
        resp = _mock_response({"success": True})
        client._http.post = AsyncMock(return_value=resp)

        await client.liquidate_position()

        url = client._http.post.call_args.args[0]
        assert "/api/Position/closeContract" in url
        sent = client._http.post.call_args.kwargs["json"]
        assert sent["contractId"] == "CON.F.US.ENQ.M26"
        assert sent["accountId"] == 42
        await client.close()


class TestTopstepXClientQueries:
    @pytest.mark.asyncio
    async def test_get_positions_list_response(self):
        client = _make_client()
        positions = [{"contractId": "CON.F.US.ENQ.M26", "netPos": 1}]
        resp = _mock_response(positions)
        client._http.post = AsyncMock(return_value=resp)

        result = await client.get_positions()
        assert result == positions
        await client.close()

    @pytest.mark.asyncio
    async def test_get_positions_dict_response(self):
        client = _make_client()
        positions = [{"contractId": "CON.F.US.ENQ.M26", "netPos": -1}]
        resp = _mock_response({"positions": positions})
        client._http.post = AsyncMock(return_value=resp)

        result = await client.get_positions()
        assert result == positions
        await client.close()

    @pytest.mark.asyncio
    async def test_get_orders_list_response(self):
        client = _make_client()
        orders = [{"orderId": 123, "status": "Working"}]
        resp = _mock_response(orders)
        client._http.post = AsyncMock(return_value=resp)

        result = await client.get_orders()
        assert result == orders
        await client.close()

    @pytest.mark.asyncio
    async def test_get_orders_dict_response(self):
        client = _make_client()
        orders = [{"orderId": 456, "status": "Working"}]
        resp = _mock_response({"orders": orders})
        client._http.post = AsyncMock(return_value=resp)

        result = await client.get_orders()
        assert result == orders
        await client.close()


class TestTopstepXClientSideHelper:
    def test_side_buy(self):
        client = _make_client()
        assert client._side("Buy") == SIDE_BUY

    def test_side_sell(self):
        client = _make_client()
        assert client._side("Sell") == SIDE_SELL
