"""Tests for broker adapter risk rules."""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.broker.adapter import BrokerAdapter
from src.broker.config import BrokerConfig
from src.broker.position_tracker import PositionTracker


@pytest.fixture
def mock_client():
    client = AsyncMock()
    client.place_market_order = AsyncMock(return_value={"orderId": 1})
    client.place_stop_order = AsyncMock(return_value={"orderId": 2})
    client.cancel_order = AsyncMock(return_value={})
    client.liquidate_position = AsyncMock(return_value={})
    client.modify_order = AsyncMock(return_value={})
    return client


@pytest.fixture
def adapter(mock_client):
    config = BrokerConfig(enabled=True, max_daily_loss=500, max_position=2)
    return BrokerAdapter(client=mock_client, config=config)


def test_enter_long(adapter, mock_client):
    signal = {"action": "enter_long", "price": 25000.0, "stop_price": 24990.0, "size": 1.0}
    result = asyncio.get_event_loop().run_until_complete(adapter.on_signal(signal))
    assert result is not None
    mock_client.place_market_order.assert_called_once_with("Buy", 1)
    mock_client.place_stop_order.assert_called_once()


def test_enter_short(adapter, mock_client):
    signal = {"action": "enter_short", "price": 25000.0, "stop_price": 25010.0, "size": 1.0}
    result = asyncio.get_event_loop().run_until_complete(adapter.on_signal(signal))
    mock_client.place_market_order.assert_called_once_with("Sell", 1)


def test_reject_when_daily_loss_exceeded(adapter, mock_client):
    adapter.tracker.session_pnl = -600  # exceeds $500 limit
    signal = {"action": "enter_long", "price": 25000.0, "stop_price": 24990.0, "size": 1.0}
    result = asyncio.get_event_loop().run_until_complete(adapter.on_signal(signal))
    assert result is None
    mock_client.place_market_order.assert_not_called()


def test_reject_when_too_soon(adapter, mock_client):
    import time
    adapter.tracker.last_trade_ts = time.time()  # just traded
    signal = {"action": "enter_long", "price": 25000.0, "stop_price": 24990.0, "size": 1.0}
    result = asyncio.get_event_loop().run_until_complete(adapter.on_signal(signal))
    assert result is None


def test_reject_exceeds_max_position(adapter, mock_client):
    signal = {"action": "enter_long", "price": 25000.0, "stop_price": 24990.0, "size": 5.0}
    result = asyncio.get_event_loop().run_until_complete(adapter.on_signal(signal))
    # Should clamp to max_position=2
    mock_client.place_market_order.assert_called_once_with("Buy", 2)


def test_flatten(adapter, mock_client):
    adapter.tracker.on_fill(side="long", price=25000.0, size=1, stop_price=24990.0)
    adapter.tracker.stop_order_id = 42
    result = asyncio.get_event_loop().run_until_complete(adapter.flatten("test"))
    mock_client.liquidate_position.assert_called_once()
    mock_client.cancel_order.assert_called_once_with(42)


def test_skip_and_hold_ignored(adapter, mock_client):
    for action in ["skip", "hold", "move_to_breakeven"]:
        signal = {"action": action, "price": 25000.0}
        result = asyncio.get_event_loop().run_until_complete(adapter.on_signal(signal))
        assert result is None
    mock_client.place_market_order.assert_not_called()
