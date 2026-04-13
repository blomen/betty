# LeoVegas / KambiWorkflow Full Wiring — Design Spec

**Date:** 2026-04-14  
**Scope:** Wire LeoVegas (and all Kambi-platform providers) into the full fire window workflow — balance, login, navigation, betslip automation, and history sync.

---

## Background

LeoVegas uses the Kambi white-label sportsbook platform (brand ID `leose`). It is already mapped to `KambiWorkflow` in the registry, but `KambiWorkflow` is a stub: `sync_balance`, `check_login`, `sync_history`, `navigate_to_event`, and `place_bet` are all no-ops or manual fallbacks.

Kambi uses **WebSocket** for bet placement (no HTTP POST), so bet interception follows the `_place_event` path (user clicks Place in FirevSports UI → `confirm_bet()` clicks DOM button) rather than the HTTP interception path used by Altenar and Gecko.

The balance API for LeoVegas is a GraphQL relay endpoint (`/api?relay`) already detected by the interceptor. History is SSR-scraped from `/betting/sports/bethistory`, already handled by `service.py`.

---

## Architecture

### Two implementation phases

**Phase 1 — Code without live browsing** (all based on known API shapes):
- Balance / login via GraphQL relay (`/api?relay`)
- `navigate_to_event()` using Kambi standard event URL
- `sync_history()` navigating to history page (service.py SSR scraper handles parsing)
- `kambi_event_id` / `kambi_outcome_id` mapped in BetProxy so the play loop can pass them to the workflow

**Phase 2 — Live discovery session, then code**:
- Open LeoVegas in the Playwright browser
- Verify `window.KambiWidget.api.BETSLIP_ITEM_ADD` is exposed
- Find stake input DOM selector in betslip
- Find Place button DOM selector and confirmation element
- Implement `prep_betslip()` + `confirm_bet()` from discovered selectors

### Placement flow (two-phase, no HTTP interception)

```
navigate_to_event()
  → page.goto("https://leovegas.com/betting/sports/event/{kambi_event_id}")

prep_betslip()
  → JS: window.KambiWidget.api.BETSLIP_ITEM_ADD({ outcomes: [{ id: kambi_outcome_id }] })
  → wait for betslip DOM to update (stake input appears)
  → fill stake input → return PlacementResult(status="prepped", actual_odds=..., actual_stake=stake)

[FirevSports UI emits bet_ready; user clicks Place]

confirm_bet()
  → click DOM "Lägg spel" / "Place bet" button
  → poll DOM for confirmation element (e.g. receipt/success message)
  → return PlacementResult(status="placed"|"failed", ...)
```

Bet is recorded via the `_place_event` branch in `play_loop.py` (line ~452). No intercepted HTTP body — `actual_odds` and `actual_stake` come from `prep_result`.

---

## Components

### 1. `KambiWorkflow` improvements (`firevsports/mirror/workflows/kambi.py` + backend copy)

#### Balance / login

LeoVegas uses a GraphQL relay, not a REST endpoint. Add a `_BALANCE_GRAPHQL` mapping:

```python
_BALANCE_GRAPHQL: dict[str, str] = {
    "leovegas": "https://www.leovegas.com/api?relay",
}
```

GraphQL query body (operation `balance`):
```json
{"query": "{ viewer { user { balance { totalAmount currency } } } }"}
```

Expected response shape (already confirmed in service.py):
```json
{"data": {"viewer": {"user": {"balance": {"totalAmount": 1076, "currency": "SEK"}}}}}
```

`check_login()`: POST to relay URL → parse `data.viewer.user.balance.totalAmount` → returns True if parseable.

`sync_balance()`: same call → return `float(totalAmount)`.

Existing REST endpoint map (`_BALANCE_ENDPOINTS`) stays for unibet; GraphQL relay is a fallback for providers in `_BALANCE_GRAPHQL`.

#### `navigate_to_event()`

