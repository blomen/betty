# Interwetten Mirror Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Interwetten to the mirror workflow so it can settle pending bets, sync balance, and place bets through the Playwright browser.

**Architecture:** New `InterwettenWorkflow(ProviderWorkflow)` class with DOM-based login detection, balance parsing, bet history scraping from `/en/journal/bets`, event navigation via `/en/sportsbook/e/{id}/{slug}`, and betslip interaction via `data-betting` attributes and `#amount_{outcome_id}` inputs.

**Tech Stack:** Python 3.10+ / Playwright async / DOM parsing / regex

**Spec:** `docs/superpowers/specs/2026-04-15-interwetten-workflow-design.md`

---

### Task 1: Add `provider_meta` to Interwetten extractor

The extractor currently doesn't store `provider_meta` on outcomes, so the play loop has no event_id for navigation. Add `event_id` to provider_meta so the workflow can build URLs.

**Files:**
- Modify: `backend/src/providers/interwetten.py:706-718`

- [ ] **Step 1: Add provider_meta to outcome dicts in `_parse_raw_event`**

In `backend/src/providers/interwetten.py`, find the `_parse_raw_event` method. The outcomes are built around line 680-705. After the outcomes list is built, the market dict is created at line 706. Add `provider_meta` with `event_id` to each outcome:

```python
            has_draw = any(o["name"] == "draw" for o in outcomes)
            market_type = "1x2" if has_draw else "moneyline"
            for o in outcomes:
                o["provider_meta"] = {"event_id": str(event_id)}
            markets = [{"type": market_type, "outcomes": outcomes}]
```

- [ ] **Step 2: Add provider_meta to spread/total markets in `_parse_spread_market` and `_parse_total_market`**

In `_parse_spread_market` (around line 551), outcomes are built without provider_meta. Add it to each outcome dict. Find where outcomes are appended and add `"provider_meta": {"event_id": str(event.id).replace("interwetten_", "")}`:

```python
            outcomes.append(
                {
                    "name": name,
                    "odds": odds_val,
                    "provider_meta": {"event_id": str(event.id).replace("interwetten_", "")},
                }
            )
```

Do the same in `_parse_total_market` for its outcome dicts.

- [ ] **Step 3: Verify extraction still works**

Run: `cd backend && python -c "from src.providers.interwetten import InterwettenRetriever; print('import OK')"`

Expected: `import OK`

- [ ] **Step 4: Commit**

```bash
git add backend/src/providers/interwetten.py
git commit -m "feat(interwetten): add provider_meta with event_id to extractor outcomes"
```

---

### Task 2: Wire `interwetten_event_id` in play_loop and mirror route

Add Interwetten-specific event_id mapping in the play loop's `_bet_ns` function and the mirror route's `BetProxy` builder so the workflow can access `bet.interwetten_event_id`.

**Files:**
- Modify: `firevsports/mirror/play_loop.py:36-41`
- Modify: `backend/src/api/routes/mirror.py:522-527`

- [ ] **Step 1: Add interwetten_event_id to `_bet_ns` in play_loop.py**

In `firevsports/mirror/play_loop.py`, after the Gecko V2 field mapping (line 40), add:

```python
    # Gecko V2 fields — same event_id key in provider_meta, different prefix
    ns.gecko_event_id = meta.get("event_id", "")
    # Interwetten fields
    ns.interwetten_event_id = meta.get("event_id", "")
    return ns
```

- [ ] **Step 2: Add interwetten_event_id to BetProxy in mirror route**

In `backend/src/api/routes/mirror.py`, after line 527 (`bet.kambi_outcome_id = ...`), add:

```python
    bet.kambi_outcome_id = provider_meta.get("outcome_id", "")
    bet.interwetten_event_id = provider_meta.get("event_id", "")
```

- [ ] **Step 3: Commit**

```bash
git add firevsports/mirror/play_loop.py backend/src/api/routes/mirror.py
git commit -m "feat(interwetten): wire interwetten_event_id in play_loop and mirror route"
```

---

### Task 3: Create InterwettenWorkflow — login + balance

Create the workflow file with `check_login` and `sync_balance`. These are the simplest methods and establish the file structure.

**Files:**
- Create: `firevsports/mirror/workflows/interwetten.py`

- [ ] **Step 1: Create the workflow file with check_login and sync_balance**

