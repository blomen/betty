# Kalshi Mirror Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `KalshiWorkflow` up to Polymarket-parity practical behavior — settle merge, live edge, stake math, order lifecycle — without changing architecture, then verify on a live $1–2 placement.

**Architecture:** Keep the dedicated `KalshiWorkflow` class. All edits land in `arnold/mirror/workflows/kalshi.py` (and the mirrored copy at `backend/src/mirror/workflows/kalshi.py`). New unit tests at `arnold/tests/workflows/test_kalshi_workflow.py` mock the SDK; the live-fire pass is a manual test driven by the spec.

**Tech Stack:** Python 3.10+, `kalshi-python` v2.1.4 SDK (pydantic v2 models), pytest + pytest-asyncio for unit tests, postgres MCP for DB verification.

**Spec:** `docs/superpowers/specs/2026-04-30-kalshi-polish-design.md`

---

## File Structure

| File | Role |
|---|---|
| `arnold/mirror/workflows/kalshi.py` | Primary workflow — all behavior changes land here first |
| `backend/src/mirror/workflows/kalshi.py` | Server-side mirror copy — kept in lockstep with arnold copy |
| `arnold/tests/workflows/test_kalshi_workflow.py` | New unit-test module with mocked SDK clients |

No new files outside tests. No registry changes. No frontend.

---

## Task 1: Test scaffolding

**Files:**
- Create: `arnold/tests/workflows/test_kalshi_workflow.py`

This file holds all unit tests for the workflow. We seed it with imports + a fixture that builds a `KalshiWorkflow` whose `_portfolio` and `_markets` are `MagicMock` (so we never need real creds during tests). All later tasks add `class Test*` blocks to this file.

- [ ] **Step 1: Create the test file with shared fixture**

```python
# arnold/tests/workflows/test_kalshi_workflow.py
"""Unit tests for KalshiWorkflow — settle merge, edge calc, stake math, order lifecycle.

All tests mock the kalshi-python SDK clients so they run without creds.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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
```

- [ ] **Step 2: Run pytest collection to confirm the fixture imports cleanly**

Run: `cd arnold && python -m pytest tests/workflows/test_kalshi_workflow.py --collect-only -q`
Expected: `1 test collected` is wrong (no tests yet), but the message should be `no tests ran` with no import errors.

If you see `ImportError` / `ModuleNotFoundError`, fix that before continuing — the fixture is foundational.

- [ ] **Step 3: Commit**

```bash
git add arnold/tests/workflows/test_kalshi_workflow.py
git commit -m "test(kalshi): scaffold workflow test module with mocked SDK fixture"
```

---

## Task 2: Live edge in `check_live_price`

**Files:**
- Modify: `arnold/mirror/workflows/kalshi.py:240-253`
- Test: `arnold/tests/workflows/test_kalshi_workflow.py`

`check_live_price` returns `(odds, None)` today. We change it to compute `live_edge` against `bet.fair_odds`, matching Polymarket. We also accept both `yes_ask_dollars` (float 0–1, current SDK) and `yes_ask` (cents int, older docs) as fallback.

- [ ] **Step 1: Write the failing tests**

Add this class to `arnold/tests/workflows/test_kalshi_workflow.py`:

```python
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
        # Strip yes_ask_dollars so the fallback path is exercised.
        del_attr = lambda: None  # noqa: E731
        # SimpleNamespace without yes_ask_dollars: getattr returns None
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd arnold && python -m pytest tests/workflows/test_kalshi_workflow.py::TestCheckLivePrice -v`
Expected: `test_returns_odds_and_edge_from_yes_ask_dollars` and `test_no_fair_odds_returns_none_edge` FAIL because the current implementation always returns `(odds, None)`. The other three may pass coincidentally — that's fine.

- [ ] **Step 3: Implement the change in `arnold/mirror/workflows/kalshi.py`**

Replace the `check_live_price` method body (currently lines ~240-253) with:

```python
    async def check_live_price(self, page: "Page", bet) -> tuple[float | None, float | None]:
        if not self.has_api or not self._pending_ticker:
            return None, None
        try:
            resp = self._markets.get_market(self._pending_ticker)
            mkt = getattr(resp, "market", None)
            if mkt is None:
                return None, None
            # SDK ships both yes_ask (cents int) and yes_ask_dollars (float 0-1).
            # Prefer dollars (newer field), fall back to cents.
            yad = getattr(mkt, "yes_ask_dollars", None)
            if yad is not None and float(yad) > 0:
                yes_ask_cents = float(yad) * 100.0
            else:
                yes_ask_cents = float(getattr(mkt, "yes_ask", 0) or 0)
            if yes_ask_cents <= 0:
                return None, None
            live_odds = round(100.0 / yes_ask_cents, 4)
            fair = getattr(bet, "fair_odds", None) if not isinstance(bet, dict) else bet.get("fair_odds")
            live_edge = (
                round((live_odds / float(fair) - 1.0) * 100.0, 2)
                if fair
                else None
            )
            return live_odds, live_edge
        except Exception as e:
            logger.warning(f"[kalshi] check_live_price failed: {e}")
            return None, None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd arnold && python -m pytest tests/workflows/test_kalshi_workflow.py::TestCheckLivePrice -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add arnold/mirror/workflows/kalshi.py arnold/tests/workflows/test_kalshi_workflow.py
git commit -m "feat(kalshi): compute live_edge in check_live_price (Polymarket parity)"
```

---

## Task 3: Stake math fix in `prep_betslip`

**Files:**
- Modify: `arnold/mirror/workflows/kalshi.py:217-232`
- Test: `arnold/tests/workflows/test_kalshi_workflow.py`

Today: `count = max(1, int(stake // max(yes_price_dollars, 0.01)))` — truncates. New: round-to-nearest, and report `actual_stake = count * yes_price`.

- [ ] **Step 1: Write the failing tests**

Add this class:

```python
class TestPrepBetslip:
    @pytest.mark.asyncio
    async def test_stake_round_nearest_not_truncate(self, workflow):
        # $5 stake at yes_price=$0.67: floor → 7 contracts ($4.69),
        # round-nearest → 7 ($4.69) too in this case. Use a clearer one:
        # $5 at $0.66: floor → 7 ($4.62); round-nearest → 8 ($5.28). Pick 8.
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd arnold && python -m pytest tests/workflows/test_kalshi_workflow.py::TestPrepBetslip -v`
Expected: `test_stake_round_nearest_not_truncate` FAILS (current code floors to 7, test wants 8). Others may pass already.

- [ ] **Step 3: Implement the change**

In `arnold/mirror/workflows/kalshi.py`, replace `prep_betslip` (currently lines ~217-232) with:

```python
    async def prep_betslip(self, page: "Page", bet, stake: float) -> PlacementResult:
        # No DOM interaction; stash the order params for place_bet().
        self._pending_ticker = getattr(bet, "provider_market_ticker", None) or getattr(
            bet, "provider_event_id", None
        )
        if not self._pending_ticker:
            return PlacementResult(status="failed", bet_id=getattr(bet, "bet_id", 0), reason="no_ticker")
        yes_price_dollars = self._infer_yes_price(bet)
        self._pending_yes_price_cents = max(1, min(99, int(round(yes_price_dollars * 100))))
        # Round-nearest (not floor) so a $5 stake at 66¢ buys 8 contracts ($5.28),
        # not 7 ($4.62). Floor systematically under-stakes; the spread is small
        # enough that round-nearest is the closer fill to user intent.
        self._pending_count = max(1, round(stake / max(yes_price_dollars, 0.01)))
        actual_stake = round(self._pending_count * yes_price_dollars, 2)
        return PlacementResult(
            status="ready",
            bet_id=getattr(bet, "bet_id", 0),
            actual_odds=round(1.0 / yes_price_dollars, 4),
            actual_stake=actual_stake,
        )
```

Note the `bet_id=getattr(bet, "bet_id", 0)` — current code uses `getattr(bet, "id", 0)` which is wrong (the bet ns uses `bet_id`). Keep both attributes for backward compat by trying `bet_id` first, then `id`:

```python
        bid = getattr(bet, "bet_id", None)
        if bid is None:
            bid = getattr(bet, "id", 0)
```

