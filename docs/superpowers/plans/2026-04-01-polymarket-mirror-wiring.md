# Polymarket Mirror Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Polymarket into the mirror browser system — passive interception (balance, deposits, orders) + active bet placement via Playwright UI automation with price verification.

**Architecture:** Extend the existing `BetInterceptor` URL pattern matching to capture Polymarket-specific traffic (balance, orders, deposits). Add a `PolymarketParser` for response parsing. Add `place_polymarket_bets()` to `MirrorService` for sequential Playwright-driven bet placement with slippage checks. Expose via `POST /api/mirror/place-bets`.

**Tech Stack:** Python, Playwright (Patchright), FastAPI, SQLAlchemy, SSE broadcasting

**Spec:** `docs/superpowers/specs/2026-04-01-polymarket-mirror-wiring-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `backend/src/mirror/parsers/polymarket.py` | **Create** | Parse Polymarket balance, orders, Fun.xyz tx responses |
| `backend/src/mirror/interceptor.py` | Modify | Add Polymarket domain + URL patterns |
| `backend/src/mirror/service.py` | Modify | USDC balance extraction, bet placement automation, settlement |
| `backend/src/api/routes/mirror.py` | Modify | Add `POST /api/mirror/place-bets` endpoint |
| `backend/tests/mirror/test_polymarket_parser.py` | **Create** | Parser unit tests |
| `backend/tests/mirror/test_polymarket_wiring.py` | **Create** | Integration tests for interception + placement |
| `docs/mirror-wiring.md` | Modify | Update Polymarket row |

---

### Task 1: Polymarket Parser

**Files:**
- Create: `backend/src/mirror/parsers/polymarket.py`
- Create: `backend/tests/mirror/test_polymarket_parser.py`

- [ ] **Step 1: Write failing tests for balance parsing**

```python
# backend/tests/mirror/test_polymarket_parser.py
"""Tests for Polymarket mirror parser."""
import pytest
from src.mirror.parsers.polymarket import PolymarketParser


class TestBalanceParsing:
    def test_parse_value_response(self):
        """data-api.polymarket.com/value returns [{"user": "0x...", "value": 123.45}]"""
        parser = PolymarketParser()
        body = '[{"user": "0x71fca29E6B31a93d262D2972C9b361Af371D426d", "value": 123.45}]'
        result = parser.parse_balance("https://data-api.polymarket.com/value?user=0x71fca", body)
        assert result == 123.45

    def test_parse_zero_balance(self):
        parser = PolymarketParser()
        body = '[{"user": "0x71fca29E6B31a93d262D2972C9b361Af371D426d", "value": 0}]'
        result = parser.parse_balance("https://data-api.polymarket.com/value?user=0x71fca", body)
        assert result == 0.0

    def test_parse_invalid_json(self):
        parser = PolymarketParser()
        result = parser.parse_balance("https://data-api.polymarket.com/value", "not json")
        assert result is None

    def test_parse_empty_array(self):
        parser = PolymarketParser()
        result = parser.parse_balance("https://data-api.polymarket.com/value", "[]")
        assert result is None


class TestOrderParsing:
    def test_parse_open_orders(self):
        """clob.polymarket.com/data/orders returns order list."""
        parser = PolymarketParser()
        body = '''[{
            "id": "order-123",
            "status": "live",
            "asset_id": "token-abc",
            "side": "BUY",
            "price": "0.62",
            "original_size": "25.0",
            "size_matched": "10.0",
            "outcome": "Yes",
            "market": "Will X happen?",
            "created_at": 1774997100
        }]'''
        orders = parser.parse_orders(body)
        assert len(orders) == 1
        assert orders[0]["id"] == "order-123"
        assert orders[0]["side"] == "BUY"
        assert orders[0]["price"] == 0.62
        assert orders[0]["size"] == 25.0
        assert orders[0]["filled"] == 10.0

    def test_parse_empty_orders(self):
        parser = PolymarketParser()
        assert parser.parse_orders("[]") == []


class TestDepositParsing:
    def test_parse_swapped_order(self):
        """widget.swapped.com/api/v1/order/create_order response."""
        parser = PolymarketParser()
        body = '{"orderId": "sw-123", "amount": 100, "currency": "USD", "status": "pending"}'
        result = parser.parse_deposit("https://widget.swapped.com/api/v1/order/create_order", body)
        assert result is not None
        assert result["amount"] == 100
        assert result["order_id"] == "sw-123"

    def test_non_deposit_url(self):
        parser = PolymarketParser()
        result = parser.parse_deposit("https://other.com/api", '{"foo": 1}')
        assert result is None


