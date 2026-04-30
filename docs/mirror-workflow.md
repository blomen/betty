# Mirror Workflow — Canonical Reference

> Single source of truth for the entire mirror automation pipeline.
> Every provider follows the same flow: open tab → wait for login → settle pending → idle at the run gate → run the bet loop on user toggle → record placement.
>
> When wiring a new provider or auditing an existing one, walk this document top-to-bottom. The agent checklist in §12 is the acceptance criterion.

---

## 1. Overview

The **mirror** is a headed Playwright Chromium browser that runs locally on your PC. It intercepts network traffic (HTTP + WebSocket), automates navigation, drives a per-provider state machine that pauses at a user-controlled run gate, and records bets to the server DB via API proxy.

### Key files

| File | Purpose |
|------|---------|
| `arnold/mirror/browser.py` | Playwright lifecycle, network interception, provider detection |
| `arnold/mirror/play_loop.py` | Multi-provider coordinator, cluster queues, event blocking, run-gate dispatcher |
| `arnold/mirror/provider_runner.py` | Per-provider value-bet runner. Owns the canonical run-gate primitive |
| `arnold/mirror/arb_runner.py` | Per-provider arb runner. 1:1 mirror of the gate; preserves anchor/counter coordination |
| `arnold/mirror/pending_loop.py` | Background settlement sync (60s poll) |
| `arnold/mirror/data_stream.py` | Continuous per-provider polling (balance/positions/history) |
| `arnold/mirror/sse.py` | Server-Sent Events broadcaster to frontend |
| `arnold/mirror/router.py` | `/mirror/*` API endpoints (browser/play/run/pause/settlement) |
| `arnold/mirror/workflows/` | Per-platform workflow implementations |
| `arnold/frontend/src/pages/PlayPage.tsx` | UI: 5-state card, click handler, SSE ↔ card-color mapping |

---

## 2. The five card states (user-facing)

Each provider card on the Play page advances through five visual states. The card itself is the click region — no separate Run button.

```
                  user clicks idle card
   ┌─── IDLE ─────────────────────────────────────┐
   │                                              ▼
   │                                          TAB_OPEN  (blue)
   │  user re-clicks                           tab opened, awaiting login on
   │  ◀───────────────────────────────────       provider site
   │                                          ▼
   │                                    LOGGED_IN_SYNCING  (cyan)
   │                                          login detected; balance +
   │                                          pending settlement running
   │  user re-clicks                          ▼
   │  ◀───────────────────────────────────  READY_TO_RUN  (yellow + "Press to run" pill)
   │                                          settled, daily-cap OK,
   │                                          gate closed; passive sync
   │                                          continues every 60s/300s
   │  user clicks (toggle)                    ▼ user clicks
   │           ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─►  RUNNING  (green + "Running" pill)
   │           ◄─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ bet placement loop active
   ▼                                            ▼ stop / queue empty
  STOP / DESELECT  ──────────────────────────────┘
```

### Click semantics

| Card state | Click → |
|---|---|
| IDLE (unselected) | Open tab + start runner; advance to TAB_OPEN |
| TAB_OPEN | Deselect / close session |
| LOGGED_IN_SYNCING | Deselect / close session |
| READY_TO_RUN | Toggle gate open → RUNNING (`POST /mirror/play/run/{pid}`) |
| RUNNING | Toggle gate closed → READY_TO_RUN (`POST /mirror/play/pause/{pid}`); active bet auto-skips with `reason: "paused"` |

### Color palette

Tailwind utilities, sole owner of the active-state appearance (legacy `isLoggedIn → bg-green-700/50` was retired 2026-04-30):

| State | Class |
|---|---|
| IDLE | (existing zinc styling) |
| TAB_OPEN | `bg-blue-500/40 text-blue-100 border border-blue-400/60` |
| LOGGED_IN_SYNCING | `bg-cyan-500/40 text-cyan-100 border border-cyan-400/60` |
| READY_TO_RUN | `bg-yellow-500/50 text-yellow-100 border border-yellow-400/70` |
| RUNNING | `bg-emerald-600/50 text-emerald-100 border border-emerald-500/70` |

Source of truth: `arnold/frontend/src/pages/PlayPage.tsx` `CARD_STATE_CLASSES`.

---

## 3. Backend runner state machine

Every per-provider runner — `ProviderRunner` (value bets) and `ArbRunner` (arbs) — follows this state machine. One asyncio task per active provider, spawned by `PlayLoop`.

```
PROVIDER_OPENING
   │ workflow.find_tab(context)  (retry 10x, 1s spacing)
   ▼
LOGIN_WAITING
   │ poll workflow.check_login(page) every LOGIN_POLL_INTERVAL=5s
   │ timeout LOGIN_TIMEOUT=120s → exit with provider_skipped
   ▼
SETTLING
   │ workflow.sync_history(page) → _detect_settlements() → broadcast
   │ record unknown bets to DB
   ▼
(daily-cap check)
   │ if placed_today >= DAILY_BET_CAP and pid not UNCAPPED → exit
   ▼
READY_TO_RUN  ─── gated ───
   │ broadcast `provider_ready`
   │ spawn _ready_sync_task (passive balance + pending refresh)
   │ await self._run_event.wait()    ← user clicks yellow card to release
   │ cancel _ready_sync_task in finally
   │ broadcast `provider_running`
   ▼
BET LOOP ─── per-iteration ─────────────────────────┐
   │  if not _run_event.is_set():                   │
   │     ↳ go back to READY_TO_RUN block            │
   │  pop bet from cluster queue                     │
   │  workflow.navigate_to_event(page, bet)          │
   │  state = NAVIGATING                             │
   │  workflow.prep_betslip(page, bet, stake)        │
   │  workflow.check_live_price(page, bet)           │
   │  if live_edge < 0: bet_skipped, continue        │
   │  state = READY                                  │
   │  if not _run_event.is_set():                    │  ← paused mid-flight: auto-skip
   │     ↳ bet_skipped reason='paused'               │
   │     ↳ continue (gate-check re-parks runner)     │
   │  await intercept OR skip event                  │
   │  state = PLACING → record → DB                  │
   └─────────────────────────────────────────────────┘
   ▼ queue drained
exit (provider_complete)
```

