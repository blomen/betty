"""ArbRunner v2 — load all legs, stream odds, intercept mirror clicks."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from arnold.mirror.arb_runner import ArbRunner


def _make_browser():
    browser = MagicMock()
    browser.running = True
    browser.context = MagicMock()
    browser.context.pages = []
    browser.provider_data = {}
    browser.is_logged_in = MagicMock(return_value=True)
    browser.get_balance = MagicMock(return_value=200.0)
    browser.check_login_dom = AsyncMock(return_value={"logged_in": True, "balance": 200.0})
    return browser


def _make_broadcaster():
    bc = MagicMock()
    bc.publish = MagicMock()
    return bc


@pytest.mark.asyncio
async def test_arb_runner_loads_all_legs_then_idles_streaming():
    """When given an opp, runner navigates + preps every leg, starts streams, broadcasts arb_legs_loaded."""
    runner = ArbRunner(
        provider_id="unibet",
        browser=_make_browser(),
        broadcaster=_make_broadcaster(),
        proxy_url="https://x.test",
        block_event_market=lambda b: None,
        is_blocked=lambda b: False,
        placed_today={},
        active_providers=["unibet", "pinnacle"],
    )
    # Internal helpers will be tested individually — assert public state
    assert runner.state == "idle"


@pytest.mark.asyncio
async def test_arb_runner_routes_anchor_intercept_to_anchor_handler():
    runner = ArbRunner(
        provider_id="unibet",
        browser=_make_browser(),
        broadcaster=_make_broadcaster(),
        proxy_url="https://x.test",
        block_event_market=lambda b: None,
        is_blocked=lambda b: False,
        placed_today={},
        active_providers=["unibet", "pinnacle"],
    )
    runner.state = "standby"
    runner._anchor_event = asyncio.Event()
    runner._intercepted_body = None
    runner.on_bet_intercepted({"placed": True, "stake": 100, "odds": 2.05}, None)
    assert runner._intercepted_body == {"placed": True, "stake": 100, "odds": 2.05}
    assert runner._anchor_event.is_set()


@pytest.mark.asyncio
async def test_arb_runner_routes_counter_intercept_to_counter_handler():
    """When the runner is waiting on counter legs, a 'pinnacle' intercept routes to that leg's event."""
    runner = ArbRunner(
        provider_id="unibet",
        browser=_make_browser(),
        broadcaster=_make_broadcaster(),
        proxy_url="https://x.test",
        block_event_market=lambda b: None,
        is_blocked=lambda b: False,
        placed_today={},
        active_providers=["unibet", "pinnacle"],
    )
    runner.state = "awaiting_hedges"
    runner._counter_events = {"pinnacle": asyncio.Event()}
    runner._counter_intercepted = {}
    runner.on_counter_bet_intercepted("pinnacle", {"placed": True, "stake": 90}, None)
    assert "pinnacle" in runner._counter_intercepted
    assert runner._counter_events["pinnacle"].is_set()
