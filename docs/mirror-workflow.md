# Mirror Workflow — Canonical Reference

> Single source of truth for the entire mirror automation pipeline.
> Every provider follows this exact flow. No exceptions.

## Overview

The **mirror** is a headed Playwright Chromium browser that runs locally on your PC. It intercepts network traffic (HTTP + WebSocket), automates navigation, and records bets to the server DB via API proxy.

### Key Files

| File | Purpose |
|------|---------|
| `firevsports/mirror/browser.py` | Playwright lifecycle, network interception, provider detection |
| `firevsports/mirror/play_loop.py` | Multi-provider coordinator, cluster queues, event blocking |
| `firevsports/mirror/provider_runner.py` | Per-provider state machine (login → settle → navigate → place) |
| `firevsports/mirror/pending_loop.py` | Background settlement sync (60s poll) |
| `firevsports/mirror/data_stream.py` | Continuous per-provider polling (balance/positions/history) |
| `firevsports/mirror/sse.py` | Server-Sent Events broadcaster to frontend |
| `firevsports/mirror/router.py` | `/mirror/*` API endpoints for browser/play/settlement control |
| `firevsports/mirror/workflows/` | Per-platform workflow implementations |

---

## Provider Checklist

This checklist applies to **every** provider. Complete each step in order.

### 1. Interception Wiring

Wire the browser's network interceptor to recognize this provider's traffic.

| Task | Where | Details |
|------|-------|---------|
| Balance URL pattern | `browser.py` `_BALANCE_KEYWORDS` | e.g. `account/balance`, `/wallets`, `mainbalance` |
| History URL pattern | `browser.py` `_HISTORY_KEYWORDS` | e.g. `bethistory`, `coupon-history`, `mybets` |
| Bet placement URL pattern | `browser.py` `_BET_PLACEMENT_KEYWORDS` | e.g. `placewidget`, `/coupons`, `bets/straight` |
| WebSocket keywords (if applicable) | `browser.py` `_WS_MONITOR_KEYWORDS` | e.g. `kambi`, `push.aws` — for WS-based placement |
| Domain → provider_id mapping | `browser.py` `_detect_provider()` | Map all brand domains to their provider_id |
| Balance JSON extraction | `browser.py` `_extract_balance()` | Handle non-standard JSON shapes (nested wallets, arrays, etc.) |

**Current interception keywords:**
```python
_BALANCE_KEYWORDS = ("account/balance", "/wallets", "mainbalance", "wallet/balance", "payment-stats", "/cashier/balance")
_HISTORY_KEYWORDS = ("bethistory", "bet-history", "mybets", "my-bets", "widgetbethistory", "coupon-history")
_BET_PLACEMENT_KEYWORDS = ("placewidget", "placebet", "/coupons", "bets/straight", "bets/parlay", "bets/place", "clob.polymarket.com/order")
```

### 2. Open Site & Await Login

User highlights provider in UI → clicks Start → browser opens provider tab.

| Task | Method | Details |
|------|--------|---------|
| Find existing tab | `workflow.find_tab(context)` | Searches `context.pages` for matching domain, returns deepest URL |
| Open new tab | `POST /mirror/open-provider-tab` | Opens provider domain in fresh tab if no existing tab |
| Detect login | `workflow.check_login(page)` | Returns `True` when user is authenticated |
| Login timeout | `LOGIN_TIMEOUT = 120s` | Polls every `LOGIN_POLL_INTERVAL = 5s`, skips provider if timeout |
| SSE events | `login_waiting` → `login_detected` | Frontend shows waiting indicator, then green highlight with balance |

**Login detection methods by platform:**

| Platform | Method | How |
|----------|--------|-----|
| Pinnacle | API balance fetch | `GET /0.1/wallet/balance` — retry 3x |
| Altenar | Authed API + wallet | `GET /api/v3/account/balance` — parses cash/bonus/sport |
| Kambi | REST or GraphQL | `GET /wallitt/mainbalance` (Unibet) or GraphQL relay (LeoVegas) |
| Gecko V2 | API wallets | `GET /wallets` — any currency response = logged in |
| Polymarket | API + DOM | SDK balance call OR DOM scrape "Cash $" text |
| Interwetten | CSRF-aware AJAX + DOM | `Common.AjaxCall('refreshaccountbalance')` OR `#acc-balance` element |
| Generic | Strategy-driven | Intel JSON: balance API endpoint or DOM indicator selector |

