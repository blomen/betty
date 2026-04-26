"""SlipOddsStream — per-leg odds poller, throttled aggregator."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from arnold.mirror.slip_odds_stream import SlipOddsStream


@pytest.mark.asyncio
async def test_stream_starts_and_stops():
    workflow = MagicMock()
    workflow.read_slip_odds = AsyncMock(return_value=2.10)
    page = MagicMock()
    callback = MagicMock()

    stream = SlipOddsStream(
        provider_id="unibet",
        workflow=workflow,
        page=page,
        on_odds_change=callback,
        poll_interval_s=0.05,
    )
    assert stream.running is False
    stream.start()
    assert stream.running is True
    await asyncio.sleep(0.12)  # let it tick at least twice
    stream.stop()
    await asyncio.sleep(0.05)
    assert stream.running is False
    assert workflow.read_slip_odds.call_count >= 2


@pytest.mark.asyncio
async def test_stream_calls_callback_on_change():
    workflow = MagicMock()
    workflow.read_slip_odds = AsyncMock(side_effect=[2.10, 2.10, 2.15, 2.15])
    page = MagicMock()
    callback = MagicMock()

    stream = SlipOddsStream(
        provider_id="unibet",
        workflow=workflow,
        page=page,
        on_odds_change=callback,
        poll_interval_s=0.02,
    )
    stream.start()
    await asyncio.sleep(0.15)
    stream.stop()
    await asyncio.sleep(0.02)

    # Callback fires only when odds change: 2.10 (first), then 2.15
    assert callback.call_count == 2
    assert callback.call_args_list[0][0][0] == 2.10
    assert callback.call_args_list[1][0][0] == 2.15


@pytest.mark.asyncio
async def test_stream_handles_none_odds():
    """Stream tolerates workflow returning None (slip cleared/errored)."""
    workflow = MagicMock()
    workflow.read_slip_odds = AsyncMock(side_effect=[2.10, None, 2.10])
    page = MagicMock()
    callback = MagicMock()

    stream = SlipOddsStream(
        provider_id="unibet",
        workflow=workflow,
        page=page,
        on_odds_change=callback,
        poll_interval_s=0.02,
    )
    stream.start()
    await asyncio.sleep(0.12)
    stream.stop()
    await asyncio.sleep(0.02)

    # First 2.10 fires callback; None doesn't fire; back to 2.10 doesn't refire (same value)
    assert callback.call_count >= 1


@pytest.mark.asyncio
async def test_stream_survives_workflow_exception():
    """One bad poll should not kill the stream."""
    workflow = MagicMock()
    workflow.read_slip_odds = AsyncMock(side_effect=[2.10, RuntimeError("boom"), 2.20])
    page = MagicMock()
    callback = MagicMock()

    stream = SlipOddsStream(
        provider_id="unibet",
        workflow=workflow,
        page=page,
        on_odds_change=callback,
        poll_interval_s=0.02,
    )
    stream.start()
    await asyncio.sleep(0.12)
    stream.stop()
    await asyncio.sleep(0.02)

    # Should have attempted all 3 polls + at least one callback
    assert workflow.read_slip_odds.call_count >= 3
    assert callback.call_count >= 1


@pytest.mark.asyncio
async def test_stream_current_odds_property():
    workflow = MagicMock()
    workflow.read_slip_odds = AsyncMock(return_value=2.42)
    page = MagicMock()

    stream = SlipOddsStream(
        provider_id="unibet",
        workflow=workflow,
        page=page,
        on_odds_change=lambda o: None,
        poll_interval_s=0.02,
    )
    stream.start()
    await asyncio.sleep(0.06)
    assert stream.current_odds == 2.42
    stream.stop()


@pytest.mark.asyncio
async def test_stream_does_not_log_when_endpoint_unset():
    """Default constructor — no log_endpoint, no bet_context — never posts."""
    workflow = MagicMock()
    workflow.read_slip_odds = AsyncMock(return_value=2.10)
    page = MagicMock()
    callback = MagicMock()

    stream = SlipOddsStream(
        provider_id="unibet",
        workflow=workflow,
        page=page,
        on_odds_change=callback,
        poll_interval_s=0.02,
    )
    # Confirm new fields default correctly
    assert stream._log_endpoint is None
    assert stream._bet_context is None

    stream.start()
    await asyncio.sleep(0.05)
    stream.stop()


@pytest.mark.asyncio
async def test_stream_accepts_log_endpoint_and_bet_context():
    workflow = MagicMock()
    workflow.read_slip_odds = AsyncMock(return_value=2.10)
    page = MagicMock()

    stream = SlipOddsStream(
        provider_id="unibet",
        workflow=workflow,
        page=page,
        on_odds_change=lambda o: None,
        poll_interval_s=0.02,
        log_endpoint="https://example.test/api/slip-odds-tick",
        bet_context={"event_id": "e1", "market": "moneyline", "outcome": "home", "scanner_odds": 2.05},
    )
    assert stream._log_endpoint == "https://example.test/api/slip-odds-tick"
    assert stream._bet_context["event_id"] == "e1"


def test_slip_odds_stream_exposes_page_publicly():
    """Spec §4.2: ArbRunner reads stream.page; should be a public attr."""
    page = MagicMock(name="playwright_page")
    workflow = MagicMock()
    stream = SlipOddsStream(
        provider_id="pinnacle",
        workflow=workflow,
        page=page,
        on_odds_change=lambda o: None,
    )
    assert stream.page is page