### Key invariants

- `_run_event` is an `asyncio.Event`, default cleared. Set → release gate. Clear → next iteration parks back at READY_TO_RUN.
- The pause path during NAVIGATING/PLACING is critical: when the runner transitions to `STATE_READY` waiting for user Place/Skip, it FIRST checks `_run_event`. If cleared, it auto-skips with `reason="paused"` and `continue`s — the next iteration's gate-check then parks the runner. **Without this check the runner sits forever on Place/Skip after a pause.**
- `stop()` calls `_task.cancel()` only — it does NOT set `_run_event`. Cancellation propagates through `await self._run_event.wait()` cleanly. Setting `_run_event` in `stop()` would create a race where the runner briefly proceeds past the gate before cancellation lands.
- The `_ready_sync_task` MUST be cancelled in a `finally` block on every gate exit, including `stop()`, to avoid leaked tasks.
- `_detect_pending` writes `state = STATE_SETTLING` and broadcasts settling SSE events. While at READY_TO_RUN, the passive sync task must restore `state = STATE_READY_TO_RUN` AND **re-broadcast `provider_ready`** after the periodic refresh, so the card snaps back to yellow instead of getting stuck on cyan.
- `set_run(False)` while at `STATE_READY` ALSO sets `_skip_event` (in `ProviderRunner` only) so the wait on Place/Skip wakes up. ArbRunner's anchor wait is `_anchor_event`; pause-mid-anchor is intentionally handled at the next iteration boundary, not the current anchor wait.

### Code references

- `arnold/mirror/play_loop.py` — `STATE_*` constants, `PlayLoop.set_run` dispatcher.
- `arnold/mirror/provider_runner.py` — value-bet runner. Canonical `_await_run_gate`, `_ready_sync_loop`, paused-state auto-skip.
- `arnold/mirror/arb_runner.py` — arb runner. 1:1 mirror of the gate; preserves anchor/counter coordination.
- `arnold/mirror/router.py` — `POST /mirror/play/run/{pid}` and `POST /mirror/play/pause/{pid}`.

---

## 4. SSE event contract (frontend ↔ backend)

The runner emits these events. The frontend's `mirror.lastEvent` handler in `PlayPage.tsx` maps them to `loopProviderStatus[pid].state`, which `deriveCardState` reads to pick the card color.

| Event | Payload | Card state set |
|---|---|---|
| `provider_opening` | `{provider_id}` | `tab_open` (blue) |
| `login_waiting` | `{provider_id}` | `tab_open` (blue) |
| `login_detected` | `{provider_id}` | `tab_open` (still blue until settling kicks in) |
| `settling_pending` / `settling_done` | `{provider_id, …}` | `logged_in_syncing` (cyan) |
| `provider_ready` | `{provider_id, state: "ready_to_run", placed_today, daily_cap, [mode: "arb"]}` | `ready_to_run` (yellow) |
| `provider_running` | `{provider_id, [mode: "arb"]}` | `running` (green) |
| `bet_navigating` / `bet_ready` / `bet_placed` / `bet_skipped` / `bet_failed` | `{provider_id, bet, …}` | `running` (green) |
| `provider_complete` / `provider_skipped` | `{provider_id, reason}` | resets to `idle` |
| `settlements_detected` / `settlements_confirmed` / `unknown_bets_recorded` | `{provider_id, …}` | (no state change) |
| `stake_limited` / `bet_error` | `{provider_id, …}` | (no state change) |
| `runner_stale_intel` | `{provider_id, consecutive_hard_fails, hint}` | (no state change; UI warns user) |

**Special-case:** `bet_skipped` with `reason: "paused"` MUST NOT update card state. It's the auto-skip emitted when the user pauses during navigation — the immediately-following `provider_ready` event drives the cyan→yellow transition.

---

## 5. Per-provider workflow class contract

Every provider implements a `Workflow` subclass at `arnold/mirror/workflows/<platform>.py`. The runner calls these methods at the corresponding state-machine step. **Match the method signatures exactly** — the runner is provider-agnostic.