### 3. Sync Balance → DB + Backend

Balance must be current before placing any bet (stake sizing depends on it).

| Task | Method | Details |
|------|--------|---------|
| Sync balance | `workflow.sync_balance(page)` | Returns `float` — current available balance |
| Auto-post on interception | `router._on_browser_event()` | Intercepted balance → `POST /api/bankroll/set/{provider_id}` |
| Cache in data stream | `ProviderDataStream._poll_balance()` | Polls every 30s, skips if interceptor delivered within 10s |
| Detection priority | Interceptor → workflow API → DOM scrape | Falls through until one succeeds |

**Balance JSON shapes by platform:**

| Platform | JSON path |
|----------|-----------|
| Pinnacle | `{"amount": 535.0, "currency": "SEK"}` |
| Altenar | `{result: {cash: {total}, bonus: {total}, sport: {total}}}` → sum all |
| Kambi | `{mainBalance: {amount: X}}` or GraphQL relay |
| Gecko V2 | `{Balances: {SEK: {Real: {Balance: X}}}}` |
| Polymarket | CLOB SDK `get_balance()` or DOM "Cash $X.XX" |
| Interwetten | `refreshaccountbalance` AJAX or `#acc-balance` DOM |
| Generic | Regex/DOM extraction per intel JSON |

### 4. Check Pending → Settle → Sync

**Settlement MUST complete before any bet placement.** The provider's bet history is the source of truth — the DB may have fewer bets.

| Task | Method | Details |
|------|--------|---------|
| Fetch pending from DB | `GET /api/opportunities/play/pending-bets` | Returns `{provider_id: [bets]}` |
| Sync provider history | `workflow.sync_history(page)` | Returns `list[HistoryEntry]` — all open + settled bets |
| Match pending vs history | `_detect_settlements()` | Three-tier fuzzy matching (see below) |
| Broadcast for review | SSE `settlements_detected` | UI shows toast with matched settlements |
| User confirms | UI callback | User reviews and clicks confirm |
| Record to DB | `POST /api/opportunities/play/settle-batch` | Batch-records all confirmed settlements |
| Record unknown bets | Auto | Open bets in history not in DB → recorded as unknown |

**Three-tier settlement matching:**

| Tier | Criteria | Tolerance |
|------|----------|-----------|
| **1 — Exact ID** | `provider_bet_id` matches | Exact string match |
| **2 — Name + Odds** | Event name fuzzy match (`token_overlap ≥ 0.5`) + odds | 5% odds tolerance |
| **3 — Fuzzy** | Odds + stake (when name match fails) | 10% odds + 30% stake tolerance |

Each history entry matched at most once (`used_history` set prevents reuse).

**HistoryEntry fields:**
```python
HistoryEntry(
    provider_bet_id: str,    # Provider's bet ID (for exact matching)
    event_name: str,         # "Team A vs Team B"
    market: str,             # "1x2", "spread", "total"
    outcome: str,            # "1", "X", "2", "over", "under"
    odds: float,             # Decimal odds
    stake: float,            # Amount wagered
    status: str,             # "won" | "lost" | "void" | "cashout" | "pending"
    payout: float,           # Amount returned (0 if lost)
)
```

**History sync methods by platform:**

| Platform | Method |
|----------|--------|
| Pinnacle | DOM text scrape (regex) + API fallback `/bets?status=settled` |
| Altenar | Authed API `/api/v3/history` → parses event + outcome + status |
| Kambi | CDN `/coupon/history.json` (intercepts auth token) + KSP fallback |
| Gecko V2 | API coupon-history after navigating to history page |
| Polymarket | Data API `/trades` (fuzzy match against DB) + DOM history fallback |
| Interwetten | DOM journal `/journal/bets` → flex-table parsing |
| Generic | Strategy override + API/DOM intel-driven extraction |

### 5. Navigate → Highest Edge Event

Pop next bet from queue, navigate the browser to the event page.

| Task | Method | Details |
|------|--------|---------|
| Pop from cluster queue | `pop_bet()` | Sorted by edge %, highest first |
| Check cluster blocking | `_is_blocked(event_id, market)` | No duplicate event+market across cluster siblings |
| Check daily cap | `DAILY_BET_CAP = 10` | Per provider per day. Uncapped: pinnacle, polymarket, cloudbet |
| Navigate to event | `workflow.navigate_to_event(page, bet)` | Platform-specific URL/API navigation |
| Detect closed event | DOM text scan | Checks for "closed", "stängd", "avbruten", etc. |

