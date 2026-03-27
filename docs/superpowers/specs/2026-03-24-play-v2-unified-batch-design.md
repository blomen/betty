# Play v2: Unified Batch System Design

**Date:** 2026-03-24
**Status:** Draft
**Builds on:** Play v1 (2026-03-24-play-system-design.md)

## Problem

Play v1 only shows soft value bets filtered to one provider at a time. Pinnacle reverse value and Polymarket opportunities live on separate tabs. The user must manually decide what to bet, where, and in what order. Balance sits idle on providers with no +EV bets while other providers have more bets than balance.

## Strategy

One unified batch across all opportunity types (soft value, Pinnacle reverse, Polymarket). The system builds the optimal batch, the user reviews and fires it all in one action.

### Core principles

1. **Pinnacle + Polymarket first** — no limiting risk, permanent balance, compound forever
2. **Fire everything +EV** — no artificial daily caps, no warm-up bets, no edge ceilings
3. **Accounts are expendable** — extract max value before limiting, rotate to next sibling
4. **One copy per event** — deduplicate across siblings in the same cluster
5. **Balance optimization** — show where money is needed vs wasted
6. **Batch confirm** — user reviews and clicks "Fire" (semi-auto, not full auto)

### Limiting stance

No conservative limiting rules. The only constraints are:
- **Balance** — can't bet what you don't have
- **Bonus phase min_odds** — bets below threshold don't count for wagering
- **Stake rounding** — use existing `round_stake_natural()` to avoid algorithmic-looking stakes
- **No warm-up bets** — fire +EV from bet #1

## Batch Builder

### Input

Collect all active +EV opportunities from three sources:

| Source | Type | Provider field | Edge source |
|--------|------|---------------|-------------|
| `opportunities` WHERE type='value' AND provider1_id NOT IN ('pinnacle','polymarket') | Soft value | `provider1_id` (soft book) | vs Pinnacle fair odds |
| `opportunities` WHERE type='reverse_value' AND provider1_id='pinnacle' | Pinnacle reverse | `pinnacle` | vs market consensus |
| Computed on-the-fly via `polymarket_routes.get_polymarket_value()` logic | Polymarket value | `polymarket` | vs Pinnacle fair odds |

**Note on Polymarket:** Polymarket opportunities are not stored in the `opportunities` table. They are computed at query time by comparing Polymarket odds against Pinnacle fair odds (see `backend/src/api/routes/polymarket.py`). The batch builder should call this same logic to collect Polymarket candidates.

### Deduplication

For soft value bets within the same cluster: if Plzen Under 4.5 exists on both spelklubben and betsson (both Gecko V2), include only the one on the provider with:
1. Highest balance (can place more bets before draining)
2. If tied, highest wagering urgency

### Ranking

Two tiers, each sorted by expected profit (`edge_pct / 100 * kelly_stake`):

```
Tier 1: Pinnacle + Polymarket (no limiting risk)
  - Sorted by expected profit descending

Tier 2: Soft value (expendable accounts)
  - Sorted by expected profit descending
```

### Stake sizing

- **Pinnacle/Polymarket:** Full Kelly (existing `StakeCalculator`)
- **Soft providers in wagering/trigger phase:** Kelly, filtered by bonus min_odds
- **Soft providers in playing/limited state:** Kelly, no restrictions
- **Single-shot trigger:** Fixed stake = trigger_amount
- **Freebet:** Fixed stake = freebet_amount, marked `is_bonus=true`
- **All stakes:** Rounded via `round_stake_natural()`

### Balance allocation

Walk down the ranked list. For each bet:
1. Check if provider has remaining balance >= stake
2. If yes: include in batch, decrement provider's remaining balance
3. If no: skip (but track as "missed" for balance optimization)

### Output

```python
{
  "batch": [
    {
      "rank": 1,
      "tier": "sharp",  # or "soft"
      "provider_id": "pinnacle",
      "event_id": "...",
      "market": "total",
      "outcome": "over",
      "point": 210.5,
      "odds": 2.15,
      "fair_odds": 2.00,
      "edge_pct": 7.5,
      "stake": 150,
      "expected_profit": 11.25,
      "is_bonus": false,
      "bonus_type": null,
      # event display fields
      "home_team": "Lakers",
      "away_team": "Celtics",
      "sport": "basketball",
      "league": "NBA",
      "starts_at": "2026-03-25T03:00:00Z",
    },
    # ... more bets
  ],
  "summary": {
    "total_bets": 24,
    "total_stake": 4200,
    "total_expected_profit": 380,
    "sharp_bets": 5,
    "soft_bets": 19,
  },
  "balance_status": [
    {
      "provider_id": "spelklubben",
      "cluster": "gecko_v2",
      "balance": 1100,
      "allocated": 490,
      "excess": 610,
    },
    {
      "provider_id": "betinia",
      "cluster": "altenar_main",
      "balance": 200,
      "needed": 1200,
      "shortfall": 1000,
      "missed_bets": 8,
      "missed_ev": 120,
    },
    # ...
  ],
  "missed_opportunities": {
    "total_bets": 8,
    "total_ev": 120,
    "reason": "insufficient_balance",
  }
}
```

## Balance Optimizer