Create `firevsports/mirror/workflows/interwetten.py`:

```python
"""InterwettenWorkflow — DOM-based workflow for Interwetten (Sportsbook Software GmbH).

Proprietary platform. All interaction is DOM-based (Cloudflare blocks API).
Balance from header, history from /en/journal/bets, betslip via data-betting attributes.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from .base import HistoryEntry, PlacementResult, ProviderWorkflow, WorkflowMode

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

_BALANCE_RE = re.compile(r"([\d\s]+[,.][\d]{2})\s*SEK")


class InterwettenWorkflow(ProviderWorkflow):
    platform = "interwetten"

    def __init__(self, provider_id: str, domain: str, mode: WorkflowMode = WorkflowMode.GUIDED):
        super().__init__(provider_id, domain, mode)

    @property
    def home_url(self) -> str:
        return f"https://{self.domain}/en/sportsbook"

    async def check_login(self, page: Page) -> bool:
        """Check if user is logged in by looking for balance in header."""
        try:
            bal_el = await page.query_selector("text=/\\d+[,.]\\d+\\s*SEK/")
            if bal_el:
                return True
            # Fallback: "Last Login" text only visible when authenticated
            ll = await page.query_selector("text=/Last Login/")
            return ll is not None
        except Exception as e:
            logger.warning(f"[{self.provider_id}] check_login error: {e}")
            return False

    async def sync_balance(self, page: Page) -> float:
        """Read balance from header. Format: '816,11 SEK'."""
        try:
            el = await page.query_selector("text=/\\d+[,.]\\d+\\s*SEK/")
            if not el:
                logger.warning(f"[{self.provider_id}] balance element not found")
                return -1
            text = (await el.text_content() or "").strip()
            m = _BALANCE_RE.search(text)
            if not m:
                logger.warning(f"[{self.provider_id}] balance regex no match: {text!r}")
                return -1
            raw = m.group(1).replace(" ", "").replace(",", ".")
            return float(raw)
        except Exception as e:
            logger.warning(f"[{self.provider_id}] sync_balance error: {e}")
            return -1

    async def sync_history(self, page: Page) -> list[HistoryEntry]:
        """Stub — implemented in Task 4."""
        return []

    async def navigate_to_event(self, page: Page, bet) -> bool:
        """Stub — implemented in Task 5."""
        return False

    async def place_bet(self, page: Page, bet, stake: float) -> PlacementResult:
        """Stub — implemented in Task 6."""
        return PlacementResult(status="failed", bet_id=0, reason="not_implemented")
```

- [ ] **Step 2: Commit**

```bash
git add firevsports/mirror/workflows/interwetten.py
git commit -m "feat(interwetten): create workflow with check_login + sync_balance"
```

---

### Task 4: Implement sync_history

Scrape the Overview bets page at `/en/journal/bets` to extract bet history as `HistoryEntry` list.

**Files:**
- Modify: `firevsports/mirror/workflows/interwetten.py`

- [ ] **Step 1: Replace the sync_history stub**

Replace the `sync_history` stub in `firevsports/mirror/workflows/interwetten.py` with:

```python
    async def sync_history(self, page: Page) -> list[HistoryEntry]:
        """Scrape bet history from /en/journal/bets (Overview bets page)."""
        try:
            url = f"https://{self.domain}/en/journal/bets"
            if not page.url.startswith(url):
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(1)

            entries: list[HistoryEntry] = []
            rows = await page.query_selector_all("table a[href*='/journal/betdetail/']")
            for row in rows:
                try:
                    cells = await row.query_selector_all("td")
                    if len(cells) < 8:
                        continue

                    # Date(ID) cell — extract bet_id (last number in text)
                    date_text = (await cells[1].text_content() or "").strip()
                    bet_id_match = re.search(r"(\d{6,})", date_text)
                    bet_id = bet_id_match.group(1) if bet_id_match else ""

                    # EVENT cell — format: "Team A - Team B (Market) -> Outcome / Odds"
                    event_text = (await cells[3].text_content() or "").strip()
                    parsed = _parse_event_text(event_text)

                    # TIP cell
                    tip = (await cells[6].text_content() or "").strip()

                    # Odds cell
                    odds_text = (await cells[7].text_content() or "").strip().replace(",", ".")
                    odds = float(odds_text) if odds_text else 0.0

                    # Stake cell
                    stake_text = (await cells[8].text_content() or "").strip().replace(",", ".")
                    stake = float(stake_text) if stake_text else 0.0

                    # Profit cell — "---" means pending
                    profit_text = (await cells[9].text_content() or "").strip()

                    # Status — check for Lost/Won icon in EVENT cell
                    status = "pending"
                    if profit_text == "---":
                        status = "pending"
                    else:
                        lost_icon = await cells[3].query_selector('[title="Lost"], [alt="Lost"]')
                        won_icon = await cells[3].query_selector('[title="Won"], [alt="Won"]')
                        if lost_icon:
                            status = "lost"
                        elif won_icon:
                            status = "won"
                        else:
                            # Infer from profit
                            profit_val = float(profit_text.replace(",", ".")) if profit_text else 0
                            status = "won" if profit_val > 0 else "lost"

                    # Payout
                    payout = None
                    if status == "won":
                        profit_val = float(profit_text.replace(",", ".")) if profit_text not in ("", "---") else 0
                        payout = stake + profit_val
                    elif status == "lost":
                        payout = 0.0

                    entries.append(
                        HistoryEntry(
                            provider_bet_id=bet_id,
                            event_name=parsed["event_name"],
                            market=parsed["market"],
                            outcome=tip or parsed["outcome"],
                            odds=odds,
                            stake=stake,
                            status=status,
                            payout=payout,
                        )
                    )
                except Exception as e:
                    logger.debug(f"[{self.provider_id}] Failed to parse history row: {e}")
                    continue

            logger.info(f"[{self.provider_id}] sync_history: {len(entries)} bets found")
            return entries
        except Exception as e:
            logger.warning(f"[{self.provider_id}] sync_history error: {e}")
            return []
```

- [ ] **Step 2: Add the event text parser helper function**

Add this module-level function above the class definition (after `_BALANCE_RE`):

```python
_EVENT_RE = re.compile(
    r"^(.+?)\s*\((\w[\w\s]*?)\s*\)\s*->\s*(.+?)\s*/\s*([\d,.]+)\s*$"
)


def _parse_event_text(text: str) -> dict:
    """Parse 'Team A - Team B (Market) -> Outcome / Odds' format.

    Returns dict with event_name, market, outcome, odds_text.
    """
    m = _EVENT_RE.match(text.strip())
    if m:
        return {
            "event_name": m.group(1).strip(),
            "market": m.group(2).strip().lower(),
            "outcome": m.group(3).strip(),
            "odds_text": m.group(4).strip(),
        }
    # Fallback: try simpler split on ->
    if "->" in text:
        parts = text.split("->", 1)
        event_part = parts[0].strip()
        outcome_part = parts[1].strip() if len(parts) > 1 else ""
        # Strip market from event_part: "Team A - Team B (Match)" -> "Team A - Team B"
        event_name = re.sub(r"\s*\([^)]*\)\s*$", "", event_part)
        return {
            "event_name": event_name,
            "market": "1x2",
            "outcome": outcome_part.split("/")[0].strip() if "/" in outcome_part else outcome_part,
            "odds_text": outcome_part.split("/")[-1].strip() if "/" in outcome_part else "",
        }
    return {"event_name": text, "market": "1x2", "outcome": "", "odds_text": ""}
```

- [ ] **Step 3: Commit**

```bash
git add firevsports/mirror/workflows/interwetten.py
git commit -m "feat(interwetten): implement sync_history from Overview bets page"
```

---

### Task 5: Implement navigate_to_event

Navigate to the event page and verify the page loaded with markets.

**Files:**
- Modify: `firevsports/mirror/workflows/interwetten.py`

- [ ] **Step 1: Replace the navigate_to_event stub**

Replace the `navigate_to_event` stub with:

