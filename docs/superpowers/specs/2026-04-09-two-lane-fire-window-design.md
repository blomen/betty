# Two-Lane Fire Window Design

**Date:** 2026-04-09
**Status:** Approved

## Problem

The current fire window is a single sequential flow: activate provider → settle → navigate → place → next provider. State is pull-based (frontend requests `/next-bet`, `/check-bet`), settlements are in-memory only (lost on restart), prices are fetched on-demand, and there's no persistent sync between browser interception and DB/frontend.

## Solution

Split the fire window into two side-by-side lanes with event-driven streaming:

- **Sync Lane (left)** — autonomous live dashboard streaming balance, pending bets, settlements, and notification mute status per provider. No user interaction except settlement confirmation.
- **Betting Lane (right)** — auto-navigates to next best bet, auto-fills stake, streams live prices. User confirms Place or Skip.

A new **Event Router** replaces fire-and-forget interceptor callbacks: every intercepted response is classified, persisted to DB, then broadcast via dedicated SSE channels.

## Architecture

### Event Router

Sits between the interceptor and the rest of the system:

```
interceptor._on_response()
    ↓
EventRouter.route(provider_id, response)
    ↓ classify → balance | history | odds | bet_confirm | notification
    ↓ persist to DB (always first)
    ↓ broadcast to SSE channel
```

Replaces the current pattern where `MirrorService._handle_*()` methods do ad-hoc DB writes and SSE broadcasts. The router guarantees persist-then-broadcast ordering so nothing is lost.

### SSE Channels

Three dedicated channels replace the single `/api/extraction/stream` for mirror events:

**`GET /api/mirror/stream/sync`** — feeds the sync lane:
- `balance_update` — `{provider_id, amount, currency, ts}`
- `history_update` — `{provider_id, bets: [...], ts}`
- `settlement_pending` — `{provider_id, settlements: [...]}`
- `settlement_confirmed` — `{provider_id, applied: [...]}`
- `notification_status` — `{provider_id, email, sms, push}`
- `provider_state` — `{provider_id, state: synced|syncing|queued}`

**`GET /api/mirror/stream/prices`** — feeds the betting lane:
- `price_update` — `{provider_id, event_id, market, outcome, odds, ts}`
- `price_verified` — `{bet_id, dom_odds, api_odds, match: bool}`
- `edge_update` — `{bet_id, new_edge, fair_odds}`

**`GET /api/mirror/stream/actions`** — betting lane status:
- `navigated` — `{bet_id, event_url, ts}`
- `autofilled` — `{bet_id, stake, odds_in_slip, ts}`
- `bet_placed` — `{bet_id, confirmation, ts}`
- `bet_skipped` — `{bet_id, reason, ts}`

### Bootstrap Endpoints (reconnect / late-join)

Frontend calls these on mount, then switches to SSE for live updates:

- `GET /api/mirror/state/{provider_id}` → `{balance, pending_bets, pending_settlements, notification_status}`
- `GET /api/mirror/prices/{provider_id}` → `[{event_id, market, outcome, odds, age_seconds}]`
- `GET /api/mirror/queue` → `[{provider_id, state, bets_remaining, pre_sync_progress}]`

### New DB Tables

**`balance_log`** — append-only balance history:
```sql
id              SERIAL PRIMARY KEY
provider_id     VARCHAR NOT NULL
amount          DECIMAL NOT NULL
currency        VARCHAR(3) NOT NULL
source          VARCHAR NOT NULL  -- 'intercepted' | 'api_fetch'
created_at      TIMESTAMPTZ DEFAULT now()
```
Latest row per provider = current balance.

**`settlement_queue`** — persistent settlement tracking:
```sql
id              SERIAL PRIMARY KEY
provider_id     VARCHAR NOT NULL
bet_id          INTEGER REFERENCES bets(id)
result          VARCHAR NOT NULL  -- 'won' | 'lost' | 'void'
payout          DECIMAL NOT NULL
status          VARCHAR NOT NULL DEFAULT 'pending'  -- 'pending' | 'confirmed'
detected_at     TIMESTAMPTZ DEFAULT now()
confirmed_at    TIMESTAMPTZ
```
Survives restarts. User confirms → status='confirmed' → bankroll updated.

**`price_cache`** — live price ticks:
```sql
id              SERIAL PRIMARY KEY
provider_id     VARCHAR NOT NULL
event_id        INTEGER REFERENCES events(id)
market          VARCHAR NOT NULL
outcome         VARCHAR NOT NULL
odds            DECIMAL NOT NULL
source          VARCHAR NOT NULL  -- 'intercepted' | 'dom' | 'api'
updated_at      TIMESTAMPTZ DEFAULT now()
```
Upsert on each tick. Stale after 30s.

## Sync Lane (Left Panel)

Autonomous live stream — no buttons except settlement confirm.

### What it shows
- **Balance** — live-updating from intercepted API responses, with last-update timestamp
- **Pending bets** — list of open bets with matchup, odds, stake
- **Settlement gate** — pending settlements with win/loss amounts, "Confirm All" button. Only interactive element in this lane. Once confirmed, bankroll is updated in DB.
- **Notification status** — email/SMS/push mute status per provider (auto-muted on activation)
- **Provider queue** — all providers with status: active (green), pre-syncing (blue), queued (gray)

### Autonomous behaviors
- Auto-syncs bet history on provider activation
- Auto-syncs balance continuously from intercepted responses
- Auto-mutes email/SMS/push notifications in provider account settings
- Pre-syncs next 1-2 providers in queue while user bets on current provider
- Quick-refresh on actual activation (in case pre-synced data is stale)