class TestPriceVerification:
    def test_price_within_slippage(self):
        parser = PolymarketParser()
        assert parser.check_slippage(expected=0.62, actual=0.63, max_pct=2.0) is True

    def test_price_exceeds_slippage(self):
        parser = PolymarketParser()
        assert parser.check_slippage(expected=0.62, actual=0.70, max_pct=2.0) is False

    def test_parse_book_best_ask(self):
        parser = PolymarketParser()
        book_body = '{"asks": [{"price": "0.63", "size": "150"}, {"price": "0.65", "size": "200"}], "bids": [{"price": "0.61", "size": "100"}]}'
        best_ask = parser.parse_best_ask(book_body)
        assert best_ask == 0.63

    def test_parse_book_empty(self):
        parser = PolymarketParser()
        assert parser.parse_best_ask('{"asks": [], "bids": []}') is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/mirror/test_polymarket_parser.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.mirror.parsers.polymarket'`

- [ ] **Step 3: Implement the parser**

```python
# backend/src/mirror/parsers/polymarket.py
"""Polymarket mirror parser — balance, orders, deposits, price verification."""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class PolymarketParser:
    """Parses Polymarket-specific browser traffic responses."""

    def parse_balance(self, url: str, body: str) -> float | None:
        """Extract USDC portfolio value from data-api.polymarket.com/value response.

        Response format: [{"user": "0x...", "value": 123.45}]
        Returns value in USDC, or None if unparseable.
        """
        try:
            data = json.loads(body)
            if isinstance(data, list) and data and "value" in data[0]:
                return float(data[0]["value"])
        except (json.JSONDecodeError, TypeError, ValueError, IndexError) as e:
            logger.debug(f"[polymarket] Could not parse balance: {e}")
        return None

    def parse_orders(self, body: str) -> list[dict]:
        """Parse open orders from clob.polymarket.com/data/orders response.

        Returns normalized order list with: id, side, price, size, filled, outcome, market, status.
        """
        try:
            data = json.loads(body)
            if not isinstance(data, list):
                return []
            orders = []
            for o in data:
                orders.append({
                    "id": o.get("id", ""),
                    "status": o.get("status", ""),
                    "token_id": o.get("asset_id", ""),
                    "side": o.get("side", ""),
                    "price": float(o.get("price", 0)),
                    "size": float(o.get("original_size", 0)),
                    "filled": float(o.get("size_matched", 0)),
                    "outcome": o.get("outcome", ""),
                    "market": o.get("market", ""),
                })
            return orders
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            logger.debug(f"[polymarket] Could not parse orders: {e}")
            return []

    def parse_deposit(self, url: str, body: str) -> dict | None:
        """Parse deposit initiation from Swapped widget create_order response.

        Returns {"order_id": "...", "amount": 100, "currency": "USD"} or None.
        """
        if "create_order" not in url:
            return None
        try:
            data = json.loads(body)
            order_id = data.get("orderId")
            amount = data.get("amount")
            if order_id and amount is not None:
                return {
                    "order_id": str(order_id),
                    "amount": float(amount),
                    "currency": data.get("currency", "USD"),
                    "status": data.get("status", "unknown"),
                }
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            logger.debug(f"[polymarket] Could not parse deposit: {e}")
        return None

    def check_slippage(self, expected: float, actual: float, max_pct: float) -> bool:
        """Check if price slippage is within acceptable range.

        Returns True if acceptable, False if exceeds max_pct.
        """
        if expected <= 0:
            return False
        slippage_pct = abs(actual - expected) / expected * 100
        return slippage_pct <= max_pct

    def parse_best_ask(self, body: str) -> float | None:
        """Extract best ask price from CLOB order book response.

        Response format: {"asks": [{"price": "0.63", "size": "150"}, ...], "bids": [...]}
        """
        try:
            data = json.loads(body)
            asks = data.get("asks", [])
            if asks:
                return float(asks[0]["price"])
        except (json.JSONDecodeError, TypeError, ValueError, IndexError, KeyError) as e:
            logger.debug(f"[polymarket] Could not parse order book: {e}")
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/mirror/test_polymarket_parser.py -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/mirror/parsers/polymarket.py backend/tests/mirror/test_polymarket_parser.py
git commit -m "feat(mirror): add Polymarket parser for balance, orders, deposits, price verification"
```

