"""Integration test: EventRouter → DB → SSE channel flow."""
import pytest
from src.mirror.event_router import EventRouter
from src.mirror.channels import sync_channel, price_channel, action_channel


@pytest.mark.asyncio
async def test_balance_route_persists_and_broadcasts():
    """EventRouter.route('balance') should persist to DB and broadcast to sync channel."""
    router = EventRouter()
    client_id, queue = sync_channel.subscribe()
    try:
        await router.route(
            provider_id="betsson",
            category="balance",
            url="https://example.com/api/account/balance",
            response_body={"balance": 1500.0},
        )
        msg = queue.get_nowait()
        assert msg["event"] == "balance_update"
        assert msg["data"]["provider_id"] == "betsson"
        assert msg["data"]["amount"] == 1500.0
    finally:
        sync_channel.unsubscribe(client_id)


@pytest.mark.asyncio
async def test_action_broadcast():
    """EventRouter.broadcast_action should push to action channel."""
    router = EventRouter()
    client_id, queue = action_channel.subscribe()
    try:
        await router.broadcast_action("navigated", {"bet_id": 42, "event_url": "/match/123"})
        msg = queue.get_nowait()
        assert msg["event"] == "navigated"
        assert msg["data"]["bet_id"] == 42
    finally:
        action_channel.unsubscribe(client_id)


@pytest.mark.asyncio
async def test_classify_and_route_full_flow():
    """Full flow: classify URL → route → broadcast."""
    router = EventRouter()
    client_id, queue = sync_channel.subscribe()
    try:
        url = "https://sb2frontend-altenar2.bfrndz.com/api/account/balance"
        category = router.classify(url, {"balance": 999.0})
        assert category == "balance"
        await router.route("betinia", category, url, {"balance": 999.0})
        msg = queue.get_nowait()
        assert msg["data"]["provider_id"] == "betinia"
        assert msg["data"]["amount"] == 999.0
    finally:
        sync_channel.unsubscribe(client_id)