```python
async def navigate_to_event(self, page, bet) -> bool:
    event_id = getattr(bet, "outcome_id", "") and getattr(bet, "event_id", "")
    # Use kambi_event_id from provider_meta (flattened by _bet_ns)
    kambi_eid = getattr(bet, "kambi_event_id", "") or getattr(bet, "event_id", "")
    if not kambi_eid:
        return True  # No ID — user navigates manually
    url = f"https://www.{self.domain}/betting/sports/event/{kambi_eid}"
    # Skip if already on this event page
    if kambi_eid in (page.url or ""):
        return True
    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
    return True
```

Kambi event ID comes from `provider_meta.event_id` stored during extraction (the Kambi API's `eventId` field from the betoffer). The `_bet_ns()` in play_loop.py auto-flattens `provider_meta` into the namespace, so `bet_ns.event_id` (from provider_meta, not the canonical event ID) needs to be disambiguated — see BetProxy section below.

#### `sync_history()`

Navigate to `/betting/sports/bethistory` and return `[]`. The SSR scraper in `service.py._scrape_ssr_bet_history()` already handles parsing when the page loads.

```python
async def sync_history(self, page, bet=None) -> list[HistoryEntry]:
    hist_url = f"https://www.{self.domain}/betting/sports/bethistory"
    if "/bethistory" not in (page.url or ""):
        await page.goto(hist_url, wait_until="networkidle", timeout=15000)
        await asyncio.sleep(3)
    return []
```

#### `prep_betslip()` (Phase 2 — after live discovery)

```python
async def prep_betslip(self, page, bet, stake: float) -> PlacementResult:
    outcome_id = getattr(bet, "kambi_outcome_id", "") or getattr(bet, "outcome_id", "")
    if not outcome_id:
        return PlacementResult(status="no_prep", bet_id=0, reason="no_outcome_id")

    # Step 1: Add outcome to betslip via Kambi Widget API
    added = await page.evaluate(f"""
        () => {{
            const api = window.KambiWidget && window.KambiWidget.api;
            if (!api || !api.BETSLIP_ITEM_ADD) return false;
            api.BETSLIP_ITEM_ADD({{ outcomes: [{{ id: {outcome_id} }}] }});
            return true;
        }}
    """)
    if not added:
        return PlacementResult(status="no_prep", bet_id=0, reason="kambi_widget_not_found")

    await asyncio.sleep(1.5)

    # Step 2: Fill stake input (selector TBD from discovery session)
    # Discovered selector expected: input[class*="stake"] or input[data-kambi*="stake"]
    stake_filled = await page.evaluate(f"""
        () => {{
            const input = document.querySelector(/* STAKE_INPUT_SELECTOR */);
            if (!input) return false;
            input.value = '';
            input.dispatchEvent(new Event('input', {{ bubbles: true }}));
            input.value = '{stake}';
            input.dispatchEvent(new Event('input', {{ bubbles: true }}));
            input.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return true;
        }}
    """)

    status = "prepped" if stake_filled else "no_prep"
    reason = None if stake_filled else "stake_input_not_found"
    return PlacementResult(
        status=status,
        bet_id=getattr(bet, "bet_id", 0),
        actual_stake=stake,
        reason=reason,
    )
```

#### `confirm_bet()` (Phase 2 — after live discovery)

```python
async def confirm_bet(self, page) -> PlacementResult:
    # Click Place button (selector TBD from discovery session)
    # Expected: button[class*="place"] or button containing "Lägg spel"
    clicked = await page.evaluate("""
        () => {
            const btns = document.querySelectorAll('button');
            for (const btn of btns) {
                const t = (btn.textContent || '').trim().toLowerCase();
                if (t === 'lägg spel' || t === 'place bet' || t.includes('place')) {
                    btn.click();
                    return true;
                }
            }
            return false;
        }
    """)
    if not clicked:
        return PlacementResult(status="failed", bet_id=0, reason="place_button_not_found")

    # Poll for confirmation DOM element (TBD from discovery)
    for _ in range(20):
        await asyncio.sleep(0.5)
        confirmed = await page.evaluate("""
            () => {
                const body = document.body.innerText || '';
                return body.includes('kupong') || body.includes('placed') || body.includes('accepted');
            }
        """)
        if confirmed:
            return PlacementResult(status="placed", bet_id=0, reason=None)

    # Timeout — assume placed (WS-based, confirmation may not show in DOM)
    return PlacementResult(status="placed", bet_id=0, reason="timeout_assumed_placed")
```

---

### 2. `provider_meta` mapping — BetProxy (`backend/src/api/routes/mirror.py`)

Add two new BetProxy fields so the workflow gets the Kambi-specific IDs:

```python
bet.kambi_event_id = provider_meta.get("event_id", "")    # Kambi eventId (from betoffer)
bet.kambi_outcome_id = provider_meta.get("outcome_id", "") # Kambi outcome ID
```

The play_loop's `_bet_ns()` auto-flattens `provider_meta`, so `bet_ns.event_id` already gets the provider_meta `event_id` if there's no top-level collision. The explicit `kambi_event_id` / `kambi_outcome_id` names avoid ambiguity with the canonical `event_id`.

---

### 3. `outcome_id` in `Odds.provider_meta` — storage verification

The Kambi extractor stores `outcome_id` at the outcome level and `event_id`/`betoffer_id` at the market level. Need to verify that `storage.py` merges these into the single `Odds.provider_meta` JSONB field. If not, update storage to include `outcome_id` in the merged metadata.

---

### 4. Discovery session procedure (Phase 2 prerequisite)

Before implementing `prep_betslip` and `confirm_bet`:

1. Navigate to `https://www.leovegas.com` in the Playwright browser and log in
2. Open DevTools console → run `window.KambiWidget` → confirm API object exists
3. Navigate to any event page → run `window.KambiWidget.api` → list available methods
4. Add an outcome to betslip manually → inspect DOM → find stake input selector
5. Inspect Place button selector
6. Submit a test bet → inspect DOM for confirmation element
7. Capture `/api?relay` balance request → confirm GraphQL query body format

---

## Data flow summary

```
Extraction:
  KambiRetriever → Odds.provider_meta = {
      "event_id": "12345678",      # Kambi eventId
      "betoffer_id": "99999",      # Kambi betOffer ID
      "outcome_id": "111222333",   # Kambi outcome ID
  }

Play loop bet queue → _bet_ns() flattens provider_meta:
  bet_ns.event_id = "canonical-uuid"   # top-level (canonical)
  bet_ns.outcome_id = "111222333"      # from provider_meta (no collision)

BetProxy (mirror.py):
  bet.kambi_event_id = "12345678"
  bet.kambi_outcome_id = "111222333"

KambiWorkflow:
  navigate_to_event()  → uses kambi_event_id
  prep_betslip()       → uses kambi_outcome_id
  confirm_bet()        → clicks DOM button
```

---

## Error handling

| Failure | Behavior |
|---------|----------|
| No `kambi_event_id` | `navigate_to_event()` returns True (user navigates manually) |
| `KambiWidget` not found | `prep_betslip()` returns `no_prep` — play loop shows bet_ready with `prep_ok=False` |
| Stake input not found | Same — `no_prep`, user places manually |
| Place button not found | `confirm_bet()` returns `failed` |
| Confirmation DOM timeout | Returns `placed` (assumed — WS confirmation not visible in DOM) |
| Balance relay fails | `sync_balance()` returns -1; login assumed False |

---

## Files changed

| File | Change |
|------|--------|
| `firevsports/mirror/workflows/kambi.py` | Balance relay, navigate, sync_history, prep_betslip, confirm_bet |
| `backend/src/mirror/workflows/kambi.py` | Sync same changes (service.py uses this copy) |
| `backend/src/api/routes/mirror.py` | Add `kambi_event_id`, `kambi_outcome_id` to BetProxy |
| `backend/src/pipeline/storage.py` | Verify/fix `outcome_id` stored in `Odds.provider_meta` |

No changes to: `__init__.py` (registry already correct), `play_loop.py` (`_bet_ns` already flattens provider_meta), `interceptor.py` (GraphQL relay already detected).