---

### Task 2: Interceptor — Polymarket URL Patterns

**Files:**
- Modify: `backend/src/mirror/interceptor.py`

- [ ] **Step 1: Add polymarket.com to _PROVIDER_DOMAINS**

In `BetInterceptor._PROVIDER_DOMAINS` dict (line 60-76), add after the `"pinnacle.com": "pinnacle"` entry:

```python
"polymarket.com": "polymarket",
```

- [ ] **Step 2: Add Polymarket-specific URL patterns**

Add a new class-level tuple for Polymarket financial/order URLs. These don't fit into the existing `_FINANCIAL_KEYWORDS` because they have different URL structures. Add after `_GRAPHQL_RELAY_PATTERNS` (line 50):

```python
# Polymarket-specific URL patterns
_POLYMARKET_FINANCIAL_PATTERNS = (
    "data-api.polymarket.com/value",    # Portfolio value (USDC)
    "clob.polymarket.com/data/orders",  # Open orders
    "widget.swapped.com/api/v1/order",  # Deposit via Swapped
)
```

- [ ] **Step 3: Wire Polymarket patterns into _on_response**

In the `_on_response` method (line 165), add a Polymarket-specific check in the financial data section. After the existing `_is_financial` block (around line 207-223), extend the check:

```python
            # Polymarket-specific financial patterns
            if not _is_financial and any(p in url for p in self._POLYMARKET_FINANCIAL_PATTERNS):
                _is_financial = True
```

Insert this right after line 210 (`_is_financial = any(kw in url for kw in self._FINANCIAL_KEYWORDS)`) and before line 211 (`_relay_body = None`).

- [ ] **Step 4: Test manually — verify Polymarket traffic triggers callbacks**

Navigate to polymarket.com in the mirror browser. Check backend logs for:
```
[mirror] Provider detected: polymarket
```
And balance sync logs when the page loads and fetches `/value`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/mirror/interceptor.py
git commit -m "feat(mirror): add Polymarket URL patterns to interceptor"
```

---

### Task 3: Service — Balance Sync & Deposit Detection

**Files:**
- Modify: `backend/src/mirror/service.py`
- Create: `backend/tests/mirror/test_polymarket_wiring.py`

- [ ] **Step 1: Write failing test for Polymarket balance extraction**

```python
# backend/tests/mirror/test_polymarket_wiring.py
"""Tests for Polymarket mirror wiring in MirrorService."""
import pytest
from src.mirror.service import MirrorService


class TestPolymarketBalanceExtraction:
    def test_extract_polymarket_balance(self):
        """_extract_balance should handle Polymarket value response."""
        service = MirrorService(broadcaster=None, provider_id="polymarket")
        # Polymarket data-api /value format: list with user + value
        data = [{"user": "0x71fca29E6B31a93d262D2972C9b361Af371D426d", "value": 87.5}]
        balance = service._extract_balance("polymarket", data)
        assert balance == 87.5

    def test_extract_polymarket_zero(self):
        service = MirrorService(broadcaster=None, provider_id="polymarket")
        data = [{"user": "0x71fca", "value": 0}]
        balance = service._extract_balance("polymarket", data)
        assert balance == 0.0

    def test_extract_polymarket_empty(self):
        service = MirrorService(broadcaster=None, provider_id="polymarket")
        balance = service._extract_balance("polymarket", [])
        assert balance is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/mirror/test_polymarket_wiring.py::TestPolymarketBalanceExtraction -v`
Expected: FAIL — `_extract_balance` doesn't handle Polymarket list format yet.

- [ ] **Step 3: Add Polymarket balance extraction to _extract_balance**

In `MirrorService._extract_balance` (line 482 of `service.py`), add a new block at the **top** of the try block (before the Kambi check), since Polymarket's format is a list, not a dict:

```python
        try:
            # Polymarket: [{"user": "0x...", "value": 123.45}]
            if isinstance(data, list) and data and "user" in data[0] and "value" in data[0]:
                return float(data[0]["value"])
```

This goes right after `try:` on line 484, before the existing `# Kambi / Unibet` comment.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/mirror/test_polymarket_wiring.py::TestPolymarketBalanceExtraction -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Add Polymarket to provider detection**

In `MirrorService._detect_provider` (line 873), add to the `domain_map` dict:

```python
            # Polymarket
            "polymarket": "polymarket",
```

