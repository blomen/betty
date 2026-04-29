"""Unit tests for KalshiWorkflow — settle merge, edge calc, stake math, order lifecycle.

All tests mock the kalshi-python SDK clients so they run without creds.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from arnold.mirror.workflows.kalshi import KalshiWorkflow


@pytest.fixture
def workflow(monkeypatch):
    """KalshiWorkflow with mocked PortfolioApi + MarketsApi (no real SDK / creds)."""
    # Skip _init_client by clearing creds; we wire mocks manually.
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PEM", raising=False)
    wf = KalshiWorkflow(provider_id="kalshi", domain="kalshi.com")
    wf._client = MagicMock(name="KalshiClient")
    wf._portfolio = MagicMock(name="PortfolioApi")
    wf._markets = MagicMock(name="MarketsApi")
    return wf


def _make_bet(**overrides) -> SimpleNamespace:
    """Build a bet SimpleNamespace matching what _bet_ns produces in play_loop."""
    base = dict(
        bet_id=42,
        odds=1.5,
        fair_odds=1.6,
        outcome="yes",
        provider_event_id="kalshi_KXNBAGAME-25APR30LAKWAR-LAK",
        provider_market_ticker="KXNBAGAME-25APR30LAKWAR-LAK",
    )
    base.update(overrides)
    return SimpleNamespace(**base)