```python
class Workflow:
    domain: str        # e.g. "polymarket.com" — used by find_tab and _detect_provider
    home_url: str      # landing URL when opening a fresh tab

    async def find_tab(self, context) -> Page | None:
        """Locate an open tab matching self.domain. Return deepest URL match."""

    async def check_login(self, page) -> bool:
        """Return True iff user is authenticated. May call API or scrape DOM."""

    async def sync_balance(self, page) -> float:
        """Return current available balance in provider's native currency.
        Run automatically on login."""

    async def fetch_balance(self, page) -> float | None:
        """OPTIONAL — used by _ready_sync_loop. If present, called every
        READY_BALANCE_SYNC_INTERVAL_S=60s while the runner is at READY_TO_RUN.
        Workflows without it skip background balance refresh; live interception
        still works whenever the user interacts with the provider site."""

    async def sync_history(self, page) -> list[HistoryEntry]:
        """Return all open + recently-settled bets from the provider.
        Source of truth for settlement matching."""

    async def navigate_to_event(self, page, bet) -> bool:
        """Navigate the page to bet's event. Return True on success."""

    async def prep_betslip(self, page, bet, stake) -> PlacementResult:
        """Pre-fill the slip: select outcome, set stake.
        For autonomous-API providers (Pinnacle, Polymarket SDK) this may be a no-op."""

    async def check_live_price(self, page, bet) -> tuple[float, float] | None:
        """Read the live odds currently on the slip. Return (odds, edge_pct)
        or None if unavailable."""

    async def place_bet(self, page, bet, stake) -> PlacementResult:
        """Autonomous placement (API providers). Two-phase providers leave this
        as a no-op and rely on user-click → interceptor instead."""

    def parse_placement_response(self, body) -> int | None: ...
    def parse_placement_status(self, body) -> dict: ...
    def parse_placement_details(self, body) -> dict: ...

    def cache_event_details(self, event_id, body) -> None:  # OPTIONAL
        """Seeded by network interceptor so navigate_to_event can short-circuit."""
```

`HistoryEntry` and `PlacementResult` dataclasses live in `arnold/mirror/workflows/base.py`.

---

## 6. Provider classes

### Soft books (Kambi, Altenar, Gecko V2, ComeOn, Spectate, Interwetten, …)

| Trait | Value |
|---|---|
| Daily bet cap | 10 (`DAILY_BET_CAP`) |
| Eligible play modes | Value (ProviderRunner) AND Arb (ArbRunner) |
| Arb role | Anchor only; counter pool excludes cluster siblings |
| Gate semantics | Standard (yellow → green = run, green → yellow = pause) |
| Counter while yellow | YES — a yellow soft book auto-fires hedges when its anchor isn't this provider |

Soft books cluster: siblings share an odds engine and produce identical odds, so a single placement blocks all cluster members (`_block_event_market`).

### Sharp source (Pinnacle)

| Trait | Value |
|---|---|
| Daily bet cap | UNCAPPED (`UNLIMITED_PROVIDERS`) |
| Eligible play modes | Value only — never the anchor of an arb |
| Arb role | Counter only (sharp source — used to hedge soft anchors) |
| Gate semantics | Standard |
| Counter while yellow | YES — yellow Pinnacle still hedges other providers' anchors |

Pinnacle is in `UNLIMITED_PROVIDERS = {"pinnacle", "polymarket", "cloudbet", "kalshi"}` because daily caps don't apply, but its placement role is value-bet-only (sharp odds means edge against itself is always zero).

### Unlimited / playable sharp-adjacent (Polymarket, Cloudbet, Kalshi)

| Trait | Value |
|---|---|
| Daily bet cap | UNCAPPED |
| Eligible play modes | Value (ProviderRunner) AND Arb (ArbRunner anchor or counter) |
| Arb role | Anchor or counter |
| Gate semantics | Standard |
| Counter while yellow | YES |
| Auto-activate on login | YES — `PlayPage.tsx` polls each unlimited provider every 5s; on detected login + positive balance + positive-edge bets in batch, automatically adds to `activeProviders`. The gate still holds at yellow — auto-activation does NOT auto-press Run. |

The auto-activate behavior is unique to unlimited providers because they have no soft cluster and benefit from being permanently online. Soft books require manual selection because the user typically picks 2-3 funded clusters per session.

### Counter participation invariant

**A yellow (READY_TO_RUN) provider continues to serve as an arb counter when another provider's anchor fires.**

The gate only controls the runner's **own** placement loop. Counter-bet routing in `play_loop.on_bet_intercepted` walks all runners looking for one in `STATE_AWAITING_HEDGES` and forwards regardless of `_run_event`. A counter at READY_TO_RUN simply has its slip pre-loaded by the anchor's `_load_all_legs` and waits for the user to click Place inside the counter tab — neither of which crosses the run-gate.

Mental model: **Run = "this provider can place bets from its own queue"**. Yellow still allows hedging triggered elsewhere because the anchor's Run was authorization for the multi-leg arb as a whole.

---

## 7. The 8-step provider checklist

Below: the per-step implementation surface. Use alongside §12 for end-to-end verification.

### Step 1 — Interception wiring

Wire the browser's network interceptor to recognize this provider's traffic.

| Task | Where | Details |
|------|-------|---------|
| Balance URL pattern | `browser.py` `_BALANCE_KEYWORDS` | e.g. `account/balance`, `/wallets`, `mainbalance` |
| History URL pattern | `browser.py` `_HISTORY_KEYWORDS` | e.g. `bethistory`, `coupon-history`, `mybets` |
| Bet placement URL pattern | `browser.py` `_BET_PLACEMENT_KEYWORDS` | e.g. `placewidget`, `/coupons`, `bets/straight` |
| WebSocket keywords (if applicable) | `browser.py` `_WS_MONITOR_KEYWORDS` | e.g. `kambi`, `push.aws` — for WS-based placement |
| Domain → provider_id mapping | `browser.py` `_detect_provider()` | Map all brand domains to their provider_id |
| Balance JSON extraction | `browser.py` `_extract_balance()` | Handle non-standard JSON shapes (nested wallets, arrays, etc.) |