Replace each `getattr(bet, "id", 0)` with this lookup.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd arnold && python -m pytest tests/workflows/test_kalshi_workflow.py::TestPrepBetslip -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add arnold/mirror/workflows/kalshi.py arnold/tests/workflows/test_kalshi_workflow.py
git commit -m "fix(kalshi): round-nearest contract count + report actual_stake from prep"
```

---

## Task 4: `sync_balance` cache on transient failure

**Files:**
- Modify: `arnold/mirror/workflows/kalshi.py` (`__init__` adds cache; `sync_balance` uses it)
- Test: `arnold/tests/workflows/test_kalshi_workflow.py`

Today `sync_balance` returns `0.0` on any exception — masks outages as "broke account." Add a small in-memory `(value, ts)` cache; return last known value on transient failure, only return `0.0` if there's never been a successful read.

- [ ] **Step 1: Write the failing tests**

Add this class:

```python
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
        # Subsequent failure should return the cached value, not 0.0.
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
        workflow._portfolio = None  # simulate no-creds stub
        bal = await workflow.sync_balance(page=None)
        assert bal == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd arnold && python -m pytest tests/workflows/test_kalshi_workflow.py::TestSyncBalance -v`
Expected: `test_caches_last_known_value` FAILS (current code returns 0.0 on exception, not cached value).

- [ ] **Step 3: Implement the change**

In `arnold/mirror/workflows/kalshi.py`:

(a) In `__init__`, after the existing fields, add:
```python
        self._balance_cache: float | None = None  # last successful sync_balance value
```

(b) Replace `sync_balance` body with:
```python
    async def sync_balance(self, page: "Page") -> float:
        if not self.has_api:
            return 0.0
        try:
            resp = self._portfolio.get_balance()
            cents = getattr(resp, "balance", None) or 0
            value = round(float(cents) / 100.0, 2)
            self._balance_cache = value
            return value
        except Exception as e:
            logger.warning(f"[kalshi] sync_balance failed: {e}")
            # Return cached value on transient failure to avoid masking outages
            # as a 0.0 balance (which would block placements / look like a broke
            # account in the UI). 0.0 only if we've never had a successful read.
            if self._balance_cache is not None:
                return self._balance_cache
            return 0.0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd arnold && python -m pytest tests/workflows/test_kalshi_workflow.py::TestSyncBalance -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add arnold/mirror/workflows/kalshi.py arnold/tests/workflows/test_kalshi_workflow.py
git commit -m "feat(kalshi): cache last-known balance on transient sync_balance failure"
```

---

## Task 5: Order lifecycle polling in `place_bet`

**Files:**
- Modify: `arnold/mirror/workflows/kalshi.py:255-279` (place_bet) + add helper
- Test: `arnold/tests/workflows/test_kalshi_workflow.py`

Today: fire-and-forget create_order, trust create response, return `placed`. New: poll `get_order` for up to ~5 seconds, branch on terminal state, cancel resting orders to release capital and surface a `failed` reason.

The SDK's exact field/value names for `get_order` will be verified in implementation; this plan assumes `resp.order.status` returns one of {`resting`, `executed`, `canceled`, `failed`} and `resp.order.fill_count` / `resp.order.fill_price` carry fills. If the SDK shape differs, the helper `_classify_order_state` is the only spot to update.

- [ ] **Step 1: Write the failing tests**

Add this class:

```python
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
        executed = SimpleNamespace(
            order=SimpleNamespace(status="executed", fill_count=10, fill_price=50)
        )
        workflow._portfolio.get_order.return_value = executed

        workflow._pending_ticker = "T"
        workflow._pending_yes_price_cents = 50
        workflow._pending_count = 10

        result = await workflow.place_bet(page=None, bet=_make_bet(), stake=5.0)
        assert result.status == "placed"
        assert result.actual_odds == pytest.approx(2.0, abs=0.001)
        # actual_stake reflects fill: 10 contracts * $0.50 = $5.00
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
    async def test_polling_errors_twice_falls_back_to_create_response(
        self, workflow, monkeypatch
    ):
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
        # After 2 polling errors, trust the create response → placed
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd arnold && python -m pytest tests/workflows/test_kalshi_workflow.py::TestPlaceBet -v`
Expected: `test_resting_then_canceled_after_timeout`, `test_canceled_terminal_state`, `test_polling_errors_twice_falls_back_to_create_response` FAIL because the current code has no polling loop.

- [ ] **Step 3: Implement the change**

In `arnold/mirror/workflows/kalshi.py`, add this helper near the top of the class (above `place_bet`):

```python
    @staticmethod
    def _classify_order_state(resp) -> tuple[str, dict]:
        """Read terminal/non-terminal state from an SDK get_order response.

        Centralizes SDK field-name knowledge so a future SDK change touches one spot.
        Returns (state, fill_info) where state is one of:
          "filled"   — terminal, fill_info has fill_count + fill_price
          "canceled" — terminal, fill_info has reason
          "failed"   — terminal, fill_info has reason
          "resting"  — non-terminal, retry
        """
        order = getattr(resp, "order", None) or resp
        status = (getattr(order, "status", "") or "").lower()
        if status in {"executed", "filled"}:
            return "filled", {
                "fill_count": int(getattr(order, "fill_count", 0) or 0),
                "fill_price": int(getattr(order, "fill_price", 0) or 0),
            }
        if status in {"canceled", "cancelled"}:
            return "canceled", {"reason": getattr(order, "reason", None) or "canceled"}
        if status in {"failed", "rejected"}:
            return "failed", {"reason": getattr(order, "reason", None) or status}
        return "resting", {}
