# Polymarket API-First Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the DOM-based Polymarket workflow with an API-first approach using `py-clob-client` SDK, enabling precise limit orders, reliable price reads, and structured position/history data.

**Architecture:** The workflow switches from AUTONOMOUS (user clicks Buy on site) to API-driven placement via the CLOB SDK. `prep_betslip()` builds and signs an order locally; `place_bet()` submits it when the user confirms. Public CLOB endpoints provide live prices; Data API provides positions/trades. DOM fallback preserved for all methods when no private key is configured.

**Tech Stack:** `py-clob-client` (Polymarket CLOB SDK), `requests` (Data API), Playwright (visual navigation + redeem/claim only)

**Spec:** `docs/superpowers/specs/2026-04-14-polymarket-api-workflow-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `pyproject.toml` | Modify | Add `py-clob-client` dependency |
| `backend/src/providers/polymarket.py` | Modify | Store `token_id` in outcome `provider_meta` |
| `firevsports/mirror/workflows/polymarket.py` | Rewrite | API-first workflow with DOM fallback |
| `firevsports/mirror/play_loop.py` | No change | Already supports `autonomous_placement` path |

---

### Task 1: Add `py-clob-client` dependency

**Files:**
- Modify: `pyproject.toml:59-70` (optional-dependencies section)

- [ ] **Step 1: Add py-clob-client to optional dependencies**

Add a new `poly` optional dependency group in `pyproject.toml`. This keeps the heavy web3/eth dependencies out of the server (which doesn't need them).

In `pyproject.toml`, after the `scrape` group and before the `ml` group, add:

```toml
poly = [
    "py-clob-client>=0.18.0",
]
```

- [ ] **Step 2: Install locally**

Run:
```bash
pip install -e ".[poly]"
```

Expected: Successfully installs `py-clob-client` and its dependencies (`web3`, `eth-account`, etc.)

- [ ] **Step 3: Verify import works**

Run:
```bash
python -c "from py_clob_client.client import ClobClient; print('OK')"
```

Expected: Prints `OK`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "feat(poly): add py-clob-client SDK dependency"
```

---

### Task 2: Store `token_id` in outcome `provider_meta`

**Files:**
- Modify: `backend/src/providers/polymarket.py:189-203` (`_build_outcome` method)

Currently `_build_outcome` stores `bid`, `ask`, `depth_usd` as top-level outcome fields but does NOT persist `token_id`. The storage pipeline already merges `outcome.get("provider_meta", {})` into the final `provider_meta` per odds row. We just need to put `token_id` there.

- [ ] **Step 1: Modify `_build_outcome` to include `token_id` in `provider_meta`**

In `backend/src/providers/polymarket.py`, replace the `_build_outcome` method (lines 189-203):

```python
def _build_outcome(self, name: str, price: float, token_id: str = None, **extra) -> dict:
    """Build an outcome dict with odds and optional CLOB microstructure data."""
    outcome = {"name": name, "odds": self._price_to_odds(price)}
    if token_id:
        bid = self._clob_bids.get(token_id)
        ask = self._clob_asks.get(token_id)
        depth = self._clob_depth.get(token_id)
        if bid is not None:
            outcome["bid"] = bid
        if ask is not None:
            outcome["ask"] = ask
        if depth is not None:
            outcome["depth_usd"] = depth
        # Persist token_id in provider_meta for play loop API access
        outcome["provider_meta"] = {"token_id": token_id}
    outcome.update(extra)
    return outcome
```

The key change: `outcome["provider_meta"] = {"token_id": token_id}`. The storage pipeline merges this with market-level `provider_meta` (`event_slug`, `poly_home`, `poly_away`), so the final per-odds `provider_meta` becomes:
```json
{"event_slug": "...", "poly_home": "...", "poly_away": "...", "token_id": "7132104..."}
```

- [ ] **Step 2: Verify no conflicts with `outcome.update(extra)`**

The `**extra` dict may contain `point` for spread/total markets. It does NOT contain `provider_meta`, so there's no collision. The `outcome.update(extra)` call after our `provider_meta` assignment is safe because no caller passes `provider_meta` in `**extra`.

Verify by searching for all `_build_outcome` calls:
```bash
cd backend && grep -n "_build_outcome" src/providers/polymarket.py
```

Expected: All calls pass positional `name, price, token_id` and keyword `point=` only.

- [ ] **Step 3: Commit**

```bash
git add backend/src/providers/polymarket.py
git commit -m "feat(extraction): store token_id in Polymarket outcome provider_meta"
```