### Settlement flow
1. Interceptor detects settled bets → EventRouter persists to `settlement_queue` (status=pending)
2. SSE broadcasts `settlement_pending` → sync lane shows pending settlements
3. User clicks "Confirm All" → `POST /api/mirror/settlements/confirm`
4. Backend updates `settlement_queue` status=confirmed, applies to bankroll
5. SSE broadcasts `settlement_confirmed` → sync lane updates, betting lane recalculates Kelly stakes

## Betting Lane (Right Panel)

Auto-navigates and auto-fills — user confirms Place or Skip.

### What it shows
- **Current bet** — matchup, league, date, market, outcome, odds, fair odds, edge, stake, Kelly %, TTK
- **Live price stream** — DOM price vs intercepted API price vs Pinnacle fair. Continuously verified. Shows green checkmark when DOM matches API.
- **Navigation + autofill status** — checkmarks for: navigated to event, stake auto-filled, price verified
- **Place / Skip** — the only user input
- **Up next** — preview of remaining bets sorted by edge descending

### Price streaming
- Interceptor captures odds responses (HTTP for REST APIs, WebSocket frames for Kambi/Polymarket)
- EventRouter persists to `price_cache`, broadcasts `price_update`
- Frontend compares DOM odds (from autofill) with intercepted odds and Pinnacle fair
- If price drops below edge threshold during review → visual warning
- Edge recalculated live as prices change

### Bet flow
1. Auto-navigate to event URL via mirror workflow
2. SSE: `navigated {bet_id, event_url}`
3. Auto-fill stake in betslip via DOM automation
4. SSE: `autofilled {bet_id, stake, odds_in_slip}`
5. Price stream verifies DOM odds match intercepted odds
6. User reviews and clicks Place or Skip
7. Place → `POST /api/fire-window/place-bet/{bet_id}` → mirror places via workflow
8. SSE: `bet_placed` → sync lane shows new pending bet, balance updates
9. Auto-advance to next bet

### Cross-lane interaction
- Balance updates from sync lane feed into Kelly/stake recalculation in betting lane
- After placing, sync lane immediately reflects new pending bet + updated balance
- Settlement confirmations trigger stake recalculation for remaining bets

## Frontend

### Component tree
```
<FireWindowPage>
  <ProviderQueue />           — shared top bar: provider pills with status dots
  <SyncLane>                  — left panel, flex:1
    <BalanceStream />
    <PendingBets />
    <SettlementGate />        — confirm button
    <NotificationStatus />
  </SyncLane>
  <BettingLane>               — right panel, flex:1.2
    <CurrentBet />            — matchup, odds, edge, stake
    <PriceStream />           — live DOM vs API vs fair
    <BetActions />            — Place / Skip
    <UpNext />
  </BettingLane>
</FireWindowPage>
```

### Hooks

**`useSyncStream(providerId)`** — connects to `/api/mirror/stream/sync`, bootstraps via `GET /api/mirror/state/{providerId}`:
```ts
{
  balance: { amount: number, currency: string, updatedAt: Date },
  pendingBets: Bet[],
  settlements: Settlement[],
  notifications: { email: boolean, sms: boolean, push: boolean },
  connected: boolean
}
```

**`useProviderQueue()`** — bootstraps via `GET /api/mirror/queue`, updates from sync channel:
```ts
{
  providers: [{ id: string, state: string, betsLeft: number }],
  activeProvider: string,
  preSyncing: string[]
}
```

**`usePriceStream(providerId, betId)`** — connects to `/api/mirror/stream/prices`:
```ts
{
  domOdds: number,
  apiOdds: number,
  fairOdds: number,
  edge: number,
  priceMatch: boolean,
  lastUpdate: Date
}
```

**`useBettingLane(providerId)`** — connects to `/api/mirror/stream/actions`, exposes mutations:
```ts
{
  currentBet: BetDetails | null,
  upNext: BetDetails[],
  status: 'navigating' | 'filling' | 'ready' | 'placing',
  placeBet: () => Promise<void>,
  skipBet: () => Promise<void>
}
```

### Replaces
- `useBetMirror.ts` → split into the 4 hooks above
- `FireWindow.tsx` single-phase queue → `FireWindowPage` with two-lane layout
- `/api/fire-window/*` request-response endpoints remain for mutations (Place, Skip, Confirm), but all state flows through SSE streams
- Mirror events move from `/api/extraction/stream` to dedicated `/api/mirror/stream/*` channels

## Mirror Refactor Summary

### What changes
1. **EventRouter** (new) — classifies intercepted responses, persist-then-broadcast
2. **Interceptor** — captures odds tickers (not just bet responses), forwards all to EventRouter
3. **MirrorService** — delegates to EventRouter instead of ad-hoc handling
4. **SSE infrastructure** — 3 dedicated mirror channels with per-provider filtering
5. **DB** — 3 new tables (balance_log, settlement_queue, price_cache)
6. **Fire window API** — mutations stay REST, state moves to SSE bootstrap + streaming
7. **Workflows** — add notification muting capability, price ticker interception

### What stays the same
- Provider-specific workflow classes (Altenar, Pinnacle, Kambi, etc.)
- GenericWorkflow + discovery engine
- Mirror browser management (Playwright tabs, interceptor setup)
- Bet placement DOM automation
- Navigation to events

## Path to Full Autonomy

Current design is semi-autonomous (settlement confirmation gate). To reach full auto:
1. Validate settlement accuracy over N confirmed batches
2. Add confidence scoring to settlement detection
3. Remove confirmation gate when confidence > threshold
4. Settlement auto-applies to bankroll, SSE notifies frontend after the fact

Same pattern applies to betting lane if desired later — auto-place above edge threshold.