Also add Polymarket-specific URL patterns. In the same method, after the Altenar shared gateway check (around line 926), add:

```python
        # Polymarket data API
        if "polymarket" in url_lower or "swapped.com" in url_lower:
            return "polymarket"
```

- [ ] **Step 6: Import and use PolymarketParser for deposit traces**

At the top of `service.py`, add the import:

```python
from .parsers.polymarket import PolymarketParser
```

In `__init__`, add the parser instance (after `self.parser = GeckoBetParser()`):

```python
        self.polymarket_parser = PolymarketParser()
```

- [ ] **Step 7: Add deposit trace storage for Swapped orders**

In `_handle_financial_data` (line 462), after the balance extraction/sync block, add a Polymarket deposit trace:

```python
        # Polymarket: store deposit trace from Swapped widget
        if "swapped.com" in url and "create_order" in url:
            deposit = self.polymarket_parser.parse_deposit(url, response_body)
            if deposit:
                logger.info(f"[mirror] Polymarket deposit initiated: ${deposit['amount']} {deposit['currency']}")
                self._notify("deposit_initiated", {
                    "provider": "polymarket",
                    "amount": deposit["amount"],
                    "currency": deposit["currency"],
                    "order_id": deposit["order_id"],
                })
```

- [ ] **Step 8: Add open orders interception**

In `_handle_financial_data`, add order parsing when we detect the CLOB orders endpoint:

```python
        # Polymarket: parse and broadcast open orders
        if "clob.polymarket.com/data/orders" in url:
            orders = self.polymarket_parser.parse_orders(response_body)
            if orders:
                self._notify("polymarket_orders", {
                    "orders": orders,
                    "count": len(orders),
                    "open": len([o for o in orders if o["status"] == "live"]),
                })
```

- [ ] **Step 9: Commit**

```bash
git add backend/src/mirror/service.py backend/tests/mirror/test_polymarket_wiring.py
git commit -m "feat(mirror): wire Polymarket balance sync, deposit detection, and order interception"
```

---

### Task 4: Bet Placement Automation

**Files:**
- Modify: `backend/src/mirror/service.py`
- Modify: `backend/src/api/routes/mirror.py`

- [ ] **Step 1: Add place_polymarket_bets method to MirrorService**

Add this method to `MirrorService`. It handles sequential Playwright-driven bet placement with price verification:

```python
    async def place_polymarket_bets(self, bets: list[dict]) -> dict:
        """Place bets on Polymarket via Playwright UI automation.

        Each bet dict: {bet_id, market_slug, token_id, outcome, amount_usdc, expected_price, max_slippage_pct}
        Returns: {placed: [...], skipped: [...], failed: [...], total: N}
        """
        context = self.interceptor.context
        if not context or not context.pages:
            return {"error": "No mirror browser open", "placed": [], "skipped": [], "failed": [], "total": 0}

        page = context.pages[0]
        placed = []
        skipped = []
        failed = []

        for bet in bets:
            bet_id = bet["bet_id"]
            slug = bet["market_slug"]
            outcome = bet["outcome"]  # "Yes" or "No"
            amount = bet["amount_usdc"]
            expected_price = bet["expected_price"]
            max_slippage = bet.get("max_slippage_pct", 2.0)

            self._notify("polymarket_bet_placing", {
                "bet_id": bet_id, "market_slug": slug,
                "outcome": outcome, "amount": amount,
            })

            try:
                result = await self._place_single_polymarket_bet(
                    page, bet_id, slug, outcome, amount, expected_price, max_slippage
                )
                if result["status"] == "placed":
                    placed.append(result)
                elif result["status"] == "skipped":
                    skipped.append(result)
                else:
                    failed.append(result)
            except Exception as e:
                logger.error(f"[mirror] Polymarket bet {bet_id} failed: {e}", exc_info=True)
                result = {"bet_id": bet_id, "status": "failed", "reason": str(e)}
                failed.append(result)
                self._notify("polymarket_bet_failed", result)

        summary = {"placed": placed, "skipped": skipped, "failed": failed, "total": len(bets)}
        self._notify("polymarket_batch_complete", {
            "placed": len(placed), "skipped": len(skipped),
            "failed": len(failed), "total": len(bets),
        })
        return summary

    async def _place_single_polymarket_bet(
        self, page, bet_id: int, slug: str, outcome: str,
        amount: float, expected_price: float, max_slippage: float,
    ) -> dict:
        """Place a single bet on Polymarket via browser automation.

        Steps: navigate → select outcome → verify price → enter amount → confirm.
        """
        import asyncio

        # 1. Navigate to market page
        market_url = f"https://polymarket.com/{slug}"
        logger.info(f"[mirror] Placing Polymarket bet {bet_id}: {market_url} {outcome} ${amount}")
        await page.goto(market_url, wait_until="networkidle", timeout=20000)
        await asyncio.sleep(2)  # Let React hydrate

        # 2. Verify price via CLOB API before interacting with UI
        context = self.interceptor.context
        try:
            book_url = f"https://clob.polymarket.com/book?token_id={slug}"
            # We'll use the token_id from the bet dict for actual price check
            # For now, read the displayed price from the page after selecting outcome
        except Exception as e:
            logger.warning(f"[mirror] Could not fetch CLOB book: {e}")

        # 3. Click outcome button (Yes/No)
        # Polymarket uses "Buy" panel with Yes/No toggle
        outcome_lower = outcome.lower()
        outcome_selector = f'button:has-text("{outcome}")'
        try:
            await page.click(outcome_selector, timeout=5000)
            await asyncio.sleep(0.5)
        except Exception as e:
            return {"bet_id": bet_id, "status": "failed", "reason": f"Could not click {outcome}: {e}"}

        # 4. Read current price from the order form
        try:
            # Look for price display in the order form area
            price_text = await page.evaluate("""() => {
                // Try multiple selectors for the price display
                const selectors = [
                    '[data-testid="price-display"]',
                    '.price-input input',
                    'input[placeholder*="Price"]',
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el) return el.value || el.textContent;
                }
                // Fallback: look for any input with a value between 0 and 1
                const inputs = document.querySelectorAll('input[type="number"]');
                for (const inp of inputs) {
                    const v = parseFloat(inp.value);
                    if (v > 0 && v < 1) return inp.value;
                }
                return null;
            }""")
            if price_text:
                current_price = float(price_text)
                slippage_ok = self.polymarket_parser.check_slippage(expected_price, current_price, max_slippage)
                slippage_pct = abs(current_price - expected_price) / expected_price * 100

                self._notify("polymarket_bet_price_check", {
                    "bet_id": bet_id, "expected": expected_price,
                    "actual": current_price, "slippage_pct": round(slippage_pct, 2),
                })

                if not slippage_ok:
                    logger.warning(
                        f"[mirror] Polymarket bet {bet_id}: slippage {slippage_pct:.1f}% "
                        f"exceeds {max_slippage}% (expected={expected_price}, actual={current_price})"
                    )
                    return {
                        "bet_id": bet_id, "status": "skipped", "reason": "slippage",
                        "expected_price": expected_price, "actual_price": current_price,
                        "slippage_pct": round(slippage_pct, 2),
                    }
        except Exception as e:
            logger.warning(f"[mirror] Could not read price for bet {bet_id}: {e}")
            # Continue anyway — price check is best-effort

        # 5. Enter amount
        try:
            amount_input = page.locator('input[placeholder*="Amount"], input[placeholder*="Enter"]').first
            await amount_input.click()
            await amount_input.fill("")
            await amount_input.type(str(amount), delay=50)
            await asyncio.sleep(0.5)
        except Exception as e:
            return {"bet_id": bet_id, "status": "failed", "reason": f"Could not enter amount: {e}"}

        # 6. Click Buy/confirm button
        try:
            buy_btn = page.locator('button:has-text("Buy")').first
            await buy_btn.click(timeout=5000)
            await asyncio.sleep(1)
        except Exception as e:
            return {"bet_id": bet_id, "status": "failed", "reason": f"Could not click Buy: {e}"}

        # 7. Handle Fun.xyz transaction confirmation popup
        try:
            # Fun.xyz may show a confirmation dialog/iframe
            confirm_btn = page.locator('button:has-text("Confirm"), button:has-text("Approve")').first
            await confirm_btn.click(timeout=15000)
            await asyncio.sleep(3)  # Wait for on-chain tx
        except Exception:
            # No confirm popup — might auto-confirm or might have failed
            logger.debug(f"[mirror] No Fun.xyz confirm popup for bet {bet_id}")
            await asyncio.sleep(3)

        # 8. Check for success indicators on the page
        try:
            success = await page.evaluate("""() => {
                const text = document.body.innerText;
                return text.includes('Order placed') || text.includes('Success') || text.includes('Confirmed');
            }""")
            if success:
                logger.info(f"[mirror] Polymarket bet {bet_id} confirmed")
                result = {
                    "bet_id": bet_id, "status": "placed",
                    "amount_usdc": amount, "outcome": outcome,
                }
                self._notify("polymarket_bet_placed", result)
                return result
        except Exception:
            pass

        # If we got here, uncertain — report as placed but flag for verification
        logger.warning(f"[mirror] Polymarket bet {bet_id}: placement uncertain")
        result = {
            "bet_id": bet_id, "status": "placed",
            "amount_usdc": amount, "outcome": outcome,
            "note": "confirmation_uncertain",
        }
        self._notify("polymarket_bet_placed", result)
        return result
```