**Navigation methods by platform:**

| Platform | Method | URL pattern |
|----------|--------|-------------|
| Pinnacle | Direct URL | `/sv/matchup/{matchup_id}` |
| Altenar | Query params | `sportRoutingParams` with sport/category/championship/event IDs |
| Kambi | Widget API | `KambiWidget.navigateClient('#/event/{id}')` + hash fallback |
| Gecko V2 | Event param | `?eventId={gecko_event_id}` (f- prefix handling) |
| Polymarket | Direct slug | `/event/{market_slug}` |
| Interwetten | Direct URL | `/en/sportsbook/e/{event_id}/{slug}` OR search-by-team fallback |
| Generic | URL template | Intel JSON template with `{event_id}` substitution |

### 6. Sync Odds → Confirm Edge

Read live odds from the page, verify the bet is still +EV before placing.

| Task | Method | Details |
|------|--------|---------|
| Auto-select outcome | `workflow.prep_betslip(page, bet, stake)` | Platform-specific: WSDK toggle, Kambi addOutcome, DOM click |
| Read live odds | `workflow.check_live_price(page, bet)` | Returns `(odds, edge%)` |
| Compute live edge | `live_edge = (live_odds / fair_odds - 1) × 100` | Fair odds from Pinnacle devig |
| Auto-skip if -EV | `live_edge < 0` | Broadcasts `bet_skipped` with reason |
| Fill stake | Kelly-sized, capped to balance | Max stake ≤ available balance |
| Broadcast ready | SSE `bet_ready` | UI shows Place/Skip buttons |

**Prep/betslip methods by platform:**

| Platform | Prep method | Live price |
|----------|-------------|------------|
| Pinnacle | None (API placement) | API markets, American→Decimal conversion |
| Altenar | `WSDK.toggleSelections([oddId])` | Cached GetEventDetails interceptor |
| Kambi | `isolatedBetslip.addOutcomeIds([id])` | DOM `.mod-KambiBC-betslip-outcome__odds` |
| Gecko V2 | None (manual) | Not implemented |
| Polymarket | SDK order build OR DOM click + fill | CLOB orderbook API or DOM button text |
| Interwetten | DOM click outcome by data-betting ID + fill stake | DOM `_find_outcome_element()` → parse odds |
| Generic | None (guided manual) | Strategy-driven or None |

### 7. Await User Place → Intercept → Record

User clicks the provider's "Place Bet" button. The interceptor catches it.

| Task | Method | Details |
|------|--------|---------|
| Wait for signal | `asyncio.wait([intercept_event, skip_event])` | First completed wins |
| Intercept placement | `browser._on_response()` or `_on_websocket()` | Catches request + response body |
| Parse response | `workflow.parse_placement_response(body)` | Extract provider_bet_id, actual_odds, actual_stake |
| Check stake limitation | `actual_stake < 0.9 * requested` | Broadcasts `stake_limited` warning |
| Record to DB | `POST /api/bets` | Full bet record with provider_bet_id, odds, stake, event |
| Sync balance | Auto | Re-reads balance after placement |
| Block cluster siblings | `_block_event_market(event_id, market)` | Prevents duplicate exposure |

**Placement types:**

| Type | Providers | How |
|------|-----------|-----|
| **Autonomous API** | Pinnacle, Polymarket | workflow.place_bet() calls API directly on user confirm |
| **Two-phase semi-auto** | Altenar, Kambi, Interwetten | prep_betslip() selects outcome, user clicks confirm on site |
| **Manual** | Gecko V2, Generic | User navigates + fills betslip entirely, interceptor catches |

**Interception patterns:**

| Platform | HTTP/WS | URL pattern | Response fields |
|----------|---------|-------------|-----------------|
| Pinnacle | HTTP POST | `bets/straight` | `{betId, odds, stake}` |
| Altenar | HTTP POST | `placewidget`, `placebet` | `{data: {betId}}` or `{bets: [{id}]}` |
| Kambi | WebSocket | `kambi`, `push.aws` frames | `{couponId, placeBetResult}` |
| Gecko V2 | HTTP POST | `/coupons` | `{couponId}` in response |
| Polymarket | HTTP POST | `clob.polymarket.com/order` | SDK handles (no HTTP interception) |
| Interwetten | HTTP POST | `placebet` | DOM confirmation (betslip clears) |

