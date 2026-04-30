# Pinnacle Strategy Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate Pinnacle from the dedicated `PinnacleMirrorWorkflow` class to the `GenericWorkflow + intel JSON + strategies/pinnacle.py` pattern (B2: mirror placement, API settlement) — same registration pattern as Polymarket.

**Architecture:** `arnold/mirror/workflows/__init__.py:get_workflow("pinnacle")` will fall through the explicit-platform check, hit the intel-JSON path, and return a `GenericWorkflow` instance whose `strategy` field is loaded from `arnold/mirror/workflows/strategies/pinnacle.py`. The strategy file gets a new `_prep_betslip` (DOM-click + stake fill ported from the dedicated class), four new module-level hooks (`_read_slip_odds`, `_update_slip_stake`, `parse_placement_response`, `parse_placement_status`), and a corrected `Strategy(...)` export wiring `scan` + `settle_all` (already defined but currently unwired). `GenericWorkflow` and the `Strategy` dataclass each grow four new fields/methods so these hooks reach the call sites in `arb_runner.py`, `slip_odds_stream.py`, and `provider_runner.py`.

**Tech Stack:** Python 3.10+, Playwright async, pytest + pytest-asyncio, dataclasses.

**Spec:** [docs/superpowers/specs/2026-04-30-pinnacle-strategy-migration-design.md](../specs/2026-04-30-pinnacle-strategy-migration-design.md) (commit `b3a9d2b1`).

---

## File map

| File | Action | Why |
|---|---|---|
| `arnold/mirror/workflows/strategies/__init__.py` | Modify | Add 4 fields to `Strategy` dataclass |
| `arnold/mirror/workflows/generic.py` | Modify | Add 4 delegating methods |
| `arnold/mirror/workflows/strategies/pinnacle.py` | Modify | Add prep_betslip + slip helpers + parsers; fix Strategy export; drop _place_bet |
| `data/mirror_intel/pinnacle.json` | Modify | Drop `autonomous_placement: true` |
| `arnold/mirror/workflows/__init__.py` | Modify | Drop pinnacle from explicit platform map |
| `arnold/mirror/workflows/pinnacle.py` | Delete | Dedicated class superseded |
| `arnold/tests/workflows/test_pinnacle_slip.py` | Delete | All assertions target dedicated class |
| `arnold/tests/workflows/test_pinnacle_strategy.py` | Create | Re-implements 9 placement-parser tests |

---

## Task 1: Extend `Strategy` dataclass with 4 new fields

**Files:**
- Modify: `arnold/mirror/workflows/strategies/__init__.py:18-40`

- [ ] **Step 1: Edit the `Strategy` dataclass** — add 4 new optional fields after the existing `redeem_all` line:

```python
@dataclass
class Strategy:
    """Optional per-provider method overrides.

    Each field is an async callable(page, intel) -> result, or None to use generic.
    """

    check_login: Callable | None = None
    sync_balance: Callable | None = None
    sync_history: Callable | None = None
    navigate_to_event: Callable | None = None
    prep_betslip: Callable | None = None
    place_bet: Callable | None = None
    check_live_price: Callable | None = None
    # Optional settlement extensions (Polymarket uses these for claim + redeem on-chain).
    # Provider runner delegates to the strategy when all three are present.
    scrape_portfolio: Callable | None = None  # (page, intel) -> list[dict] open positions
    claim_banner: Callable | None = None  # (page, intel) -> {claimed, amount}
    redeem_all: Callable | None = None  # (page, intel) -> {redeemed, skipped_open, errors, total}
    # Optional account-level methods referenced by GenericWorkflow.scan / .settle_all.
    # Without these fields the dataclass would AttributeError on access.
    scan: Callable | None = None  # (page, intel) -> dict read-only account preview
    settle_all: Callable | None = None  # (page, intel) -> dict full settlement run
    # Slip + placement-XHR hooks consumed by ArbRunner / SlipOddsStream / provider_runner
    # placement interceptor. Async for read/write of slip state, sync for parsing
    # placement response bodies (no I/O).
    read_slip_odds: Callable | None = None  # async (page, intel) -> float | None
    update_slip_stake: Callable | None = None  # async (page, stake, intel) -> bool
    parse_placement_response: Callable | None = None  # (body) -> str | None
    parse_placement_status: Callable | None = None  # (body) -> dict
```

- [ ] **Step 2: Verify import path still works**

Run: `python -c "from arnold.mirror.workflows.strategies import Strategy, load_strategy; s = Strategy(); print(s.read_slip_odds, s.update_slip_stake, s.parse_placement_response, s.parse_placement_status)"`
Expected output: `None None None None` (all four default to None).

- [ ] **Step 3: Commit**

```bash
git add arnold/mirror/workflows/strategies/__init__.py
git commit -m "feat(workflows/strategy): add slip + placement-XHR hook fields

Pinnacle migration off PinnacleMirrorWorkflow needs the strategy dataclass
to carry read_slip_odds / update_slip_stake / parse_placement_response /
parse_placement_status so GenericWorkflow can route them. Polymarket
strategy leaves them None — no behavior change there."
```

---

## Task 2: Add 4 delegating methods to `GenericWorkflow`

**Files:**
- Modify: `arnold/mirror/workflows/generic.py:407` (insert after `check_live_price`)

- [ ] **Step 1: Add a typing import for the `Page` reference**