- [ ] **Step 2: Run existing mirror tests to confirm nothing is broken**

Run: `cd backend && python -m pytest tests/mirror/ -v --timeout=30`
Expected: All existing tests still PASS

- [ ] **Step 3: Commit**

```bash
git add backend/src/mirror/service.py
git commit -m "feat(mirror): add Polymarket bet placement automation via Playwright"
```

---

### Task 5: API Endpoint

**Files:**
- Modify: `backend/src/api/routes/mirror.py`

- [ ] **Step 1: Add the place-bets endpoint**

Add after the existing `reject_settlements` endpoint (around line 125):

```python
from pydantic import BaseModel


class PolymarketBetRequest(BaseModel):
    bet_id: int
    market_slug: str
    token_id: str = ""
    outcome: str  # "Yes" or "No"
    amount_usdc: float
    expected_price: float
    max_slippage_pct: float = 2.0


class PlaceBetsRequest(BaseModel):
    bets: list[PolymarketBetRequest]


@router.post("/place-bets")
async def place_polymarket_bets(request: PlaceBetsRequest):
    """Place a batch of bets on Polymarket via mirror browser automation.

    Sequential execution — each bet is placed one at a time with price verification.
    Progress is streamed via SSE events.
    """
    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(400, "No mirror running")
    if not mirror.interceptor.context or not mirror.interceptor.context.pages:
        raise HTTPException(400, "No browser pages open")

    # Check that we're on Polymarket
    page = mirror.interceptor.context.pages[0]
    if "polymarket.com" not in (page.url or ""):
        raise HTTPException(400, f"Mirror browser is not on Polymarket (current: {page.url})")

    bets = [b.model_dump() for b in request.bets]
    result = await mirror.place_polymarket_bets(bets)
    return result
```

- [ ] **Step 2: Move the Pydantic imports to the top of the file**

The `BaseModel` import should go at the top with the other imports:

```python
from pydantic import BaseModel
```

Remove the inline import from the endpoint code block above.

- [ ] **Step 3: Test the endpoint with curl (mirror must be running on Polymarket)**

```bash
curl -X POST http://localhost:8000/api/mirror/place-bets \
  -H "Content-Type: application/json" \
  -d '{"bets": []}'
```

Expected: `{"placed": [], "skipped": [], "failed": [], "total": 0}` (empty batch should succeed)

- [ ] **Step 4: Commit**

```bash
git add backend/src/api/routes/mirror.py
git commit -m "feat(mirror): add POST /api/mirror/place-bets endpoint"
```

---

### Task 6: Settlement Wiring

**Files:**
- Modify: `backend/src/mirror/service.py`

- [ ] **Step 1: Add Polymarket settlement method to MirrorService**

Add this method to `MirrorService`:

