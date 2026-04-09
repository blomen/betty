"""Tests for dedicated SSE broadcast channels (sync, price, action)."""
import pytest
import pytest_asyncio

from src.mirror.channels import action_channel, price_channel, sync_channel


@pytest.mark.asyncio
async def test_sync_channel_publish_subscribe():
    client_id, queue = sync_channel.subscribe()
    try:
        sync_channel.publish("balance_update", {"balance": 500.0, "currency": "EUR"})
        msg = queue.get_nowait()
        assert msg["event"] == "balance_update"
        assert msg["data"]["balance"] == 500.0
        assert msg["data"]["currency"] == "EUR"
    finally:
        sync_channel.unsubscribe(client_id)


@pytest.mark.asyncio
async def test_price_channel_publish_subscribe():
    client_id, queue = price_channel.subscribe()
    try:
        price_channel.publish("price_update", {"odds": 2.15, "market": "1x2", "edge": 3.2})
        msg = queue.get_nowait()
        assert msg["event"] == "price_update"
        assert msg["data"]["odds"] == 2.15
        assert msg["data"]["market"] == "1x2"
        assert msg["data"]["edge"] == 3.2
    finally:
        price_channel.unsubscribe(client_id)


@pytest.mark.asyncio
async def test_action_channel_publish_subscribe():
    client_id, queue = action_channel.subscribe()
    try:
        action_channel.publish("bet_placed", {"bet_id": "abc123", "stake": 25.0, "accepted": True})
        msg = queue.get_nowait()
        assert msg["event"] == "bet_placed"
        assert msg["data"]["bet_id"] == "abc123"
        assert msg["data"]["stake"] == 25.0
        assert msg["data"]["accepted"] is True
    finally:
        action_channel.unsubscribe(client_id)
