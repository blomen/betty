# Generic Mirror Workflow Design

**Date:** 2026-04-08
**Approach:** B — GenericWorkflow + per-provider strategy plugins

## Problem

Wiring each new provider requires rediscovering the same patterns (balance, history, betslip) from scratch. We have 15+ unwired providers in `docs/mirror-wiring.md`. The existing `ManualWorkflow` fallback does nothing — it just returns "manual" for every method.

## Goal

A data-driven `GenericWorkflow` that reads a per-provider intel JSON file and executes the standard mirror lifecycle for any provider. A discovery engine populates the intel file by mining JSONL recordings + live DOM inspection. Optional Python strategy overrides handle edge cases (shadow DOM, Imperva, custom auth).

## File Layout

```
backend/src/mirror/workflows/generic.py       # GenericWorkflow class
backend/src/mirror/workflows/discovery.py      # DOM/API discovery engine
backend/src/mirror/workflows/strategies/       # Per-provider Python overrides
backend/src/mirror/workflows/strategies/__init__.py
backend/data/mirror_intel/                     # Per-provider intel JSON files
backend/data/mirror_intel/{provider_id}.json
```

## Intel JSON Schema

Each provider gets one JSON file at `backend/data/mirror_intel/{provider_id}.json`:

```json
{
  "provider_id": "coolbet",
  "platform": "coolbet",
  "discovered_at": "2026-04-08T14:30:00Z",
  "updated_at": "2026-04-08T14:30:00Z",
  "capabilities": {
    "login": "discovered | manual | none",
    "balance": "discovered | manual | none",
    "history": "discovered | manual | none",
    "placement": "discovered | manual | none"
  },
  "login": {
    "method": "balance_api | dom",
    "indicator": {
      "selector": ".user-balance",
      "regex": "[\\d.,]+"
    }
  },
  "balance": {
    "method": "api | dom",
    "api": {
      "url": "/api/wallet/balance",
      "path": "data.balance",
      "currency": "SEK"
    },
    "dom": {
      "selector": ".balance-amount",
      "regex": "[\\d.,]+",
      "multiplier": 1.0
    }
  },
  "history": {
    "method": "api | dom",
    "url": "/account/bet-history",
    "api": {
      "endpoint": "/api/bets/history",
      "settled_filter": {"status": "settled"},
      "open_filter": {"status": "open"},
      "mapping": {
        "bet_id": "id",
        "odds": "odds",
        "stake": "stake",
        "status": "result",
        "payout": "payout",
        "event_name": "event.name",
        "status_map": {"won": "won", "lost": "lost", "void": "void"}
      }
    },
    "dom": {
      "container": ".bet-history-list",
      "row_selector": ".bet-row",
      "fields": {
        "odds": {"selector": ".odds", "regex": "[\\d.]+"},
        "stake": {"selector": ".stake", "regex": "[\\d.]+"},
        "status": {"selector": ".status", "text_map": {"Won": "won", "Lost": "lost"}},
        "payout": {"selector": ".payout", "regex": "[\\d.]+"},
        "event_name": {"selector": ".event-name"}
      }
    }
  },
  "betslip": {
    "odds_buttons": ".outcome-button, .odds-btn",
    "stake_input": "input[name='stake'], #stake-input",
    "confirm_button": ".place-bet-btn, .confirm-bet",
    "confirmation_selector": ".bet-confirmed, .success-toast"
  },
  "navigation": {
    "history_path": "/account/bet-history",
    "event_url_template": "/sport/event/{event_id}"
  },
  "api_endpoints": {
    "balance": ["GET /api/wallet/balance"],
    "history": ["GET /api/bets/history"],
    "placement": ["POST /api/bets/place"]
  },
  "notes": "Free-text notes about quirks, rate limits, auth requirements."
}
```

Fields are nullable — discovery fills what it can, leaves the rest as `null`. The `capabilities` dict summarizes what's wired (`discovered`), what needs manual user action (`manual`), or what's not available (`none`).

## Discovery Engine

`backend/src/mirror/workflows/discovery.py`

### Entry point

```python
async def discover(page: Page, provider_id: str) -> dict:
    """Run full discovery for a provider. Returns intel dict, saves to JSON."""
```