---

### Task 3: Rewrite PolymarketWorkflow — client init + DOM fallback helpers

**Files:**
- Modify: `firevsports/mirror/workflows/polymarket.py` (full rewrite)

This is the largest task. We'll split it into sub-steps. First, set up the client initialization and move existing DOM methods into private `_*_dom` fallback methods.

- [ ] **Step 1: Rewrite the class header, imports, and `__init__`**

Replace the top of `firevsports/mirror/workflows/polymarket.py` (lines 1-22):

```python
"""PolymarketWorkflow — API-first automation for Polymarket via py-clob-client SDK.

Uses CLOB API for: balance, prices, order placement, positions.
Uses DOM for: navigation (visual context), redeem/claim (on-chain tx).
Falls back to DOM for all methods if POLY_PRIVATE_KEY not configured.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

from .base import HistoryEntry, PlacementResult, PositionEntry, ProviderWorkflow, WorkflowMode

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

# Lazy-loaded SDK types (only available if py-clob-client installed)
_ClobClient = None
_OrderArgs = None
_MarketOrderArgs = None
_ApiCreds = None
_BalanceAllowanceParams = None
_AssetType = None
_BUY = None
_OrderType = None


def _load_sdk():
    """Lazy-load py-clob-client SDK types. Returns True if available."""
    global _ClobClient, _OrderArgs, _MarketOrderArgs, _ApiCreds
    global _BalanceAllowanceParams, _AssetType, _BUY, _OrderType
    if _ClobClient is not None:
        return True
    try:
        from py_clob_client.client import ClobClient as _CC
        from py_clob_client.clob_types import (
            ApiCreds as _AC,
            AssetType as _AT,
            BalanceAllowanceParams as _BAP,
            MarketOrderArgs as _MOA,
            OrderArgs as _OA,
            OrderType as _OT,
        )
        from py_clob_client.order_builder.constants import BUY as _B

        _ClobClient = _CC
        _OrderArgs = _OA
        _MarketOrderArgs = _MOA
        _ApiCreds = _AC
        _BalanceAllowanceParams = _BAP
        _AssetType = _AT
        _BUY = _B
        _OrderType = _OT
        return True
    except ImportError:
        logger.warning("[polymarket] py-clob-client not installed — API features disabled")
        return False


class PolymarketWorkflow(ProviderWorkflow):
    platform = "polymarket"
    autonomous_placement = True  # place_bet() submits order via SDK on user confirm

    def __init__(self, provider_id: str, domain: str, mode: WorkflowMode = WorkflowMode.GUIDED):
        super().__init__(provider_id, domain, mode)
        self._client = None  # ClobClient instance (None if no key)
        self._pending_order = None  # Signed order awaiting submission
        self._pending_price: float = 0.0
        self._pending_size: float = 0.0
        self._tabs: dict[str, Page] = {}
        self._init_client()

    def _init_client(self):
        """Initialize CLOB client from env vars. No-op if key missing."""
        key = os.getenv("POLY_PRIVATE_KEY")
        funder = os.getenv("POLY_FUNDER_ADDRESS")
        if not key:
            logger.info("[polymarket] No POLY_PRIVATE_KEY — DOM-only mode")
            return
        if not _load_sdk():
            return
        try:
            sig_type = int(os.getenv("POLY_SIGNATURE_TYPE", "1"))
            self._client = _ClobClient(
                host="https://clob.polymarket.com",
                key=key,
                chain_id=137,
                signature_type=sig_type,
                funder=funder,
            )
            self._client.set_api_creds(self._client.create_or_derive_api_creds())
            logger.info("[polymarket] CLOB client initialized (API mode)")
        except Exception as e:
            logger.error(f"[polymarket] CLOB client init failed: {e}")
            self._client = None

    @property
    def has_api(self) -> bool:
        """True if CLOB client is initialized and ready."""
        return self._client is not None
```

- [ ] **Step 2: Move existing DOM methods into private `_*_dom` fallback methods**

Keep the existing DOM implementations as private methods. They'll be called when `has_api` is False.

Rename the existing methods by adding `_dom` suffix:
- `check_login` → `_check_login_dom`
- `sync_balance` → `_sync_balance_dom`
- `check_live_price` → `_check_live_price_dom`
- `sync_history` → `_sync_history_dom`
- `navigate_to_event` (DOM parts for outcome clicking + stake filling) → `_navigate_and_fill_dom`