**Current keyword sets:**
```python
_BALANCE_KEYWORDS = ("account/balance", "/wallets", "mainbalance", "wallet/balance", "payment-stats", "/cashier/balance")
_HISTORY_KEYWORDS = ("bethistory", "bet-history", "mybets", "my-bets", "widgetbethistory", "coupon-history")
_BET_PLACEMENT_KEYWORDS = ("placewidget", "placebet", "/coupons", "bets/straight", "bets/parlay", "bets/place", "clob.polymarket.com/order")
```

### Step 2 — Open site & await login

User clicks the provider's idle card → `startSkin(pid)` → `POST /mirror/open-provider-tab` → runner spawned via `PlayLoop`.

| Task | Method | Details |
|------|--------|---------|
| Find existing tab | `workflow.find_tab(context)` | Searches `context.pages` for matching domain; returns deepest URL match |
| Open new tab | `POST /mirror/open-provider-tab` | Opens provider domain in fresh tab if no existing tab |
| Detect login | `workflow.check_login(page)` | Returns `True` when user is authenticated |
| Login timeout | `LOGIN_TIMEOUT=120s` | Polls every `LOGIN_POLL_INTERVAL=5s`, skips provider if timeout |

**Login detection methods by platform:**

| Platform | Method | How |
|----------|--------|-----|
| Pinnacle | API balance fetch | `GET /0.1/wallet/balance` — retry 3x |
| Altenar | Authed API + wallet | `GET /api/v3/account/balance` |
| Kambi | REST or GraphQL | `GET /wallitt/mainbalance` (Unibet) or GraphQL relay (LeoVegas) |
| Gecko V2 | API wallets | `GET /wallets` — any currency response = logged in |
| Polymarket | API + DOM | SDK balance call OR DOM scrape "Cash $" text |
| Interwetten | CSRF-aware AJAX + DOM | `Common.AjaxCall('refreshaccountbalance')` OR `#acc-balance` element |
| Generic | Strategy-driven | Intel JSON: balance API endpoint or DOM indicator selector |

### Step 3 — Sync balance → DB + backend

| Task | Method | Details |
|------|--------|---------|
| Sync balance | `workflow.sync_balance(page)` | Returns `float` — current available balance |
| Auto-post on interception | `router._on_browser_event()` | Intercepted balance → `POST /api/bankroll/set/{provider_id}` |
| Cache in data stream | `ProviderDataStream._poll_balance()` | Polls every 30s; skips if interceptor delivered within 10s |
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

### Step 4 — Settle pending → record unknown

**Settlement MUST complete before any bet placement.** The provider's bet history is the source of truth.

| Task | Method | Details |
|------|--------|---------|
| Fetch pending from DB | `GET /api/opportunities/play/pending-bets` | `{provider_id: [bets]}` |
| Sync provider history | `workflow.sync_history(page)` | `list[HistoryEntry]` — all open + settled |
| Match pending vs history | `_detect_settlements()` | Three-tier fuzzy matching |
| Broadcast for review | SSE `settlements_detected` | UI shows toast |
| User confirms | UI callback | User reviews and clicks confirm |
| Record to DB | `POST /api/opportunities/play/settle-batch` | Batch-records confirmed settlements |
| Record unknown bets | Auto | Open bets in history not in DB → recorded as unknown |

**Three-tier settlement matching:**

| Tier | Criteria | Tolerance |
|------|----------|-----------|
| **1 — Exact ID** | `provider_bet_id` matches | Exact string match |
| **2 — Name + Odds** | Event name fuzzy match (`token_overlap ≥ 0.5`) + odds | 5% odds tolerance |
| **3 — Fuzzy** | Odds + stake (when name match fails) | 10% odds + 30% stake tolerance |

Each history entry matched at most once (`used_history` set).

```python
HistoryEntry(
    provider_bet_id: str,
    event_name: str,         # "Team A vs Team B"
    market: str,             # "1x2", "spread", "total"
    outcome: str,            # "1", "X", "2", "over", "under"
    odds: float,
    stake: float,
    status: str,             # "won" | "lost" | "void" | "cashout" | "pending"
    payout: float,           # 0 if lost
)
```

**History sync methods by platform:**

| Platform | Method |
|----------|--------|
| Pinnacle | DOM text scrape (regex) + API fallback `/bets?status=settled` |
| Altenar | Authed API `/api/v3/history` |
| Kambi | CDN `/coupon/history.json` (intercepts auth token) + KSP fallback |
| Gecko V2 | API coupon-history after navigating to history page |
| Polymarket | Data API `/trades` (fuzzy match against DB) + DOM history fallback |
| Interwetten | DOM journal `/journal/bets` → flex-table parsing |
| Generic | Strategy override + API/DOM intel-driven extraction |

### Step 5 — Wait at the run gate (READY_TO_RUN)

Runner sits at `STATE_READY_TO_RUN`. Card is yellow with "Press to run" pill. `_ready_sync_task` runs in the background:
- Every `READY_BALANCE_SYNC_INTERVAL_S = 60` — call `workflow.fetch_balance(page)` if defined.
- Every `READY_PENDING_SYNC_INTERVAL_S = 300` — call `workflow.sync_history` via `_detect_pending`. After completion, restore `state = STATE_READY_TO_RUN` and **re-broadcast `provider_ready`** so the card snaps back to yellow.

User clicks yellow card → `POST /mirror/play/run/{pid}` → `play_loop.set_run(pid, True)` → `runner.set_run(True)` → `_run_event.set()` → bet loop starts.

