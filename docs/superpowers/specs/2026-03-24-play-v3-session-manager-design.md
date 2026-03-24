# Play v3: Session Manager Design

**Date:** 2026-03-24
**Status:** Draft
**Builds on:** Play v2 (2026-03-24-play-v2-unified-batch-design.md), BatchBuilder (`backend/src/services/batch_builder.py`)

## Problem

Play v2 shows the batch and balance info in a flat layout. But running a session has a natural order: first make sure capital is deployed where the opportunities are, then calculate the batch, then execute site by site. The current page doesn't enforce or guide this flow. Capital recommendations exist but are disconnected from the batch — you see "missed bets" after the fact instead of solving the funding gap before building.

Additionally, bonus wagering bets aren't separated — they're regular soft +EV bets that happen to also progress wagering. The batch should reflect this (inline context, not a separate tier).

## Strategy

Restructure the Play page into a **3-panel stacked layout** that guides the session flow top to bottom:

1. **Capital Plan** — opportunity-driven deposit/withdraw recommendations
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
| **DEPOSIT (bonus)** | Provider has active bonus with achievable wagering deadline | 2 |
| **DEPOSIT (new)** | Unfunded provider/cluster has +EV opportunities worth deploying to | 3 |
| **WITHDRAW** | Dormant provider with balance but zero opportunities | 4 (optional, dimmed) |

**No TRANSFER type.** You can't transfer between bookmakers — they're separate accounts. If money needs to move from provider A to provider B, that's a WITHDRAW from A and a DEPOSIT to B, shown as two separate actions.

### Recommendation columns

- Action (DEPOSIT / WITHDRAW)
- Provider (+ "new" tag for unfunded, bonus info if available)
- Amount
- Unlocks (bet count)
- Avg Edge
- Expected +EV
- Status (pending → done, user marks manually)

### Currency handling

Summary bars show currency-separated totals: "Net: 5,000 kr + 200 USDC". Polymarket recommendations are always in USDC, everything else in SEK. Never mix currencies in a single total.

### Bonus pipeline logic

Scoped to **active bonuses only** (data already in `ProfileProviderBonus` table). We do NOT track "claimable but not yet activated" bonus offers — those are managed manually.

When deciding whether to recommend funding a provider with an active bonus:

1. Check `ProfileProviderBonus` for active bonus with wagering remaining
2. Calculate wagering feasibility from bet history:
   - Query `get_avg_daily_wager(profile_id, lookback_days=14)` — average total stake per day from `bets` table
   - **Edge cases:** If fewer than 3 days of history, fall back to heuristic (assume 1 session/day with avg stake = current balance). If avg_daily_wager is 0, mark as infeasible.
   - If `wagering_remaining / avg_daily_wager > days_until_deadline`, mark as infeasible and skip
3. If feasible, recommend deposit to ensure enough balance to keep wagering

### Capital plan summary bar

- Net capital needed: X kr + Y USDC (deposits - withdrawals, per currency)
- Bets unlocked
- Expected +EV gain from deploying

### Interactions

- **Dismiss individual** — remove a recommendation (dimmed)
- **Dismiss All** — collapse the panel, proceed with current balances
- **Mark Done** — user marks each action after doing it manually on the provider site
- **Confirm & Recalc** — updates balances in DB for "done" items, triggers batch rebuild

### Backend

Extend existing `BatchBuilder._build_capital_plan()` to:
- Include sharp provider shortfalls (Polymarket USDC, Pinnacle SEK) at priority 1
- Add bonus wagering recommendations for active bonuses (scoped to `ProfileProviderBonus` data)
- Derive wagering feasibility from actual bet history
- Return `capital_plan.actions[]` with action type, provider, amount, unlocks, avg_edge, expected_ev

New query: `ProfileRepo.get_avg_daily_wager(profile_id, lookback_days=14)` — average total stake per day from bets table. Must count all placed bets (settled + pending). Returns 0.0 with a `has_history: bool` flag so callers can distinguish "new profile" from "inactive profile."

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

### Batch building changes — tier labels

Current code uses `tier = "sharp"` for both Pinnacle and Polymarket (via `SHARP_PROVIDERS` set). Change to provider-specific tier strings:

```python
# Old
TIER_PRIORITY = {"sharp": 1, "soft": 0}
SHARP_PROVIDERS = frozenset({"pinnacle", "polymarket"})

# New
TIER_PRIORITY = {"polymarket": 2, "pinnacle": 1, "soft": 0}
```

**Migration checklist:**
- `_make_candidate()` line 441: change `tier = "sharp" if provider_id in SHARP_PROVIDERS` to assign `"polymarket"` or `"pinnacle"` based on provider_id
- `_build_summary()` line 562: produce three tier groups instead of two
- `_allocate_with_dedup()` line 524: update `bet.tier == "soft"` check — Polymarket and Pinnacle each use provider_id as cluster key (no cross-dedup between sharps)
- Frontend `BatchBet.tier` type: change from `'sharp' | 'soft'` to `'polymarket' | 'pinnacle' | 'soft'`
- Frontend PlayPage: update any `b.tier === 'sharp'` filters to check both `'polymarket'` and `'pinnacle'`

### Round-robin implementation

The current `_allocate_with_dedup` processes a globally-ranked list and deduplicates inline. For the soft tier, replace with a two-pass approach:

**Pass 1: Assign providers via round-robin**

