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


class TestCheckLivePrice:
    @pytest.mark.asyncio
    async def test_returns_odds_and_edge_from_yes_ask_dollars(self, workflow):
        # yes_ask_dollars=0.5 → 50¢ → live_odds=2.0, fair_odds=1.6 → edge=+25%
        market = SimpleNamespace(yes_ask_dollars=0.5, yes_ask=0)
        workflow._markets.get_market.return_value = SimpleNamespace(market=market)
        workflow._pending_ticker = "TICKER"

        bet = _make_bet(fair_odds=1.6)
        odds, edge = await workflow.check_live_price(page=None, bet=bet)

        assert odds == pytest.approx(2.0, abs=0.001)
        assert edge == pytest.approx(25.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_falls_back_to_yes_ask_cents_when_dollars_missing(self, workflow):
        market = SimpleNamespace(yes_ask=50)  # 50 cents, no yes_ask_dollars
        workflow._markets.get_market.return_value = SimpleNamespace(market=market)
        workflow._pending_ticker = "TICKER"

        bet = _make_bet(fair_odds=2.0)
        odds, edge = await workflow.check_live_price(page=None, bet=bet)

        assert odds == pytest.approx(2.0, abs=0.001)
        assert edge == pytest.approx(0.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_no_fair_odds_returns_none_edge(self, workflow):
        market = SimpleNamespace(yes_ask_dollars=0.5)
        workflow._markets.get_market.return_value = SimpleNamespace(market=market)
        workflow._pending_ticker = "TICKER"

        bet = _make_bet(fair_odds=None)
        odds, edge = await workflow.check_live_price(page=None, bet=bet)

        assert odds == pytest.approx(2.0, abs=0.001)
        assert edge is None

    @pytest.mark.asyncio
    async def test_zero_yes_ask_returns_none_none(self, workflow):
        market = SimpleNamespace(yes_ask_dollars=0, yes_ask=0)
        workflow._markets.get_market.return_value = SimpleNamespace(market=market)
        workflow._pending_ticker = "TICKER"

        bet = _make_bet()
        odds, edge = await workflow.check_live_price(page=None, bet=bet)

        assert odds is None
        assert edge is None

    @pytest.mark.asyncio
    async def test_no_pending_ticker_returns_none_none(self, workflow):
        workflow._pending_ticker = None
        bet = _make_bet()
        odds, edge = await workflow.check_live_price(page=None, bet=bet)
        assert odds is None
        assert edge is None
