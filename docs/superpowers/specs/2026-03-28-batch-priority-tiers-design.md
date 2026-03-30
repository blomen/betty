# Batch Priority Tiers Design

## Problem

The batch builder and Soft page use different filters:
- **Soft page**: hardcoded `min_edge=3%`, TTK 1min-7d
- **Batch builder**: profile `min_edge_pct=2%`, no TTK cap

This causes the batch to show 290 soft bets while the Soft page shows 125. More importantly, the batch treats all qualifying bets equally — a 15% edge bet kicking off in 2 hours gets the same priority as a 2.5% edge bet 6 days out.

## Design

Replace the flat min-edge filter with a **priority tier system** that allocates capital to the best clusters first.

### Priority Tiers

Each cluster (event+market+outcome) is assigned to the highest tier it qualifies for based on its best available edge and time to kickoff:

| Priority | Edge     | TTK     |
|----------|----------|---------|
| 1        | 10%+     | 0-12h   |
| 2        | 10%+     | 12-24h  |
| 3        | 10%+     | 24-48h  |
| 4        | 5-10%    | 0-12h   |
| 5        | 5-10%    | 12-24h  |
| 6        | 5-10%    | 24-48h  |
| 7        | 2-5%     | 0-12h   |
| 8        | 2-5%     | 12-24h  |
| 9        | 2-5%     | 24-48h  |

- **TTK max = 48 hours.** Events beyond 48h are excluded entirely. Research shows lines sharpen significantly in the final 48h; longer-dated edges are more likely stale than real.
- **Min edge = 2%.** Unchanged from current profile default.
- Capital flows **top-down per provider**: provider balance is consumed by tier 1 clusters first, then tier 2 gets whatever remains, etc.

### Cluster-Level Assignment

A cluster's tier is determined by its **best candidate bet's edge** (across all sibling providers) and the **event's TTK**. The cluster gets one tier assignment; all candidate bets within that cluster inherit it.

### Allocation Flow

1. Collect all candidates as today (value + reverse_value opps, edge >= 2%, TTK <= 48h)
2. Compute each candidate's tier (1-9) based on edge bucket + TTK bucket
3. Group candidates into clusters (event+market+outcome+point)
4. Each cluster gets the tier of its best candidate (lowest tier number = highest priority)
5. Sort clusters by tier ascending, then by best edge descending within same tier
6. Run existing round-robin allocation in this order — provider balances deplete top-down

### What Changes

| Component | Before | After |
|-----------|--------|-------|
| `batch_builder._collect_candidates` | No TTK filter | Filter TTK <= 48h |
| `batch_builder._collect_candidates` | Returns flat list | Returns list with `tier` field on each candidate |
| `batch_builder._allocate_round_robin` | Sorts by edge only | Sorts by tier first, then edge within tier |
| `BatchBet` dataclass | No tier field | Add `tier: int` field |
| Soft page (ValuePage) | Hardcoded `min_edge=3` | Align to `min_edge=2` (matches batch) |

### Soft Page Alignment

The Soft page should show the same universe of bets the batch considers:
- Change `min_edge` from 3 to 2
- Change TTK max from 7d to 48h
- Optionally show the tier assignment per row so the user sees prioritization

### Constants

Define tier boundaries in `batch_builder.py` (or a shared config):

```python
EDGE_TIERS = [10.0, 5.0, 2.0]   # edge_pct thresholds (descending)
TTK_TIERS = [12.0, 24.0, 48.0]  # hours thresholds (ascending)
MAX_TTK_HOURS = 48.0
```

Tier number = `edge_tier_index * len(TTK_TIERS) + ttk_tier_index + 1`

### UI Changes

The Capital Plan panel and Session Batch panel should display tier info:
- Show tier breakdown in the summary header (e.g., `T1: 12  T2: 8  T3: 5 ...`)
- Optionally color-code or group bets by tier in the expanded view

## Out of Scope

- Changing Kelly fraction or stake sizing per tier (future consideration)
- Dynamic tier boundaries based on historical performance
- Sharp provider (Pinnacle/Polymarket) tier assignment — they keep existing logic