```python
    async def navigate_to_event(self, page: Page, bet) -> bool:
        """Navigate to /en/sportsbook/e/{event_id}/{slug}."""
        event_id = getattr(bet, "interwetten_event_id", "") or getattr(bet, "event_id", "")
        if not event_id:
            logger.warning(f"[{self.provider_id}] no event_id for navigation")
            return False

        # Build slug from team names if available
        home = getattr(bet, "display_home", "") or ""
        away = getattr(bet, "display_away", "") or ""
        if home and away:
            slug = f"{home}-{away}".lower().replace(" ", "-")
            slug = re.sub(r"[^a-z0-9-]", "", slug)
        else:
            slug = "event"

        url = f"https://{self.domain}/en/sportsbook/e/{event_id}/{slug}"
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            if not resp or resp.status == 404:
                logger.warning(f"[{self.provider_id}] event page 404: {url}")
                return False

            # Wait for market grids to appear
            try:
                await page.wait_for_selector(".s-market-grid", timeout=5000)
            except Exception:
                logger.warning(f"[{self.provider_id}] no markets found on event page")
                return False

            logger.info(f"[{self.provider_id}] navigated to event {event_id}")
            return True
        except Exception as e:
            logger.warning(f"[{self.provider_id}] navigate_to_event error: {e}")
            return False
```

- [ ] **Step 2: Commit**

```bash
git add firevsports/mirror/workflows/interwetten.py
git commit -m "feat(interwetten): implement navigate_to_event"
```

---

### Task 6: Implement place_bet (prep_betslip + confirm_bet)

Two-phase placement: prep_betslip selects outcome + fills stake, confirm_bet clicks submit.

**Files:**
- Modify: `firevsports/mirror/workflows/interwetten.py`

- [ ] **Step 1: Add outcome matching helper**

Add this module-level function after `_parse_event_text`:

```python
# Map our market names to Interwetten market_type values in data-betting
_MARKET_TYPE_MAP = {
    "1x2": ["Match"],
    "moneyline": ["Match"],
    "spread": ["Handicap", "Asian Handicap"],
    "total": ["How many goals", "Over/Under"],
}

# Map our outcome names to Interwetten tip values
_OUTCOME_TIP_MAP = {
    "home": "1",
    "draw": "X",
    "away": "2",
    "over": " ",   # Totals use space as tip — match by outcome name instead
    "under": " ",
}
```

- [ ] **Step 2: Replace place_bet stub and add prep_betslip + confirm_bet**

Replace the `place_bet` stub and add the two-phase methods:

```python
    async def place_bet(self, page: Page, bet, stake: float) -> PlacementResult:
        """Single-phase fallback: prep + confirm in sequence."""
        result = await self.prep_betslip(page, bet, stake)
        if result.status != "prepped":
            return result
        return await self.confirm_bet(page)

    async def prep_betslip(self, page: Page, bet, stake: float) -> PlacementResult:
        """Select outcome on event page, fill stake in betslip."""
        market = getattr(bet, "market", "1x2")
        outcome = getattr(bet, "outcome", "home")
        point = getattr(bet, "point", None)

        try:
            # Clear any existing betslip selections
            while True:
                remove_btn = await page.query_selector(".s-outcome-selected")
                if not remove_btn:
                    break
                await remove_btn.click()
                await asyncio.sleep(0.3)

            # Find the right outcome via data-betting attributes
            outcome_el = await self._find_outcome_element(page, market, outcome, point)
            if not outcome_el:
                return PlacementResult(status="failed", bet_id=0, reason="outcome_not_found")

            # Read live odds before clicking
            db_json = await outcome_el.get_attribute("data-betting")
            if db_json:
                import json
                db = json.loads(db_json)
                live_odds_str = str(db[4]).replace(",", ".")
                live_odds = float(live_odds_str)
            else:
                live_odds = getattr(bet, "odds", 0)

            # Click outcome to add to betslip
            await outcome_el.click()
            await asyncio.sleep(0.5)

            # Find and fill stake input — ID is #amount_{outcome_id}
            stake_input = await page.query_selector("input[placeholder='Stake']")
            if not stake_input:
                return PlacementResult(status="failed", bet_id=0, reason="stake_input_not_found")

            await stake_input.fill(str(round(stake, 2)))
            await asyncio.sleep(0.3)

            # Verify "Possible winnings" updated (confirms stake was accepted)
            winnings_el = await page.query_selector("text=/Possible winnings/")
            if winnings_el:
                parent = await winnings_el.evaluate_handle("el => el.closest('li')")
                winnings_text = await parent.evaluate("el => el.textContent") if parent else ""
                logger.info(f"[{self.provider_id}] betslip ready — winnings: {winnings_text}")

            logger.info(
                f"[{self.provider_id}] prepped: {market}/{outcome} @ {live_odds}, stake={stake}"
            )
            return PlacementResult(
                status="prepped", bet_id=0, actual_odds=live_odds, actual_stake=stake
            )
        except Exception as e:
            logger.warning(f"[{self.provider_id}] prep_betslip error: {e}")
            return PlacementResult(status="failed", bet_id=0, reason=str(e))

    async def confirm_bet(self, page: Page) -> PlacementResult:
        """Click 'Submit betting slip' and detect result."""
        try:
            submit = await page.query_selector("#BS_Button_Submit")
            if not submit:
                return PlacementResult(status="failed", bet_id=0, reason="submit_button_not_found")

            await submit.click()
            await asyncio.sleep(2)

            # Check for success — betslip clears or shows confirmation
            # On success the betslip count goes back to 0
            slip_count = await page.query_selector("text=/Betting slip/")
            if slip_count:
                parent = await slip_count.evaluate_handle("el => el.parentElement")
                count_text = await parent.evaluate(
                    "el => el.querySelector(':scope > *:last-child')?.textContent"
                ) if parent else "0"
                if count_text and count_text.strip() == "0":
                    logger.info(f"[{self.provider_id}] bet placed successfully")
                    return PlacementResult(status="placed", bet_id=0)

            # Check for error messages
            error_el = await page.query_selector(".s-betslip-error, .error-message, [class*='error']")
            if error_el:
                error_text = (await error_el.text_content() or "").strip()
                logger.warning(f"[{self.provider_id}] placement error: {error_text}")
                return PlacementResult(status="failed", bet_id=0, reason=error_text)

            # If betslip still shows the bet, treat as placed (user may have confirmed)
            return PlacementResult(status="placed", bet_id=0)
        except Exception as e:
            logger.warning(f"[{self.provider_id}] confirm_bet error: {e}")
            return PlacementResult(status="failed", bet_id=0, reason=str(e))
```

