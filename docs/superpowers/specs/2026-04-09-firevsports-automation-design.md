# FirevSports Automation Design

**Date:** 2026-04-09
**Status:** Approved

## Problem

The FirevSports local client has all 5 tabs working with live data from the server, but the Play and Pending pages are read-only. The mirror browser exists but isn't wired to the UI. Users need to manually navigate to each bet on each provider site, manually fill stakes, and manually check for settlements.

## Solution

Two parallel automation loops sharing a single Playwright browser:

- **Play Loop** — iterates through funded providers, auto-navigates to each bet, auto-fills stake, waits for user Place/Skip, records to server DB
- **Pending Loop** — iterates through providers with pending bets, opens history pages, detects settlements via interceptor, waits for user Confirm, records to server DB

Both loops communicate to the frontend via a single SSE stream (`/mirror/stream`).

## Architecture

```
Single Playwright Browser (headed, shared cookies)
├── Play Loop (event page tab per provider)
│   ├── Open provider site → detect login
│   ├── Navigate to event → autofill stake
│   ├── Wait for Place/Skip from UI
│   ├── Record bet to server DB via API proxy
│   └── Advance to next bet
│
├── Pending Loop (bet history tab per provider)
│   ├── Open bet history page
│   ├── Intercept history API → detect settlements
│   ├── Show in Pending tab → wait for Confirm
│   ├── Record settlement to server DB via API proxy
│   └── Sync balance
│
└── Interceptor (shared, runs on all tabs)
    ├── Captures bet placements → auto-record
    ├── Captures balance responses → update UI
    └── Captures history responses → detect settlements
```

## Play Loop

### Flow

1. User clicks "Start" on Play page → `POST /mirror/play/start`
2. Loop picks first funded provider (highest EV cluster first, matching Play page sort)
3. Opens provider tab if not already open, waits for login detection via interceptor
4. For each bet (sorted by edge desc within provider):
   a. Navigate to event URL via provider workflow (`workflow.navigate_to_event()`)
   b. Autofill stake in betslip via workflow (`workflow.place_bet()` prep step)
   c. Broadcast SSE: `bet_ready {provider_id, event_id, display_home, display_away, market, outcome, odds, fair_odds, stake, edge_pct}`
   d. Wait for user action via `POST /mirror/play/place` or `POST /mirror/play/skip`
   e. If Place: workflow confirms placement → interceptor captures bet confirmation
   f. Record to server DB: `POST /api/opportunities/play/settle-bet` with full bet info
   g. Broadcast SSE: `bet_placed {bet_id, actual_odds, actual_stake, confirmation_id}`
   h. Advance to next bet
5. When all bets for a provider are done → broadcast `provider_complete`, move to next
6. When all funded providers are done → broadcast `play_complete` with summary

### State Machine

```
idle → starting → provider_opening → login_waiting → navigating → filling → ready → placing → recording → navigating...
                                                                           → skipping → navigating...
provider_complete → provider_opening (next) | play_complete
```

### Error Handling

- Navigation fails → skip bet, broadcast `bet_skipped {reason: "nav_failed"}`
- Login not detected after 120s → skip provider, broadcast `provider_skipped {reason: "login_timeout"}`
- Placement fails → broadcast `bet_failed`, keep in ready state for retry
- User can stop at any time via `POST /mirror/play/stop`

## Pending Loop

### Flow

1. User clicks "Sync All" on Pending page → `POST /mirror/pending/start`
2. For each provider with pending bets in DB:
   a. Open bet history tab (or navigate existing tab to history URL)
   b. Interceptor captures history API response (HTTP interception)
   c. Parse response via provider workflow (`workflow.sync_history()`)
   d. Compare against pending bets in server DB
   e. For each settled bet detected: broadcast SSE `settlement_detected {provider_id, bet_id, result, payout}`
   f. UI shows settlements in Pending page with Confirm button
   g. User clicks Confirm → `POST /mirror/pending/confirm {provider_id}`
   h. Record to server DB: `POST /api/opportunities/play/settle-confirm`
   i. Broadcast SSE: `settlements_confirmed {provider_id, count}`
3. After all providers synced → sync balance for each
4. Loop repeats every 60s while running

### Balance Sync

When the interceptor captures a balance API response:
- Parse amount from response body
- Broadcast SSE: `balance_updated {provider_id, amount, currency}`
- Server DB is updated via: `POST /api/bankroll/set/{provider_id}` (existing endpoint)