### Step 6 — Navigate → highest-edge event

| Task | Method | Details |
|------|--------|---------|
| Pop from cluster queue | `pop_bet()` | Sorted by edge %, highest first |
| Check cluster blocking | `_is_blocked(event_id, market)` | No duplicate event+market across cluster siblings |
| Check daily cap | `DAILY_BET_CAP=10` | Per provider per day. Uncapped: pinnacle, polymarket, cloudbet, kalshi |
| Navigate to event | `workflow.navigate_to_event(page, bet)` | Platform-specific URL/API |
| Detect closed event | DOM text scan | "closed", "stängd", "avbruten", … |

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

### Step 7 — Sync odds → confirm edge

| Task | Method | Details |
|------|--------|---------|
| Auto-select outcome | `workflow.prep_betslip(page, bet, stake)` | Platform-specific |
| Read live odds | `workflow.check_live_price(page, bet)` | `(odds, edge%)` |
| Compute live edge | `live_edge = (live_odds / fair_odds - 1) × 100` | Fair odds from Pinnacle devig |
| Auto-skip if -EV | `live_edge < 0` | Broadcasts `bet_skipped` with reason |
| Fill stake | Kelly-sized, capped to balance | Max stake ≤ available balance |
| Broadcast ready | SSE `bet_ready` | UI shows Place/Skip buttons |

**Prep / live-price methods:**

| Platform | Prep method | Live price |
|----------|-------------|------------|
| Pinnacle | None (API placement) | API markets, American→Decimal conversion |
| Altenar | `WSDK.toggleSelections([oddId])` | Cached GetEventDetails interceptor |
| Kambi | `isolatedBetslip.addOutcomeIds([id])` | DOM `.mod-KambiBC-betslip-outcome__odds` |
| Gecko V2 | None (manual) | Not implemented |
| Polymarket | SDK order build OR DOM click + fill | CLOB orderbook API or DOM button text |
| Interwetten | DOM click outcome by data-betting ID + fill stake | DOM `_find_outcome_element()` → parse odds |
| Generic | None (guided manual) | Strategy-driven or None |

### Step 8 — Await user place → intercept → record

User clicks the provider's "Place Bet" button. The interceptor catches it.

| Task | Method | Details |
|------|--------|---------|
| Wait for signal | `asyncio.wait([intercept_event, skip_event])` | First completed wins |
| Intercept placement | `browser._on_response()` or `_on_websocket()` | Catches request + response body |
| Parse response | `workflow.parse_placement_response(body)` | Extract provider_bet_id, actual_odds, actual_stake |
| Check stake limitation | `actual_stake < 0.9 * requested` | Broadcasts `stake_limited` warning |
| Record to DB | `POST /api/bets` | Full bet record |
| Sync balance | Auto | Re-reads balance after placement |
| Block cluster siblings | `_block_event_market(event_id, market)` | Prevents duplicate exposure |

**Placement types:**

| Type | Providers | How |
|------|-----------|-----|
| **Autonomous API** | Pinnacle, Polymarket | `workflow.place_bet()` calls API directly on user confirm |
| **Two-phase semi-auto** | Altenar, Kambi, Interwetten | `prep_betslip()` selects outcome, user clicks confirm on site |
| **Manual** | Gecko V2, Generic | User navigates + fills betslip entirely; interceptor catches |

**Interception patterns:**

| Platform | HTTP/WS | URL pattern | Response fields |
|----------|---------|-------------|-----------------|
| Pinnacle | HTTP POST | `bets/straight` | `{betId, odds, stake}` |
| Altenar | HTTP POST | `placewidget`, `placebet` | `{data: {betId}}` or `{bets: [{id}]}` |
| Kambi | WebSocket | `kambi`, `push.aws` frames | `{couponId, placeBetResult}` |
| Gecko V2 | HTTP POST | `/coupons` | `{couponId}` |
| Polymarket | HTTP POST | `clob.polymarket.com/order` | SDK handles (no HTTP interception) |
| Interwetten | HTTP POST | `placebet` | DOM confirmation (betslip clears) |

After placement → recorded to DB → PendingLoop picks up → runner pops next bet from cluster queue → return to Step 6. Queue empty → `provider_complete`.

---

## 8. Pseudo-code: the canonical happy path

Same logic for value and arb runners; bet-loop bodies differ only in the inner placement block.

```python
async def _run(self):
    self.state = PROVIDER_OPENING
    page = await retry(workflow.find_tab, max=10, sleep=1)
    if page is None: emit("provider_skipped", "no_tab"); return

    self.state = LOGIN_WAITING
    emit("login_waiting")
    if not await wait_for_login(timeout=120, poll=5):
        emit("provider_skipped", "login_timeout"); return

    self.state = SETTLING
    await detect_pending(workflow, page)   # broadcasts settling_*
    if pid not in UNCAPPED and placed_today >= DAILY_CAP:
        emit("provider_complete", "daily cap"); return

    await self._await_run_gate(workflow, page)   # blocks at yellow

    while True:
        if not self._run_event.is_set():
            await self._await_run_gate(workflow, page)   # repark on pause

        if pid not in UNCAPPED and placed_today >= DAILY_CAP:
            emit("provider_complete", "daily cap"); break

        bet = pop_bet_from_cluster_queue()
        if bet is None: idle_wait_or_break(); continue
        if is_blocked(bet): continue

        self.state = NAVIGATING
        if not await workflow.navigate_to_event(page, bet):
            emit("bet_skipped", "navigation_failed"); continue

        if await is_event_closed(page):
            emit("bet_skipped", "event_closed"); continue

        prep, live_odds, live_edge = await prep_and_read_live(bet, workflow, page)
        if prep.failed:
            emit("bet_skipped", prep.reason); continue
        if live_edge < 0:
            emit("bet_skipped", "negative_ev"); continue

        self.state = READY
        if not self._run_event.is_set():     # paused while we were navigating
            emit("bet_skipped", reason="paused")
            stats["skipped"] += 1
            cleanup_slip_stream()
            continue                          # next iter parks at gate

        # await user click on provider site → interceptor catches → place
        await asyncio.wait([_bet_intercepted_event, _skip_event], FIRST_COMPLETED)
        # ...record placement, block siblings, sync balance...
```

