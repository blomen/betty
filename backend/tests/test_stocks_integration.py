"""Integration test: TopstepXClient + SignalRelay + BrokerAdapter."""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.stocks.config import TopstepXConfig
from src.stocks.topstepx_client import TopstepXClient
from src.stocks.signal_relay import SignalRelayClient
from src.broker.adapter import BrokerAdapter
from src.broker.config import BrokerConfig


@pytest.fixture
def topstepx_client():
    cfg = TopstepXConfig(username="test", api_key="key", contract_id="CON.F.US.ENQ.M26")
    client = TopstepXClient(cfg)
    client._token = "test_token"
    client._account_id = 42
    client._post = AsyncMock()  # Mock HTTP layer
    return client


@pytest.fixture
def relay(topstepx_client):
    return SignalRelayClient(
        server_ws_url="ws://localhost:18000/ws/signals",
        topstepx_client=topstepx_client,
    )


def test_topstepx_client_is_broker_compatible(topstepx_client):
    """TopstepXClient has same interface as TradovateClient."""
    required = [
        "connect", "place_market_order", "place_stop_order",
        "modify_order", "cancel_order", "liquidate_position",
        "get_positions", "get_orders", "close",
    ]
    for method in required:
        assert hasattr(topstepx_client, method), f"Missing: {method}"
        assert asyncio.iscoroutinefunction(getattr(topstepx_client, method)), f"Not async: {method}"


@pytest.mark.asyncio
async def test_broker_adapter_works_with_topstepx(topstepx_client):
    """BrokerAdapter executes signals via TopstepXClient."""
    topstepx_client._post.return_value = {"success": True, "orderId": 123}
    config = BrokerConfig(enabled=True, max_daily_loss=1000, max_position=2)
    adapter = BrokerAdapter(client=topstepx_client, config=config)

    signal = {"action": "enter_long", "price": 21450.0, "stop_price": 21446.0, "size": 1}
    result = await adapter.on_signal(signal)
    assert result is not None
    assert result["side"] == "long"
    assert topstepx_client._post.call_count == 2  # market + stop


@pytest.mark.asyncio
async def test_relay_executes_signal_on_topstepx(relay, topstepx_client):
    """SignalRelayClient places orders when receiving signal."""
    topstepx_client.place_market_order = AsyncMock(return_value={"success": True, "orderId": 456})
    topstepx_client.place_stop_order = AsyncMock(return_value={"success": True, "orderId": 457})
    relay._ws = AsyncMock()  # mock websocket for forward_fill

    signal = {
        "type": "signal",
        "action": "enter_short",
        "price": 21450.0,
        "stop_price": 21454.0,
        "size": 1,
        "confidence": 0.78,
    }
    await relay._execute_signal(signal)
    topstepx_client.place_market_order.assert_called_once_with("Sell", 1)
    topstepx_client.place_stop_order.assert_called_once_with("Buy", 1, 21454.0)


@pytest.mark.asyncio
async def test_broker_adapter_risk_check_with_topstepx(topstepx_client):
    """Risk rules still apply when using TopstepXClient."""
    topstepx_client._post.return_value = {"success": True, "orderId": 789}
    config = BrokerConfig(enabled=True, max_daily_loss=500, max_position=2)
    adapter = BrokerAdapter(client=topstepx_client, config=config)
    adapter.tracker.session_pnl = -600  # exceeds limit

    signal = {"action": "enter_long", "price": 21450.0, "stop_price": 21446.0, "size": 1}
    result = await adapter.on_signal(signal)
    assert result is None  # rejected by risk check
    topstepx_client._post.assert_not_called()