## SSE Stream

Single endpoint: `GET /mirror/stream`

### Play Events
- `play_started` — loop began
- `provider_activated {provider_id, status}` — provider tab opened / login detected
- `provider_skipped {provider_id, reason}` — login timeout or no bets
- `bet_navigated {provider_id, event_id}` — navigated to event page
- `bet_ready {provider_id, ...full bet details}` — waiting for Place/Skip
- `bet_placed {bet_id, actual_odds, confirmation_id}` — bet confirmed
- `bet_skipped {bet_id, reason}` — bet skipped
- `bet_failed {bet_id, error}` — placement failed
- `provider_complete {provider_id, placed, skipped}` — all bets for provider done
- `play_complete {total_placed, total_skipped, total_ev}` — all providers done
- `play_stopped` — user stopped manually

### Pending Events
- `pending_started` — sync loop began
- `history_synced {provider_id, total_bets}` — history page loaded
- `settlement_detected {provider_id, bet_id, result, payout}` — settled bet found
- `settlements_confirmed {provider_id, count}` — user confirmed settlements
- `balance_updated {provider_id, amount, currency}` — balance intercepted
- `pending_stopped` — user stopped

## Local Endpoints

### Play Control
```
POST /mirror/play/start      — start automated play loop (body: {batch from play page})
POST /mirror/play/place      — confirm current bet (user clicks Place)
POST /mirror/play/skip       — skip current bet (user clicks Skip)
POST /mirror/play/stop       — stop the loop
GET  /mirror/play/status     — {state, current_bet, provider_progress, queue}
```

### Pending Control
```
POST /mirror/pending/start   — start pending sync loop
POST /mirror/pending/confirm — confirm settlements {provider_id}
POST /mirror/pending/stop    — stop the loop
GET  /mirror/pending/status  — {running, providers: [{id, last_sync, pending_count}]}
```

### SSE
```
GET  /mirror/stream          — single SSE stream for all mirror events
```

## DB Recording

All recording goes through the server API (via the proxy tunnel). No direct DB access from firevsports.

### Bet Placement Recording
```
POST /api/opportunities/play/settle-bet
{
  provider_id: string,
  event_id: string,
  market: string,
  outcome: string,
  point: float | null,
  odds: float,
  fair_odds: float,
  stake: float,
  confirmation_id: string | null,
  actual_odds: float | null,
  bet_type: "value"
}
```
Server creates a Bet row with all fields + behavioral tracking (hour, day, risk score).

### Settlement Recording
```
POST /api/opportunities/play/settle-confirm
{
  provider_id: string,
  settlements: [{bet_id: int, result: "won"|"lost"|"void", payout: float}]
}
```
Server updates Bet rows: result, payout, settled_at, settlement_source="mirror_auto".

## UI Changes

### Play Page

Add to top of page:
- **Start/Stop button** — toggles play loop
- **Status bar** — current state: "Navigating to Betinia..." / "Ready: Place or Skip" / "Placing..."
- **Current bet highlight** — the bet being placed is highlighted in the table with amber background
- **Provider progress** — "Betinia 3/10 ✓ · Quickcasino 0/8 · ..."
- **Place/Skip buttons** — appear when `bet_ready`, disappear after action

### Pending Page

Add to top of page:
- **Sync All button** — starts pending loop
- **Per-provider sync status** — "Last synced 30s ago" / "Syncing..." / "3 settlements found"
- **Confirm button per provider** — appears when settlements detected

## File Structure

### New files
- `firevsports/mirror/play_loop.py` — PlayLoop class: async generator that drives the betting flow
- `firevsports/mirror/pending_loop.py` — PendingLoop class: async loop that syncs history + detects settlements
- `firevsports/mirror/sse.py` — SSE broadcaster for mirror events

### Modified files
- `firevsports/mirror/router.py` — add play/pending/stream endpoints
- `firevsports/server.py` — wire SSE broadcaster
- `firevsports/frontend/src/pages/PlayPage.tsx` — add Start/Stop, status bar, Place/Skip, SSE listener
- `firevsports/frontend/src/pages/PendingPage.tsx` — add Sync All, per-provider status, Confirm, SSE listener
- `firevsports/frontend/src/hooks/useMirrorStream.ts` — SSE hook for `/mirror/stream`