The existing `scrape_history`, `scrape_portfolio`, `redeem_all`, `claim_banner`, `_dismiss_modal`, `scan_portfolio_settlements`, `settle_all`, `_import_open_positions`, `cleanup` methods stay as-is (they're DOM-only operations or use the DOM fallback helpers internally).

```python
    # ------------------------------------------------------------------
    # DOM fallback methods (existing implementations, unchanged)
    # ------------------------------------------------------------------

    async def _check_login_dom(self, page: Page) -> bool:
        """Check if logged in by looking for 'Cash $XXX' in the nav."""
        try:
            text = await page.evaluate("""() => {
                const els = document.querySelectorAll('nav *');
                for (const el of els) {
                    const t = (el.textContent || '').trim();
                    if (t.startsWith('Cash') && t.includes('$')) return t;
                }
                return null;
            }""")
            return text is not None
        except Exception as e:
            logger.warning(f"[{self.provider_id}] check_login DOM failed: {e}")
            return False

    async def _sync_balance_dom(self, page: Page) -> float:
        """Scrape USDC cash balance from DOM nav text ('Cash$101.51')."""
        try:
            amount = await page.evaluate("""() => {
                const els = document.querySelectorAll('nav *');
                for (const el of els) {
                    const t = (el.textContent || '').trim();
                    if (t.startsWith('Cash') && t.includes('$')) {
                        const m = t.match(/\\$(\\d[\\d,.]*)/);
                        return m ? parseFloat(m[1].replace(',', '')) : null;
                    }
                }
                return null;
            }""")
            return amount if amount is not None else -1
        except Exception as e:
            logger.warning(f"[{self.provider_id}] sync_balance DOM failed: {e}")
            return -1

    async def _navigate_and_fill_dom(self, page: Page, bet) -> bool:
        """DOM fallback: navigate + click outcome + fill stake via quick-add buttons."""
        slug = getattr(bet, "market_slug", None) or getattr(bet, "event_slug", None)
        if not slug:
            logger.warning(f"[{self.provider_id}] No slug on bet {getattr(bet, 'bet_id', '?')}")
            return False

        outcome = getattr(bet, "poly_outcome", None) or getattr(bet, "outcome", "")
        original_outcome = getattr(bet, "original_outcome", outcome)
        stake = int(getattr(bet, "stake", 0))
        home_name = getattr(bet, "display_home", "") or ""
        away_name = getattr(bet, "display_away", "") or ""

        url = f"https://polymarket.com/event/{slug}"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            logger.warning(f"[{self.provider_id}] navigate failed: {e}")
            return False

        try:
            await page.wait_for_selector("button", timeout=10000)
        except Exception:
            await asyncio.sleep(5)

        # Click outcome button
        outcome_lower = (original_outcome or outcome).lower()
        if outcome_lower in ("home", "over"):
            target = home_name.lower()[:3] if home_name else ""
        elif outcome_lower in ("away", "under"):
            target = away_name.lower()[:3] if away_name else ""
        elif outcome_lower == "draw":
            target = "draw"
        else:
            target = outcome.lower()[:3]

        try:
            clicked = await page.evaluate("""(target) => {
                const btns = [...document.querySelectorAll('button')];
                for (const btn of btns) {
                    const text = (btn.textContent || '').toLowerCase();
                    if (target && text.includes(target) && text.includes('¢')) {
                        btn.scrollIntoView({block: 'center'});
                        btn.click();
                        return btn.textContent.trim().slice(0, 40);
                    }
                }
                return null;
            }""", target)
            if clicked:
                logger.info(f"[polymarket] DOM: Clicked outcome '{clicked}'")
                await asyncio.sleep(1)
        except Exception as e:
            logger.warning(f"[polymarket] DOM: Could not click outcome: {e}")

        # Fill stake via quick-add buttons
        if stake > 0:
            remaining = stake
            for btn_val in [100, 10, 5, 1]:
                while remaining >= btn_val:
                    ok = await page.evaluate(f"""() => {{
                        const btns = document.querySelectorAll('button');
                        for (const btn of btns) {{
                            if (btn.textContent.trim() === '+${btn_val}') {{
                                btn.click(); return true;
                            }}
                        }}
                        return false;
                    }}""")
                    if ok:
                        remaining -= btn_val
                        await asyncio.sleep(0.15)
                    else:
                        break
            if remaining > 0:
                logger.warning(f"[polymarket] DOM: Partial fill ${stake - remaining}/${stake}")

        return True
```

- [ ] **Step 3: Commit**

```bash
git add firevsports/mirror/workflows/polymarket.py
git commit -m "refactor(poly): restructure workflow with DOM fallback methods"
```

---

### Task 4: API-based `check_login` and `sync_balance`

**Files:**
- Modify: `firevsports/mirror/workflows/polymarket.py`

- [ ] **Step 1: Implement API-based `check_login`**

```python
    # ------------------------------------------------------------------
    # Login / balance
    # ------------------------------------------------------------------

    async def check_login(self, page: Page) -> bool:
        """Check login: API balance check if available, else DOM scrape."""
        if not self.has_api:
            return await self._check_login_dom(page)
        try:
            result = self._client.get_balance_allowance(
                params=_BalanceAllowanceParams(asset_type=_AssetType.COLLATERAL)
            )
            # If we get a result, the API creds are valid
            return result is not None and "balance" in result
        except Exception as e:
            logger.warning(f"[polymarket] API check_login failed, trying DOM: {e}")
            return await self._check_login_dom(page)
```

- [ ] **Step 2: Implement API-based `sync_balance`**

```python
    async def sync_balance(self, page: Page) -> float:
        """Read USDC balance: API if available, else DOM scrape."""
        if not self.has_api:
            return await self._sync_balance_dom(page)
        try:
            result = self._client.get_balance_allowance(
                params=_BalanceAllowanceParams(asset_type=_AssetType.COLLATERAL)
            )
            # USDC has 6 decimals on Polygon — py-clob-client may return raw or formatted
            balance = float(result.get("balance", 0))
            # If balance looks like raw wei (> 1M), convert
            if balance > 1_000_000:
                balance = balance / 1e6
            logger.info(f"[polymarket] API balance: ${balance:.2f}")
            return balance
        except Exception as e:
            logger.warning(f"[polymarket] API sync_balance failed, trying DOM: {e}")
            return await self._sync_balance_dom(page)
```

- [ ] **Step 3: Commit**

```bash
git add firevsports/mirror/workflows/polymarket.py
git commit -m "feat(poly): API-based check_login and sync_balance"
```

---

### Task 5: API-based `check_live_price`

**Files:**
- Modify: `firevsports/mirror/workflows/polymarket.py`

- [ ] **Step 1: Implement API-based `check_live_price`**

Uses `token_id` from `provider_meta` (stored in Task 2) to query the CLOB orderbook.

```python
    # ------------------------------------------------------------------
    # Live price
    # ------------------------------------------------------------------

    async def check_live_price(self, page: Page, bet) -> tuple[float | None, float | None]:
        """Read live odds from CLOB orderbook. Falls back to DOM if no API."""
        if not self.has_api:
            return await self._check_live_price_dom(page, bet)

        token_id = getattr(bet, "token_id", None)
        fair_odds = getattr(bet, "fair_odds", None)
        if not token_id or not fair_odds:
            return None, None

        try:
            book = self._client.get_order_book(token_id)
            asks = book.asks if hasattr(book, "asks") else book.get("asks", [])
            if not asks:
                return None, None

            # Best ask = cheapest price to buy shares
            best_ask = float(asks[0].price if hasattr(asks[0], "price") else asks[0]["price"])
            if best_ask <= 0 or best_ask >= 1:
                return None, None

            live_odds = round(1.0 / best_ask, 3)

            from ...analysis.value import compute_edge
            edge = compute_edge("polymarket", live_odds, fair_odds)
            return live_odds, edge
        except Exception as e:
            logger.warning(f"[polymarket] API check_live_price failed: {e}")
            return None, None

    async def _check_live_price_dom(self, page: Page, bet) -> tuple[float | None, float | None]:
        """DOM fallback: read prices from button text via mirror service."""
        from ...analysis.value import compute_edge

        fair_odds = getattr(bet, "fair_odds", None)
        if not fair_odds:
            return None, None

        try:
            from ...api.routes.mirror import _get_active_mirror
            mirror = _get_active_mirror()
            if mirror is None:
                return None, None

            original_outcome = getattr(bet, "original_outcome", getattr(bet, "outcome", ""))
            market_type = getattr(bet, "market", "1x2")
            btn_data = await mirror._read_btn_prices(page)
            matched = mirror._find_btn_for_market(
                btn_data, original_outcome, market_type,
                home_name=getattr(bet, "display_home", ""),
                away_name=getattr(bet, "display_away", ""),
            )
            if not matched or matched.get("price") is None:
                return None, None

            live_price = matched["price"]
            if live_price <= 0 or live_price >= 1:
                return None, None

            live_odds = 1.0 / live_price
            return round(live_odds, 3), compute_edge("polymarket", live_odds, fair_odds)
        except Exception as e:
            logger.warning(f"[{self.provider_id}] DOM check_live_price failed: {e}")
            return None, None
```

- [ ] **Step 2: Commit**

```bash
git add firevsports/mirror/workflows/polymarket.py
git commit -m "feat(poly): API-based check_live_price via CLOB orderbook"
```

---

### Task 6: API-based `navigate_to_event` + `prep_betslip`

**Files:**
- Modify: `firevsports/mirror/workflows/polymarket.py`

- [ ] **Step 1: Implement simplified `navigate_to_event`**

When API is available, navigation is just for visual context — no outcome clicking or stake filling.

```python
    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    async def navigate_to_event(self, page: Page, bet) -> bool:
        """Navigate to market page. API mode: visual only. DOM mode: full fill."""
        if not self.has_api:
            return await self._navigate_and_fill_dom(page, bet)

        # API mode: just open the page for visual context
        slug = getattr(bet, "event_slug", None) or getattr(bet, "market_slug", None)
        if not slug:
            logger.warning(f"[{self.provider_id}] No slug on bet")
            return False

        url = f"https://polymarket.com/event/{slug}"
        logger.info(f"[polymarket] navigate_to_event: {url}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_selector("button", timeout=10000)
            return True
        except Exception as e:
            logger.warning(f"[polymarket] navigate failed: {e}")
            return False
```

- [ ] **Step 2: Implement `prep_betslip` — sign order locally**

```python
    # ------------------------------------------------------------------
    # Bet preparation + placement
    # ------------------------------------------------------------------

    async def prep_betslip(self, page: Page, bet, stake: float) -> PlacementResult:
        """Phase 1: Build and sign order. API mode: SDK. DOM mode: click buttons."""
        if not self.has_api:
            # DOM fallback: navigate_and_fill already did the work
            return PlacementResult(status="prepped", bet_id=0, actual_stake=stake)

        token_id = getattr(bet, "token_id", None)
        if not token_id:
            logger.warning("[polymarket] No token_id — cannot prep via API")
            return PlacementResult(status="failed", bet_id=0, reason="no token_id in provider_meta")

        try:
            # Get current orderbook for best ask price
            book = self._client.get_order_book(token_id)
            asks = book.asks if hasattr(book, "asks") else book.get("asks", [])
            if not asks:
                return PlacementResult(status="failed", bet_id=0, reason="empty orderbook (no asks)")

            best_ask = float(asks[0].price if hasattr(asks[0], "price") else asks[0]["price"])
            if best_ask <= 0 or best_ask >= 1:
                return PlacementResult(status="failed", bet_id=0, reason=f"invalid ask price: {best_ask}")

            # shares = dollars / price_per_share
            size = round(stake / best_ask, 2)

            # Build and sign order (NOT submitted yet)
            order_args = _OrderArgs(
                price=best_ask,
                size=size,
                side=_BUY,
                token_id=token_id,
            )
            self._pending_order = self._client.create_order(order_args)
            self._pending_price = best_ask
            self._pending_size = size

            live_odds = round(1.0 / best_ask, 3)
            logger.info(
                f"[polymarket] Order signed: {size} shares @ {best_ask:.4f} "
                f"(${stake:.2f}, odds={live_odds})"
            )
            return PlacementResult(
                status="prepped",
                bet_id=0,
                actual_odds=live_odds,
                actual_stake=stake,
                reason=f"{size:.1f} shares @ {best_ask:.4f}",
            )
        except Exception as e:
            logger.error(f"[polymarket] prep_betslip failed: {e}", exc_info=True)
            return PlacementResult(status="failed", bet_id=0, reason=str(e))
```

- [ ] **Step 3: Commit**

```bash
git add firevsports/mirror/workflows/polymarket.py
git commit -m "feat(poly): API-based navigate_to_event and prep_betslip"
```

---

### Task 7: API-based `place_bet` (confirm and submit order)

**Files:**
- Modify: `firevsports/mirror/workflows/polymarket.py`

The play loop calls `place_bet()` when `autonomous_placement=True` and the user clicks Place. This submits the pre-signed order from `prep_betslip`.

- [ ] **Step 1: Implement `place_bet` — submit pre-signed order**

```python
    async def place_bet(self, page: Page, bet, stake: float) -> PlacementResult:
        """Submit the pre-signed order to CLOB. Called by play loop on user confirm."""
        if not self.has_api or not self._pending_order:
            # DOM fallback: record as manual placement
            logger.info(f"[polymarket] Manual placement: bet {getattr(bet, 'bet_id', '?')} stake=${stake}")
            return PlacementResult(status="placed", bet_id=0, actual_stake=stake)

        try:
            resp = self._client.post_order(self._pending_order, _OrderType.GTC)

            # Parse response — py-clob-client returns dict or object
            if isinstance(resp, dict):
                order_id = resp.get("orderID") or resp.get("id", "")
                success = resp.get("success", False)
                error_msg = resp.get("errorMsg", "")
            else:
                order_id = getattr(resp, "orderID", "") or getattr(resp, "id", "")
                success = getattr(resp, "success", False)
                error_msg = getattr(resp, "errorMsg", "")

            if success:
                actual_stake = round(self._pending_size * self._pending_price, 2)
                actual_odds = round(1.0 / self._pending_price, 3)
                logger.info(
                    f"[polymarket] Order placed: id={order_id} "
                    f"stake=${actual_stake} odds={actual_odds}"
                )
                return PlacementResult(
                    status="placed",
                    bet_id=order_id or 0,
                    actual_odds=actual_odds,
                    actual_stake=actual_stake,
                )
            else:
                logger.warning(f"[polymarket] Order rejected: {error_msg}")
                return PlacementResult(
                    status="failed",
                    bet_id=0,
                    reason=error_msg or "order rejected",
                )
        except Exception as e:
            logger.error(f"[polymarket] place_bet failed: {e}", exc_info=True)
            return PlacementResult(status="failed", bet_id=0, reason=str(e))
        finally:
            self._pending_order = None
            self._pending_price = 0.0
            self._pending_size = 0.0
```

- [ ] **Step 2: Commit**

```bash
git add firevsports/mirror/workflows/polymarket.py
git commit -m "feat(poly): API-based place_bet via CLOB SDK"
```

---

### Task 8: API-based `fetch_positions`

**Files:**
- Modify: `firevsports/mirror/workflows/polymarket.py`

Uses the public Data API to get current positions. This replaces DOM portfolio scraping for the settlement pre-check in `_settle_pending`.

- [ ] **Step 1: Implement `fetch_positions`**

```python
    # ------------------------------------------------------------------
    # Positions (Data API)
    # ------------------------------------------------------------------

    async def fetch_positions(self, page: Page) -> list[PositionEntry]:
        """Fetch open positions from Data API. Falls back to DOM if no API."""
        if not self.has_api:
            return []

        import requests as req

        address = os.getenv("POLY_FUNDER_ADDRESS", "")
        if not address:
            return []

        try:
            resp = req.get(
                "https://data-api.polymarket.com/positions",
                params={"user": address.lower()},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            positions = []
            for p in data:
                size = float(p.get("size", 0))
                if size <= 0:
                    continue
                avg_price = float(p.get("avgPrice", 0))
                title = p.get("title", "") or p.get("market", "")
                outcome = p.get("outcome", "")

                positions.append(PositionEntry(
                    provider_bet_id=p.get("asset", ""),
                    event_name=title[:80],
                    market="1x2",
                    outcome=outcome,
                    odds=round(1.0 / avg_price, 3) if avg_price > 0 else 2.0,
                    stake=round(size * avg_price, 2),
                    potential_payout=round(size, 2),
                ))

            logger.info(f"[polymarket] API: {len(positions)} open positions")
            return positions
        except Exception as e:
            logger.warning(f"[polymarket] fetch_positions API failed: {e}")
            return []
```

- [ ] **Step 2: Commit**

```bash
git add firevsports/mirror/workflows/polymarket.py
git commit -m "feat(poly): API-based fetch_positions via Data API"
```

---

### Task 9: API-based `sync_history`

**Files:**
- Modify: `firevsports/mirror/workflows/polymarket.py`

Uses Data API `/trades` endpoint for structured trade data instead of DOM scraping.

- [ ] **Step 1: Implement API-based `sync_history`**

```python
    # ------------------------------------------------------------------
    # History sync
    # ------------------------------------------------------------------

    async def sync_history(self, page: Page) -> list[HistoryEntry]:
        """Sync trade history: API if available, else DOM scrape + fuzzy match."""
        if not self.has_api:
            return await self._sync_history_dom(page)

        import requests as req
        from rapidfuzz import fuzz

        from ...db.models import Bet, Event, get_session
        from ...repositories.profile_repo import ProfileRepo
        from ...services.bet_service import BetService

        address = os.getenv("POLY_FUNDER_ADDRESS", "")
        if not address:
            return await self._sync_history_dom(page)

        # Fetch trades from Data API
        try:
            resp = req.get(
                "https://data-api.polymarket.com/trades",
                params={"user": address.lower()},
                timeout=15,
            )
            resp.raise_for_status()
            trades = resp.json()
        except Exception as e:
            logger.warning(f"[polymarket] API trades failed, falling back to DOM: {e}")
            return await self._sync_history_dom(page)

        if not trades:
            logger.info("[polymarket] API: no trades found")
            return []

        logger.info(f"[polymarket] API: {len(trades)} trades fetched")

        # Reconcile against DB (same logic as DOM version but with structured data)
        db = get_session()
        history_results: list[HistoryEntry] = []
        try:
            profile = ProfileRepo(db).get_active()
            if not profile:
                return []

            pending = (
                db.query(Bet, Event)
                .join(Event, Bet.event_id == Event.id, isouter=True)
                .filter(
                    Bet.profile_id == profile.id,
                    Bet.provider_id == "polymarket",
                    Bet.result == "pending",
                )
                .all()
            )

            bet_service = BetService(db)
            settled_ids: set[int] = set()

            for trade in trades:
                # Data API trade fields: market, outcome, side, price, size, status
                trade_market = trade.get("market", "") or trade.get("title", "")
                trade_status = trade.get("status", "")  # "MATCHED", "MINTED", etc.
                trade_outcome = trade.get("outcome", "")

                if not trade_market:
                    continue

                # Match trade against pending bets
                for bet, event in pending:
                    if bet.id in settled_ids:
                        continue
                    event_name = ""
                    if event:
                        h = event.display_home or event.home_team or ""
                        a = event.display_away or event.away_team or ""
                        event_name = f"{h} vs {a}" if h and a else h or a

                    score = fuzz.token_set_ratio(trade_market.lower(), event_name.lower())
                    if score < 60:
                        continue

                    # Check if this trade represents a settlement
                    # (Data API trade status indicates if resolved)
                    if trade_status in ("RESOLVED", "REDEEMED"):
                        payout = float(trade.get("payout", 0))
                        result_str = "won" if payout > 0 else "lost"

                        try:
                            bet_service.settle_bet(bet.id, result_str, round(payout, 2))
                            settled_ids.add(bet.id)
                            history_results.append(HistoryEntry(
                                provider_bet_id=str(bet.id),
                                event_name=trade_market[:80],
                                market=bet.market or "1x2",
                                outcome=bet.outcome or trade_outcome,
                                odds=bet.odds,
                                stake=bet.stake,
                                status=result_str,
                                payout=round(payout, 2),
                            ))
                            logger.info(
                                f"[polymarket] API settled bet #{bet.id} → {result_str} "
                                f"(payout=${payout:.2f})"
                            )
                        except Exception as e:
                            logger.warning(f"[polymarket] settle failed for bet #{bet.id}: {e}")
                        break

            db.commit()
            logger.info(f"[polymarket] API sync_history: {len(history_results)} settled")

        except Exception as e:
            db.rollback()
            logger.error(f"[polymarket] sync_history error: {e}", exc_info=True)
        finally:
            db.close()

        return history_results
```

- [ ] **Step 2: Move existing DOM-based `sync_history` to `_sync_history_dom`**

Rename the existing `sync_history` method body (the one with DOM scraping + fuzzy matching) to `_sync_history_dom`. This is the exact existing implementation from lines 63-232 of the current file.

- [ ] **Step 3: Commit**

```bash
git add firevsports/mirror/workflows/polymarket.py
git commit -m "feat(poly): API-based sync_history via Data API trades"
```

---

### Task 10: Keep settlement DOM methods + cleanup

**Files:**
- Modify: `firevsports/mirror/workflows/polymarket.py`

The following methods stay DOM-based (on-chain transactions must go through the UI):
- `scrape_history` — used by `_sync_history_dom` fallback
- `scrape_portfolio` — used by settlement and position import
- `redeem_all` — clicks Redeem buttons (on-chain tx)
- `claim_banner` — clicks Claim button (on-chain tx)
- `_dismiss_modal` — UI modal dismissal
- `scan_portfolio_settlements` — settlement preview
- `settle_all` — full settlement flow
- `_import_open_positions` — imports untracked positions
- `cleanup` — tab cleanup

- [ ] **Step 1: Verify all DOM settlement methods are preserved**

These methods remain unchanged from the current implementation. Ensure they are present in the file after all the API method additions. No code changes needed — just verify they weren't accidentally removed during the rewrite.

Run a quick sanity check:
```bash
grep -n "async def" firevsports/mirror/workflows/polymarket.py
```

Expected output should include all of:
- `check_login`
- `sync_balance`
- `sync_history`
- `navigate_to_event`
- `prep_betslip`
- `place_bet`
- `check_live_price`
- `fetch_positions`
- `_check_login_dom`
- `_sync_balance_dom`
- `_navigate_and_fill_dom`
- `_check_live_price_dom`
- `_sync_history_dom`
- `scrape_history`
- `scrape_portfolio`
- `redeem_all`
- `claim_banner`
- `_dismiss_modal`
- `scan_portfolio_settlements`
- `settle_all`
- `cleanup`

- [ ] **Step 2: Commit final state**

```bash
git add firevsports/mirror/workflows/polymarket.py
git commit -m "feat(poly): complete API-first workflow with DOM fallback"
```

---

### Task 11: Environment setup + smoke test

**Files:**
- Create: `firevsports/.env.local` (gitignored — local secrets)

- [ ] **Step 1: Verify `.env.local` is gitignored**

Check `.gitignore`:
```bash
grep -n "env.local" .gitignore
```

If not present, add `*.env.local` or `.env.local` to `.gitignore`.

- [ ] **Step 2: Create `.env.local` template**

The user needs to fill in their Polymarket wallet credentials:

```bash
# firevsports/.env.local
POLY_PRIVATE_KEY=0x_YOUR_PRIVATE_KEY_HERE
POLY_FUNDER_ADDRESS=0x_YOUR_WALLET_ADDRESS_HERE
POLY_SIGNATURE_TYPE=1
```

Signature types:
- `0` = EOA (MetaMask, hardware wallet)
- `1` = POLY_PROXY (email/Magic wallet — most common)
- `2` = GNOSIS_SAFE

- [ ] **Step 3: Load `.env.local` in server startup**

In `firevsports/launch.py` or `firevsports/server.py`, ensure `python-dotenv` loads `.env.local`:

Check if dotenv is already loaded:
```bash
grep -n "dotenv\|load_dotenv\|env.local" firevsports/server.py firevsports/launch.py
```

If not loading `.env.local`, add at the top of `firevsports/server.py`:
```python
from dotenv import load_dotenv
load_dotenv("firevsports/.env.local")  # Polymarket wallet credentials
```

- [ ] **Step 4: Smoke test — start FirevSports without keys**

Run `firevsports/firevsports.bat` (or `python firevsports/launch.py`).

Expected in logs:
```
[polymarket] No POLY_PRIVATE_KEY — DOM-only mode
```

This confirms graceful degradation — no crashes, DOM fallback active.

- [ ] **Step 5: Smoke test — start FirevSports with keys**

Set `POLY_PRIVATE_KEY` and `POLY_FUNDER_ADDRESS` in `.env.local`, restart.

Expected in logs:
```
[polymarket] CLOB client initialized (API mode)
```

- [ ] **Step 6: Commit**

```bash
git add .gitignore
git commit -m "feat(poly): env setup for Polymarket wallet credentials"
```

---

### Task 12: Deploy extraction change to server

**Files:** None (deployment only)

The `token_id` in `provider_meta` change (Task 2) needs to be deployed so new extraction runs store `token_id` for all Polymarket odds.

- [ ] **Step 1: Push to main**

```bash
git push origin main
```

- [ ] **Step 2: Deploy backend**

```bash
ssh root@148.251.40.251 "bash /opt/firev/scripts/server-deploy.sh rebuild backend"
```

Wait for health check to pass.

- [ ] **Step 3: Verify token_id is stored**

After the next Polymarket extraction cycle (~5 min), query the DB:

```sql
SELECT provider_meta FROM odds
WHERE provider_id = 'polymarket'
ORDER BY updated_at DESC LIMIT 5;
```

Expected: `provider_meta` contains `token_id` field alongside `event_slug`, `poly_home`, `poly_away`.

- [ ] **Step 4: Verify extraction health**

```bash
ssh root@148.251.40.251 "bash /opt/firev/scripts/server-deploy.sh logs backend 20"
```

Check for no errors in Polymarket extraction.
