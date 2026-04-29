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


class TestSyncBalance:
    @pytest.mark.asyncio
    async def test_returns_balance_in_dollars(self, workflow):
        workflow._portfolio.get_balance.return_value = SimpleNamespace(balance=12345)
        bal = await workflow.sync_balance(page=None)
        assert bal == pytest.approx(123.45, abs=0.01)

    @pytest.mark.asyncio
    async def test_caches_last_known_value(self, workflow):
        workflow._portfolio.get_balance.return_value = SimpleNamespace(balance=12345)
        first = await workflow.sync_balance(page=None)
        assert first == pytest.approx(123.45)
        workflow._portfolio.get_balance.side_effect = RuntimeError("transient")
        second = await workflow.sync_balance(page=None)
        assert second == pytest.approx(123.45)

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_cache_and_failure(self, workflow):
        workflow._portfolio.get_balance.side_effect = RuntimeError("offline")
        bal = await workflow.sync_balance(page=None)
        assert bal == 0.0

    @pytest.mark.asyncio
    async def test_no_api_returns_zero(self, workflow):
        workflow._portfolio = None
        bal = await workflow.sync_balance(page=None)
        assert bal == 0.0


class TestPlaceBet:
    @pytest.mark.asyncio
    async def test_immediate_fill_returns_placed(self, workflow, monkeypatch):
        # Skip real sleeps in the polling loop.
        sleeps = []

        async def fake_sleep(s):
            sleeps.append(s)

        monkeypatch.setattr("asyncio.sleep", fake_sleep)

        order_resp = MagicMock()
        order_resp.to_dict.return_value = {"order_id": "o-1"}
        order_resp.order_id = "o-1"
        workflow._portfolio.create_order.return_value = order_resp

        # First poll already shows executed
        executed = SimpleNamespace(order=SimpleNamespace(status="executed", fill_count=10, fill_price=50))
        workflow._portfolio.get_order.return_value = executed

        workflow._pending_ticker = "T"
        workflow._pending_yes_price_cents = 50
        workflow._pending_count = 10

        result = await workflow.place_bet(page=None, bet=_make_bet(), stake=5.0)
        assert result.status == "placed"
        assert result.actual_odds == pytest.approx(2.0, abs=0.001)
        # 10 contracts * $0.50 = $5.00
        assert result.actual_stake == pytest.approx(5.0, abs=0.01)
        # No cancel call on a filled order
        workflow._portfolio.cancel_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_resting_then_canceled_after_timeout(self, workflow, monkeypatch):
        async def fake_sleep(s):
            return

        monkeypatch.setattr("asyncio.sleep", fake_sleep)

        order_resp = MagicMock()
        order_resp.to_dict.return_value = {"order_id": "o-2"}
        order_resp.order_id = "o-2"
        workflow._portfolio.create_order.return_value = order_resp

        # All polls show resting
        resting = SimpleNamespace(order=SimpleNamespace(status="resting", fill_count=0))
        workflow._portfolio.get_order.return_value = resting
        workflow._portfolio.cancel_order.return_value = SimpleNamespace()

        workflow._pending_ticker = "T"
        workflow._pending_yes_price_cents = 50
        workflow._pending_count = 10

        result = await workflow.place_bet(page=None, bet=_make_bet(), stake=5.0)
        assert result.status == "failed"
        assert result.reason == "unfilled_within_5s"
        workflow._portfolio.cancel_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_canceled_terminal_state(self, workflow, monkeypatch):
        async def fake_sleep(s):
            return

        monkeypatch.setattr("asyncio.sleep", fake_sleep)
        order_resp = MagicMock()
        order_resp.to_dict.return_value = {"order_id": "o-3"}
        order_resp.order_id = "o-3"
        workflow._portfolio.create_order.return_value = order_resp
        workflow._portfolio.get_order.return_value = SimpleNamespace(
            order=SimpleNamespace(status="canceled", reason="user_cancel")
        )

        workflow._pending_ticker = "T"
        workflow._pending_yes_price_cents = 50
        workflow._pending_count = 10

        result = await workflow.place_bet(page=None, bet=_make_bet(), stake=5.0)
        assert result.status == "failed"
        assert "cancel" in result.reason.lower()
        workflow._portfolio.cancel_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_polling_errors_twice_falls_back_to_create_response(self, workflow, monkeypatch):
        async def fake_sleep(s):
            return

        monkeypatch.setattr("asyncio.sleep", fake_sleep)
        order_resp = MagicMock()
        order_resp.to_dict.return_value = {"order_id": "o-4"}
        order_resp.order_id = "o-4"
        workflow._portfolio.create_order.return_value = order_resp
        workflow._portfolio.get_order.side_effect = [
            RuntimeError("503"),
            RuntimeError("503"),
        ]

        workflow._pending_ticker = "T"
        workflow._pending_yes_price_cents = 50
        workflow._pending_count = 10

        result = await workflow.place_bet(page=None, bet=_make_bet(), stake=5.0)
        # After 2 polling errors, trust create response → placed
        assert result.status == "placed"
        # actual_odds derived from yes_price_cents=50 → 2.0
        assert result.actual_odds == pytest.approx(2.0, abs=0.001)
        workflow._portfolio.cancel_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_order_exception_returns_failed(self, workflow):
        workflow._portfolio.create_order.side_effect = RuntimeError("rate_limited")
        workflow._pending_ticker = "T"
        workflow._pending_yes_price_cents = 50
        workflow._pending_count = 10

        result = await workflow.place_bet(page=None, bet=_make_bet(), stake=5.0)
        assert result.status == "failed"
        assert "rate_limited" in result.reason

    @pytest.mark.asyncio
    async def test_no_pending_ticker_returns_failed(self, workflow):
        workflow._pending_ticker = None
        result = await workflow.place_bet(page=None, bet=_make_bet(), stake=5.0)
        assert result.status == "failed"
        assert result.reason == "no_client"

    @pytest.mark.asyncio
    async def test_create_response_missing_order_id_trusts_create(self, workflow):
        order_resp = MagicMock(spec=[])  # no order_id attribute
        order_resp.to_dict = lambda: {}
        workflow._portfolio.create_order.return_value = order_resp

        workflow._pending_ticker = "T"
        workflow._pending_yes_price_cents = 50
        workflow._pending_count = 10

        result = await workflow.place_bet(page=None, bet=_make_bet(), stake=5.0)
        assert result.status == "placed"
        assert result.reason == "no_order_id_trusting_create"
        workflow._portfolio.get_order.assert_not_called()
        workflow._portfolio.cancel_order.assert_not_called()
