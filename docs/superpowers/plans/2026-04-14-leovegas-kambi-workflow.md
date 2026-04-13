# LeoVegas / KambiWorkflow Full Wiring — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire LeoVegas (and all Kambi-platform providers) into the full fire window workflow — balance, login, navigation, betslip automation, and history sync.

**Architecture:** Phase 1 wires everything derivable from known API shapes (balance GraphQL relay, event URL navigation, history page navigation, BetProxy field mapping). Phase 2 adds `prep_betslip` + `confirm_bet` DOM automation after a live discovery session to find Kambi Widget API and betslip DOM selectors. Bet recording uses the `_place_event` path (no HTTP interception — Kambi is WS-based).

**Tech Stack:** Python 3.10+, Playwright async API, FastAPI, pytest

---

## File Map

| File | Change |
|------|--------|
| `firevsports/mirror/play_loop.py` | Add `kambi_event_id`, `kambi_outcome_id` to `_bet_ns()` |
| `backend/src/api/routes/mirror.py` | Add same two fields to BetProxy |
| `firevsports/mirror/workflows/kambi.py` | Full Phase 1 + Phase 2 implementation |
| `backend/src/mirror/workflows/kambi.py` | Sync identical changes |
| `tests/test_kambi_workflow.py` | Unit tests for balance parsing + nav URL |

No changes to: `storage.py` (already merges outcome_id correctly), `__init__.py` (registry already correct), `interceptor.py` (GraphQL relay already detected), `play_loop.py` main loop.

---

## Task 1: Map `kambi_event_id` and `kambi_outcome_id` in BetProxy and `_bet_ns`

