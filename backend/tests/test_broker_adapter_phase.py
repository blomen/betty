"""Tests for Phase 1 / Phase 2 broker_adapter behavior."""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from src.stocks.broker_adapter import TopstepXBrokerAdapter
from src.stocks.config import TopstepXConfig


@pytest.fixture
def mock_client():
    client = AsyncMock()
    client.place_market_order = AsyncMock(return_value={"orderId": 100, "success": True})
    client.place_stop_order = AsyncMock(return_value={"orderId": 101, "success": True})
    client.cancel_order = AsyncMock(return_value={})
    client.liquidate_position = AsyncMock(return_value={})
    client.modify_order = AsyncMock(return_value={"success": True})
    # Stop-verification path: make orderId 101 appear live so verify passes
    client._post = AsyncMock(return_value={"orders": [{"id": 101}]})
    client._account_id = 0
    return client


@pytest.fixture
def adapter(mock_client, monkeypatch):
    monkeypatch.setattr("src.stocks.broker_adapter._save_pending_trade_to_disk", lambda v: None)
    config = TopstepXConfig(max_trailing_dd=5000.0, max_position=3)
    a = TopstepXBrokerAdapter(client=mock_client, config=config)
    return a


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_entry_size_high_confidence_scales_to_two(adapter, mock_client):
    """Confidence >= 0.85 → size_multiplier = 1.5 → round(1 × 1.5) = 2 contracts."""
    signal = {
        "action": "enter_long",
        "price": 25000.0,
        "stop_price": 24990.0,
        "stop_ticks": 40,
        "confidence": 0.90,
        "ts": time.time(),
    }
    _run(adapter._execute_entry(signal))
    mock_client.place_market_order.assert_called_once_with("Buy", 2)


def test_entry_size_low_confidence_floors_at_one(adapter, mock_client):
    """Confidence < 0.30 → reckless multiplier 0.5 → round(1 × 0.5) = 1 (floor)."""
    signal = {
        "action": "enter_long",
        "price": 25000.0,
        "stop_price": 24990.0,
        "stop_ticks": 40,
        "confidence": 0.10,
        "ts": time.time(),
    }
    _run(adapter._execute_entry(signal))
    mock_client.place_market_order.assert_called_once_with("Buy", 1)


def test_entry_size_mid_confidence_one_contract(adapter, mock_client):
    """Confidence 0.50-0.85 tier → multiplier 0.6-1.0 → 1 contract."""
    signal = {
        "action": "enter_long",
        "price": 25000.0,
        "stop_price": 24990.0,
        "stop_ticks": 40,
        "confidence": 0.65,
        "ts": time.time(),
    }
    _run(adapter._execute_entry(signal))
    mock_client.place_market_order.assert_called_once_with("Buy", 1)


def test_rev_signal_flips_position_in_phase_2(adapter, mock_client):
    """In Phase 2 (locked_BE=True) long, REV signal closes long + opens short.

    NOTE: This test exercises broker_adapter.on_signal directly. The
    level_monitor gate (_is_phase2_rev_opposite) that allows the result
    to fall through is verified by code inspection, not by this test.
    A full integration test would require setting up _emit_zone_dqn_inference
    with a mock DQN — out of scope for the broker-side phase coverage.
    """
    adapter.tracker.on_fill(side="long", price=25000.0, size=1, stop_price=24990.0)
    adapter.tracker.locked_BE = True
    adapter.tracker.peak_R = 1.6
    adapter.tracker.entry_price = 25000.0
    # Push last_trade_ts into the past so MIN_TRADE_INTERVAL_S check passes.
    adapter.tracker.last_trade_ts = time.time() - 60.0

    rev_signal = {
        "action": "enter_short",
        "price": 25030.0,
        "stop_price": 25040.0,
        "stop_ticks": 40,
        "confidence": 0.70,
        "ts": time.time(),
    }
    _run(adapter.on_signal(rev_signal))

    # Flatten was called
    mock_client.liquidate_position.assert_called_once()
    # Then a fresh short was placed
    place_calls = mock_client.place_market_order.call_args_list
    assert any(call.args[0] == "Sell" for call in place_calls), place_calls