```python
async def _await_run_gate(self, workflow, page):
    self.state = READY_TO_RUN
    emit("provider_ready", placed_today, daily_cap)
    self._ready_sync_task = create_task(_ready_sync_loop(workflow, page))
    try:
        await self._run_event.wait()
    finally:
        cancel_and_await(self._ready_sync_task)
        self._ready_sync_task = None
    emit("provider_running")
```

```python
async def _ready_sync_loop(self, workflow, page):
    last_balance = 0; last_pending = 0
    while True:
        now = time.monotonic()
        if now - last_balance >= 60.0:
            try: await workflow.fetch_balance(page)   # only if hasattr
            except: pass
            last_balance = now
        if now - last_pending >= 300.0:
            try:
                await detect_pending(workflow, page)   # writes SETTLING + emits cyan
                if not self._run_event.is_set():       # gate still closed?
                    self.state = READY_TO_RUN
                    emit("provider_ready", ...)        # re-emit yellow
            except: pass
            last_pending = now
        await asyncio.sleep(5.0)
```

```python
def set_run(self, run: bool) -> bool:
    if run:
        if self._run_event.is_set(): return False
        self._run_event.set()
        return True
    else:
        if not self._run_event.is_set(): return False
        self._run_event.clear()
        if self.state == STATE_READY:        # mid-Place/Skip wait → wake up
            self._skip_event.set()
        return True
```

---

## 9. Provider capability matrix

Current wiring status as of 2026-04-30. **Legend:** ✅ working, ⚠️ partial/needs testing, ❌ not wired, A=autonomous, G=guided, M=manual

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
| 33 | cloudbet | Cloudbet | A | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 34 | kalshi | Kalshi | A | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

---

## 10. Cluster deduplication

Providers sharing a platform have identical odds. Once a bet is placed on ANY provider in a cluster, that event+market is blocked across all siblings.

| Cluster | Members |
|---------|---------|
| `kambi` | unibet, leovegas, expekt, betmgm, speedybet, x3000, goldenbull, 1x2 |
| `spectate` | 888sport, mrgreen |
| `altenar_main` | betinia, campobet, lodur, quickcasino, swiper, dbet |
| `gecko_betsson` | betsson, nordicbet, betsafe, spelklubben |
| `comeon_group` | comeon, lyllo, hajper, snabbare |

**Standalone (no cluster):** pinnacle, polymarket, kalshi, cloudbet, interwetten, 10bet, vbet, coolbet, tipwin, bethard

How blocking works:
1. PlayLoop partitions bets into per-cluster queues.
2. When bet placed on provider X, `_block_event_market(event_id, market)` marks it across ALL queues.
3. `_is_blocked()` checks before popping next bet — skips if already covered.
4. Only "funded" providers (balance > 0, user-selected) get queue entries.

---

## 11. API endpoint reference

Discovered from JSONL traffic recordings.

```
# Altenar
POST  sb2betgateway-altenar2.biahosted.com/api/widget/placeWidget                    # placement
POST  sb2bethistory-gateway-altenar2.biahosted.com/api/WidgetReports/widgetBetHistory # history
GET   {domain}/sv/api/v3/account/balance                                              # balance

# Gecko V2
POST  {domain}/api/sb/v2/coupons                                                      # placement
GET   cloud-api.{domain}/wallets                                                       # balance
GET   cloud-api.{domain}/player/payment-stats                                          # deposit/withdraw

# Kambi
WS    push.aws.kambicdn.com                                                           # all WS traffic
GET   {domain}/wallitt/mainbalance                                                    # balance (Unibet)

# Pinnacle
POST  api.arcadia.pinnacle.se/v1/bets/straight                                        # placement
GET   api.arcadia.pinnacle.se/0.1/wallet/balance                                      # balance

# Polymarket
GET   data-api.polymarket.com/value?user={proxy_wallet}                              # portfolio value
GET   clob.polymarket.com/book?token_id={id}                                         # order book
POST  clob.polymarket.com/order                                                       # placement (SDK)

# Interwetten
POST  interwetten.se/.../placebet                                                     # placement
GET   interwetten.se/.../refreshaccountbalance                                        # balance (CSRF AJAX)
```

---

## 12. Agent checklist (acceptance criterion)

When wiring or auditing a provider, walk this list. Each row produces a measurable artifact (test pass, log line, or visual check). **If any item fails, do NOT mark the provider "wired."**