```python
# After ranking soft candidates by expected_profit descending:
cluster_rotation: dict[str, Iterator[str]] = {}  # cluster → cycling iterator of funded provider_ids

for bet in soft_candidates_ranked:
    cluster = bet.cluster
    if cluster not in cluster_rotation:
        # Get all funded siblings in this cluster, sorted by balance desc
        siblings = [pid for pid, pb in provider_balances.items()
                    if pb.cluster == cluster and pb.remaining > 0]
        siblings.sort(key=lambda pid: -provider_balances[pid].remaining)
        cluster_rotation[cluster] = itertools.cycle(siblings)

    # Try each sibling in rotation until one has balance
    assigned = False
    for _ in range(len(siblings_for_cluster)):
        next_provider = next(cluster_rotation[cluster])
        pb = provider_balances[next_provider]
        if pb.remaining >= bet.stake:
            bet.provider_id = next_provider
            assigned = True
            break
    if not assigned:
        missed.append(bet)  # No sibling has enough balance
```

**Pass 2: Allocate balances**

Walk the assigned list, deduct balances, build final batch. This is the same as the existing allocation loop but with providers already assigned by round-robin.

Sharp tiers (Polymarket, Pinnacle) skip round-robin — they go through the existing direct allocation path unchanged.

### Inline bonus context

Providers with active bonuses show a small badge next to their name: `wager 62%`. The batch summary bar shows projected wagering progress for all active bonuses after this session fires.

Bonus wagering progress calculation:
- Current: `wagered_amount / wagering_requirement`
- Projected: `(wagered_amount + sum_of_stakes_in_batch_for_provider) / wagering_requirement`
- Show both in the bonus summary bar at bottom of batch panel

### Batch table columns

`#` | Event | Market | Outcome | Provider (+ cluster tag + wager badge) | Odds | Fair | Edge | Stake | × (remove)

### Boosts

Boosts are **excluded from the batch** (confirmed by commit `2786fd6`). The `_collect_boosts()` method in `BatchBuilder` is dead code and should be removed during implementation.

### Interactions

- **Remove (×)** — calls `POST /api/play/batch` with an `exclude` parameter (list of bet keys to skip). Server rebuilds the full batch minus excluded bets, redistributing freed balance to previously-missed bets. This is a full server-side recalc, not frontend-only filtering.
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

- Staked so far / total (per currency: SEK + USDC)
- EV captured / total
- Time elapsed since session start

### State persistence

Execution state (checkoffs, session start time) is persisted to **localStorage** keyed by batch hash. If the user refreshes or navigates away, progress is restored. localStorage entry is cleaned up after 24 hours or when a new batch is built.

### Interactions

- Click provider header to expand/collapse
- Click bet circle to mark placed
- "Mark All Done" per provider
- Future: mirror auto-marks bets as placed when it detects the bet placement on the provider site

### Data flow

The execution panel reads from the same batch data as Panel 2 but re-groups by provider. No separate API call — just a frontend view transformation.

When all bets for a provider are marked done, the provider section collapses and shows a green checkmark.

## Backend Changes

### Modified files

1. **`backend/src/services/batch_builder.py`**
   - Change `TIER_PRIORITY` to three tiers: polymarket, pinnacle, soft
   - Change `_make_candidate()` to assign provider-specific tier strings
   - Add round-robin allocation for soft tier (two-pass: assign providers, then allocate)
   - Extend `_build_capital_plan()` with sharp shortfall detection and active bonus recommendations
   - Add `_compute_wagering_projections()` for inline bonus progress
   - Derive wagering feasibility from bet history via `get_avg_daily_wager()`
   - Remove dead `_collect_boosts()` method
   - Add `exclude` parameter to `build()` for bet removal recalc

2. **`backend/src/repositories/profile_repo.py`**
   - Add `get_avg_daily_wager(profile_id, lookback_days=14)` → `{"avg_daily_wager": float, "has_history": bool, "days_with_bets": int}`

3. **`backend/src/api/routes/opportunities.py`**
   - `POST /api/play/batch` — returns extended capital_plan with action items. Accepts optional `exclude: list[str]` body param (bet keys to skip).
   - `POST /api/play/confirm-capital` — accepts list of completed capital actions, updates balances, returns rebuilt batch
   - Batch firing uses existing `createBatchBets` mutation (no separate `/play/fire` endpoint)

### New API: confirm-capital

```
POST /api/play/confirm-capital
Body: {
  "actions": [
    {"type": "deposit", "provider_id": "kambi", "amount": 2000},
    {"type": "withdraw", "provider_id": "spectate", "amount": 3200}
  ]
}
Response: rebuilds batch with updated balances, returns same shape as POST /api/play/batch
```

**Error handling:**
- 400: unknown provider_id
- 422: action would create negative balance
- Each action updates balance via existing `ProfileRepo.set_balance()` / `ProfileRepo.adjust_balance()`
- Balance updates are logged via existing audit mechanism in ProfileRepo

## Frontend Changes

### Modified files

1. **`frontend/src/components/Terminal/pages/PlayPage.tsx`**
   - Replace current layout with 3-panel stacked design
   - Import panel components from `play/` subdirectory

2. **`frontend/src/components/Terminal/pages/play/`** (new directory)
   - `CapitalPlanPanel.tsx` — capital recommendations table + summary bar
   - `SessionBatchPanel.tsx` — 3-tier batch table + wagering summary
   - `ExecutionPanel.tsx` — provider accordion + checkoff + progress

3. **`frontend/src/services/api/opportunities.ts`**
   - Add `confirmCapital(actions)` API call
   - Add `exclude` parameter to `getPlayBatch()`
   - Existing `createBatchBets()` call unchanged (used for firing)

4. **`frontend/src/types/index.ts`**
   - Update `BatchBet.tier` type to `'polymarket' | 'pinnacle' | 'soft'`
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
- Batch firing uses existing `createBatchBets` mutation — no new fire endpoint
- PlayPage.tsx is rewritten; panel components extracted to `pages/play/` subdirectory
- No DB schema changes required — all new data is derived from existing tables
- Dead code cleanup: remove `_collect_boosts()` from BatchBuilder