### 8. Move to Pending → Next Bet

| Task | Method | Details |
|------|--------|---------|
| Bet in pending list | Immediate | Recorded bet has status "pending" in DB |
| PendingLoop picks up | Background, 60s poll | `pending_loop.py` syncs all providers with pending bets |
| Next bet | Return to step 5 | Pop next highest-edge bet from cluster queue |
| Queue empty | SSE `provider_complete` | Runner finishes, coordinator checks all runners |
| All runners done | SSE `play_complete` | Session complete |

---

## State Machine

```
IDLE
  ↓ POST /play/start
PROVIDER_OPENING
  ↓ find_tab() (retry 10x)
LOGIN_WAITING
  ↓ check_login() polls every 5s, 120s timeout
  ↓ sync_balance()
SETTLING
  ↓ sync_history() → _detect_settlements() → broadcast for review
  ↓ record unknown bets → check daily cap
NAVIGATING ←──────────────────────────┐
  ↓ pop_bet() → navigate_to_event()   │
  ↓ prep_betslip() → check_live_price │
  ↓ auto-skip if -EV                  │
READY                                  │
  ↓ wait: intercept OR skip            │
PLACING                                │
  ↓ parse response → record to DB      │
  ↓ block cluster siblings             │
  └────────────────────────────────────┘
  ↓ queue empty
IDLE (provider_complete → play_complete)
```

**SSE events per state:**

| State | Events |
|-------|--------|
| PROVIDER_OPENING | `provider_opening` |
| LOGIN_WAITING | `login_waiting`, `login_detected` |
| SETTLING | `settling_pending`, `settlements_detected`, `settlements_confirmed`, `settling_done`, `unknown_bets_recorded` |
| NAVIGATING | `bet_skipped` (closed event, -EV, capped) |
| READY | `bet_ready` |
| PLACING | `bet_placed`, `bet_failed`, `stake_limited`, `bet_error` |
| Complete | `provider_complete`, `play_complete`, `provider_skipped` |

---

## Provider Capability Matrix

Current wiring status as of 2026-04-16.

**Legend:** ✅ = working, ⚠️ = partial/needs testing, ❌ = not wired, A = autonomous, G = guided, M = manual

| # | Provider | Platform | Mode | Login | Balance | History | Navigate | Prep | Place | Live Price |
|---|----------|----------|:----:|:-----:|:-------:|:-------:|:--------:|:----:|:-----:|:----------:|
| 1 | pinnacle | Pinnacle | A | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ |
| 2 | polymarket | Polymarket | A | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 3 | betinia | Altenar | G | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 4 | campobet | Altenar | G | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ |
| 5 | quickcasino | Altenar | G | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ |
| 6 | swiper | Altenar | G | ✅ | ✅ | ⚠️ | ✅ | ✅ | ⚠️ | ✅ |
| 7 | lodur | Altenar | G | ✅ | ✅ | ⚠️ | ✅ | ✅ | ⚠️ | ✅ |
| 8 | dbet | Altenar | G | ✅ | ✅ | ⚠️ | ✅ | ✅ | ⚠️ | ✅ |
| 9 | spelklubben | Gecko V2 | G | ✅ | ✅ | ✅ | ✅ | — | ✅ | — |
| 10 | betsson | Gecko V2 | G | ✅ | ✅ | ✅ | ✅ | — | ✅ | — |
| 11 | betsafe | Gecko V2 | G | ✅ | ✅ | ✅ | ✅ | — | ✅ | — |
| 12 | nordicbet | Gecko V2 | G | ✅ | ✅ | ✅ | ✅ | — | ✅ | — |
| 13 | bethard | Gecko V2 | G | ✅ | ✅ | ✅ | ✅ | — | ✅ | — |
| 14 | unibet | Kambi | G | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ |
| 15 | leovegas | Kambi | G | ✅ | ⚠️ | ✅ | ✅ | ✅ | ⚠️ | ✅ |
| 16 | expekt | Kambi | G | ✅ | ⚠️ | ✅ | ✅ | ✅ | ⚠️ | ✅ |
| 17 | 888sport | Kambi | M | ⚠️ | ❌ | ⚠️ | ✅ | ✅ | ⚠️ | ✅ |
| 18 | speedybet | Kambi | G | ✅ | ⚠️ | ✅ | ✅ | ✅ | ⚠️ | ✅ |
| 19 | x3000 | Kambi | G | ✅ | ⚠️ | ✅ | ✅ | ✅ | ⚠️ | ✅ |
| 20 | goldenbull | Kambi | G | ✅ | ⚠️ | ✅ | ✅ | ✅ | ⚠️ | ✅ |
| 21 | 1x2 | Kambi | G | ✅ | ⚠️ | ✅ | ✅ | ✅ | ⚠️ | ✅ |
| 22 | betmgm | Kambi | G | ✅ | ⚠️ | ✅ | ✅ | ✅ | ⚠️ | ✅ |
| 23 | interwetten | Interwetten | G | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 24 | comeon | ComeOn | M | ❌ | ❌ | ❌ | ❌ | — | ❌ | — |
| 25 | hajper | ComeOn | M | ❌ | ❌ | ❌ | ❌ | — | ❌ | — |
| 26 | lyllo | ComeOn | M | ❌ | ❌ | ❌ | ❌ | — | ❌ | — |
| 27 | snabbare | Snabbare | M | ❌ | ❌ | ❌ | ❌ | — | ❌ | — |
| 28 | 10bet | TenBet | M | ❌ | ❌ | ❌ | ❌ | — | ❌ | — |
| 29 | mrgreen | Spectate | M | ❌ | ❌ | ❌ | ❌ | — | ❌ | — |
| 30 | vbet | BetConstruct | M | ❌ | ❌ | ❌ | ❌ | — | ❌ | — |
| 31 | coolbet | Coolbet | M | ❌ | ❌ | ❌ | ❌ | — | ❌ | — |
| 32 | tipwin | Tipwin | M | ❌ | ❌ | ❌ | ❌ | — | ❌ | — |

