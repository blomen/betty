# backend/tests/test_tracker_reconciler.py
"""Tests for reconcile_tracker_from_broker."""
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.broker.position_tracker import PositionTracker
from src.stocks.tracker_reconciler import (
    ReconcileResult,
    reconcile_tracker_from_broker,
)


def _make_adapter(tracker: PositionTracker | None = None, pending: dict | None = None):
    adapter = MagicMock()
    adapter.tracker = tracker or PositionTracker()
    adapter._pending_trade = pending
    adapter._set_pending_trade = MagicMock()
    return adapter


@pytest.mark.asyncio
async def test_broker_only_populates_tracker():
    """Broker has open position; nothing on disk → tracker populated from broker."""
    adapter = _make_adapter()
    client = MagicMock()
    client.search_open_positions = AsyncMock(return_value=[
        {"contractId": "CON.F.US.ENQ.M26", "type": 1, "size": 1, "averagePrice": 27226.0}
    ])
    client.search_open_orders = AsyncMock(return_value=[
        {"orderId": 12345, "type": 4, "side": 1, "stopPrice": 27217.75}
    ])

    result = await reconcile_tracker_from_broker(adapter, client, "CON.F.US.ENQ.M26")

    assert adapter.tracker.side == "long"
    assert adapter.tracker.entry_price == 27226.0
    assert adapter.tracker.stop_price == 27217.75
    assert adapter.tracker.size == 1
    assert adapter.tracker.stop_order_id == 12345
    assert result.matched is True
    assert result.broker_only is True
    assert result.disk_only is False


@pytest.mark.asyncio
async def test_broker_and_disk_match():
    """Both sources agree → tracker populated, no warning."""
    adapter = _make_adapter(pending={"side": "long", "entry_price": 27226.0, "size": 1})
    client = MagicMock()
    client.search_open_positions = AsyncMock(return_value=[
        {"contractId": "CON.F.US.ENQ.M26", "type": 1, "size": 1, "averagePrice": 27226.0}
    ])
    client.search_open_orders = AsyncMock(return_value=[
        {"orderId": 12345, "type": 4, "side": 1, "stopPrice": 27217.75}
    ])

    result = await reconcile_tracker_from_broker(adapter, client, "CON.F.US.ENQ.M26")

    assert adapter.tracker.size == 1
    assert result.matched is True
    assert result.divergence_logged is False


@pytest.mark.asyncio
async def test_broker_and_disk_diverge_broker_wins():
    """Disk says size=2; broker says size=1 → broker wins, divergence logged."""
    adapter = _make_adapter(pending={"side": "long", "entry_price": 27200.0, "size": 2})
    client = MagicMock()
    client.search_open_positions = AsyncMock(return_value=[
        {"contractId": "CON.F.US.ENQ.M26", "type": 1, "size": 1, "averagePrice": 27226.0}
    ])
    client.search_open_orders = AsyncMock(return_value=[])

    result = await reconcile_tracker_from_broker(adapter, client, "CON.F.US.ENQ.M26")

    assert adapter.tracker.size == 1
    assert adapter.tracker.entry_price == 27226.0
    assert result.divergence_logged is True


@pytest.mark.asyncio
async def test_disk_only_means_position_closed_during_downtime():
    """No broker position; disk has stale data → clear disk, tracker stays flat."""
    adapter = _make_adapter(pending={"side": "long", "entry_price": 27226.0, "size": 1})
    client = MagicMock()
    client.search_open_positions = AsyncMock(return_value=[])
    client.search_open_orders = AsyncMock(return_value=[])

    result = await reconcile_tracker_from_broker(adapter, client, "CON.F.US.ENQ.M26")

    assert adapter.tracker.is_flat
    assert result.disk_only is True
    adapter._set_pending_trade.assert_called_once_with(None)


@pytest.mark.asyncio
async def test_broker_rest_failure_returns_degraded():
    """REST timeout → returns degraded result; caller falls back to Layer 2."""
    adapter = _make_adapter()
    client = MagicMock()
    client.search_open_positions = AsyncMock(side_effect=TimeoutError("REST timeout"))

    result = await reconcile_tracker_from_broker(adapter, client, "CON.F.US.ENQ.M26")

    assert result.degraded is True
    assert adapter.tracker.is_flat  # untouched
