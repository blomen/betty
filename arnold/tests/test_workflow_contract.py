"""Contract: every workflow must implement read_slip_odds + update_slip_stake."""

from __future__ import annotations

import asyncio
import inspect

from arnold.mirror.workflows.base import ProviderWorkflow


def test_base_workflow_defines_read_slip_odds():
    assert hasattr(ProviderWorkflow, "read_slip_odds")
    sig = inspect.signature(ProviderWorkflow.read_slip_odds)
    # (self, page) — 2 params
    assert len(sig.parameters) == 2


def test_base_workflow_defines_update_slip_stake():
    assert hasattr(ProviderWorkflow, "update_slip_stake")
    sig = inspect.signature(ProviderWorkflow.update_slip_stake)
    # (self, page, stake) — 3 params
    assert len(sig.parameters) == 3


def test_base_workflow_default_read_slip_odds_returns_none():
    """Default implementation returns None — workflows without slip-scrape opt out."""

    class _Stub(ProviderWorkflow):
        platform = "stub"

        async def check_login(self, page):
            return True

        async def sync_history(self, page):
            return []

        async def sync_balance(self, page):
            return 0.0

        async def navigate_to_event(self, page, bet):
            return True

        async def place_bet(self, page, bet, stake): ...

    wf = _Stub(provider_id="x", domain="x.com")
    result = asyncio.run(wf.read_slip_odds(page=None))
    assert result is None


def test_base_workflow_default_update_slip_stake_returns_false():
    """Default implementation returns False — workflows opt in by overriding."""

    class _Stub(ProviderWorkflow):
        platform = "stub"

        async def check_login(self, page):
            return True

        async def sync_history(self, page):
            return []

        async def sync_balance(self, page):
            return 0.0

        async def navigate_to_event(self, page, bet):
            return True

        async def place_bet(self, page, bet, stake): ...

    wf = _Stub(provider_id="x", domain="x.com")
    result = asyncio.run(wf.update_slip_stake(page=None, stake=10.0))
    assert result is False