---

## Cluster Deduplication

Providers sharing a platform have identical odds. Once a bet is placed on ANY provider in a cluster, that event+market is blocked across all siblings.

| Cluster | Members |
|---------|---------|
| `kambi` | unibet, leovegas, expekt, betmgm, speedybet, x3000, goldenbull, 1x2 |
| `spectate` | 888sport, mrgreen |
| `altenar_main` | betinia, campobet, lodur, quickcasino, swiper, dbet |
| `gecko_betsson` | betsson, nordicbet, betsafe, spelklubben |
| `comeon_group` | comeon, lyllo, hajper, snabbare |

**Standalone (no cluster):** pinnacle, polymarket, interwetten, 10bet, vbet, coolbet, tipwin, bethard

**How blocking works:**
1. PlayLoop partitions bets into per-cluster queues
2. When bet placed on provider X, `_block_event_market(event_id, market)` marks it across ALL queues
3. `_is_blocked()` checks before popping next bet — skips if already covered
4. Only "funded" providers (balance > 0, user-selected) get queue entries

---

## API Endpoint Patterns

Reference for interception wiring — discovered from browser traffic recordings.

```
# Altenar
POST  sb2betgateway-altenar2.biahosted.com/api/widget/placeWidget      # bet placement
POST  sb2bethistory-gateway-altenar2.biahosted.com/api/WidgetReports/widgetBetHistory  # history
GET   {domain}/sv/api/v3/account/balance                                # balance

# Gecko V2
POST  {domain}/api/sb/v2/coupons                                       # bet placement
GET   cloud-api.{domain}/wallets                                        # balance
GET   cloud-api.{domain}/player/payment-stats                           # deposit/withdraw

# Kambi
WS    push.aws.kambicdn.com                                             # all WS traffic (placement + live)
GET   {domain}/wallitt/mainbalance                                      # balance (Unibet pattern)

# Pinnacle
POST  api.arcadia.pinnacle.se/v1/bets/straight                         # bet placement
GET   api.arcadia.pinnacle.se/0.1/wallet/balance                       # balance

# Polymarket
GET   data-api.polymarket.com/value?user={proxy_wallet}                # portfolio value
GET   clob.polymarket.com/book?token_id={id}                           # order book
POST  clob.polymarket.com/order                                        # bet placement (SDK)

# Interwetten
POST  interwetten.se/.../placebet                                      # bet placement
GET   interwetten.se/.../refreshaccountbalance                         # balance (CSRF-aware AJAX)
```

---

## Adding a New Provider

### Phase 1: Discovery (before writing any code)