```

Then replace `place_bet` (currently ~lines 255-279) with:

```python
    async def place_bet(self, page: "Page", bet, stake: float) -> PlacementResult:
        import asyncio

        bid = getattr(bet, "bet_id", None)
        if bid is None:
            bid = getattr(bet, "id", 0)

        if not self.has_api or not self._pending_ticker:
            return PlacementResult(status="failed", bet_id=bid, reason="no_client")

        try:
            create_resp = self._portfolio.create_order(
                ticker=self._pending_ticker,
                action="buy",
                side="yes",
                type="limit",
                yes_price=self._pending_yes_price_cents,
                count=self._pending_count,
                expiration_ts=int(time.time()) + 60,
            )
        except Exception as e:
            logger.error(f"[kalshi] create_order failed: {e}")
            return PlacementResult(status="failed", bet_id=bid, reason=str(e))

        order_id = getattr(create_resp, "order_id", None)
        raw = create_resp.to_dict() if hasattr(create_resp, "to_dict") else None

        # Poll get_order up to 5 times (1s apart). After 2 consecutive polling
        # errors, fall back to trusting the create response — a flaky GET shouldn't
        # double-cancel a real fill.
        poll_errors = 0
        last_state = "resting"
        last_info: dict = {}
        if order_id:
            for _ in range(5):
                await asyncio.sleep(1.0)
                try:
                    poll_resp = self._portfolio.get_order(order_id)
                    poll_errors = 0
                except Exception as e:
                    poll_errors += 1
                    logger.warning(f"[kalshi] get_order poll failed: {e}")
                    if poll_errors >= 2:
                        # Trust create response — assume placed.
                        return PlacementResult(
                            status="placed",
                            bet_id=bid,
                            actual_odds=round(100.0 / max(self._pending_yes_price_cents, 1), 4),
                            actual_stake=round(
                                self._pending_count * self._pending_yes_price_cents / 100.0, 2
                            ),
                            reason="poll_unavailable_trusting_create",
                            raw_response=raw,
                        )
                    continue
                state, info = self._classify_order_state(poll_resp)
                last_state, last_info = state, info
                if state == "filled":
                    fc = info.get("fill_count") or self._pending_count
                    fp = info.get("fill_price") or self._pending_yes_price_cents
                    return PlacementResult(
                        status="placed",
                        bet_id=bid,
                        actual_odds=round(100.0 / max(fp, 1), 4),
                        actual_stake=round(fc * fp / 100.0, 2),
                        raw_response=raw,
                    )
                if state in {"canceled", "failed"}:
                    return PlacementResult(
                        status="failed",
                        bet_id=bid,
                        reason=info.get("reason") or state,
                        raw_response=raw,
                    )

        # Still resting after the poll budget — cancel and report failed.
        cancel_reason = "unfilled_within_5s"
        if order_id:
            try:
                self._portfolio.cancel_order(order_id)
            except Exception as e:
                logger.error(f"[kalshi] cancel_order on resting timeout failed: {e}")
                cancel_reason = "unfilled_cancel_failed"
        return PlacementResult(
            status="failed",
            bet_id=bid,
            reason=cancel_reason,
            raw_response=raw,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd arnold && python -m pytest tests/workflows/test_kalshi_workflow.py::TestPlaceBet -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add arnold/mirror/workflows/kalshi.py arnold/tests/workflows/test_kalshi_workflow.py
git commit -m "feat(kalshi): poll get_order, cancel resting after 5s, fall back on poll errors"
```

---

## Task 6: Settlement merge in `sync_history`

**Files:**
- Modify: `arnold/mirror/workflows/kalshi.py:165-198`
- Test: `arnold/tests/workflows/test_kalshi_workflow.py`

Today: every fill returns as `pending`. New: query `PortfolioApi.get_positions()` once, build a `ticker → result` map, override fill status when a position carries a settled `result` (`yes` / `no` / `void`).

All our orders are `side="yes"`, so position `result="yes"` → won, `result="no"` → lost, `result="void"` → void.

- [ ] **Step 1: Write the failing tests**

Add this class:

```python
class TestSyncHistory:
    @pytest.mark.asyncio
    async def test_open_position_stays_pending(self, workflow):
        # Position exists but no result field (market not closed).
        position = SimpleNamespace(market_ticker="T1", result=None)
        workflow._portfolio.get_positions.return_value = SimpleNamespace(positions=[position])
        fill = SimpleNamespace(ticker="T1", side="yes", count=10, price=50, order_id="o-1")
        workflow._portfolio.get_fills.return_value = SimpleNamespace(fills=[fill])

        entries = await workflow.sync_history(page=None)
        assert len(entries) == 1
        assert entries[0].status == "pending"
        assert entries[0].payout is None

    @pytest.mark.asyncio
    async def test_settled_yes_result_marks_won(self, workflow):
        position = SimpleNamespace(market_ticker="T1", result="yes", total_count=10)
        workflow._portfolio.get_positions.return_value = SimpleNamespace(positions=[position])
        fill = SimpleNamespace(ticker="T1", side="yes", count=10, price=50, order_id="o-1")
        workflow._portfolio.get_fills.return_value = SimpleNamespace(fills=[fill])

        entries = await workflow.sync_history(page=None)
        assert entries[0].status == "won"
        # Payout: 10 contracts * $1 (YES wins payout = $1/contract) = $10
        assert entries[0].payout == pytest.approx(10.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_settled_no_result_marks_lost(self, workflow):
        position = SimpleNamespace(market_ticker="T1", result="no", total_count=10)
        workflow._portfolio.get_positions.return_value = SimpleNamespace(positions=[position])
        fill = SimpleNamespace(ticker="T1", side="yes", count=10, price=50, order_id="o-1")
        workflow._portfolio.get_fills.return_value = SimpleNamespace(fills=[fill])

        entries = await workflow.sync_history(page=None)
        assert entries[0].status == "lost"
        assert entries[0].payout == pytest.approx(0.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_void_result_marks_void_with_stake_refund(self, workflow):
        position = SimpleNamespace(market_ticker="T1", result="void", total_count=10)
        workflow._portfolio.get_positions.return_value = SimpleNamespace(positions=[position])
        fill = SimpleNamespace(ticker="T1", side="yes", count=10, price=50, order_id="o-1")
        workflow._portfolio.get_fills.return_value = SimpleNamespace(fills=[fill])

        entries = await workflow.sync_history(page=None)
        assert entries[0].status == "void"
        # Stake refund: count * price = 10 * 0.5 = $5.00
        assert entries[0].payout == pytest.approx(5.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_positions_call_failure_falls_back_to_pending_only(self, workflow):
        workflow._portfolio.get_positions.side_effect = RuntimeError("offline")
        fill = SimpleNamespace(ticker="T1", side="yes", count=10, price=50, order_id="o-1")
        workflow._portfolio.get_fills.return_value = SimpleNamespace(fills=[fill])

        entries = await workflow.sync_history(page=None)
        # Settlement merge skipped — entries still come back as pending
        assert len(entries) == 1
        assert entries[0].status == "pending"

    @pytest.mark.asyncio
    async def test_fills_call_failure_returns_empty_list(self, workflow):
        workflow._portfolio.get_fills.side_effect = RuntimeError("offline")
        entries = await workflow.sync_history(page=None)
        assert entries == []

    @pytest.mark.asyncio
    async def test_no_api_returns_empty(self, workflow):
        workflow._portfolio = None
        entries = await workflow.sync_history(page=None)
        assert entries == []

    @pytest.mark.asyncio
    async def test_multiple_fills_one_position(self, workflow):
        # User bought twice on the same ticker; both fills, one position.
        position = SimpleNamespace(market_ticker="T1", result="yes", total_count=20)
        workflow._portfolio.get_positions.return_value = SimpleNamespace(positions=[position])
        fills = [
            SimpleNamespace(ticker="T1", side="yes", count=10, price=50, order_id="o-1"),
            SimpleNamespace(ticker="T1", side="yes", count=10, price=55, order_id="o-2"),
        ]
        workflow._portfolio.get_fills.return_value = SimpleNamespace(fills=fills)

        entries = await workflow.sync_history(page=None)
        assert len(entries) == 2
        assert all(e.status == "won" for e in entries)
        # Each fill payout = its own count * $1 (YES wins are $1/contract)
        assert all(e.payout == pytest.approx(10.0, abs=0.01) for e in entries)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd arnold && python -m pytest tests/workflows/test_kalshi_workflow.py::TestSyncHistory -v`
Expected: `test_settled_yes_result_marks_won`, `test_settled_no_result_marks_lost`, `test_void_result_marks_void_with_stake_refund`, `test_multiple_fills_one_position` FAIL because today's code marks everything pending.

- [ ] **Step 3: Implement the change**

Replace `sync_history` body with:

```python
    async def sync_history(self, page: "Page") -> list[HistoryEntry]:
        if not self.has_api:
            return []
        # 1. Fetch fills (always — they're the source of stake/odds/timestamps).
        try:
            fills_resp = self._portfolio.get_fills(limit=200)
            fills = getattr(fills_resp, "fills", None) or []
        except Exception as e:
            logger.warning(f"[kalshi] get_fills failed: {e}")
            return []

        # 2. Fetch positions (best-effort — if it fails we just skip the
        # settlement merge and return everything as pending, matching old
        # behavior). Build ticker → result map for O(1) lookup per fill.
        positions_by_ticker: dict[str, str] = {}
        try:
            pos_resp = self._portfolio.get_positions()
            positions = getattr(pos_resp, "positions", None) or []
            for p in positions:
                ticker = getattr(p, "market_ticker", "") or getattr(p, "ticker", "") or ""
                result = (getattr(p, "result", "") or "").lower()
                if ticker and result in {"yes", "no", "void"}:
                    positions_by_ticker[ticker] = result
        except Exception as e:
            logger.warning(f"[kalshi] get_positions failed (settlement merge skipped): {e}")

        # 3. Build entries; merge in resolved status when the position carries
        # a settled `result`. All our orders are side="yes", so:
        #   result=yes  → won  (payout = count * $1)
        #   result=no   → lost (payout = $0)
        #   result=void → void (payout = stake refund = count * price)
        out: list[HistoryEntry] = []
        for f in fills:
            ticker = getattr(f, "ticker", "") or ""
            side = getattr(f, "side", "") or ""
            count = int(getattr(f, "count", 0) or 0)
            price_cents = int(getattr(f, "price", 0) or 0)
            order_id = getattr(f, "order_id", None) or getattr(f, "fill_id", None) or ""
            odds = round(100.0 / max(price_cents, 1), 4) if price_cents else 0.0
            stake = round(count * price_cents / 100.0, 2)

            status = "pending"
            payout: float | None = None
            settled = positions_by_ticker.get(ticker)
            if settled == "yes":
                status, payout = "won", round(count * 1.0, 2)
            elif settled == "no":
                status, payout = "lost", 0.0
            elif settled == "void":
                status, payout = "void", stake

            out.append(
                HistoryEntry(
                    provider_bet_id=str(order_id),
                    event_name=ticker,
                    market=ticker,
                    outcome=side,
                    odds=odds,
                    stake=stake,
                    status=status,
                    payout=payout,
                )
            )
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd arnold && python -m pytest tests/workflows/test_kalshi_workflow.py::TestSyncHistory -v`
Expected: all 8 tests PASS.

- [ ] **Step 5: Run the full test module to catch regressions**

Run: `cd arnold && python -m pytest tests/workflows/test_kalshi_workflow.py -v`
Expected: all tests across all classes PASS.

- [ ] **Step 6: Commit**

```bash
git add arnold/mirror/workflows/kalshi.py arnold/tests/workflows/test_kalshi_workflow.py
git commit -m "feat(kalshi): merge get_positions result into sync_history for settlement"
```

---

## Task 7: Mirror changes to `backend/src/mirror/workflows/kalshi.py`

**Files:**
- Modify: `backend/src/mirror/workflows/kalshi.py`

The two copies have minor formatting drift (we saw `contextlib.suppress` vs `try/except OSError` differences earlier) but the logic must stay aligned because both are imported by their respective sides of the app. Sync the same behavior changes.

- [ ] **Step 1: Read the backend copy**

Run: `diff arnold/mirror/workflows/kalshi.py backend/src/mirror/workflows/kalshi.py | head -60`
Expected: Confirm only formatting/import-ordering differences in non-method areas before our changes; method bodies should now differ where we edited the arnold copy.

- [ ] **Step 2: Apply the same edits**

Open `backend/src/mirror/workflows/kalshi.py` and apply the same method replacements from Tasks 2, 3, 4, 5, 6:
- `check_live_price` (Task 2)
- `prep_betslip` (Task 3)
- `__init__` adds `_balance_cache` field; `sync_balance` uses it (Task 4)
- `_classify_order_state` helper + `place_bet` rewrite (Task 5)
- `sync_history` rewrite (Task 6)

The two files must be byte-equivalent in their method bodies after this.

- [ ] **Step 3: Verify the diff is now minimal (formatting-only)**

Run: `diff arnold/mirror/workflows/kalshi.py backend/src/mirror/workflows/kalshi.py | head -60`
Expected: Diff shows only the pre-existing formatting differences (e.g. `contextlib.suppress` block, import ordering). No method-body differences.

- [ ] **Step 4: Run the backend test suite to check nothing imports broken**

Run: `cd backend && python -c "from src.mirror.workflows.kalshi import KalshiWorkflow; print('ok')"`
Expected: `ok`

Run: `cd backend && python -m pytest tests/ -k kalshi -v`
Expected: existing `test_kalshi_parser.py` continues to pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/mirror/workflows/kalshi.py
git commit -m "chore(kalshi): mirror polish changes into backend/src copy"
```

---

## Task 8: Pre-deploy verification

**Files:** none

Before live-fire we sanity-check both code paths run, ruff/eslint hooks already auto-format on save, full pytest run is clean, no dangling pyflakes warnings.

- [ ] **Step 1: Full local test run**

Run: `cd arnold && python -m pytest tests/ -v`
Expected: all tests PASS (no Kalshi regressions, no other workflow regressions).

- [ ] **Step 2: Backend test run**

Run: `cd backend && python -m pytest tests/ -v`
Expected: all tests PASS.

- [ ] **Step 3: Lint**

Run: `cd backend && ruff check src/mirror/workflows/kalshi.py`
Run: `cd .. && ruff check arnold/mirror/workflows/kalshi.py arnold/tests/workflows/test_kalshi_workflow.py`
Expected: no errors.

- [ ] **Step 4: Confirm autonomous_placement path will route correctly**

Run a quick repl check that `place_bet` in the runner's autonomous branch will invoke our new logic:

```bash
cd arnold && python -c "
from arnold.mirror.workflows.kalshi import KalshiWorkflow
wf = KalshiWorkflow(provider_id='kalshi', domain='kalshi.com')
print('autonomous_placement:', wf.autonomous_placement)
print('platform:', wf.platform)
print('has_api (no creds):', wf.has_api)
"
```
Expected output:
```
autonomous_placement: True
platform: kalshi
has_api (no creds): False
```

- [ ] **Step 5: Commit any auto-format changes (if hooks fired)**

```bash
git status
# If anything changed:
git add -u && git commit -m "chore(kalshi): apply auto-format from PostToolUse hooks"
```

---

## Task 9: Server deploy

**Files:** none — deploy via script.

The server runs the same code; rebuild backend so the merged changes ship.

- [ ] **Step 1: Push to main**

```bash
git push origin main
```

- [ ] **Step 2: Check deploy gate**

Run: `ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh status"`
Expected: lock free, no deploy in progress, no live broker position.

If a position is open and the user confirms it's safe to flatten, deploy with the override flag (per CLAUDE.md autonomous-broker rules). Otherwise wait until flat.

- [ ] **Step 3: Rebuild backend**

Run: `ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh rebuild backend"`
Expected: deploy completes; `/health` returns 200 within ~2 minutes.

- [ ] **Step 4: Confirm Kalshi extraction continues to run**

Run: `ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T backend cat /app/logs/extraction.log | tail -30 | grep -i kalshi"`
Expected: at least one Kalshi extraction line within the last hour, no traceback.

---

## Task 10: Live-fire test (manual, follows spec section 6)

**Files:** none — execution follows `docs/superpowers/specs/2026-04-30-kalshi-polish-design.md` Section 6 verbatim.

This task is the proof that the polish actually works in practice.

- [ ] **Step 1: Pre-flight checks**

Launch arnold locally:
```bash
arnold.bat
```

Confirm in the UI:
- Kalshi visible in the provider list with green login indicator
- Balance shown in UI matches kalshi.com header
- A few `pending` Kalshi history rows exist if any prior bets, OR the list is empty cleanly (no exception spam in arnold logs)

If any of those fail, fix and re-run before proceeding to placement.

- [ ] **Step 2: Independent price probe**

Pick the ticker for the value bet you'll place. From a separate shell:

```bash
# Replace TICKER with the actual market ticker
curl -s 'https://api.elections.kalshi.com/trade-api/v2/markets/TICKER' \
     -H "KALSHI-ACCESS-KEY: $KALSHI_API_KEY_ID" \
     -H "..." | jq '.market | {yes_ask, yes_ask_dollars}'
```

Confirm the cents/dollars value matches what arnold is showing as live odds.

- [ ] **Step 3: Place a small live bet**

In arnold, click Place on a Kalshi value bet sized $1–2. Watch the backend logs (`docker compose exec -T backend tail -f /app/logs/...` is irrelevant since arnold logs locally — watch the terminal where arnold.bat runs):

Expected log lines (in order):
- `[kalshi] prep_betslip stashed ...` (count, yes_price_cents, actual_stake)
- `[kalshi] check_live_price ...` (live_odds, live_edge)
- `[kalshi] create_order called ...`
- `[kalshi] get_order poll ...` (state) — should see `executed`/`filled` quickly on a liquid market
- `[runner] bet_placed ...` (broadcaster event)

- [ ] **Step 4: Verify the DB row**

Use postgres MCP to query:
```sql
SELECT id, provider_id, event_name, outcome, odds, stake, status, created_at
FROM bets
WHERE provider_id = 'kalshi'
ORDER BY created_at DESC LIMIT 5;
```
Expected: the bet you just placed appears, status=`pending`, stake matches the rounded `actual_stake` from prep, odds matches the fill odds.

- [ ] **Step 5: Verify position on Kalshi**

Refresh https://kalshi.com/portfolio. The position should appear with the count and price you placed.

- [ ] **Step 6: Failure-path test**

In arnold, set up a deliberately off-market limit by picking a stale-priced bet (or, simpler: temporarily edit `_pending_yes_price_cents` via a debug flag to a clearly non-fillable value like 5¢ on a 50¢ market).

Trigger placement; expect:
- `place_bet` polls 5 times, each `resting`
- `cancel_order` called
- `PlacementResult(status='failed', reason='unfilled_within_5s')`
- UI shows the bet as failed/skipped, no DB row written

- [ ] **Step 7: Settlement run (after market close)**

Wait for the test market to settle. Trigger or wait for the next mirror sync_history cycle (or call `POST /mirror/sync-history` if it's exposed; otherwise wait the standard cadence).

- Confirm the `bets` row flips from `pending` to `won` / `lost` matching reality
- Confirm the bankroll updated by the payout amount

- [ ] **Step 8: Final sweep**

Manually visit https://kalshi.com/portfolio — confirm no orphaned resting orders from any of the test runs.

- [ ] **Step 9: Update memory**

If anything surfaced that contradicts the existing memory `project_kalshi_smarkets_integration.md`, update it. Otherwise add a brief note saying live-fire test passed on YYYY-MM-DD.

```bash
# Edit C:\Users\rasmu\.claude\projects\c--Users-rasmu-arnold\memory\project_kalshi_smarkets_integration.md
# Append a status line about the live-fire pass.
```

---

## Self-Review

**Spec coverage:**
- Section 2 (settlement merge) — Task 6 ✓
- Section 3 (live edge) — Task 2 ✓
- Section 4 (stake math + order lifecycle + DB record verify) — Tasks 3, 5, 10 ✓
- Section 5 (error handling) — Tasks 4 (balance cache), 5 (poll fallback), 6 (positions failure) ✓
- Section 6 (live-fire test plan) — Task 10 ✓
- Architecture unchanged — no migration tasks ✓

**Placeholders:** none — every step has concrete code and commands.

**Type consistency:** `_pending_ticker`, `_pending_yes_price_cents`, `_pending_count`, `_balance_cache` named identically across all tasks. `PlacementResult` fields match base.py. `HistoryEntry` fields match base.py. `bet_id` lookup pattern (`getattr(bet, "bet_id", None)` then `id`) used consistently.

**Spec gaps:** none — every spec requirement maps to a task.
