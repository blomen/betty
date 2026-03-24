# Play v3: Session Manager Design

**Date:** 2026-03-24
**Status:** Draft
**Builds on:** Play v2 (2026-03-24-play-v2-unified-batch-design.md), BatchBuilder (`backend/src/services/batch_builder.py`)

## Problem

Play v2 shows the batch and balance info in a flat layout. But running a session has a natural order: first make sure capital is deployed where the opportunities are, then calculate the batch, then execute site by site. The current page doesn't enforce or guide this flow. Capital recommendations exist but are disconnected from the batch — you see "missed bets" after the fact instead of solving the funding gap before building.

Additionally, bonus wagering bets aren't separated — they're regular soft +EV bets that happen to also progress wagering. The batch should reflect this (inline context, not a separate tier).

## Strategy

Restructure the Play page into a **3-panel stacked layout** that guides the session flow top to bottom:

1. **Capital Plan** — opportunity-driven deposit/transfer/withdraw recommendations
2. **Session Batch** — all playable bets with updated balances
3. **Execution** — bets grouped by provider as a manual checklist

### Core principles (unchanged from v2)

1. Polymarket first, Pinnacle second, soft third — no limiting risk on sharps
2. Fire everything +EV — no artificial caps
3. Accounts are expendable — extract max value
4. One copy per event per cluster — round-robin siblings
5. Balance optimization — capital plan solves funding gaps before batch calc
6. Bonus wagering is inline — soft bets that also progress wagering, not a separate category

## Panel 1: Capital Plan

### Purpose

Scan all +EV opportunities, cross-reference with provider balances, and recommend capital actions to maximize the session's EV capture.

### Recommendation types

| Type | Condition | Priority |
|------|-----------|----------|
| **DEPOSIT (sharp)** | Polymarket/Pinnacle has +EV bets but insufficient balance | 1 (always fund) |
| **DEPOSIT (bonus)** | Provider finished current bonus, has spare balance or new bonus available, wagering deadline achievable | 2 |
| **DEPOSIT (new)** | Unfunded provider/cluster has +EV opportunities worth deploying to | 3 |
| **TRANSFER** | Provider A in cluster has excess, provider B in same cluster has shortfall | 4 |
| **WITHDRAW** | Dormant provider with balance but zero opportunities | 5 (optional, dimmed) |

### Recommendation columns

- Action (DEPOSIT / TRANSFER / WITHDRAW)
- Provider (+ "new" tag for unfunded, bonus info if available)
- Amount
- Unlocks (bet count)
- Avg Edge
- Expected +EV
- Status (pending → done, user marks manually)

### Bonus pipeline logic

When deciding whether to recommend a new bonus deposit:

1. Check if provider has a claimable bonus offer
2. Calculate wagering requirement
3. **Derive wagering feasibility from bet history**: query `bets` table for average wagering volume per session (avg total stake placed per day over last 14 days). If `wagering_requirement / avg_daily_wager > days_until_deadline`, mark as infeasible and skip.
4. If feasible and expected bonus value (bonus amount × expected retention rate) exceeds deposit cost, recommend

### Capital plan summary bar

- Net capital needed (sum of deposits - withdrawals)
- Bets unlocked
- Expected +EV gain from deploying

### Interactions

- **Dismiss individual** — remove a recommendation (optional, dimmed)
- **Dismiss All** — collapse the panel, proceed with current balances
- **Mark Done** — user marks each action after doing it manually on the provider site
- **Confirm & Recalc** — updates balances in DB for "done" items, triggers batch rebuild

### Backend

Extend existing `BatchBuilder._build_capital_plan()` to:
- Include sharp provider shortfalls (Polymarket USDC, Pinnacle SEK) at priority 1
- Add bonus pipeline recommendations (new deposits to claim bonuses)
- Derive wagering feasibility from actual bet history instead of heuristic session estimates
- Return `capital_plan.actions[]` with action type, provider, amount, unlocks, avg_edge, expected_ev

New query: `ProfileRepo.get_avg_daily_wager(profile_id, lookback_days=14)` — average total stake per day from bets table.

## Panel 2: Session Batch

### Layout

Summary bar at top, then a table with three tiers:

```
POLYMARKET — N bets (always fires)
PINNACLE — N bets (reverse value)
SOFT VALUE — N bets across M providers (round-robin within clusters)
```

### Tier order

1. **Polymarket** — separate currency (USDC), fires every session
2. **Pinnacle** — reverse value vs market consensus, SEK
3. **Soft** — all soft providers, ranked by expected profit, round-robin within clusters

### Batch building changes

Current `BatchBuilder` uses two tiers (sharp/soft). Change to three explicit tiers:

```python
TIER_PRIORITY = {"polymarket": 2, "pinnacle": 1, "soft": 0}
```

Within each tier, sort by expected profit descending. For soft tier, apply **round-robin within clusters**: after placing a bet on Unibet (Kambi), the next Kambi-cluster bet goes to 888sport, then LeoVegas, etc. This distributes action to avoid triggering limits on any single provider.

### Round-robin implementation

After ranking soft candidates by expected profit:

1. Group candidates by `(cluster, event_id, market, outcome, point)` — dedup key
2. For each unique opportunity, collect all providers that offer it
3. Walk the ranked list. For each cluster, maintain a provider index that rotates through funded siblings
4. When allocating a bet, pick the next provider in rotation for that cluster (skip if balance insufficient, try next sibling)