### Discovery phases

**Phase 1: Analyze JSONL recordings** (`analyze_recordings`)
- Read all JSONL files from `data/mirror_recordings/{provider_id}/`
- Extract unique API endpoints, group by pattern:
  - Balance-like: URLs containing `/wallet`, `/balance`, `/account`
  - History-like: URLs containing `/bets`, `/history`, `/coupons`, `/betHistory`
  - Placement-like: URLs containing `/place`, `/bet`, `/coupon` (POST only)
- Store matched endpoints in `api_endpoints`
- Parse response bodies to infer field mappings (JSON path extraction)

**Phase 2: Discover balance** (`discover_balance`)
- If API endpoint found in recordings: test it via `page.evaluate(fetch)` → map response path
- Fallback: scan visible DOM for money-like patterns in nav/header areas
- Record whichever works as the balance method

**Phase 3: Discover history** (`discover_history`)
- Navigate to common history URLs: `/account/bet-history`, `/my-bets`, `/betting/history`, etc.
- If API endpoint found in recordings: map response fields (bet_id, odds, stake, status, payout)
- Fallback: find bet rows in DOM, identify field selectors by label proximity
- Record settled vs open bet filters

**Phase 4: Discover betslip** (`discover_betslip`)
- Find odds-like buttons on current page (numbers like `1.50`, `2.10` in clickable elements)
- Click one → detect betslip appearance
- Find stake input (type=number or text near betslip)
- Find confirm button (submit/button near stake input)
- Record selectors

**Phase 5: Save intel** (`save_intel`)
- Write to `data/mirror_intel/{provider_id}.json`
- Log what was discovered vs what needs manual wiring

### Key principle

**Recordings first, live DOM second.** The interceptor already captures all HTTP traffic in JSONL. Discovery mines recordings for API endpoints before touching the DOM. API is always more reliable than DOM selectors.

## GenericWorkflow Class

`backend/src/mirror/workflows/generic.py`

```python
class GenericWorkflow(ProviderWorkflow):
    """Data-driven workflow that reads intel JSON + optional strategy overrides."""
    
    def __init__(self, provider_id: str, domain: str):
        super().__init__(provider_id, domain)
        self.intel = load_intel(provider_id)        # JSON file or empty dict
        self.strategy = load_strategy(provider_id)   # Optional Python module
        self.mode = WorkflowMode.GUIDED              # Always guided for generic
```

### Method dispatch pattern

Every ABC method follows this pattern:

```python
async def sync_balance(self, page) -> float:
    # 1. Strategy override (custom Python)
    if self.strategy and self.strategy.sync_balance:
        return await self.strategy.sync_balance(page, self.intel)
    
    # 2. Intel-driven (JSON config)
    if not self.intel or not self.intel.get("balance"):
        return -1.0  # Unknown
    
    bal = self.intel["balance"]
    if bal["method"] == "api" and bal.get("api"):
        # page.evaluate(fetch) with cookies from browser session
        data = await self._evaluate_api(page, bal["api"]["url"])
        return extract_path(data, bal["api"]["path"])
    
    if bal["method"] == "dom" and bal.get("dom"):
        el = await page.query_selector(bal["dom"]["selector"])
        text = await el.text_content()
        match = re.search(bal["dom"]["regex"], text)
        return float(match.group().replace(",", "")) if match else -1.0
    
    return -1.0
```

### sync_history → HistoryEntry list

- Navigate to `intel.navigation.history_path`
- If API method: fetch endpoint, iterate results, map fields via `intel.history.api.mapping`
- If DOM method: find rows via `intel.history.dom.row_selector`, extract fields per row
- Return `list[HistoryEntry]` with normalized status (won/lost/void/pending)

### place_bet → PlacementResult (always guided)

- `navigate_to_event()`: use URL template or let user navigate manually
- Click odds button matching the target outcome
- Fill stake input
- **Highlight confirm button but do NOT click** — user must confirm
- Return `PlacementResult(status="manual")` until interceptor catches the actual placement

## Strategy Overrides

`backend/src/mirror/workflows/strategies/{provider_id}.py`