After building the batch, compute balance imbalances:

For each provider:
- `allocated` = sum of stakes in batch for this provider
- `excess` = balance - allocated (if > 0)
- `shortfall` = allocated - balance (if balance < needed)
- `missed_bets` = number of bets skipped due to insufficient balance
- `missed_ev` = sum of expected profit from missed bets

Show actionable recommendations:
- "Deposit 1,000 kr on betinia to unlock 8 more bets (+120 kr EV)"
- "spelklubben has 610 kr excess after batch"

No auto-transfers — just recommendations. One fire mode:
- **"Fire playable"** — places all bets where balance is sufficient, skips shortfall providers
- To capture missed EV: deposit on shortfall providers, click "Build Batch" again, fire the new batch

## Session Flow (UI)

### Layout

```
┌────────────────────────────────────────────────────────────┐
│ Play v2                                     [Build Batch]  │
├────────────────────────────────────────────────────────────┤
│ BATCH: 24 bets │ 4,200 kr staked │ +380 kr EV             │
│ Sharp: 5 bets (+95 kr) │ Soft: 19 bets (+285 kr)          │
├────────────────────────────────────────────────────────────┤
│ TIER 1 — SHARP (Pinnacle + Polymarket)                     │
│ #  PROVIDER    EVENT            OUTCOME      ODDS EDGE STAKE│
│ 1  pinnacle    Lakers v Celts   Over 210.5   2.15 +7%  150 │
│ 2  polymarket  Trump wins MI    Yes          1.45 +12% 200 │
│ ...                                                         │
├────────────────────────────────────────────────────────────┤
│ TIER 2 — SOFT                                               │
│ #  PROVIDER      EVENT            OUTCOME      ODDS EDGE ST│
│ 6  spelklubben   Plzen v Sparta   Under 4.5    2.30 +25% 190│
│ 7  betinia       Madrid v Girona  Girona [1X2]  11.5 +16% 50│
│ ...                                                         │
├────────────────────────────────────────────────────────────┤
│ BALANCE                                                     │
│ ✓ pinnacle      3,200 → 2,750 (450 allocated)              │
│ ✓ spelklubben   1,100 → 610   (490 allocated)              │
│ ✗ betinia         200 → needs 1,000 more (8 bets, +120 EV) │
│                                                             │
│ [Fire playable (16 bets, +260 EV)]  [Rebuild]  [Copy deposits]│
└────────────────────────────────────────────────────────────┘
```

### Interactions

- **"Build Batch"** — re-fetches opportunities and rebuilds (or auto-builds on page load)
- **"Fire playable"** — places all bets with sufficient balance via `createBatchBets` API
- **Remove bet** — click X on any row to exclude from batch (recalculates)
- **"Copy deposit list"** — copies shortfall providers + amounts to clipboard
- After firing: show results (success/fail per bet), update balances, rebuild batch for remaining

### Auto-rebuild

After firing, the batch automatically rebuilds with:
- Updated balances (decremented by placed stakes)
- Removed opportunities (placed bets filtered out)
- Any new opportunities that appeared

## Provider Lifecycle (from v1)

Unchanged from Play v1. Lifecycle states (available, deposited, wagering, freebet, playing, limited, dormant) are auto-derived from existing DB data. Bonus phase filtering (min_odds, trigger_mode) applies to soft providers in the batch.

## Deploy Recommendations (from v1)

Unchanged. The balance optimizer naturally surfaces which providers need deposits. The "Deploy" section from v1 merges into the balance status view — if a cluster has 0 active siblings but opportunities exist, it shows as a deposit recommendation.

## Backend Changes

### New/modified files

1. **Create: `backend/src/services/batch_service.py`** — BatchBuilder: collects all opportunities, deduplicates, ranks, allocates balance, returns batch + balance status
2. **Modify: `backend/src/api/routes/opportunities.py`** — New endpoint: `POST /api/play/batch` (builds batch), `POST /api/play/fire` (places batch of bets)
3. **Reuse:** PlaySessionService (lifecycle/cluster data), StakeCalculator, ProviderAllocator, BetService, existing opportunity queries

### API endpoints

**`POST /api/play/batch`** — Build a batch
- Input: `{ profile_id }` (or uses active profile)
- Output: batch + summary + balance_status + missed_opportunities

**`POST /api/play/fire`** — Fire the batch
- Input: `{ bets: [{ provider_id, event_id, market, outcome, point, odds, stake, is_bonus, bonus_type }] }`
- Output: `{ results: [{ bet_id, success, error }], placed: int, failed: int }`
- Uses existing `BetService.create_bet()` per leg with retry logic
- **Partial failure handling:** Continue placing remaining bets if one fails. Report per-bet success/error. Never roll back successful bets. Frontend shows results with green/red indicators per bet.

## Frontend Changes

### New/modified files

1. **Modify: `frontend/src/components/Terminal/pages/PlayPage.tsx`** — Replace v1 cluster-driven UI with batch-driven UI
2. **Modify: `frontend/src/services/api/opportunities.ts`** — Add `buildBatch()`, `fireBatch()` API calls
3. **Modify: `frontend/src/types/index.ts`** — Add BatchBet, BatchSummary, BalanceStatus types
