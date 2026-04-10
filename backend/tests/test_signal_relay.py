"""Tests for SignalRelayClient."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.stocks.signal_relay import SignalRelayClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_relay(topstepx=None):
    if topstepx is None:
        topstepx = MagicMock()
    return SignalRelayClient("ws://localhost:18000/ws/signals", topstepx)


# ---------------------------------------------------------------------------
# Message factory tests
# ---------------------------------------------------------------------------

class TestTickMsg:
    def test_format(self):
        msg = SignalRelayClient._tick_msg(21345.5, 3, 1712500000.0, "B")
        assert msg == {"type": "tick", "price": 21345.5, "size": 3, "ts": 1712500000.0, "side": "B"}

    def test_type_field(self):
        msg = SignalRelayClient._tick_msg(0.0, 0, 0.0, "B")
        assert msg["type"] == "tick"

    def test_values_preserved(self):
        msg = SignalRelayClient._tick_msg(99999.99, 100, 1.23, "A")
        assert msg["price"] == 99999.99
        assert msg["size"] == 100
        assert msg["ts"] == 1.23
        assert msg["side"] == "A"


class TestFillMsg:
    def test_format(self):
        msg = SignalRelayClient._fill_msg("Buy", 21345.5, 1, 21200.0)
        assert msg == {"type": "fill", "side": "Buy", "price": 21345.5, "size": 1, "stop_price": 21200.0}

    def test_sell_side(self):
        msg = SignalRelayClient._fill_msg("Sell", 21000.0, 2, 21100.0)
        assert msg["side"] == "Sell"
        assert msg["type"] == "fill"

    def test_no_stop(self):
        msg = SignalRelayClient._fill_msg("Buy", 500.0, 1, 0.0)
        assert msg["stop_price"] == 0.0


# ---------------------------------------------------------------------------
# _execute_signal tests
# ---------------------------------------------------------------------------

class TestExecuteSignal:
    @pytest.mark.asyncio
    async def test_long_signal_places_buy_and_stop(self):
        client = AsyncMock()
        client.place_market_order.return_value = {"price": 21345.5}
        client.place_stop_order.return_value = {"orderId": 42}

        relay = _make_relay(client)
        relay._connected = True
        relay._ws = AsyncMock()

        signal = {"type": "signal", "action": "long", "size": 2, "stop_price": 21200.0}
        await relay._execute_signal(signal)

        client.place_market_order.assert_awaited_once_with("Buy", 2)
        client.place_stop_order.assert_awaited_once_with("Sell", 2, 21200.0)

    @pytest.mark.asyncio
    async def test_short_signal_places_sell_and_stop(self):
        client = AsyncMock()
        client.place_market_order.return_value = {"price": 21000.0}
        client.place_stop_order.return_value = {"orderId": 43}

        relay = _make_relay(client)
        relay._connected = True
        relay._ws = AsyncMock()

        signal = {"type": "signal", "action": "short", "size": 1, "stop_price": 21150.0}
        await relay._execute_signal(signal)

        client.place_market_order.assert_awaited_once_with("Sell", 1)
        client.place_stop_order.assert_awaited_once_with("Buy", 1, 21150.0)

    @pytest.mark.asyncio
    async def test_no_stop_price_skips_stop_order(self):
        client = AsyncMock()
        client.place_market_order.return_value = {"price": 21345.5}

        relay = _make_relay(client)
        relay._connected = True
        relay._ws = AsyncMock()

        signal = {"type": "signal", "action": "long", "size": 1, "stop_price": 0}
        await relay._execute_signal(signal)

        client.place_market_order.assert_awaited_once_with("Buy", 1)
        client.place_stop_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_stop_price_skips_stop_order(self):
        """Signal with no stop_price key at all should not place a stop."""
        client = AsyncMock()
        client.place_market_order.return_value = {}

        relay = _make_relay(client)
        relay._connected = True
        relay._ws = AsyncMock()

        signal = {"type": "signal", "action": "short", "size": 1}
        await relay._execute_signal(signal)

        client.place_market_order.assert_awaited_once_with("Sell", 1)
        client.place_stop_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_default_size_is_one(self):
        """Signal without size should default to 1."""
        client = AsyncMock()
        client.place_market_order.return_value = {}

        relay = _make_relay(client)
        relay._connected = True
        relay._ws = AsyncMock()

        signal = {"type": "signal", "action": "long"}
        await relay._execute_signal(signal)

        client.place_market_order.assert_awaited_once_with("Buy", 1)

    @pytest.mark.asyncio
    async def test_forwards_fill_after_market_order(self):
        """After placing a market order, a fill should be forwarded to the server."""
        client = AsyncMock()
        client.place_market_order.return_value = {"price": 21345.5}

        relay = _make_relay(client)
        relay._connected = True
        ws = AsyncMock()
        relay._ws = ws

        signal = {"type": "signal", "action": "long", "size": 1, "stop_price": 0}
        await relay._execute_signal(signal)

        ws.send.assert_awaited_once()
        sent = ws.send.call_args[0][0]
        import json
        msg = json.loads(sent)
        assert msg["type"] == "fill"
        assert msg["side"] == "Buy"

    @pytest.mark.asyncio
    async def test_order_error_does_not_raise(self):
        """Exceptions from TopstepX should be caught and logged, not propagated."""
        client = AsyncMock()
        client.place_market_order.side_effect = RuntimeError("API down")

        relay = _make_relay(client)
        relay._connected = True
        relay._ws = AsyncMock()

        signal = {"type": "signal", "action": "long", "size": 1, "stop_price": 200.0}
        # Should not raise
        await relay._execute_signal(signal)


# ---------------------------------------------------------------------------
# is_connected property
# ---------------------------------------------------------------------------

class TestIsConnected:
    def test_initially_false(self):
        relay = _make_relay()
        assert relay.is_connected is False

    def test_true_when_set(self):
        relay = _make_relay()
        relay._connected = True
        assert relay.is_connected is True