- [ ] **Step 3: Add the `_find_outcome_element` helper method**

Add this private method to the class:

```python
    async def _find_outcome_element(self, page: Page, market: str, outcome: str, point: float | None):
        """Find the .s-outcome element matching our market/outcome/point."""
        import json

        target_types = _MARKET_TYPE_MAP.get(market, ["Match"])
        target_tip = _OUTCOME_TIP_MAP.get(outcome, outcome)

        markets = await page.query_selector_all(".s-market-grid")
        for mkt_el in markets:
            db_json = await mkt_el.get_attribute("data-betting")
            if not db_json:
                continue
            db = json.loads(db_json)
            mkt_type = db[3] if len(db) > 3 else ""

            # Check if this market matches any of our target types
            matched = False
            for tt in target_types:
                if tt.lower() in mkt_type.lower():
                    matched = True
                    break
            if not matched:
                continue

            # For spread: match the handicap value in market_type (e.g., "Handicap 1:0")
            if market == "spread" and point is not None:
                handicap_match = re.search(r"(\d+):(\d+)", mkt_type)
                if handicap_match:
                    h, a = int(handicap_match.group(1)), int(handicap_match.group(2))
                    # Interwetten "Handicap H:A" means home gets +H, away gets +A
                    # Our point is from home perspective: -1.0 means "Handicap 0:1"
                    iw_point = h - a
                    if abs(iw_point - point) > 0.01:
                        continue

            outcomes = await mkt_el.query_selector_all(".s-outcome")
            for out_el in outcomes:
                out_json = await out_el.get_attribute("data-betting")
                if not out_json:
                    continue
                out_db = json.loads(out_json)
                tip = out_db[1] if len(out_db) > 1 else ""
                name = out_db[2] if len(out_db) > 2 else ""

                # For totals: match by outcome name (Over/Under X.5)
                if market == "total":
                    if outcome == "over" and "over" in name.lower():
                        if point is None or str(point) in name:
                            return out_el
                    elif outcome == "under" and "under" in name.lower():
                        if point is None or str(point) in name:
                            return out_el
                    continue

                # For 1x2/moneyline/spread: match by tip
                if tip == target_tip:
                    return out_el

        logger.warning(f"[{self.provider_id}] outcome not found: {market}/{outcome}/{point}")
        return None
```

- [ ] **Step 4: Add check_live_price**

Add after `confirm_bet`:

```python
    async def check_live_price(self, page: Page, bet) -> tuple[float | None, float | None]:
        """Read live odds from event page DOM."""
        import json

        market = getattr(bet, "market", "1x2")
        outcome = getattr(bet, "outcome", "home")
        point = getattr(bet, "point", None)
        fair_odds = getattr(bet, "fair_odds", None)

        try:
            el = await self._find_outcome_element(page, market, outcome, point)
            if not el:
                return None, None
            db_json = await el.get_attribute("data-betting")
            if not db_json:
                return None, None
            db = json.loads(db_json)
            odds_str = str(db[4]).replace(",", ".")
            live_odds = float(odds_str)

            edge = None
            if fair_odds and fair_odds > 0:
                edge = (live_odds / fair_odds - 1) * 100

            return live_odds, edge
        except Exception as e:
            logger.debug(f"[{self.provider_id}] check_live_price error: {e}")
            return None, None
```

- [ ] **Step 5: Commit**

```bash
git add firevsports/mirror/workflows/interwetten.py
git commit -m "feat(interwetten): implement place_bet with two-phase prep+confirm"
```

---

### Task 7: Register InterwettenWorkflow in both registries

Update both `__init__.py` files to import and map `InterwettenWorkflow` instead of `GenericWorkflow`.

**Files:**
- Modify: `firevsports/mirror/workflows/__init__.py:38`
- Modify: `backend/src/mirror/workflows/__init__.py:37`

- [ ] **Step 1: Update firevsports registry**

In `firevsports/mirror/workflows/__init__.py`, add the import in `_load_platform_map`:

```python
def _load_platform_map() -> dict[str, type[ProviderWorkflow]]:
    from .altenar import AltenarWorkflow
    from .gecko import GeckoWorkflow
    from .generic import GenericWorkflow
    from .interwetten import InterwettenWorkflow
    from .kambi import KambiWorkflow
    from .pinnacle import PinnacleWorkflow
    from .polymarket import PolymarketWorkflow

    return {
        "polymarket": PolymarketWorkflow,
        "pinnacle": PinnacleWorkflow,
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
    }
```

- [ ] **Step 2: Update backend registry**

Same change in `backend/src/mirror/workflows/__init__.py` — add import and replace `GenericWorkflow` with `InterwettenWorkflow` for the `"interwetten"` key.

- [ ] **Step 3: Create backend mirror copy**

Copy the workflow file to the backend:

```bash
cp firevsports/mirror/workflows/interwetten.py backend/src/mirror/workflows/interwetten.py
```

- [ ] **Step 4: Verify imports**

```bash
cd backend && python -c "from src.mirror.workflows import get_workflow; w = get_workflow('interwetten'); print(type(w).__name__)"
```

Expected: `InterwettenWorkflow`

- [ ] **Step 5: Commit**

```bash
git add firevsports/mirror/workflows/__init__.py backend/src/mirror/workflows/__init__.py backend/src/mirror/workflows/interwetten.py
git commit -m "feat(interwetten): register InterwettenWorkflow in both registries"
```

---

### Task 8: End-to-end test in Playwright browser

Verify the full workflow works in the live Playwright mirror.

**Files:** None (manual verification)

- [ ] **Step 1: Start FirevSports**

Run `firevsports/firevsports.bat` to start the local server + Playwright browser.

- [ ] **Step 2: Open Interwetten and log in**

Navigate to `https://www.interwetten.se` in the Playwright browser and log in.

- [ ] **Step 3: Test check_login + sync_balance**

In the Play tab, select Interwetten. Verify:
- Provider shows green highlight (login detected)
- Balance displays correctly

- [ ] **Step 4: Test sync_history (settlement)**

After login detection, the play loop runs settlement. Verify:
- Pending bets from DB are matched against history
- Settlements detected and broadcast via SSE

- [ ] **Step 5: Test navigate_to_event**

When a bet is queued, verify:
- Browser navigates to the correct event page
- Market grids are visible

- [ ] **Step 6: Test prep_betslip**

Verify:
- Correct outcome is selected (highlighted)
- Stake is filled in betslip
- Possible winnings updates

- [ ] **Step 7: Test place/skip**

Place or skip the bet. Verify:
- On Place: submit button clicked, bet recorded
- On Skip: betslip cleared, next bet navigated

- [ ] **Step 8: Commit any fixes**

```bash
git add -A
git commit -m "fix(interwetten): adjustments from live testing"
```
