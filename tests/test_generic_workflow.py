"""Tests for GenericWorkflow intel loading and method dispatch."""

import json
import pytest
from pathlib import Path


@pytest.fixture
def intel_dir(tmp_path):
    d = tmp_path / "mirror_intel"
    d.mkdir()
    return d


@pytest.fixture
def sample_intel():
    return {
        "provider_id": "testprovider",
        "platform": "custom",
        "discovered_at": "2026-04-08T14:30:00Z",
        "updated_at": "2026-04-08T14:30:00Z",
        "capabilities": {
            "login": "discovered",
            "balance": "discovered",
            "history": "none",
            "placement": "none",
        },
        "login": {
            "method": "dom",
            "indicator": {"selector": ".user-balance", "regex": "[\\d.,]+"},
        },
        "balance": {
            "method": "api",
            "api": {"url": "/api/wallet/balance", "path": "data.balance", "currency": "SEK"},
            "dom": None,
        },
        "history": None,
        "betslip": None,
        "navigation": None,
        "api_endpoints": {},
        "notes": "",
    }


def test_load_intel_returns_dict(intel_dir, sample_intel):
    from src.mirror.workflows.generic import load_intel
    (intel_dir / "testprovider.json").write_text(json.dumps(sample_intel))
    result = load_intel("testprovider", intel_dir)
    assert result["provider_id"] == "testprovider"
    assert result["capabilities"]["balance"] == "discovered"


def test_load_intel_missing_returns_none(intel_dir):
    from src.mirror.workflows.generic import load_intel
    result = load_intel("nonexistent", intel_dir)
    assert result is None


def test_save_intel_roundtrip(intel_dir, sample_intel):
    from src.mirror.workflows.generic import save_intel, load_intel
    save_intel("testprovider", sample_intel, intel_dir)
    result = load_intel("testprovider", intel_dir)
    assert result["provider_id"] == "testprovider"
    assert result["balance"]["api"]["path"] == "data.balance"


def test_load_strategy_missing_returns_none():
    from src.mirror.workflows.strategies import load_strategy
    result = load_strategy("nonexistent_provider_xyz")
    assert result is None


def test_strategy_dataclass_fields():
    from src.mirror.workflows.strategies import Strategy
    s = Strategy(sync_balance=lambda page, intel: 42.0)
    assert s.sync_balance is not None
    assert s.check_login is None
    assert s.sync_history is None
    assert s.navigate_to_event is None
    assert s.place_bet is None
    assert s.check_live_price is None


import asyncio
from unittest.mock import AsyncMock, MagicMock


def test_extract_path_nested():
    from src.mirror.workflows.generic import _extract_path
    data = {"data": {"balance": 123.45}}
    assert _extract_path(data, "data.balance") == 123.45


def test_extract_path_missing():
    from src.mirror.workflows.generic import _extract_path
    assert _extract_path({"a": 1}, "b.c") is None


def test_generic_workflow_init_with_intel(intel_dir, sample_intel):
    from src.mirror.workflows.generic import GenericWorkflow, save_intel
    save_intel("testprovider", sample_intel, intel_dir)
    wf = GenericWorkflow("testprovider", "test.com", intel_dir=intel_dir)
    assert wf.intel is not None
    assert wf.intel["provider_id"] == "testprovider"
    assert wf.mode.value == "guided"


def test_generic_workflow_init_no_intel(intel_dir):
    from src.mirror.workflows.generic import GenericWorkflow
    wf = GenericWorkflow("unknown", "unknown.com", intel_dir=intel_dir)
    assert wf.intel is None


def test_sync_balance_api(intel_dir, sample_intel):
    from src.mirror.workflows.generic import GenericWorkflow, save_intel
    save_intel("testprovider", sample_intel, intel_dir)
    wf = GenericWorkflow("testprovider", "test.com", intel_dir=intel_dir)
    page = AsyncMock()
    wf._evaluate_api = AsyncMock(return_value={"data": {"balance": 1234.56}})
    result = asyncio.get_event_loop().run_until_complete(wf.sync_balance(page))
    assert result == 1234.56
    wf._evaluate_api.assert_called_once_with(page, "/api/wallet/balance")


def test_sync_balance_no_intel(intel_dir):
    from src.mirror.workflows.generic import GenericWorkflow
    wf = GenericWorkflow("unknown", "unknown.com", intel_dir=intel_dir)
    page = AsyncMock()
    result = asyncio.get_event_loop().run_until_complete(wf.sync_balance(page))
    assert result == -1


def test_navigate_to_event(intel_dir, sample_intel):
    from src.mirror.workflows.generic import GenericWorkflow, save_intel
    # Add navigation to intel
    sample_intel["navigation"] = {"history_path": "/bets", "event_url_template": "/event/{event_id}"}
    save_intel("testprovider", sample_intel, intel_dir)
    wf = GenericWorkflow("testprovider", "test.com", intel_dir=intel_dir)

    page = AsyncMock()
    page.url = "https://test.com/home"
    page.goto = AsyncMock()
    bet = MagicMock()
    bet.provider_event_id = "12345"

    result = asyncio.get_event_loop().run_until_complete(wf.navigate_to_event(page, bet))
    assert result is True
    page.goto.assert_called_once()
    assert "12345" in page.goto.call_args[0][0]


def test_place_bet_guided(intel_dir, sample_intel):
    from src.mirror.workflows.generic import GenericWorkflow, save_intel
    sample_intel["betslip"] = {
        "odds_buttons": ".odds-btn",
        "stake_input": "#stake",
        "confirm_button": ".confirm",
        "confirmation_selector": ".success",
    }
    save_intel("testprovider", sample_intel, intel_dir)
    wf = GenericWorkflow("testprovider", "test.com", intel_dir=intel_dir)

    page = AsyncMock()
    page.query_selector = AsyncMock(return_value=AsyncMock())
    page.evaluate = AsyncMock()
    bet = MagicMock()
    bet.bet_id = 1

    result = asyncio.get_event_loop().run_until_complete(wf.place_bet(page, bet, 50.0))
    assert result.status == "manual"
    assert result.reason == "generic_guided_user_confirms"


def test_place_bet_no_betslip_intel(intel_dir, sample_intel):
    from src.mirror.workflows.generic import GenericWorkflow, save_intel
    save_intel("testprovider", sample_intel, intel_dir)
    wf = GenericWorkflow("testprovider", "test.com", intel_dir=intel_dir)

    page = AsyncMock()
    bet = MagicMock()
    bet.bet_id = 1

    result = asyncio.get_event_loop().run_until_complete(wf.place_bet(page, bet, 50.0))
    assert result.status == "manual"
    assert result.reason == "no_betslip_intel"