1. **Set language to English**, mute all notification overlays and cookie banners
2. **Open the site in mirror** — let the interceptor record all traffic to JSONL
3. **Log in manually** — note which API call returns balance (= login proof)
4. **Navigate to bet history** — note the API endpoint or DOM structure
5. **Navigate to an event** — note the URL pattern (IDs, slugs, query params)
6. **Place a small bet manually** — note the placement API endpoint + request/response body
7. **Check the JSONL recordings** for all captured endpoints:
   ```bash
   grep "provider-domain.com" data/mirror_recordings/mirror/*.jsonl | jq -r '.url' | sort -u
   ```

### Phase 2: Wire Interception

1. Add domain → provider_id to `browser.py:_detect_provider()`
2. Add balance/history/placement URL keywords to `_BALANCE_KEYWORDS`, `_HISTORY_KEYWORDS`, `_BET_PLACEMENT_KEYWORDS`
3. If provider uses WebSocket for placement (like Kambi), add to `_WS_MONITOR_KEYWORDS` + `_WS_BET_RECEIVED_KEYWORDS`
4. If balance JSON shape is non-standard, add extraction logic to `_extract_balance()`

### Phase 3: Implement Workflow

Either a **dedicated workflow class** (if platform has unique API/DOM patterns) or an **intel JSON** for GenericWorkflow.

**Dedicated workflow** (`firevsports/mirror/workflows/{platform}.py`):
```python
class PlatformWorkflow(BaseWorkflow):
    async def check_login(self, page) -> bool
    async def sync_balance(self, page) -> float
    async def sync_history(self, page) -> list[HistoryEntry]
    async def navigate_to_event(self, page, bet) -> bool
    async def prep_betslip(self, page, bet, stake) -> PlacementResult  # optional
    async def place_bet(self, page, bet, stake) -> PlacementResult
    async def find_tab(self, context) -> Page | None
    async def check_live_price(self, page, bet) -> tuple[float, float]  # optional
```

**GenericWorkflow** (`data/mirror_intel/{provider_id}.json`):
- Balance: API endpoint + JSON path, or DOM selector + regex
- History: API endpoint + field mapping, or DOM selectors
- Navigation: URL template with `{event_id}`
- Betslip: CSS selectors for odds buttons, stake input, confirm button
- Optional strategy override in `workflows/strategies/{provider_id}.py`

### Phase 4: Register & Test

1. Add provider to `get_workflow()` factory
2. Add to cluster map in `play_loop.py:_CLUSTER_MEMBERS` if sibling of existing platform
3. Test each method independently:
   - `GET /mirror/browser/provider/{id}` — login + balance
   - `GET /mirror/browser/test-settle/{id}` — sync_history raw output
4. Full flow test: login → settle → navigate → prep → place → record

---

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| Login timeout (120s) | Provider changed auth flow | Check `check_login()` — may need new DOM selector or API endpoint |
| Balance always 0 | JSON shape changed | Check `_extract_balance()` and `sync_balance()` — log raw response |
| Settlement matching fails | Team name normalization | Check `_token_overlap()` — may need alias in normalization |
| Navigation fails | Event ID format changed | Check `navigate_to_event()` — log URL being constructed |
| Interception misses placement | URL pattern changed | Check JSONL recordings for new endpoint, update `_BET_PLACEMENT_KEYWORDS` |
| Stale balance after placement | Interceptor didn't fire | Data stream polls every 30s as fallback; check domain detection |
| "Existing open position" skip | Kambi-only: open bet on same event | Expected behavior — prevents double exposure |
| Daily cap reached | 10 bets placed today | Expected for soft providers. Uncapped: pinnacle, polymarket, cloudbet |
| Cluster sibling blocked | Bet already placed on sibling | Expected — same odds across cluster, one bet covers the value |

---

## Background Processes

### PendingLoop (settlement sync)
- Polls every 60s
- For each provider with pending bets: find tab → check login → sync history → detect settlements → broadcast
- User must confirm settlements before they're recorded
- Runs independently of play loop

### DataStream (per-provider polling)
- Started on-demand via `POST /mirror/data-stream/start/{provider_id}`
- Staggered polls: balance 30s, positions 45s, history 60s
- Interceptor freshness window: skips poll if recent intercept (< 10s)
- History cache TTL: 90s (shared with ProviderRunner)

### Browser Interception (passive, always-on)
- Every HTTP response checked against keyword lists
- Provider detected from page URL (primary) or API domain (fallback)
- Balance, history, placement events broadcast via SSE
- WebSocket frames monitored for Kambi placement responses