| # | Item | Verify by |
|---|---|---|
| 1 | Workflow class implements all required methods (§5) | `grep` for method definitions in `workflows/<platform>.py` |
| 2 | `domain` and `home_url` set | Manual read |
| 3 | Network interceptor recognizes balance / history / placement URLs | §7 Step 1 |
| 4 | `find_tab` returns the tab on a fresh launch | Open `arnold.bat` → click card → log shows tab found |
| 5 | `check_login` polls and returns True after manual login | Card transitions blue → cyan within 5s of login |
| 6 | `sync_balance` produces the expected float | `_post_balance_async` log line; balance shows in `BalanceCell` |
| 7 | `sync_history` returns HistoryEntry list with all status values populated | `_detect_settlements` log line; existing pending bets clear |
| 8 | Daily cap applies (if soft) or is bypassed (if UNCAPPED) | First 10 placements work for soft; 11th hits cap |
| 9 | **Card reaches yellow with "Press to run" pill** | Visual — this is THE acceptance criterion |
| 10 | Click yellow → `POST /mirror/play/run/{pid}` returns 200 | Browser DevTools network tab |
| 11 | Card turns green; bet appears with Place/Skip | Visual |
| 12 | Click green → bet auto-skips with reason="paused"; card returns to yellow | Visual + SSE stream |
| 13 | Card stays yellow during 5-min `_detect_pending` refresh (no flap to cyan) | Wait 6 minutes; observe card |
| 14 | `navigate_to_event` lands on the right slip | Visual |
| 15 | `prep_betslip` selects the outcome and fills stake | Visual |
| 16 | `check_live_price` returns (odds, edge%) close to batch values | Console log; auto-skip fires when -EV |
| 17 | Manual click Place on provider site → `bet_placed` SSE → DB record | `/api/bets` log + DB query |
| 18 | (Soft only) Cluster siblings blocked after placement | Try same event on sibling — blocked |
| 19 | (Sharp/unlimited) Auto-activate on login + positive edge | Log into Pinnacle/Polymarket; card advances without manual click |
| 20 | (Arb) Yellow runner serves as counter when another provider anchors | Run anchor on provider A; counter fires on yellow B |

---

## 13. Adding a new provider

### Phase 1 — Discovery (before writing any code)

1. **Set language to English**, mute all notification overlays and cookie banners.
2. **Open the site in mirror** — let the interceptor record all traffic to JSONL.
3. **Log in manually** — note which API call returns balance (= login proof).
4. **Navigate to bet history** — note the API endpoint or DOM structure.
5. **Navigate to an event** — note the URL pattern (IDs, slugs, query params).
6. **Place a small bet manually** — note the placement API endpoint + request/response body.
7. **Check the JSONL recordings** for all captured endpoints:
   ```bash
   grep "provider-domain.com" data/mirror_recordings/mirror/*.jsonl | jq -r '.url' | sort -u
   ```

### Phase 2 — Wire interception (§7 Step 1)

1. Add domain → provider_id to `browser.py:_detect_provider()`.
2. Add balance/history/placement URL keywords.
3. If WebSocket placement (Kambi-like), add to `_WS_MONITOR_KEYWORDS` + `_WS_BET_RECEIVED_KEYWORDS`.
4. Non-standard balance JSON → `_extract_balance()`.

### Phase 3 — Implement workflow

Either a **dedicated workflow class** (unique platform) or an **intel JSON** for `GenericWorkflow`.

**Dedicated** (`arnold/mirror/workflows/{platform}.py`):
```python
class PlatformWorkflow(BaseWorkflow):
    domain = "..."
    home_url = "..."
    async def find_tab(self, context) -> Page | None
    async def check_login(self, page) -> bool
    async def sync_balance(self, page) -> float
    async def fetch_balance(self, page) -> float | None  # optional, for ready-state sync
    async def sync_history(self, page) -> list[HistoryEntry]
    async def navigate_to_event(self, page, bet) -> bool
    async def prep_betslip(self, page, bet, stake) -> PlacementResult  # optional
    async def check_live_price(self, page, bet) -> tuple[float, float] | None  # optional
    async def place_bet(self, page, bet, stake) -> PlacementResult  # optional (autonomous only)
```

**GenericWorkflow** (`data/mirror_intel/{provider_id}.json`):
- Balance: API endpoint + JSON path, or DOM selector + regex
- History: API endpoint + field mapping, or DOM selectors
- Navigation: URL template with `{event_id}`
- Betslip: CSS selectors for odds buttons, stake input, confirm button
- Optional strategy override at `workflows/strategies/{provider_id}.py`

### Phase 4 — Register & test

1. Add provider to `get_workflow()` factory in `workflows/__init__.py`.
2. Add to cluster map in `play_loop.py:_CLUSTER_MEMBERS` if sibling of existing platform.
3. Test each method independently:
   - `GET /mirror/browser/provider/{id}` — login + balance
   - `GET /mirror/browser/test-settle/{id}` — `sync_history` raw output
4. Walk the §12 agent checklist end-to-end.

---

## 14. Background processes

### PendingLoop (settlement sync)
- Polls every 60s.
- Per provider with pending bets: find tab → check login → sync history → detect settlements → broadcast.
- User must confirm settlements before they're recorded.
- Runs independently of play loop.

### DataStream (per-provider continuous polling)
- Started on-demand via `POST /mirror/data-stream/start/{provider_id}`.
- Staggered polls: balance 30s, positions 45s, history 60s.
- Interceptor freshness window: skips poll if recent intercept (< 10s).
- History cache TTL: 90s (shared with ProviderRunner).

