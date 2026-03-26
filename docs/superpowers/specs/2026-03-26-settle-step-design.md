# Settle Step — Play Page Step 0

**Date:** 2026-03-26
**Branch:** play-v3-session-manager

## Problem

The capital plan (Panel 1) calculates deposit/transfer/withdraw recommendations based on provider balances. But pending bets from previous sessions have stakes deducted without outcomes credited — making balances inaccurate until those bets are settled. The play page needs a settlement step before the capital plan runs.

## Design

### Play Page Flow Change

The play page becomes 4 steps:

```
Settle → Capital Plan → Batch → Execute
```

Default step: **Settle** (was Capital Plan).

### Mirror Lifecycle: Always-On, Lazy-Start

The mirror browser launches **once** on the first SSE connection from the frontend and stays running for the entire app session. No start/stop UI.

**Implementation:**
- Add a dedicated `POST /api/mirror/ensure-started` endpoint that the Play page calls on mount.
- The endpoint checks if a mirror is already running; if not, starts one. Idempotent.
- The mirror stays running until the backend shuts down (no stop trigger).
- A small status dot in the StepIndicator shows mirror health: green (connected) or gray (not running).

**Why lazy-start:** Starting the browser on backend boot would open a Playwright window even for headless extraction runs. Lazy-start ensures the browser only appears when someone is actually using the UI.

### Settle Panel (`SettlePanel.tsx`)

**Data source:** `GET /api/play/pending-bets` — returns all bets with `result = 'pending'`, grouped by provider.

**Layout:**
- Provider-grouped sections (collapsible, like ExecutionPanel)
- Each section header: provider name + pending count badge
- Per-bet row: event name, market/outcome, odds, stake, date placed
- Per-bet actions: **W** (won) / **L** (lost) / **V** (void) buttons — or auto-settled badge from mirror

**Auto-settlement flow:**
1. Mirror is already running (lazy-started on SSE connect)
2. User browses to a provider site in the mirror browser
3. Mirror intercepts bet history API response → `settlements_pending` SSE event fires
4. SettlePanel receives SSE event, matches bet_ids, marks those rows as auto-settled (green badge)
5. User confirms batch: `POST /api/mirror/settlements/confirm` (existing endpoint)
6. Rows disappear, balances update

**Manual settlement flow:**
1. User clicks W/L/V on a bet row
2. Frontend calls `POST /api/play/settle-bet` with `{bet_id, result, payout}`
3. Payout auto-calculated: won = `stake * odds`, lost = `0`, void = `stake`
4. Row updates to show settled state
5. Balance recalculated

**Navigation:**
- User can advance to Capital Plan at any time (button always available)
- If unsettled bets remain, the step indicator shows warning count: `Settle (3)`
- No blocking gate — user decides when they've settled enough

### Backend Changes

#### New endpoint: `GET /api/play/pending-bets`

Returns pending bets grouped by provider:

```json
{
  "providers": [
    {
      "provider_id": "unibet",
      "provider_name": "Unibet",
      "pending_count": 3,
      "total_stake": 450.0,
      "bets": [
        {
          "id": 142,
          "event_name": "Real Madrid vs Barcelona",
          "market": "1x2",
          "outcome": "home",
          "odds": 2.10,
          "stake": 150.0,
          "currency": "SEK",
          "placed_at": "2026-03-25T14:30:00Z"
        }
      ]
    }
  ],
  "total_pending": 8,
  "total_stake": 1200.0
}
```

Query: `SELECT * FROM bets WHERE result = 'pending' AND profile_id = :active_profile ORDER BY provider_id, placed_at`

Join with `events` table for `event_name` (fallback to `home_team vs away_team` from Event if bet has no event_name stored).

#### New endpoint: `POST /api/play/settle-bet`

Manual single-bet settlement.

```json
// Request
{ "bet_id": 142, "result": "won" }

// Response
{ "bet_id": 142, "result": "won", "payout": 315.0, "settled_at": "2026-03-26T10:00:00Z" }
```

Payout calculation:
- `won`: `stake * odds`
- `lost`: `0`
- `void`: `stake` (returned)

Delegates to existing `BetService.settle_bet(bet_id, result, payout)`.

#### Mirror lazy-start

Add to `backend/src/api/routes/mirror.py`:
- `POST /api/mirror/ensure-started` — idempotent endpoint. If no mirror is running, starts one. If already running, returns current status. Called by PlayPage on mount.
- Reuses existing `start_mirror()` logic but without the 400 error when already running.

### Frontend Changes

#### New: `SettlePanel.tsx`

Location: `frontend/src/components/Terminal/pages/play/SettlePanel.tsx`

Props:
```typescript
interface SettlePanelProps {
  onContinue: () => void;  // Advance to capital plan
}
```

State:
- Fetches `GET /api/play/pending-bets` via react-query
- Listens for `settlements_pending` SSE events via existing `useBetMirror` hook
- Tracks which bets have been manually settled (local state, optimistic updates)

UI structure:
```
┌─────────────────────────────────────────────────┐
│ Settle Pending Bets                    [3 left] │
├─────────────────────────────────────────────────┤
│ ▼ unibet (2)                                    │
│   Real Madrid vs Barcelona  1x2 H  2.10  150kr  │
│   [W] [L] [V]                         Mar 25    │
│                                                  │
│   Liverpool vs Arsenal      total O  1.85  200kr │
│   ✓ won — auto-settled via mirror               │
│                                                  │
│ ▼ betsson (1)                                    │
│   PSG vs Bayern  spread -1.5  1.90  100kr        │
│   [W] [L] [V]                         Mar 24    │
├─────────────────────────────────────────────────┤
│                          Continue to Capital → │
└─────────────────────────────────────────────────┘
```

#### Modified: `PlayPage.tsx`

- Step type: `'settle' | 'capital' | 'batch' | 'execute'`
- Default step: `'settle'`
- StepIndicator gets 4 steps, with pending count on Settle
- Mirror status dot: tiny green/gray circle in the step bar

#### Modified: `useBetMirror.ts`

Already handles `settlements_pending` SSE events. The SettlePanel will consume the `pendingSettlements` state from this hook to show auto-settled badges and enable batch confirm.

### SSE Event Flow

```
Mirror running (always-on after first UI connect)
    ↓
User visits unibet.se in mirror browser
    ↓
BetInterceptor fires on_bet_history → _stage_settlements_sync()
    ↓
settlements_pending SSE event → { provider, count, settlements: [...] }
    ↓
SettlePanel receives event, matches bet_ids to pending list
    ↓
Matched bets show green "auto-settled" badge with result
    ↓
User clicks "Confirm All" → POST /api/mirror/settlements/confirm
    ↓
Bets settled in DB, balances updated
    ↓
settlements_confirmed SSE event → SettlePanel refetches pending list
```

### What This Does NOT Change

- No new DB tables or columns
- No changes to batch_builder.py or capital plan logic
- No changes to ExecutionPanel or SessionBatchPanel
- Mirror parsers unchanged — existing Gecko/Altenar/Kambi parsers work as-is
- `BetService.settle_bet()` unchanged — reused for both manual and mirror settlement