Look at the top of `generic.py`. The `Page` type is not currently imported (it's only used inside `if TYPE_CHECKING:` blocks elsewhere). Check whether `generic.py` already has the right import. Run:

```bash
grep -n "TYPE_CHECKING\|from playwright" arnold/mirror/workflows/generic.py | head -5
```

If `Page` is not imported, the existing methods (`check_login`, `sync_balance`, etc.) work because they don't annotate the `page` argument. We'll match that style — no annotation on `page`.

- [ ] **Step 2: Add four delegating methods**

Insert after the existing `check_live_price` method (around line 407, before the `# Scan` separator comment). Use Edit to find:

```python
    async def check_live_price(self, page: Page, bet) -> tuple[float | None, float | None]:
        if self.strategy and self.strategy.check_live_price:
            result = await self.strategy.check_live_price(page, bet, self.intel)
            # Strategies may return (odds, edge) tuple or bare edge float — normalise
            if isinstance(result, tuple):
                return result
            return None, result
        return None, None

    # ------------------------------------------------------------------
    # Scan — read-only account state preview
    # ------------------------------------------------------------------
```

And replace with:

```python
    async def check_live_price(self, page: Page, bet) -> tuple[float | None, float | None]:
        if self.strategy and self.strategy.check_live_price:
            result = await self.strategy.check_live_price(page, bet, self.intel)
            # Strategies may return (odds, edge) tuple or bare edge float — normalise
            if isinstance(result, tuple):
                return result
            return None, result
        return None, None

    # ------------------------------------------------------------------
    # Slip read/write — consumed by ArbRunner + SlipOddsStream
    # ------------------------------------------------------------------

    async def read_slip_odds(self, page) -> float | None:
        if self.strategy and self.strategy.read_slip_odds:
            return await self.strategy.read_slip_odds(page, self.intel)
        return await super().read_slip_odds(page)

    async def update_slip_stake(self, page, stake: float) -> bool:
        if self.strategy and self.strategy.update_slip_stake:
            return await self.strategy.update_slip_stake(page, stake, self.intel)
        return await super().update_slip_stake(page, stake)

    # ------------------------------------------------------------------
    # Placement response parsing — called by browser placement interceptor
    # ------------------------------------------------------------------

    def parse_placement_response(self, body: dict) -> str | None:
        if self.strategy and self.strategy.parse_placement_response:
            return self.strategy.parse_placement_response(body)
        return super().parse_placement_response(body)

    def parse_placement_status(self, body: dict) -> dict:
        if self.strategy and self.strategy.parse_placement_status:
            return self.strategy.parse_placement_status(body)
        return super().parse_placement_status(body)

    # ------------------------------------------------------------------
    # Scan — read-only account state preview
    # ------------------------------------------------------------------
```

Note `parse_placement_*` were `@staticmethod` on `ProviderWorkflow`. They become **instance methods** on `GenericWorkflow` here. The base class still has them as static, so `super().parse_placement_response(body)` works (you can call a static method through `super()`). Existing dedicated subclasses (Altenar, Kambi) that call `Class.parse_placement_status(body)` directly are unaffected.

- [ ] **Step 3: Smoke-check the wiring on Polymarket (which doesn't override these)**

```bash
python -c "
from arnold.mirror.workflows import get_workflow
w = get_workflow('polymarket')
print(type(w).__name__)
print('parse_status default:', w.parse_placement_status({}))
print('parse_response default:', w.parse_placement_response({}))
"
```

Expected:
```
GenericWorkflow
parse_status default: {'success': True, 'error': None, 'max_stake': None}
parse_response default: None
```

If you see `AttributeError`, the base class's `parse_placement_*` are not reachable via `super()` — check the inheritance and re-read `arnold/mirror/workflows/base.py:174-189`.

- [ ] **Step 4: Commit**

```bash
git add arnold/mirror/workflows/generic.py
git commit -m "feat(workflows/generic): delegate slip + placement parsers to strategy

Routes read_slip_odds / update_slip_stake / parse_placement_response /
parse_placement_status through self.strategy when set, falls back to base
class. Polymarket (no overrides) keeps base defaults — no behavior change."
```

---

## Task 3: Port placement-parser tests (TDD red phase)

**Files:**
- Create: `arnold/tests/workflows/test_pinnacle_strategy.py`

This task is the red phase: write the tests against the strategy module before the functions exist there. All 9 tests must fail.

- [ ] **Step 1: Create the new test file**

Write `arnold/tests/workflows/test_pinnacle_strategy.py`:

```python
"""Pinnacle strategy — placement-XHR response parser tests.

Ported from test_pinnacle_slip.py. The dedicated PinnacleMirrorWorkflow class
has been replaced by strategies/pinnacle.py + GenericWorkflow routing.
"""

from __future__ import annotations

from arnold.mirror.workflows.strategies.pinnacle import (
    parse_placement_response,
    parse_placement_status,
)


# ---- parse_placement_status ----


def test_parse_placement_status_success_via_wagerNumber():
    body = {"wagerNumber": 12345678}
    result = parse_placement_status(body)
    assert result["success"] is True
    assert result["error"] is None


def test_parse_placement_status_success_via_betId():
    body = {"betId": "abc123"}
    result = parse_placement_status(body)
    assert result["success"] is True


def test_parse_placement_status_failure():
    body = {"error": "STAKE_LIMIT_EXCEEDED"}
    result = parse_placement_status(body)
    assert result["success"] is False
    assert result["error"] == "STAKE_LIMIT_EXCEEDED"


def test_parse_placement_status_failure_unknown():
    body = {}
    result = parse_placement_status(body)
    assert result["success"] is False
    assert result["error"] == "unknown"


def test_parse_placement_status_failure_extracts_max_stake():
    body = {"error": "STAKE_LIMIT_EXCEEDED", "maxStake": 50.0}
    result = parse_placement_status(body)
    assert result["success"] is False
    assert result["max_stake"] == 50.0


def test_parse_placement_status_failure_extracts_max_stake_from_limits():
    body = {
        "error": "STAKE_LIMIT_EXCEEDED",
        "limits": [
            {"amount": 3.71, "type": "minRiskStake"},
            {"amount": 100.0, "type": "maxRiskStake"},
        ],
    }
    result = parse_placement_status(body)
    assert result["success"] is False
    assert result["max_stake"] == 100.0


# ---- parse_placement_response ----


def test_parse_placement_response_extracts_wagerNumber():
    assert parse_placement_response({"wagerNumber": 12345}) == "12345"


def test_parse_placement_response_extracts_betId():
    assert parse_placement_response({"betId": "abc"}) == "abc"


def test_parse_placement_response_returns_none_on_missing():
    assert parse_placement_response({}) is None
```

- [ ] **Step 2: Run the tests, expect ImportError on collection**

Run: `pytest arnold/tests/workflows/test_pinnacle_strategy.py -v`
Expected: collection ERROR — `ImportError: cannot import name 'parse_placement_response' from 'arnold.mirror.workflows.strategies.pinnacle'`

This confirms the parsers don't exist yet at module level. Task 4 adds them.

- [ ] **Step 3: Stage but don't commit yet**

```bash
git add arnold/tests/workflows/test_pinnacle_strategy.py
```

We commit together with the implementation in Task 4 to keep TDD red→green visible in one commit boundary.

---

## Task 4: Add placement-XHR parsers to the strategy module (green phase)

**Files:**
- Modify: `arnold/mirror/workflows/strategies/pinnacle.py` (insert before the `strategy = Strategy(...)` block)

- [ ] **Step 1: Add parser functions at module level**

Edit `arnold/mirror/workflows/strategies/pinnacle.py`. Find the existing closing block:

```python
strategy = Strategy(
    check_login=_check_login,
    sync_balance=_sync_balance,
    sync_history=_sync_history,
    place_bet=_place_bet,
    check_live_price=_check_live_price,
    navigate_to_event=_navigate_to_event,
)
```

Insert the parsers ABOVE that block (after `_navigate_to_event`):

```python
# ------------------------------------------------------------------
# Placement-XHR parsers — called by browser placement interceptor when
# the user clicks CONFIRM on pinnacle.se and the placement XHR returns.
# Pure functions — no Page, no intel.
# ------------------------------------------------------------------


def parse_placement_status(body: dict) -> dict:
    """Infer success/failure from Pinnacle placement XHR response.

    Returns dict with success: bool, error: str | None, max_stake: float | None.
    Success path: response carries wagerNumber or betId.
    Failure path: extract max_stake from top-level keys or limits[].type=='maxRiskStake'.
    """
    if body.get("wagerNumber") or body.get("betId"):
        return {"success": True, "error": None, "max_stake": None}
    max_stake = body.get("maxStake") or body.get("max_stake") or body.get("maximumStake")
    if max_stake is None:
        for limit in body.get("limits") or []:
            if limit.get("type") == "maxRiskStake":
                max_stake = limit.get("amount")
                break
    return {
        "success": False,
        "error": body.get("error") or body.get("errorCode") or "unknown",
        "max_stake": max_stake,
    }


def parse_placement_response(body: dict) -> str | None:
    """Extract provider_bet_id from Pinnacle placement response.

    Tries wagerNumber first (inferred primary), then betId.
    """
    bid = body.get("wagerNumber") or body.get("betId")
    return str(bid) if bid else None
```

- [ ] **Step 2: Run the tests, expect all green**

Run: `pytest arnold/tests/workflows/test_pinnacle_strategy.py -v`
Expected: 9 passed.

- [ ] **Step 3: Commit (test + implementation together)**

```bash
git add arnold/mirror/workflows/strategies/pinnacle.py arnold/tests/workflows/test_pinnacle_strategy.py
git commit -m "feat(strategy/pinnacle): port placement-XHR parsers + tests

Module-level parse_placement_status / parse_placement_response replace
the @staticmethod versions on the dedicated class. 9 tests ported from
test_pinnacle_slip.py."
```

---

## Task 5: Port `_read_slip_odds` + `_update_slip_stake` from dedicated class

**Files:**
- Modify: `arnold/mirror/workflows/strategies/pinnacle.py` (insert after the parsers added in Task 4)

- [ ] **Step 1: Add slip helpers**

Edit `arnold/mirror/workflows/strategies/pinnacle.py`. Insert AFTER the `parse_placement_response` function and BEFORE the `strategy = Strategy(...)` block:

```python
# ------------------------------------------------------------------
# Slip helpers — read odds + update stake without re-navigating.
# Called by SlipOddsStream and ArbRunner.
# ------------------------------------------------------------------


async def _read_slip_odds(page: Page, intel: dict | None) -> float | None:
    """Read American price from localStorage['Main:Betslip'].Selections[0],
    convert to decimal. Returns None when slip empty or storage missing.

    Polled ~1Hz by SlipOddsStream while a counter slip is loaded — must be
    fast and exception-safe.
    """
    try:
        price = await page.evaluate(
            r"""() => {
                const raw = localStorage.getItem("Main:Betslip");
                if (!raw) return null;
                try {
                    const data = JSON.parse(raw);
                    const sels = data?.Selections ?? [];
                    if (sels.length === 0) return null;
                    return sels[0].price;
                } catch { return null; }
            }"""
        )
        if price is None:
            return None
        return _american_to_decimal(float(price))
    except Exception:
        return None


async def _update_slip_stake(page: Page, stake: float, intel: dict | None) -> bool:
    """Write stake to Pinnacle's React-controlled input via the hidden-setter
    pattern. Used by ArbRunner to keep counter slips in sync with anchor
    placements. Returns True iff the React onChange handler fired.
    """
    try:
        result = await page.evaluate(
            """((stake) => {
                const el = document.querySelector('input[placeholder="Stake"]');
                if (!el) return false;
                const setter = Object.getOwnPropertyDescriptor(
                    HTMLInputElement.prototype, 'value'
                ).set;
                setter.call(el, String(stake));
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
            })""",
            stake,
        )
        return bool(result)
    except Exception:
        return False
```

Note: `_american_to_decimal` already exists in `strategies/pinnacle.py` (line ~105) — confirm by `grep -n "_american_to_decimal" arnold/mirror/workflows/strategies/pinnacle.py`. The helper here just calls it.

- [ ] **Step 2: Verify the file still imports cleanly**

Run: `python -c "from arnold.mirror.workflows.strategies.pinnacle import _read_slip_odds, _update_slip_stake; print(_read_slip_odds, _update_slip_stake)"`
Expected: two function objects printed.

- [ ] **Step 3: Commit**

```bash
git add arnold/mirror/workflows/strategies/pinnacle.py
git commit -m "feat(strategy/pinnacle): port _read_slip_odds + _update_slip_stake

Direct port from PinnacleMirrorWorkflow.read_slip_odds / update_slip_stake.
Localstorage Main:Betslip read for odds, React hidden-setter for stake.
Wired in Task 7 once prep_betslip is also in place."
```

---

## Task 6: Port `_click_market_btn` + constants + `_prep_betslip`

**Files:**
- Modify: `arnold/mirror/workflows/strategies/pinnacle.py`

This is the largest port — the DOM-click logic that selects the right outcome button on a Pinnacle event page.

- [ ] **Step 1: Add module-level constants**

Edit `arnold/mirror/workflows/strategies/pinnacle.py`. Find the section near the top with `_PINNACLE_HEADERS` (around line 115). Add the following constants right after that block (before `_build_headers`):

```python
# ------------------------------------------------------------------
# DOM-click constants — Pinnacle event page market-btn layout.
# ------------------------------------------------------------------

# Market label text (lower-cased) → canonical market type.
_MARKET_LABEL_MAP = {
    "money line": "moneyline",
    "moneyline": "moneyline",
    "1x2": "1x2",
    "spread": "spread",
    "handicap": "spread",
    "total": "total",
    "total points": "total",
    "over/under": "total",
}

# Visual button order within a market section → outcome.
# Pinnacle renders home → (draw) → away. Totals: over → under.
_OUTCOME_POSITION: dict[str, dict[str, int]] = {
    "1x2": {"home": 0, "draw": 1, "away": 2},
    "moneyline": {"home": 0, "away": 1},
    "spread": {"home": 0, "away": 1},
    "total": {"over": 0, "under": 1},
}
```

- [ ] **Step 2: Add `_click_market_btn` helper**

Insert AFTER the slip helpers (`_read_slip_odds`, `_update_slip_stake`) added in Task 5, BEFORE the `strategy = Strategy(...)` block:

```python
# ------------------------------------------------------------------
# Betslip prep — DOM click to select outcome, then fill stake.
# ------------------------------------------------------------------


async def _click_market_btn(page: Page, market: str, outcome: str) -> bool:
    """Click the button.market-btn matching market + outcome.

    Strategy: scan button.market-btn elements, group by parent market section
    label (e.g. "Money Line"), pick by visual position (home=0, draw=1, away=2
    for 1x2; over=0, under=1 for totals). Returns True iff a click was dispatched.
    """
    try:
        canon_market = _MARKET_LABEL_MAP.get(market, market)
        position_map = _OUTCOME_POSITION.get(canon_market) or _OUTCOME_POSITION.get("moneyline", {})
        target_pos = position_map.get(outcome)
        if target_pos is None:
            logger.warning(f"[pinnacle] _click_market_btn: unknown outcome {outcome!r} for market {canon_market!r}")
            return False

        js = """
        (([market, outcome, pos]) => {
            const allBtns = Array.from(document.querySelectorAll('button.market-btn'));
            if (!allBtns.length) return -1;

            const groups = [];
            let currentGroup = null;
            let currentHeader = null;

            for (const btn of allBtns) {
                let el = btn.parentElement;
                let foundHeader = null;
                for (let i = 0; i < 10 && el; i++) {
                    const t = el.textContent || "";
                    const lower = t.toLowerCase();
                    if (lower.includes("money line") || lower.includes("1x2") ||
                        lower.includes("spread") || lower.includes("handicap") ||
                        lower.includes("total") || lower.includes("over/under")) {
                        foundHeader = t.toLowerCase();
                        break;
                    }
                    el = el.parentElement;
                }
                if (foundHeader !== currentHeader) {
                    currentGroup = { header: foundHeader, btns: [] };
                    groups.push(currentGroup);
                    currentHeader = foundHeader;
                }
                if (currentGroup) {
                    currentGroup.btns.push(btn);
                }
            }

            const marketLower = market.toLowerCase();
            let targetGroup = null;
            for (const g of groups) {
                const h = g.header || "";
                if (h.includes(marketLower) ||
                    (marketLower === "moneyline" && h.includes("money line")) ||
                    (marketLower === "1x2" && h.includes("1x2")) ||
                    (marketLower === "spread" && (h.includes("spread") || h.includes("handicap"))) ||
                    (marketLower === "total" && (h.includes("total") || h.includes("over")))) {
                    targetGroup = g;
                    break;
                }
            }

            if (!targetGroup) return -2;
            if (pos >= targetGroup.btns.length) return -3;
            return allBtns.indexOf(targetGroup.btns[pos]);
        })
        """

        idx = await page.evaluate(js, [market, outcome, target_pos])
        if idx is None or idx < 0:
            logger.warning(
                f"[pinnacle] _click_market_btn: btn lookup returned {idx} "
                f"(market={market!r} outcome={outcome!r} pos={target_pos})"
            )
            return False

        await page.evaluate(f"() => document.querySelectorAll('button.market-btn')[{idx}].click()")
        logger.info(f"[pinnacle] Clicked market-btn[{idx}] for {market}/{outcome}")
        return True
    except Exception as e:
        logger.warning(f"[pinnacle] _click_market_btn failed: {e}")
        return False
```

- [ ] **Step 3: Add `_prep_betslip`**

Insert immediately after `_click_market_btn`:

```python
async def _prep_betslip(page: Page, bet, stake: float, intel: dict | None) -> PlacementResult:
    """Click the correct outcome → wait for slip → write stake.

    Steps:
      1. Resolve market + outcome from bet (dict or attr).
      2. Call _click_market_btn.
      3. Poll localStorage["Main:Betslip"].Selections.length > 0 (5s, 250ms).
      4. Call _update_slip_stake.

    Returns PlacementResult(prepped) on success, (failed, reason) on either gate.
    """
    import asyncio

    def _g(obj, k, default=None):
        if isinstance(obj, dict):
            return obj.get(k, default)
        return getattr(obj, k, default)

    market = (_g(bet, "market") or "moneyline").lower()
    outcome = (_g(bet, "outcome") or "home").lower()
    bet_id = _g(bet, "bet_id", 0) or 0

    clicked = await _click_market_btn(page, market, outcome)
    if not clicked:
        logger.warning(f"[pinnacle] prep_betslip: outcome click failed market={market!r} outcome={outcome!r}")
        return PlacementResult(status="failed", bet_id=bet_id, reason="outcome_btn_not_found")

    slip_populated = False
    for _ in range(20):
        try:
            count = await page.evaluate(
                """() => {
                    const raw = localStorage.getItem("Main:Betslip");
                    if (!raw) return 0;
                    try {
                        const d = JSON.parse(raw);
                        return (d?.Selections ?? []).length;
                    } catch { return 0; }
                }"""
            )
            if count and int(count) > 0:
                slip_populated = True
                break
        except Exception:
            pass
        await asyncio.sleep(0.25)

    if not slip_populated:
        logger.warning(f"[pinnacle] prep_betslip: slip not populated within 5s")
        return PlacementResult(status="failed", bet_id=bet_id, reason="slip_not_populated")

    await _update_slip_stake(page, stake, intel)
    return PlacementResult(status="prepped", bet_id=bet_id)
```

- [ ] **Step 4: Verify imports and module-level structure**

Run: `python -c "from arnold.mirror.workflows.strategies.pinnacle import _prep_betslip, _click_market_btn, _MARKET_LABEL_MAP, _OUTCOME_POSITION; print('ok')"`
Expected: `ok`.

- [ ] **Step 5: Commit**

```bash
git add arnold/mirror/workflows/strategies/pinnacle.py
git commit -m "feat(strategy/pinnacle): port _prep_betslip + _click_market_btn

Direct port of PinnacleMirrorWorkflow.prep_betslip + _click_market_btn:
DOM walk to find market section by header text, click by visual position,
poll localStorage Main:Betslip for slip population, then _update_slip_stake.
Wired into Strategy export in Task 7."
```

---

## Task 7: Update `Strategy(...)` export — wire new fields, drop `_place_bet`

**Files:**
- Modify: `arnold/mirror/workflows/strategies/pinnacle.py` (the closing block + remove `_place_bet`)

- [ ] **Step 1: Replace the `Strategy(...)` export block**

Edit `arnold/mirror/workflows/strategies/pinnacle.py`. Find the existing closing block:

```python
strategy = Strategy(
    check_login=_check_login,
    sync_balance=_sync_balance,
    sync_history=_sync_history,
    place_bet=_place_bet,
    check_live_price=_check_live_price,
    navigate_to_event=_navigate_to_event,
)
```

Replace with:

```python
strategy = Strategy(
    check_login=_check_login,
    sync_balance=_sync_balance,
    sync_history=_sync_history,
    navigate_to_event=_navigate_to_event,
    prep_betslip=_prep_betslip,
    check_live_price=_check_live_price,
    scan=_scan,
    settle_all=_settle_all,
    read_slip_odds=_read_slip_odds,
    update_slip_stake=_update_slip_stake,
    parse_placement_response=parse_placement_response,
    parse_placement_status=parse_placement_status,
)
# place_bet intentionally omitted: GenericWorkflow.place_bet falls back to
# manual mode without autonomous_placement, and provider_runner only invokes
# place_bet when workflow.autonomous_placement is True.
```

- [ ] **Step 2: Delete `_place_bet` and its docstring**

In the same file, find and delete the `_place_bet` function (the one that builds the `/bets/straight` POST body). It's roughly 90 lines. The function signature is:

```python
async def _place_bet(page: Page, bet, stake: float, intel: dict | None) -> PlacementResult:
```

Delete from that line through the closing `return PlacementResult(...)` of the function — including the section banner comment immediately above it (`# Place bet — full API automation`).

- [ ] **Step 3: Verify the file still imports**

Run: `python -c "from arnold.mirror.workflows.strategies.pinnacle import strategy; print('fields set:', sum(1 for f in strategy.__dict__.values() if f is not None))"`
Expected: `fields set: 12` (12 callables wired).

- [ ] **Step 4: Verify `_place_bet` is gone**

Run: `grep -c "_place_bet\|async def _place_bet\|def _place_bet" arnold/mirror/workflows/strategies/pinnacle.py`
Expected: `0`.

- [ ] **Step 5: Run the strategy tests again — should still pass**

Run: `pytest arnold/tests/workflows/test_pinnacle_strategy.py -v`
Expected: 9 passed.

- [ ] **Step 6: Commit**

```bash
git add arnold/mirror/workflows/strategies/pinnacle.py
git commit -m "feat(strategy/pinnacle): wire all hooks; drop _place_bet (mirror-only)

B2 of the migration: place_bet field omitted from Strategy export — Pinnacle
becomes mirror-style (user clicks CONFIRM on pinnacle.se, browser interceptor
catches placement XHR). _place_bet implementation deleted (~90 lines)."
```

---

## Task 8: Drop `autonomous_placement` from intel JSON

**Files:**
- Modify: `data/mirror_intel/pinnacle.json`

- [ ] **Step 1: Edit the intel JSON**

Edit `data/mirror_intel/pinnacle.json`. Find:

```json
{
  "provider_id": "pinnacle",
  "domain": "pinnacle.se",
  "api_base": "https://api.arcadia.pinnacle.se/0.1",
  "autonomous_placement": true,
  "markets": {
```

Remove the `"autonomous_placement": true,` line. Final file:

```json
{
  "provider_id": "pinnacle",
  "domain": "pinnacle.se",
  "api_base": "https://api.arcadia.pinnacle.se/0.1",
  "markets": {
    "designation_map": {
      "home": "home",
      "away": "away",
      "draw": "draw",
      "over": "over",
      "under": "under"
    },
    "key_map": {
      "moneyline": "s;0;m",
      "1x2": "s;0;m",
      "spread": "s;0;s",
      "total": "s;0;ou"
    }
  },
  "navigation": {
    "event_url_template": "https://www.pinnacle.se/sv/matchup/{matchup_id}",
    "history_path": "/sv/my-account/bet-history"
  }
}
```

- [ ] **Step 2: Verify JSON parses**

Run: `python -c "import json; d = json.load(open('data/mirror_intel/pinnacle.json')); print('autonomous_placement' in d, list(d.keys()))"`
Expected: `False ['provider_id', 'domain', 'api_base', 'markets', 'navigation']`.

- [ ] **Step 3: Commit**

```bash
git add data/mirror_intel/pinnacle.json
git commit -m "feat(intel/pinnacle): drop autonomous_placement (mirror-style now)

provider_runner.py:990 only calls place_bet when this flag is true.
With the strategy migration (B2), Pinnacle becomes mirror-style — user
confirms CONFIRM on pinnacle.se, browser interceptor catches the XHR."
```

---

## Task 9: Flip routing in `arnold/mirror/workflows/__init__.py`

**Files:**
- Modify: `arnold/mirror/workflows/__init__.py:19-44, 65-91, 145-156`

This is the keystone change — once committed, `get_workflow("pinnacle")` returns `GenericWorkflow` instead of `PinnacleMirrorWorkflow`.

- [ ] **Step 1: Remove the import on line 28**

Find and edit:

```python
def _load_platform_map() -> dict[str, type[ProviderWorkflow]]:
    # Polymarket + Pinnacle migrated to data/mirror_intel/ + strategies/ and are
    # routed via GenericWorkflow in get_workflow() ahead of this map.
    from .altenar import AltenarWorkflow
    from .gecko import GeckoWorkflow
    from .generic import GenericWorkflow
    from .interwetten import InterwettenWorkflow
    from .kalshi import KalshiWorkflow
    from .kambi import KambiWorkflow
    from .pinnacle import PinnacleMirrorWorkflow

    return {
        "altenar": AltenarWorkflow,
        "pinnacle_mirror": PinnacleMirrorWorkflow,
        "gecko_v2": GeckoWorkflow,
        ...
    }
```

Change to:

```python
def _load_platform_map() -> dict[str, type[ProviderWorkflow]]:
    # Polymarket + Pinnacle migrated to data/mirror_intel/ + strategies/ and are
    # routed via GenericWorkflow in get_workflow() ahead of this map.
    from .altenar import AltenarWorkflow
    from .gecko import GeckoWorkflow
    from .generic import GenericWorkflow
    from .interwetten import InterwettenWorkflow
    from .kalshi import KalshiWorkflow
    from .kambi import KambiWorkflow

    return {
        "altenar": AltenarWorkflow,
        "gecko_v2": GeckoWorkflow,
        "kambi": KambiWorkflow,
        "spectate": GenericWorkflow,
        "tenbet": GenericWorkflow,
        "snabbare": GenericWorkflow,
        "custom": GenericWorkflow,
        "betconstruct": GenericWorkflow,
        "interwetten": InterwettenWorkflow,
        "coolbet": GenericWorkflow,
        "tipwin": GenericWorkflow,
        "kalshi": KalshiWorkflow,
    }
```

(The `"pinnacle_mirror": PinnacleMirrorWorkflow` line in the dict and the import are both removed.)

- [ ] **Step 2: Remove `"pinnacle": "pinnacle_mirror"` from `_PROVIDER_TO_PLATFORM`**

Find:

```python
_PROVIDER_TO_PLATFORM: dict[str, str] = {
    # Pinnacle mirror
    "pinnacle": "pinnacle_mirror",
    # Altenar
    "betinia": "altenar",
    ...
}
```

Change to:

```python
_PROVIDER_TO_PLATFORM: dict[str, str] = {
    # Altenar
    "betinia": "altenar",
    ...
}
```

(Both the comment line `# Pinnacle mirror` and the entry `"pinnacle": "pinnacle_mirror",` are removed.)

- [ ] **Step 3: Tighten the explanatory comment in `get_workflow`**

Find:

```python
    # Providers with an explicit dedicated class in _PROVIDER_TO_PLATFORM take
    # precedence over the intel-JSON → GenericWorkflow shortcut.  This lets us
    # register a purpose-built mirror workflow (e.g. PinnacleMirrorWorkflow)
    # even when a mirror_intel JSON also exists for that provider.
```

Change to:

```python
    # Providers with an explicit dedicated class in _PROVIDER_TO_PLATFORM take
    # precedence over the intel-JSON → GenericWorkflow shortcut. Lets us
    # register a purpose-built workflow even when a mirror_intel JSON also
    # exists for that provider.
```

- [ ] **Step 4: Smoke-test the routing change**

Run:

```bash
python -c "
from arnold.mirror.workflows import get_workflow
w = get_workflow('pinnacle')
print('class:', type(w).__name__)
print('strategy loaded:', w.strategy is not None)
print('intel loaded:', w.intel is not None)
print('autonomous_placement:', w.autonomous_placement)
"
```

Expected:
```
class: GenericWorkflow
strategy loaded: True
intel loaded: True
autonomous_placement: False
```

If `class: PinnacleMirrorWorkflow` — the explicit map removal didn't take. Re-check Step 2.
If `strategy loaded: False` — the strategy file has an import error. Run `python -c "from arnold.mirror.workflows.strategies.pinnacle import strategy"` to see the traceback.

- [ ] **Step 5: Commit**

```bash
git add arnold/mirror/workflows/__init__.py
git commit -m "feat(workflows): route pinnacle through GenericWorkflow + strategy

Drops PinnacleMirrorWorkflow import + pinnacle_mirror platform entry +
the pinnacle->pinnacle_mirror mapping. get_workflow('pinnacle') now
falls through the explicit-platform check, hits the intel-JSON path,
and returns GenericWorkflow with strategies/pinnacle.py as its strategy."
```

---

## Task 10: Delete the dedicated workflow file + its tests

**Files:**
- Delete: `arnold/mirror/workflows/pinnacle.py`
- Delete: `arnold/tests/workflows/test_pinnacle_slip.py`

- [ ] **Step 1: Confirm nothing else imports the dedicated class**

Run: `grep -rn "PinnacleMirrorWorkflow\|from .pinnacle\|from arnold.mirror.workflows.pinnacle" arnold/ docs/ 2>&1 | grep -v __pycache__ | grep -v "test_pinnacle_slip"`
Expected: empty (no hits outside the test file we're about to delete).

If you see any hits, stop and investigate — they need updating before the file is deleted.

- [ ] **Step 2: Delete the dedicated workflow file**

```bash
git rm arnold/mirror/workflows/pinnacle.py
```

- [ ] **Step 3: Delete the dedicated test file**

```bash
git rm arnold/tests/workflows/test_pinnacle_slip.py
```

- [ ] **Step 4: Verify nothing in the workflows package broke**

Run: `python -c "from arnold.mirror.workflows import get_workflow; w = get_workflow('pinnacle'); print(type(w).__name__)"`
Expected: `GenericWorkflow`.

Run: `python -c "from arnold.mirror.workflows import get_workflow; print(type(get_workflow('polymarket')).__name__, type(get_workflow('altenar')).__name__, type(get_workflow('kambi')).__name__)"`
Expected: `GenericWorkflow AltenarWorkflow KambiWorkflow` (only Pinnacle and Polymarket use Generic; the rest are unchanged).

- [ ] **Step 5: Commit**

```bash
git commit -m "chore(workflows): delete dedicated PinnacleMirrorWorkflow + tests

Strategy migration is complete: pinnacle is routed through GenericWorkflow.
The 521-line dedicated class and its 22 tests are superseded — placement-
parser tests already ported to test_pinnacle_strategy.py."
```

---

## Task 11: Run full test suite + arb-runner smoke test

- [ ] **Step 1: Run the affected test files**

Run:

```bash
pytest arnold/tests/workflows/test_pinnacle_strategy.py arnold/tests/test_arb_runner_green_gate.py arnold/tests/test_slip_odds_stream.py -v
```

Expected: all green. The `test_arb_runner_green_gate.py` tests use a mock workflow that has `parse_placement_status` defined — those mocks are still valid because GenericWorkflow exposes the method via the strategy delegation added in Task 2.

If `test_arb_runner_green_gate.py` fails on the Pinnacle path, it's likely because the mock there is set up against a `PinnacleMirrorWorkflow` instance directly. Check `arnold/tests/test_arb_runner_green_gate.py:468, 544` and adjust the mock to use `GenericWorkflow` or a plain `MagicMock` with the right method shape.

- [ ] **Step 2: Run the full mirror test directory**

Run: `pytest arnold/tests/workflows/ arnold/tests/test_arb_runner*.py arnold/tests/test_slip_odds_stream.py -v`
Expected: all green.

- [ ] **Step 3: If any test fails, investigate before proceeding**

Common breakage modes:
- A test imported `from arnold.mirror.workflows.pinnacle import ...` — update to `from arnold.mirror.workflows.strategies.pinnacle import ...`.
- A test relies on `wf.autonomous_placement is True` — that's now `False`. Check whether the test is asserting old behavior or new.
- A test asserts `type(wf) is PinnacleMirrorWorkflow` — change to `wf.provider_id == "pinnacle"` or `isinstance(wf, GenericWorkflow)`.

Fix in place; do NOT re-introduce the dedicated class.

- [ ] **Step 4: If you fixed any tests, commit them**

```bash
git add arnold/tests/
git commit -m "test: update tests broken by pinnacle strategy migration"
```

(Skip this step if no tests needed updating.)

---

## Task 12: Final verification

- [ ] **Step 1: Static smoke run**

```bash
python -c "
from arnold.mirror.workflows import get_workflow
w = get_workflow('pinnacle')
print('class:', type(w).__name__)
print('strategy:', w.strategy is not None)
print('intel keys:', sorted(w.intel.keys()))
print('autonomous_placement:', w.autonomous_placement)
print('parse_status:', w.parse_placement_status({'wagerNumber': 1}))
print('parse_response:', w.parse_placement_response({'wagerNumber': 1}))
"
```

Expected output:
```
class: GenericWorkflow
strategy: True
intel keys: ['api_base', 'domain', 'markets', 'navigation', 'provider_id']
autonomous_placement: False
parse_status: {'success': True, 'error': None, 'max_stake': None}
parse_response: 1
```

- [ ] **Step 2: Confirm git log shows the migration as a clean sequence**

```bash
git log --oneline origin/main..HEAD
```

Expected (~7 commits):
```
chore(workflows): delete dedicated PinnacleMirrorWorkflow + tests
feat(workflows): route pinnacle through GenericWorkflow + strategy
feat(intel/pinnacle): drop autonomous_placement (mirror-style now)
feat(strategy/pinnacle): wire all hooks; drop _place_bet (mirror-only)
feat(strategy/pinnacle): port _prep_betslip + _click_market_btn
feat(strategy/pinnacle): port _read_slip_odds + _update_slip_stake
feat(strategy/pinnacle): port placement-XHR parsers + tests
feat(workflows/generic): delegate slip + placement parsers to strategy
feat(workflows/strategy): add slip + placement-XHR hook fields
```

(Or test-fix commit if Task 11 needed one.)

- [ ] **Step 3: Final check — run lint if configured**

Run: `ruff check arnold/mirror/workflows/strategies/pinnacle.py arnold/mirror/workflows/generic.py arnold/mirror/workflows/__init__.py arnold/tests/workflows/test_pinnacle_strategy.py`
Expected: no errors.

- [ ] **Step 4: Manual live-test follow-up (NOT part of this plan — handed back to user)**

The unit-test path validates the migration's structural correctness but cannot exercise:
- `_check_login` reading real `localStorage['Main:User']` from a logged-in pinnacle.se tab.
- `_prep_betslip` actually finding `button.market-btn` on a real event page.
- The placement XHR interceptor in `browser.py` catching the user's CONFIRM click.
- `_update_slip_stake` syncing a counter slip during an arb run with Pinnacle as anchor.

User should:
1. Launch local arnold (`arnold.bat`).
2. Open Pinnacle in mirror, log in.
3. Verify the balance pill shows the correct SEK amount (logs: `[pinnacle] sync_balance` should show a non-negative number).
4. Trigger a value bet on Pinnacle. User clicks CONFIRM on pinnacle.se. Verify the bet appears in the DB (`SELECT * FROM bets WHERE provider_id = 'pinnacle' ORDER BY id DESC LIMIT 1`).
5. Trigger settle_all from the local UI. Verify settled bets reconcile.
6. Trigger one arb where Pinnacle is the anchor. Watch logs for `[Arb:pinnacle]` lines and confirm `update_slip_stake` is called when prices move.

Do NOT close the migration as "done" until at least step 4 has been completed manually. Until then, the strategy file ships untested in the live-Playwright sense.

---

## Completion criteria

- [ ] All 12 tasks above completed.
- [ ] `pytest arnold/tests/workflows/ arnold/tests/test_arb_runner*.py arnold/tests/test_slip_odds_stream.py` passes.
- [ ] Static smoke (Task 12 Step 1) outputs the expected lines.
- [ ] Manual live-test (Task 12 Step 4) — at least one real Pinnacle bet placed end-to-end.