### Inline bonus context

Providers with active bonuses show a small badge next to their name: `wager 62%`. The batch summary bar shows projected wagering progress for all active bonuses after this session fires.

Bonus wagering progress calculation:
- Current: `wagered_amount / wagering_requirement`
- Projected: `(wagered_amount + sum_of_stakes_in_batch_for_provider) / wagering_requirement`
- Show both in the bonus summary bar at bottom of batch panel

### Batch table columns

`#` | Event | Market | Outcome | Provider (+ cluster tag + wager badge) | Odds | Fair | Edge | Stake | × (remove)

### Interactions

- **Remove (×)** — drop bet, freed balance redistributes to lower-ranked bets, auto-recalc
- **Recalculate** — triggered by capital plan confirmation or manual button

## Panel 3: Execution

### Purpose

Once the batch is locked, regroup bets by provider for site-by-site manual placement. This is the "fire list."

### Layout

- **Progress bar** at top: `X / Y bets placed · Z / W providers done`
- **Provider sections** (collapsible accordion):
  - Polymarket (first, always)
  - Pinnacle (second)
  - Soft providers ordered by total EV descending
- Each provider section header shows: provider name, cluster tag, wager badge if active, bet count, total stake, total EV, status (pending/in-progress/done)
- Expanded section shows the bet table for that provider
- **Per-bet checkoff** — circle → checkmark when placed
- **Mark All Done** button per provider

### Provider ordering within soft

Sort by total expected profit for that provider (descending). Provider with most EV goes first — you capture the most valuable bets earliest in case you run out of time.

### Session summary bar (bottom)

- Staked so far / total
- EV captured / total
- Time elapsed since session start

### Interactions

- Click provider header to expand/collapse
- Click bet circle to mark placed
- "Mark All Done" per provider
- Future: mirror auto-marks bets as placed when it detects the bet placement on the provider site

### Data flow

The execution panel reads from the same batch data as Panel 2 but re-groups by provider. No separate API call — just a frontend view transformation. Bet placement status is tracked in local state (not persisted until the session is complete).

When all bets for a provider are marked done, the provider section collapses and shows a green checkmark.

## Backend Changes

### Modified files

1. **`backend/src/services/batch_builder.py`**
   - Change `TIER_PRIORITY` to three tiers: polymarket, pinnacle, soft
   - Add round-robin allocation for soft tier
   - Extend `_build_capital_plan()` with sharp shortfall detection and bonus pipeline
   - Add `_compute_wagering_projections()` for inline bonus progress
   - Derive wagering feasibility from bet history

2. **`backend/src/repositories/profile_repo.py`**
   - Add `get_avg_daily_wager(profile_id, lookback_days=14)` method

3. **`backend/src/api/routes/opportunities.py`**
   - `POST /api/play/batch` — returns extended capital_plan with action items
   - `POST /api/play/confirm-capital` — accepts list of completed capital actions, updates balances, returns rebuilt batch
   - Existing `POST /api/play/fire` unchanged

### New API: confirm-capital

```
POST /api/play/confirm-capital
Body: {
  "actions": [
    {"type": "deposit", "provider_id": "kambi", "amount": 2000},
    {"type": "transfer", "from_provider_id": "888sport", "to_provider_id": "unibet", "amount": 1500},
    {"type": "withdraw", "provider_id": "spectate", "amount": 3200}
  ]
}
Response: rebuilds batch with updated balances, returns same shape as POST /api/play/batch
```

## Frontend Changes

### Modified files

1. **`frontend/src/components/Terminal/pages/PlayPage.tsx`**
   - Replace current layout with 3-panel stacked design
   - Panel 1: CapitalPlanPanel component
   - Panel 2: SessionBatchPanel component
   - Panel 3: ExecutionPanel component
   - Local state for capital action statuses and bet placement checkoffs

2. **`frontend/src/services/api/opportunities.ts`**
   - Add `confirmCapital(actions)` API call
   - Existing `getPlayBatch()` and batch fire calls unchanged

3. **`frontend/src/types/index.ts`**
   - Add `CapitalAction`, `CapitalPlan`, `ExecutionState` types
   - Extend `BatchBet` with `wagering_pct` field

### Component breakdown

```
PlayPage
├── CapitalPlanPanel
│   ├── CapitalActionRow (per recommendation)
│   └── CapitalSummaryBar
├── SessionBatchPanel
│   ├── BatchSummaryBar
│   ├── TierSection (polymarket / pinnacle / soft)
│   │   └── BatchBetRow
│   └── WageringSummaryBar
└── ExecutionPanel
    ├── ExecutionProgressBar
    ├── ProviderSection (collapsible)
    │   ├── ProviderHeader
    │   └── ExecutionBetRow (with checkoff)
    └── SessionSummaryBar
```

## Migration from v2

- The existing `BatchBuilder` is preserved and extended (not rewritten)
- The existing `POST /api/play/batch` endpoint returns the same shape plus extended capital_plan
- The existing `POST /api/play/fire` endpoint is unchanged
- PlayPage.tsx is rewritten (the current v2 UI is replaced entirely)
- No DB schema changes required — all new data is derived from existing tables