```python
# strategies/coolbet.py
from dataclasses import dataclass, field
from typing import Callable, Optional

@dataclass
class Strategy:
    check_login: Optional[Callable] = None
    sync_balance: Optional[Callable] = None
    sync_history: Optional[Callable] = None
    navigate_to_event: Optional[Callable] = None
    place_bet: Optional[Callable] = None

async def coolbet_check_login(page, intel) -> bool:
    """Coolbet needs Camoufox + specific cookie wait."""
    await page.wait_for_selector(".user-menu", timeout=10000)
    return True

strategy = Strategy(
    check_login=coolbet_check_login,
)
```

### Loading

```python
def load_strategy(provider_id: str) -> Strategy | None:
    """Import strategies/{provider_id}.py if it exists, return .strategy attr."""
    try:
        mod = importlib.import_module(f"mirror.workflows.strategies.{provider_id}")
        return getattr(mod, "strategy", None)
    except ModuleNotFoundError:
        return None
```

Most providers won't have a strategy file. Only those with edge cases (shadow DOM, Imperva, custom auth) need one.

## Integration

### get_workflow() factory update

```python
def get_workflow(provider_id: str) -> ProviderWorkflow:
    # 1. Hardcoded platform workflows (pinnacle, polymarket) → unchanged
    if provider_id in _PLATFORM_WORKFLOWS:
        return _PLATFORM_WORKFLOWS[provider_id]
    
    # 2. Existing platform workflows (altenar, gecko_v2, kambi) → unchanged
    platform = _get_platform(provider_id)
    if platform in _PLATFORM_CLASSES:
        return _PLATFORM_CLASSES[platform](provider_id, domain)
    
    # 3. GenericWorkflow for everything else (replaces ManualWorkflow)
    return GenericWorkflow(provider_id, domain)
```

### Discovery trigger

- **Auto:** When `GenericWorkflow.__init__` finds no intel file and JSONL recordings exist
- **Manual:** `POST /api/mirror/discover/{provider_id}` — runs full discovery on current page
- **Re-discover:** `POST /api/mirror/discover/{provider_id}?force=true` — overwrites existing intel

### mirror-wiring.md sync

When discovery succeeds, the capabilities summary should be logged. The markdown table stays as the human-readable checklist — intel JSON is the machine-readable equivalent. Both should be kept in sync manually (or via a script later).

## Runtime Lifecycle

For any provider using GenericWorkflow:

```
1. Provider detected (user navigates to site)
   └─ load_intel() or run discover() if no intel file
   
2. check_login(page)
   └─ Balance visible (DOM/API) → logged in
   
3. sync_history(page)                          ← SETTLE FIRST
   ├─ Navigate to history URL
   ├─ Scrape/parse settled bets → stage settlements
   ├─ Scrape/parse open bets → record to DB if missing
   └─ Confirm settlements with user via frontend
   
4. sync_balance(page)
   └─ API call or DOM scrape → update DB
   
5. navigate_to_event(page, bet)
   └─ URL template from intel, or user navigates manually
   
6. place_bet(page, bet, stake)                 ← ALWAYS GUIDED
   ├─ Click odds button
   ├─ Fill stake input
   ├─ Highlight confirm button
   └─ AWAIT user click (never auto-confirm for generic)
   
7. Record result
   └─ Interceptor catches placement response → DB
```

## Scope

### In scope
- `GenericWorkflow` class implementing `ProviderWorkflow` ABC
- `discovery.py` with JSONL mining + DOM inspection
- Intel JSON schema + loader/saver
- Strategy override mechanism
- `get_workflow()` factory update
- Discovery API endpoint

### Out of scope
- Migrating existing platform workflows (altenar, gecko_v2, kambi) to generic — they stay as-is
- Auto-updating mirror-wiring.md from intel JSON
- Autonomous mode for generic workflow (always guided)
- WebSocket discovery (HTTP + DOM only for now)

## Dependencies

- Existing `ProviderWorkflow` ABC in `workflows/base.py`
- Existing `BetInterceptor` and `NetworkRecorder`
- Existing JSONL recordings in `data/mirror_recordings/`
- `HistoryEntry` and `PlacementResult` dataclasses from `base.py`