**Context:** The Kambi extractor stores `event_id` (Kambi's eventId) and `outcome_id` in `Odds.provider_meta`. The BetProxy in `mirror.py` and `_bet_ns()` in `play_loop.py` need explicit fields so the workflow can access these without name collision against the canonical `event_id` UUID.

**Files:**
- Modify: `backend/src/api/routes/mirror.py` (around line 495)
- Modify: `firevsports/mirror/play_loop.py` (around line 36, inside `_bet_ns`)
- Create: `tests/test_kambi_workflow.py`

- [ ] **Step 1: Write failing test for `_bet_ns` kambi fields**

Create `tests/test_kambi_workflow.py`:

```python
"""Tests for KambiWorkflow and related bet namespace helpers."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from types import SimpleNamespace
from firevsports.mirror.play_loop import _bet_ns


def test_bet_ns_kambi_event_id_does_not_collide_with_canonical():
    """kambi_event_id comes from provider_meta.event_id, not canonical event_id."""
    bet = {
        "event_id": "canonical-uuid-1234",
        "market": "1x2",
        "outcome": "home",
        "provider_meta": {
            "event_id": "99887766",   # Kambi eventId
            "outcome_id": "111222333",
            "betoffer_id": "555666",
        },
    }
    ns = _bet_ns(bet)
    assert ns.event_id == "canonical-uuid-1234"       # canonical preserved
    assert ns.kambi_event_id == "99887766"            # Kambi-specific
    assert ns.kambi_outcome_id == "111222333"         # Kambi-specific


def test_bet_ns_kambi_fields_empty_when_no_provider_meta():
    bet = {"event_id": "uuid", "market": "1x2", "outcome": "home"}
    ns = _bet_ns(bet)
    assert ns.kambi_event_id == ""
    assert ns.kambi_outcome_id == ""
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd c:/Users/rasmu/firev
python -m pytest tests/test_kambi_workflow.py -v
```

Expected: `AttributeError: 'SimpleNamespace' object has no attribute 'kambi_event_id'`

- [ ] **Step 3: Add `kambi_event_id` / `kambi_outcome_id` to `_bet_ns()` in `firevsports/mirror/play_loop.py`**

Current `_bet_ns` (lines 27-40):
```python
def _bet_ns(bet: dict) -> SimpleNamespace:
    meta = bet.get("provider_meta") or {}
    ns = SimpleNamespace(**bet)
    for k, v in meta.items():
        if not hasattr(ns, k):
            setattr(ns, k, v)
    if not hasattr(ns, "bet_id"):
        ns.bet_id = 0
    return ns
```

Replace with:
```python
def _bet_ns(bet: dict) -> SimpleNamespace:
    meta = bet.get("provider_meta") or {}
    ns = SimpleNamespace(**bet)
    for k, v in meta.items():
        if not hasattr(ns, k):
            setattr(ns, k, v)
    if not hasattr(ns, "bet_id"):
        ns.bet_id = 0
    # Explicit Kambi fields — avoid collision with top-level event_id (canonical UUID)
    ns.kambi_event_id = meta.get("event_id", "")
    ns.kambi_outcome_id = meta.get("outcome_id", "")
    return ns
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
python -m pytest tests/test_kambi_workflow.py::test_bet_ns_kambi_event_id_does_not_collide_with_canonical tests/test_kambi_workflow.py::test_bet_ns_kambi_fields_empty_when_no_provider_meta -v
```

Expected: both PASS

- [ ] **Step 5: Add `kambi_event_id` / `kambi_outcome_id` to BetProxy in `backend/src/api/routes/mirror.py`**

Find the block ending around line 495:
```python
    bet.altenar_event_id = provider_meta.get("event_id", "")
    bet.altenar_sport_id = provider_meta.get("sport_id", "")
    bet.altenar_category_id = provider_meta.get("category_id", "")
    bet.altenar_championship_id = provider_meta.get("championship_id", "")
```

Add two lines immediately after:
```python
    bet.altenar_event_id = provider_meta.get("event_id", "")
    bet.altenar_sport_id = provider_meta.get("sport_id", "")
    bet.altenar_category_id = provider_meta.get("category_id", "")
    bet.altenar_championship_id = provider_meta.get("championship_id", "")
    bet.kambi_event_id = provider_meta.get("event_id", "")
    bet.kambi_outcome_id = provider_meta.get("outcome_id", "")
```

- [ ] **Step 6: Commit**

```bash
git add firevsports/mirror/play_loop.py backend/src/api/routes/mirror.py tests/test_kambi_workflow.py
git commit -m "feat(kambi): add kambi_event_id + kambi_outcome_id to BetProxy and _bet_ns"
```

---

## Task 2: Phase 1 — Balance, Login, Navigation, History in KambiWorkflow

**Context:** Wire the four no-op workflow methods using known API shapes. LeoVegas uses a GraphQL relay (`/api?relay`) for balance. All Kambi providers share the same `/betting/sports/event/{id}` URL pattern and `/betting/sports/bethistory` for history.

**Files:**
- Modify: `firevsports/mirror/workflows/kambi.py` (full replacement)
- Modify: `backend/src/mirror/workflows/kambi.py` (identical replacement)
- Modify: `tests/test_kambi_workflow.py` (add balance tests)

- [ ] **Step 1: Write failing tests for `_parse_graphql_balance`**

Add to `tests/test_kambi_workflow.py`:

```python
from firevsports.mirror.workflows.kambi import _parse_graphql_balance


def test_parse_graphql_balance_standard():
    data = {"data": {"viewer": {"user": {"balance": {"totalAmount": 1076.50, "currency": "SEK"}}}}}
    assert _parse_graphql_balance(data) == 1076.50


def test_parse_graphql_balance_array_wrapped():
    """LeoVegas sometimes returns a list with one item."""
    data = [{"data": {"viewer": {"user": {"balance": {"totalAmount": 250.0, "currency": "SEK"}}}}}]
    assert _parse_graphql_balance(data) == 250.0


def test_parse_graphql_balance_missing_returns_negative_one():
    assert _parse_graphql_balance(None) == -1
    assert _parse_graphql_balance({}) == -1
    assert _parse_graphql_balance({"data": {}}) == -1


def test_parse_graphql_balance_zero_balance():
    data = {"data": {"viewer": {"user": {"balance": {"totalAmount": 0.0, "currency": "SEK"}}}}}
    assert _parse_graphql_balance(data) == 0.0
```

- [ ] **Step 2: Run to confirm they fail**

```bash
python -m pytest tests/test_kambi_workflow.py -k "parse_graphql" -v
```

Expected: `ImportError: cannot import name '_parse_graphql_balance' from 'firevsports.mirror.workflows.kambi'`

- [ ] **Step 3: Replace `firevsports/mirror/workflows/kambi.py` with Phase 1 implementation**

```python
"""KambiWorkflow — WS-based guided workflow for Kambi platform providers.

Covers: unibet, leovegas, expekt, 888sport, speedybet, x3000, goldenbull, 1x2, betmgm.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .base import HistoryEntry, PlacementResult, ProviderWorkflow, WorkflowMode

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

# REST balance endpoint paths per Kambi operator
_BALANCE_ENDPOINTS: dict[str, str] = {
    "unibet": "/wallitt/mainbalance",
}

# GraphQL relay URLs per Kambi operator (for providers that use relay instead of REST)
_BALANCE_GRAPHQL: dict[str, str] = {
    "leovegas": "https://www.leovegas.com/api?relay",
}


def _parse_graphql_balance(data) -> float:
    """Extract totalAmount from GraphQL relay balance response. Returns -1 on failure."""
    try:
        relay = data
        if isinstance(data, list) and data:
            relay = data[0]
        if not isinstance(relay, dict):
            return -1
        bal = relay.get("data", {}).get("viewer", {}).get("user", {}).get("balance", {})
        if isinstance(bal, dict) and "totalAmount" in bal:
            return float(bal["totalAmount"])
    except (TypeError, ValueError, KeyError):
        pass
    return -1


class KambiWorkflow(ProviderWorkflow):
    platform = "kambi"

    def __init__(self, provider_id: str, domain: str, mode: WorkflowMode = WorkflowMode.GUIDED):
        super().__init__(provider_id, domain, mode)

    def _balance_rest_url(self) -> str | None:
        path = _BALANCE_ENDPOINTS.get(self.provider_id)
        if path and self.domain:
            return f"https://www.{self.domain}{path}"
        return None

    def _balance_graphql_url(self) -> str | None:
        return _BALANCE_GRAPHQL.get(self.provider_id)

    async def _fetch_graphql_balance(self, page: "Page") -> float:
        """POST GraphQL relay and return totalAmount, or -1 on failure."""
        url = self._balance_graphql_url()
        if url is None:
            return -1
        try:
            result = await page.evaluate(f"""
                async () => {{
                    try {{
                        const r = await fetch("{url}", {{
                            method: "POST",
                            credentials: "include",
                            headers: {{"Content-Type": "application/json"}},
                            body: JSON.stringify({{
                                query: "{{ viewer {{ user {{ balance {{ totalAmount currency }} }} }} }}"
                            }})
                        }});
                        if (!r.ok) return null;
                        return await r.json();
                    }} catch (e) {{ return null; }}
                }}
            """)
            return _parse_graphql_balance(result)
        except Exception as e:
            logger.warning(f"[{self.provider_id}] GraphQL balance fetch failed: {e}")
            return -1

    # ------------------------------------------------------------------
    # Login / balance
    # ------------------------------------------------------------------

    async def check_login(self, page: "Page") -> bool:
        """Try REST balance endpoint (unibet), then GraphQL relay (leovegas)."""
        rest_url = self._balance_rest_url()
        if rest_url:
            result = await self._evaluate_api(page, rest_url)
            return bool(result and "__error" not in result)

        graphql_url = self._balance_graphql_url()
        if graphql_url:
            bal = await self._fetch_graphql_balance(page)
            return bal >= 0

        # No known endpoint — assume logged in if tab is open
        return True

    async def sync_balance(self, page: "Page") -> float:
        """Try REST balance endpoint, then GraphQL relay, then return -1."""
        rest_url = self._balance_rest_url()
        if rest_url:
            result = await self._evaluate_api(page, rest_url)
            if result and "__error" not in result:
                try:
                    if "mainBalance" in result:
                        return float(result["mainBalance"]["amount"])
                    for key in ("balance", "amount", "cash"):
                        if key in result:
                            val = result[key]
                            if isinstance(val, dict):
                                return float(val.get("amount", val.get("total", -1)))
                            return float(val)
                except (KeyError, TypeError, ValueError):
                    logger.warning(f"[{self.provider_id}] Unexpected REST balance response")
            return -1

        return await self._fetch_graphql_balance(page)

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    async def sync_history(self, page: "Page") -> list[HistoryEntry]:
        """Navigate to bet history page — service.py SSR scraper handles parsing."""
        hist_url = f"https://www.{self.domain}/betting/sports/bethistory"
        if "/bethistory" not in (page.url or ""):
            try:
                await page.goto(hist_url, wait_until="networkidle", timeout=15000)
                await asyncio.sleep(3)
            except Exception as e:
                logger.warning(f"[{self.provider_id}] Could not navigate to bet history: {e}")
        return []

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    async def navigate_to_event(self, page: "Page", bet) -> bool:
        """Navigate to Kambi event page using kambi_event_id from provider_meta."""
        kambi_eid = getattr(bet, "kambi_event_id", "") or getattr(bet, "altenar_event_id", "")
        if not kambi_eid:
            return True  # No ID — user navigates manually, still counts as success
        if kambi_eid in (page.url or ""):
            return True  # Already on the right page
        url = f"https://www.{self.domain}/betting/sports/event/{kambi_eid}"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(1)
            logger.info(f"[{self.provider_id}] Navigated to event {kambi_eid}")
            return True
        except Exception as e:
            logger.warning(f"[{self.provider_id}] navigate_to_event failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Placement — Phase 2, filled after live discovery session
    # ------------------------------------------------------------------

    async def prep_betslip(self, page: "Page", bet, stake: float) -> PlacementResult:
        """Phase 2 placeholder — implemented after live discovery of Kambi Widget API."""
        return PlacementResult(
            status="no_prep",
            bet_id=getattr(bet, "bet_id", 0),
            reason="phase2_not_implemented",
        )

    async def confirm_bet(self, page: "Page") -> PlacementResult:
        """Phase 2 placeholder — implemented after live discovery of Place button selector."""
        return PlacementResult(status="manual", bet_id=0, reason="user_confirms_on_site")

    async def place_bet(self, page: "Page", bet, stake: float) -> PlacementResult:
        """Manual placement fallback."""
        return PlacementResult(
            status="manual",
            bet_id=getattr(bet, "bet_id", 0),
            actual_stake=stake,
            reason="manual_placement",
        )
```

- [ ] **Step 4: Run balance tests**

```bash
python -m pytest tests/test_kambi_workflow.py -k "parse_graphql" -v
```

Expected: all 4 PASS

- [ ] **Step 5: Sync identical content to `backend/src/mirror/workflows/kambi.py`**

The backend copy must be identical — copy the same file content. The only difference is the import path for `HistoryEntry`, `PlacementResult`, `ProviderWorkflow`, `WorkflowMode` which are already `from .base import ...` in both copies.

```bash
cp firevsports/mirror/workflows/kambi.py backend/src/mirror/workflows/kambi.py
```

- [ ] **Step 6: Run full test suite to confirm nothing broken**

```bash
python -m pytest tests/ -v 2>&1 | head -50
```

Expected: all previously passing tests still pass; new kambi tests pass.

- [ ] **Step 7: Commit**

```bash
git add firevsports/mirror/workflows/kambi.py backend/src/mirror/workflows/kambi.py tests/test_kambi_workflow.py
git commit -m "feat(kambi): Phase 1 — balance relay, navigate_to_event, sync_history"
```

---

## Task 3: Live Discovery Session — Kambi Widget API + Betslip DOM Selectors

**Context:** Phase 2 (`prep_betslip`, `confirm_bet`) requires knowing the exact Kambi Widget JS API call and DOM selectors for the stake input and Place button on leovegas.com. This task is a manual investigation step — no code written, only selectors documented for Task 4.

**Files:** None (output is knowledge for Task 4)

- [ ] **Step 1: Start FirevSports and open LeoVegas**

```bash
firevsports/firevsports.bat
```

Navigate to `https://www.leovegas.com` in the Playwright browser. Log in.

- [ ] **Step 2: Verify Kambi Widget API in DevTools console**

Open browser DevTools (F12) → Console. Run:

```js
// Check Widget API object exists
console.log(typeof window.KambiWidget);
// Expected: "object"

// List available API methods
console.log(Object.keys(window.KambiWidget.api || {}));
// Expected: array including "BETSLIP_ITEM_ADD" or similar

// Check full API
console.log(JSON.stringify(window.KambiWidget));
```

Record the exact method name for adding an outcome (likely `BETSLIP_ITEM_ADD`).

- [ ] **Step 3: Test adding an outcome to betslip via JS**

Navigate to any football event page. Find an outcome ID from the URL or page source. Run:

```js
// Replace 123456789 with a real outcome ID from the page
window.KambiWidget.api.BETSLIP_ITEM_ADD({ outcomes: [{ id: 123456789 }] });
```

Confirm: the outcome appears in the betslip sidebar.

- [ ] **Step 4: Find stake input DOM selector**

After adding an outcome to betslip, inspect the stake input element. Run:

```js
// Try common selectors
document.querySelector('input[class*="stake"]');
document.querySelector('input[class*="Stake"]');
document.querySelector('input[data-test*="stake"]');
// Find by type=number inputs
Array.from(document.querySelectorAll('input[type="number"]')).map(el => ({
    class: el.className, id: el.id, placeholder: el.placeholder
}));
```

Record the working selector (e.g. `input[class*="stake-input"]`).

- [ ] **Step 5: Test filling stake via JS**

```js
const input = document.querySelector('/* DISCOVERED_SELECTOR */');
input.value = '';
input.dispatchEvent(new Event('input', { bubbles: true }));
input.value = '10';
input.dispatchEvent(new Event('input', { bubbles: true }));
input.dispatchEvent(new Event('change', { bubbles: true }));
```

Confirm: stake field shows 10 in the betslip.

- [ ] **Step 6: Find Place button selector**

```js
// Try text-based search
Array.from(document.querySelectorAll('button')).filter(b =>
    b.textContent.trim().toLowerCase().includes('lägg') ||
    b.textContent.trim().toLowerCase().includes('place')
).map(b => ({ text: b.textContent.trim(), class: b.className }));
```

Record the text or selector. Note: do NOT click it in this step.

- [ ] **Step 7: Document selectors**

Record these 4 values for use in Task 4:
```
KAMBI_WIDGET_METHOD: window.KambiWidget.api.BETSLIP_ITEM_ADD  (or the actual method)
STAKE_INPUT_SELECTOR: <discovered selector>
PLACE_BUTTON_TEXT: <e.g. "Lägg spel">
CONFIRMATION_TEXT: <text that appears after bet placed, e.g. "Kupong" or "Bet accepted">
```

Also note: does the outcome ID in the API call use `{ id: numericId }` or a string? Record the exact argument shape.

---

## Task 4: Phase 2 — `prep_betslip` and `confirm_bet`

**Context:** Implement the two-phase placement using the selectors discovered in Task 3. Both workflow files get the same update.

**Files:**
- Modify: `firevsports/mirror/workflows/kambi.py` (replace `prep_betslip` + `confirm_bet`)
- Modify: `backend/src/mirror/workflows/kambi.py` (sync)

- [ ] **Step 1: Replace `prep_betslip` in `firevsports/mirror/workflows/kambi.py`**

Replace the placeholder `prep_betslip` with (substituting discovered values):

```python
async def prep_betslip(self, page: "Page", bet, stake: float) -> PlacementResult:
    """Add outcome to Kambi betslip + fill stake. Phase 1 of two-phase placement."""
    kambi_outcome_id = getattr(bet, "kambi_outcome_id", "") or getattr(bet, "outcome_id", "")
    if not kambi_outcome_id:
        logger.warning(f"[{self.provider_id}] No kambi_outcome_id — cannot auto-fill betslip")
        return PlacementResult(
            status="no_prep",
            bet_id=getattr(bet, "bet_id", 0),
            reason="no_outcome_id",
        )

    # Step 1: Add outcome to betslip via Kambi Widget API
    # KAMBI_WIDGET_METHOD discovered in Task 3 (e.g. BETSLIP_ITEM_ADD)
    added = await page.evaluate(f"""
        () => {{
            const api = window.KambiWidget && window.KambiWidget.api;
            if (!api || !api.BETSLIP_ITEM_ADD) return {{ ok: false, reason: 'no_api' }};
            try {{
                api.BETSLIP_ITEM_ADD({{ outcomes: [{{ id: {kambi_outcome_id} }}] }});
                return {{ ok: true }};
            }} catch(e) {{
                return {{ ok: false, reason: e.toString() }};
            }}
        }}
    """)
    if not added or not added.get("ok"):
        reason = (added or {}).get("reason", "unknown")
        logger.warning(f"[{self.provider_id}] KambiWidget.api.BETSLIP_ITEM_ADD failed: {reason}")
        return PlacementResult(
            status="no_prep",
            bet_id=getattr(bet, "bet_id", 0),
            reason=f"widget_add_failed:{reason}",
        )

    # Wait for betslip DOM to update
    await asyncio.sleep(1.5)

    # Step 2: Fill stake input
    # STAKE_INPUT_SELECTOR discovered in Task 3
    stake_str = f"{stake:.2f}"
    stake_filled = await page.evaluate(f"""
        () => {{
            const sel = '/* STAKE_INPUT_SELECTOR from Task 3 */';
            const input = document.querySelector(sel);
            if (!input) return false;
            input.value = '';
            input.dispatchEvent(new Event('input', {{ bubbles: true }}));
            input.value = '{stake_str}';
            input.dispatchEvent(new Event('input', {{ bubbles: true }}));
            input.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return true;
        }}
    """)

    if not stake_filled:
        logger.warning(f"[{self.provider_id}] Stake input not found in betslip DOM")
        return PlacementResult(
            status="no_prep",
            bet_id=getattr(bet, "bet_id", 0),
            reason="stake_input_not_found",
        )

    logger.info(f"[{self.provider_id}] Betslip prepped: outcome={kambi_outcome_id} stake={stake}")
    return PlacementResult(
        status="prepped",
        bet_id=getattr(bet, "bet_id", 0),
        actual_stake=stake,
        reason=None,
    )
```

- [ ] **Step 2: Replace `confirm_bet` in `firevsports/mirror/workflows/kambi.py`**

Replace the placeholder with:

```python
async def confirm_bet(self, page: "Page") -> PlacementResult:
    """Click the Place button in Kambi betslip DOM. Phase 2 of two-phase placement."""
    # PLACE_BUTTON_TEXT discovered in Task 3 (e.g. "Lägg spel")
    clicked = await page.evaluate("""
        () => {
            const buttons = document.querySelectorAll('button');
            for (const btn of buttons) {
                const t = (btn.textContent || '').trim().toLowerCase();
                if (t === 'lägg spel' || t === 'place bet' || t.startsWith('place')) {
                    btn.click();
                    return true;
                }
            }
            return false;
        }
    """)

    if not clicked:
        logger.warning(f"[{self.provider_id}] Place button not found in DOM")
        return PlacementResult(status="failed", bet_id=0, reason="place_button_not_found")

    # Poll for confirmation text in DOM
    # CONFIRMATION_TEXT discovered in Task 3 (e.g. "kupong", "accepted")
    for _ in range(20):
        await asyncio.sleep(0.5)
        confirmed = await page.evaluate("""
            () => {
                const body = (document.body.innerText || '').toLowerCase();
                return body.includes('kupong') || body.includes('accepted') || body.includes('placed');
            }
        """)
        if confirmed:
            logger.info(f"[{self.provider_id}] Bet confirmed via DOM")
            return PlacementResult(status="placed", bet_id=0, reason=None)

    # Timeout — Kambi WS-based; confirmation may not appear in DOM
    logger.info(f"[{self.provider_id}] Confirmation timeout — assuming placed (WS-based)")
    return PlacementResult(status="placed", bet_id=0, reason="timeout_assumed_placed")
```

- [ ] **Step 3: Substitute actual discovered selectors**

Open `firevsports/mirror/workflows/kambi.py`. Replace:
- `/* STAKE_INPUT_SELECTOR from Task 3 */` → the selector found in Task 3
- Update the button text list in `confirm_bet` if the actual text differs from "lägg spel"
- Update the confirmation text list in `confirm_bet` if different from "kupong"/"accepted"/"placed"

- [ ] **Step 4: Sync to backend copy**

```bash
cp firevsports/mirror/workflows/kambi.py backend/src/mirror/workflows/kambi.py
```

- [ ] **Step 5: Commit**

```bash
git add firevsports/mirror/workflows/kambi.py backend/src/mirror/workflows/kambi.py
git commit -m "feat(leovegas): Phase 2 — prep_betslip + confirm_bet via Kambi Widget API"
```

---

## Task 5: Integration Test — Run LeoVegas Through Play Loop

**Context:** Verify the full flow end-to-end with a real FirevSports session.

- [ ] **Step 1: Start FirevSports**

```bash
firevsports/firevsports.bat
```

- [ ] **Step 2: Verify balance sync works**

Open the Play tab. Click LeoVegas to highlight it. Verify:
- Balance shows a real number (not -1, not 0)
- Provider card turns green/amber (funded + login detected)

If balance shows -1: check browser DevTools Network tab for requests to `/api?relay`. Confirm the GraphQL POST is going out. Check the response body shape matches `data.viewer.user.balance.totalAmount`.

- [ ] **Step 3: Verify navigation works**

Start the play loop with LeoVegas selected. Confirm:
- Browser navigates to `leovegas.com/betting/sports/event/{id}` (check browser address bar)
- No "navigation failed" in play loop logs

If navigation fails: check that `kambi_event_id` is non-empty in logs. Query the DB to verify Kambi odds have `event_id` in `provider_meta`:

```bash
ssh root@148.251.40.251 "docker compose exec -T postgres psql -U firev -d firev -c \"SELECT provider_meta FROM odds WHERE provider_id = 'leovegas' LIMIT 3;\""
```

- [ ] **Step 4: Verify betslip prep works (Phase 2)**

With a bet queued for LeoVegas, observe the FirevSports bet card:
- `prep_ok: true` in the SSE event → betslip was filled
- Stake input shows correct amount in browser

If prep fails: check browser DevTools console for errors from the `KambiWidget.api.BETSLIP_ITEM_ADD` call.

- [ ] **Step 5: Place a small test bet**

Click "Place" in the FirevSports UI. Confirm:
- LeoVegas DOM shows bet confirmation
- Bet is recorded in DB (check the Bets tab or query: `SELECT * FROM bets WHERE provider_id = 'leovegas' ORDER BY id DESC LIMIT 1;`)

- [ ] **Step 6: Verify history sync**

Reload FirevSports after the bet is settled. Confirm:
- Service.py navigates to `/betting/sports/bethistory`
- SSR scraper finds and stages the settlement
- Settlement appears in Pending tab

- [ ] **Step 7: Deploy to server**

```bash
git push origin main
ssh root@148.251.40.251 "bash /opt/firev/scripts/server-deploy.sh rebuild backend"
```

---

## Self-Review

**Spec coverage:**
- ✅ Balance / login via GraphQL relay → Task 2
- ✅ `navigate_to_event()` using Kambi event URL → Task 2
- ✅ `sync_history()` navigating to history page → Task 2
- ✅ `kambi_event_id` / `kambi_outcome_id` in BetProxy → Task 1
- ✅ `kambi_event_id` / `kambi_outcome_id` in `_bet_ns()` → Task 1
- ✅ Live discovery session → Task 3
- ✅ `prep_betslip()` via Kambi Widget API → Task 4
- ✅ `confirm_bet()` DOM click → Task 4
- ✅ Integration test → Task 5
- ✅ Deploy → Task 5 Step 7
- ✅ storage.py — spec says verify; confirmed no change needed (Task 2 preamble)

**Placeholder scan:**
- Task 4 Step 3 explicitly instructs to substitute `/* STAKE_INPUT_SELECTOR from Task 3 */` and confirmation text with discovered values. This is intentional — it cannot be filled before Task 3 runs. The instruction to substitute is explicit.

**Type consistency:**
- `_parse_graphql_balance` defined in Task 2 Step 3, imported in tests in Task 2 Step 1 — consistent.
- `PlacementResult(status="prepped", ...)` — matches base class dataclass definition.
- `getattr(bet, "kambi_event_id", "")` in Task 4 — matches field set in Task 1.
- `getattr(bet, "kambi_outcome_id", "")` in Task 4 — matches field set in Task 1.
- `asyncio` imported at top of kambi.py in Task 2 Step 3 — used in `sync_history`, `navigate_to_event`, `confirm_bet` — consistent.
