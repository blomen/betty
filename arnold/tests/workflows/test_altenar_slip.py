"""Altenar read_slip_odds + update_slip_stake — both via localStorage WSDK keys."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from arnold.mirror.workflows.altenar import AltenarWorkflow


def _wf(provider_id: str = "betinia", domain: str = "betinia.se") -> AltenarWorkflow:
    return AltenarWorkflow(provider_id=provider_id, domain=domain)


def _populated_selections(price: float) -> str:
    return json.dumps(
        {
            "state": {
                "selections": [{"odd": {"id": 12345, "price": price, "priceDir": 1}}],
                "oddIds": [12345],
            },
            "version": 0,
        }
    )


def _populated_stakes(value: float) -> str:
    return json.dumps(
        {
            "state": {
                "singleStakes": [{"value": value, "preciseValue": value, "type": 3, "isEnabled": True}],
                "selectionCount": 1,
            },
            "version": 0,
        }
    )


@pytest.mark.asyncio
async def test_read_slip_odds_returns_price_from_localstorage():
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=_populated_selections(price=1.6154))
    wf = _wf()
    odds = await wf.read_slip_odds(page)
    assert odds == 1.6154
    js = page.evaluate.call_args[0][0]
    assert "WSDK_betiniase2_betSelections" in js


@pytest.mark.asyncio
async def test_read_slip_odds_returns_none_when_empty_selections():
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=json.dumps({"state": {"selections": []}}))
    wf = _wf()
    odds = await wf.read_slip_odds(page)
    assert odds is None


@pytest.mark.asyncio
async def test_read_slip_odds_returns_none_when_localstorage_missing():
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=None)
    wf = _wf()
    odds = await wf.read_slip_odds(page)
    assert odds is None


@pytest.mark.asyncio
async def test_read_slip_odds_returns_none_on_exception():
    page = MagicMock()
    page.evaluate = AsyncMock(side_effect=RuntimeError("page crashed"))
    wf = _wf()
    odds = await wf.read_slip_odds(page)
    assert odds is None


@pytest.mark.asyncio
async def test_read_slip_odds_uses_per_provider_integration_key():
    """Different Altenar providers map to different WSDK localStorage keys."""
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=_populated_selections(price=2.5))
    wf = _wf(provider_id="campobet", domain="campobet.se")
    odds = await wf.read_slip_odds(page)
    assert odds == 2.5
    js = page.evaluate.call_args[0][0]
    assert "WSDK_campose_betSelections" in js


@pytest.mark.asyncio
async def test_update_slip_stake_returns_true_on_success():
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=True)
    wf = _wf()
    ok = await wf.update_slip_stake(page, 75.5)
    assert ok is True
    args = page.evaluate.call_args
    js, payload = args[0]
    assert "WSDK_betiniase2_betStakes" in payload["key"]
    assert payload["stake"] == 75.5


@pytest.mark.asyncio
async def test_update_slip_stake_returns_false_on_exception():
    page = MagicMock()
    page.evaluate = AsyncMock(side_effect=RuntimeError("nope"))
    wf = _wf()
    ok = await wf.update_slip_stake(page, 50.0)
    assert ok is False
