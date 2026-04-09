"""Tests for EventRouter — classification and routing."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.mirror.event_router import EventRouter


# ---------------------------------------------------------------------------
# Classification tests
# ---------------------------------------------------------------------------

def test_classify_balance_response():
    router = EventRouter()
    assert router.classify("https://altenar.example.com/api/sb/v2/balance", {}) == "balance"


def test_classify_history_response():
    router = EventRouter()
    assert router.classify("https://example.com/widgetBetHistory?page=1", {}) == "history"


def test_classify_bet_confirm():
    router = EventRouter()
    assert router.classify("https://example.com/placeWidget", {}) == "bet_confirm"


def test_classify_odds_response():
    router = EventRouter()
    assert router.classify("https://example.com/GetEventDetails?id=123", {}) == "odds"


def test_classify_notification():
    router = EventRouter()
    assert router.classify("https://example.com/notification/preferences", {}) == "notification"


def test_classify_unknown():
    router = EventRouter()
    assert router.classify("https://example.com/some/random/endpoint", {}) is None


# ---------------------------------------------------------------------------
# Balance extraction tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persist_balance_direct_key():
    router = EventRouter()
    result = await router._persist_balance("altenar", {"balance": 250.50})
    assert result == {"provider_id": "altenar", "amount": 250.50}


@pytest.mark.asyncio
async def test_persist_balance_nested_amount():
    router = EventRouter()
    result = await router._persist_balance("gecko", {"balance": {"amount": 100.0}})
    assert result == {"provider_id": "gecko", "amount": 100.0}


@pytest.mark.asyncio
async def test_persist_balance_gecko_v2_wallets():
    router = EventRouter()
    result = await router._persist_balance("gecko", {"wallets": [{"balance": 300.0}]})
    assert result == {"provider_id": "gecko", "amount": 300.0}


@pytest.mark.asyncio
async def test_persist_balance_no_amount_returns_none():
    router = EventRouter()
    result = await router._persist_balance("unknown", {"foo": "bar"})
    assert result is None


@pytest.mark.asyncio
async def test_persist_balance_non_dict_returns_none():
    router = EventRouter()
    result = await router._persist_balance("unknown", [1, 2, 3])
    assert result is None


# ---------------------------------------------------------------------------
# Route test — mock _persist_balance and _broadcast, verify both called
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_route_balance_persists_and_broadcasts():
    router = EventRouter()

    # Mock _persist_balance to return a payload without hitting the DB
    mock_payload = {"provider_id": "altenar", "amount": 150.0}
    router._persist_balance = AsyncMock(return_value=mock_payload)
    router._broadcast = AsyncMock()

    await router.route(
        provider_id="altenar",
        category="balance",
        url="https://example.com/api/sb/v2/balance",
        response_body={"balance": 150.0},
    )

    router._persist_balance.assert_awaited_once_with("altenar", {"balance": 150.0})
    router._broadcast.assert_awaited_once_with("sync", "balance_update", mock_payload)
