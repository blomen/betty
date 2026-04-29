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


class TestPrepBetslip:
    @pytest.mark.asyncio
    async def test_stake_round_nearest_not_truncate(self, workflow):
        # $5 stake at yes_price=$0.66: floor → 7 ($4.62); round-nearest → 8 ($5.28).
        bet = _make_bet(odds=round(1.0 / 0.66, 4))  # yes_price ≈ 0.66
        result = await workflow.prep_betslip(page=None, bet=bet, stake=5.0)

        assert result.status == "ready"
        # 5 / 0.66 ≈ 7.576 → round → 8
        assert workflow._pending_count == 8
        # actual_stake reflects what will be charged: 8 * 0.66 = 5.28
        assert result.actual_stake == pytest.approx(5.28, abs=0.01)
        assert workflow._pending_yes_price_cents == 66

    @pytest.mark.asyncio
    async def test_stake_below_one_contract_floors_to_one(self, workflow):
        # $0.30 at yes_price=$0.50 → 0.6 contracts → must clamp to 1
        bet = _make_bet(odds=2.0)  # yes_price=0.5
        result = await workflow.prep_betslip(page=None, bet=bet, stake=0.30)
        assert workflow._pending_count == 1
        assert result.actual_stake == pytest.approx(0.50, abs=0.01)

    @pytest.mark.asyncio
    async def test_no_ticker_returns_failed(self, workflow):
        bet = _make_bet(provider_market_ticker=None, provider_event_id=None)
        result = await workflow.prep_betslip(page=None, bet=bet, stake=5.0)
        assert result.status == "failed"
        assert result.reason == "no_ticker"

    @pytest.mark.asyncio
    async def test_yes_price_clamped_1_to_99_cents(self, workflow):
        # Implausibly low odds → yes_price > 1.0 should clamp to 99¢
        bet = _make_bet(odds=1.001)  # yes_price ≈ 0.999
        await workflow.prep_betslip(page=None, bet=bet, stake=5.0)
        assert workflow._pending_yes_price_cents == 99
        # Implausibly high odds → yes_price < 0.01 should clamp to 1¢
        bet = _make_bet(odds=10000.0)  # yes_price ≈ 0.0001
        await workflow.prep_betslip(page=None, bet=bet, stake=5.0)
        assert workflow._pending_yes_price_cents == 1