### Ready-state passive sync (per-runner)
- Active only while runner is at `STATE_READY_TO_RUN` (yellow card).
- Spawned by `_await_run_gate`, cancelled in `finally` on gate release or `stop()`.
- Balance refresh: every `READY_BALANCE_SYNC_INTERVAL_S = 60`s (no-op if `workflow.fetch_balance` not defined).
- Pending refresh: every `READY_PENDING_SYNC_INTERVAL_S = 300`s. After completion, re-emits `provider_ready` so the card snaps back to yellow from the cyan flash.

### Browser interception (passive, always-on)
- Every HTTP response checked against keyword lists.
- Provider detected from page URL (primary) or API domain (fallback).
- Balance, history, placement events broadcast via SSE.
- WebSocket frames monitored for Kambi placement responses.

---

## 15. Common pitfalls

1. **Forgetting the paused-state auto-skip in the bet loop.** Without it, pause during NAVIGATING leaves the runner stuck at STATE_READY waiting for Place/Skip forever. Always re-check `_run_event` immediately after `self.state = STATE_READY`.
2. **Setting `_run_event` in `stop()`.** Causes a race where the runner proceeds past the gate before `_task.cancel()` lands. Rely on cancel propagation alone.
3. **Not re-emitting `provider_ready` after `_detect_pending` in the ready-sync loop.** Causes the card to flap cyan ↔ yellow every 5 minutes during idle.
4. **Frontend SSE handler missing `provider_ready` / `provider_running`.** Card never reaches yellow. Verify the handler in `PlayPage.tsx` has both event types in its dispatch list.
5. **Two coexisting color systems on the card** (legacy `isLoggedIn → bg-green-700/50` + new gate palette). One must own the active state. As of 2026-04-30 the gate palette is the sole owner; idle keeps the legacy zinc styling.
6. **Asymmetric ProviderRunner vs ArbRunner gate code.** The two runners must use identical `_await_run_gate` / `_ready_sync_loop` patterns. Drift here causes hard-to-diagnose user-visible asymmetry between Sports and Arb sub-tabs.
7. **`set_run(False)` not waking a runner waiting on Place/Skip at STATE_READY.** ProviderRunner sets `_skip_event`. ArbRunner intentionally does NOT — pause-mid-anchor unwinds at the next iteration boundary.
8. **`hasattr(workflow, "fetch_balance")` returning False** — graceful degradation, not a bug. The interceptor still picks up balance changes whenever the user touches the provider site. Don't add `fetch_balance` stubs that 404 — leave it absent and rely on the interceptor.
9. **Stale `arnoldsports/mirror/...` paths in code/docs.** The project was renamed (firev → arnold, arnoldsports/ collapsed into arnold/) on 2026-04-23/24. All paths now live under `arnold/mirror/`.

---

## 16. Where to look when debugging

| Symptom | First place to look |
|---|---|
| Card stuck on cyan | `PlayPage.tsx` SSE event handler — does it include `provider_ready`? |
| Card flaps cyan ↔ yellow every 5 min | `_ready_sync_loop` — is the post-`_detect_pending` re-emit of `provider_ready` present? |
| Click on yellow does nothing / 409 | `play_loop.set_run(pid, True)` — does the runner actually exist for that pid? Check `/mirror/play/status` |
| Card stays green after pause | The bet-loop `if not _run_event.is_set()` re-check at iteration top — is it the FIRST thing in the loop body? |
| Pause during NAVIGATING leaves runner stuck | Paused-state auto-skip at `state = STATE_READY` — present and `continue`s? |
| Counter doesn't fire when yellow | `play_loop.on_bet_intercepted` should NOT check `_run_event` — only routes by `STATE_AWAITING_HEDGES` |
| Provider stuck at LOGIN_WAITING | `workflow.check_login` returning False — strategy intel JSON or DOM selector mismatch |
| Settlement stale during yellow idle | `_ready_sync_loop` — `READY_PENDING_SYNC_INTERVAL_S = 300s` is by design |
| Login timeout (120s) | Provider changed auth flow → check `check_login()` |
| Balance always 0 | JSON shape changed → check `_extract_balance()` and `sync_balance()`; log raw response |
| Settlement matching fails | Team name normalization — check `_token_overlap()`; alias may be missing |
| Navigation fails | Event ID format changed → log URL being constructed |
| Interception misses placement | URL pattern changed → check JSONL recordings, update `_BET_PLACEMENT_KEYWORDS` |
| Stale balance after placement | Interceptor didn't fire; data stream polls every 30s as fallback; check domain detection |
| "Existing open position" skip | Kambi-only: open bet on same event — expected behavior |
| Cluster sibling blocked | Bet already placed on sibling — expected, same odds across cluster |

---

## 17. Reference commit history (run-gate landing)

| Commit | What it shipped |
|---|---|
| 85a777d0 | STATE_READY_TO_RUN constant + PlayLoop.set_run helper |
| 32acd0d5 | ProviderRunner gate (initial) |
| 9144e234 | Gate hardening: paused-state auto-skip, `_await_run_gate` helper, `time.monotonic`, race-guarded state restore |
| b77ac178 | ArbRunner gate mirroring ProviderRunner |
| 2b816390 | `/play/run` and `/play/pause` endpoints |
| d1a8af07 | Run-gate state-machine unit tests |
| d6c2a8fc | `runProvider` / `pauseProvider` API stubs |
| ca139098 | PlayPage card-state mapping + click handler + initial palette |
| af05c8fd | Frontend SSE handler wired to `provider_ready` / `provider_running`; ready-sync re-emits yellow |
| 1423ef0d | Dropped legacy active/loggedIn coloring; new palette owns active states |

Walk these in order if context-loading the full feature.