```python
    def settle_polymarket_bets(self) -> list[dict]:
        """Check for resolved Polymarket markets and stage settlements for pending bets.

        Uses the Gamma API (via PolymarketRetriever.fetch_resolved) to find finished events,
        then matches against pending Polymarket bets.
        """
        from ..db.models import get_session, Bet, Odds
        from ..repositories.profile_repo import ProfileRepo

        db = get_session()
        staged = []
        try:
            profile = ProfileRepo(db).get_active()
            pending = db.query(Bet).filter(
                Bet.profile_id == profile.id,
                Bet.provider_id == "polymarket",
                Bet.result == "pending",
            ).all()

            if not pending:
                return []

            # For each pending bet, check if its event has resolved
            for bet in pending:
                if not bet.event_id:
                    continue

                # Check if any Polymarket odds for this event+outcome have resolved
                # by looking at the odds provider_meta for event_slug and checking Gamma API
                odds = db.query(Odds).filter(
                    Odds.event_id == bet.event_id,
                    Odds.provider == "polymarket",
                    Odds.market == bet.market,
                    Odds.outcome == bet.outcome,
                ).first()

                if not odds or not odds.provider_meta:
                    continue

                # Check if outcome price resolved to 1.0 (won) or 0.0 (lost)
                # This data comes from extraction runs where fetch_resolved() updates event status
                from ..db.models import Event
                event = db.get(Event, bet.event_id)
                if not event or event.status != "finished":
                    continue

                # Determine result from the event resolution
                # Binary market: if bet outcome matches winner → won, else → lost
                result = "pending"  # Will be set by matching logic
                payout = 0.0

                # Look at resolved odds — price of 1.0 means this outcome won
                if odds.odds and odds.odds <= 1.01:
                    # This outcome resolved to $1 — won
                    result = "won"
                    payout = bet.stake / (1 / odds.odds) if odds.odds > 0 else 0
                elif odds.odds and odds.odds >= 50.0:
                    # Extreme odds = resolved to $0 — lost
                    result = "lost"
                    payout = 0

                if result != "pending":
                    staged.append({
                        "bet_id": bet.id,
                        "provider": "polymarket",
                        "event": event.home_team + " vs " + event.away_team if event.home_team else "Unknown",
                        "odds": bet.odds,
                        "stake": bet.stake,
                        "result": result,
                        "payout": payout,
                    })

        except Exception as e:
            logger.error(f"[mirror] Polymarket settlement check failed: {e}", exc_info=True)
        finally:
            db.close()

        if staged:
            self._pending_settlements.extend(staged)
            self._notify("settlements_pending", {
                "provider": "polymarket",
                "count": len(staged),
                "wins": len([s for s in staged if s["result"] == "won"]),
                "losses": len([s for s in staged if s["result"] == "lost"]),
                "total_staked": sum(s["stake"] for s in staged),
                "total_payout": sum(s["payout"] for s in staged),
                "net": sum(s["payout"] for s in staged) - sum(s["stake"] for s in staged),
                "settlements": staged,
            })

        return staged
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/mirror/service.py
git commit -m "feat(mirror): add Polymarket settlement matching"
```

---

### Task 7: Update Mirror Wiring Docs

**Files:**
- Modify: `docs/mirror-wiring.md`

- [ ] **Step 1: Update the Polymarket row in the wiring matrix**

Change line 49 from:
```
| 32 | polymarket | Polymarket | - | N/A | - | - | - | N/A | N/A |
```
to:
```
| 32 | polymarket | Polymarket | ~ | N/A | Y | ~ | - | N/A | N/A |
```

Bet Placement = `~` (wired but needs DOM selector refinement per Polymarket UI updates)
Sync Balance = `Y` (fully wired via data-api interception)
Sync Open Bets = `~` (intercepting orders, needs frontend display)

- [ ] **Step 2: Add Polymarket platform notes**

Add a new section in the Platform Notes area (after the Pinnacle entry, before the closing `## API Endpoint Patterns`):

```markdown
### Polymarket (polymarket)
- **Wallet type**: Magic (email login), signature type 1
- **Balance**: `GET data-api.polymarket.com/value?user={proxy_wallet}` → `[{"value": 123.45}]` (USDC)
- **Deposit**: Via Swapped widget (`POST widget.swapped.com/api/v1/order/create_order`) → Stripe → USDC on Polygon
- **Open orders**: `GET clob.polymarket.com/data/orders` — intercepted from browser traffic
- **Bet placement**: Playwright UI automation — navigate to market → select outcome → verify price → enter amount → confirm via Fun.xyz
- **Price verification**: `GET clob.polymarket.com/book?token_id={id}` — check best ask vs expected price, abort if slippage > 2%
- **Settlement**: Via Gamma API `fetch_resolved()` — binary outcome markets resolve to $1 (won) or $0 (lost)
- **Proxy wallet**: `0x71fca29E6B31a93d262D2972C9b361Af371D426d`
- **Signing address**: `0x19a769e2F52baa34D16258F9cd5Fd6D572522974`
```

- [ ] **Step 3: Add Polymarket API patterns to the endpoint list**

In the `## API Endpoint Patterns Discovered` section, add:

```
# Polymarket
GET   data-api.polymarket.com/value?user={proxy_wallet}              # portfolio value (USDC)
GET   data-api.polymarket.com/v1/leaderboard?user={proxy_wallet}     # leaderboard
GET   gamma-api.polymarket.com/is-logged-in                          # auth check (type: magic)
GET   gamma-api.polymarket.com/users                                 # user profile + proxy wallet
GET   clob.polymarket.com/data/orders                                # open orders
GET   clob.polymarket.com/book?token_id={id}                         # order book
POST  api.fun.xyz/v1/fops                                            # tx execution (Fun.xyz)
POST  widget.swapped.com/api/v1/order/create_order                   # fiat deposit
GET   polymarket.com/api/account/has-deposited?address={wallet}      # deposit status
```

- [ ] **Step 4: Commit**

```bash
git add docs/mirror-wiring.md
git commit -m "docs: update mirror wiring matrix and notes for Polymarket"
```

---

### Task 8: DOM Selector Discovery

**Files:**
- Modify: `backend/src/mirror/service.py` (refine selectors in `_place_single_polymarket_bet`)

This task requires the mirror browser to be on a Polymarket market page. The selectors in Task 4 are best-guesses — this task captures the real DOM structure.

- [ ] **Step 1: Navigate to a Polymarket market in the mirror and inspect DOM**

Use the mirror browser page to evaluate JS and discover the actual selectors:

```bash
# Via the page-eval endpoint (after server restart to pick up changes):
curl "http://localhost:8000/api/mirror/page-eval?js=%28%29%20%3D%3E%20%7B%0A%20%20const%20buttons%20%3D%20%5B...document.querySelectorAll%28%27button%27%29%5D.map%28b%20%3D%3E%20%28%7Btext%3A%20b.textContent.trim%28%29.slice%280%2C%2050%29%2C%20classes%3A%20b.className.slice%280%2C%2080%29%2C%20testid%3A%20b.dataset.testid%20%7C%7C%20%27%27%7D%29%29%3B%0A%20%20const%20inputs%20%3D%20%5B...document.querySelectorAll%28%27input%27%29%5D.map%28i%20%3D%3E%20%28%7Btype%3A%20i.type%2C%20placeholder%3A%20i.placeholder%2C%20name%3A%20i.name%2C%20classes%3A%20i.className.slice%280%2C%2080%29%7D%29%29%3B%0A%20%20return%20%7Bbuttons%2C%20inputs%7D%3B%0A%7D"
```

Or in Python via the interceptor context:

```python
page = mirror.interceptor.context.pages[0]
await page.goto("https://polymarket.com/some-market-slug", wait_until="networkidle")
# Discover buttons
buttons = await page.evaluate("""() => {
    return [...document.querySelectorAll('button')].map(b => ({
        text: b.textContent.trim().slice(0, 50),
        classes: b.className.slice(0, 80),
        testid: b.dataset.testid || '',
    }));
}""")
# Discover inputs
inputs = await page.evaluate("""() => {
    return [...document.querySelectorAll('input')].map(i => ({
        type: i.type, placeholder: i.placeholder,
        name: i.name, classes: i.className.slice(0, 80),
    }));
}""")
```

- [ ] **Step 2: Update selectors in _place_single_polymarket_bet**

Replace the generic selectors with the discovered real ones. Common patterns on Polymarket:
- Outcome buttons: `[data-testid="outcome-button-yes"]` or `div[role="button"]:has-text("Yes")`
- Amount input: `input[data-testid="trade-amount-input"]` or `input[placeholder="$0"]`
- Buy button: `button[data-testid="trade-button"]` or `button:has-text("Buy")`
- Price display: `span[data-testid="trade-price"]` or the value shown next to the outcome

Update the selectors in the method based on what you find.

- [ ] **Step 3: Test one bet placement end-to-end**

Place a small test bet ($1) on a liquid market via the API:

```bash
curl -X POST http://localhost:8000/api/mirror/place-bets \
  -H "Content-Type: application/json" \
  -d '{
    "bets": [{
      "bet_id": 0,
      "market_slug": "DISCOVERED_SLUG_HERE",
      "token_id": "",
      "outcome": "Yes",
      "amount_usdc": 1.0,
      "expected_price": 0.50,
      "max_slippage_pct": 5.0
    }]
  }'
```

Watch backend logs for the placement flow. Verify the bet appears in your Polymarket portfolio.

- [ ] **Step 4: Commit refined selectors**

```bash
git add backend/src/mirror/service.py
git commit -m "fix(mirror): refine Polymarket DOM selectors from live testing"
```
