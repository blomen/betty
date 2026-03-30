# Batch Priority Tiers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat min-edge filter with a priority tier system that allocates capital to the best clusters (highest edge + soonest TTK) first, and align the Soft page with the batch universe.

**Architecture:** Add a `priority` integer field to `BatchBet`. A helper function computes priority from edge bucket (10%+, 5-10%, 2-5%) crossed with TTK bucket (0-12h, 12-24h, 24-48h) yielding tiers 1-9. The round-robin allocator sorts by priority ascending (best first), then edge descending within each tier. A 48h TTK cap is enforced in `_collect_candidates`. The Soft page aligns its `min_edge` to 2 and `MAX_TTK_HOURS` to 48.

**Tech Stack:** Python (batch_builder.py), React/TypeScript (ValuePage.tsx, formatters.ts, SessionBatchPanel.tsx)

---

### Task 1: Add priority tier computation to batch_builder.py

**Files:**
- Modify: `backend/src/services/batch_builder.py:25-27` (constants)
- Modify: `backend/src/services/batch_builder.py:29-69` (BatchBet dataclass)

- [ ] **Step 1: Add tier constants and helper function**

After `SHARP_PROVIDERS` (line 26), add:

```python
# Priority tier boundaries for soft bet allocation
# Edge buckets (descending): 10%+, 5-10%, 2-5%
# TTK buckets (ascending): 0-12h, 12-24h, 24-48h
# Priority = edge_tier_index * 3 + ttk_tier_index + 1  (1 = best, 9 = worst)
EDGE_THRESHOLDS = [10.0, 5.0, 2.0]   # edge_pct cutoffs (descending)
TTK_THRESHOLDS = [12.0, 24.0, 48.0]  # hours cutoffs (ascending)
MAX_TTK_HOURS = 48.0


def compute_priority(edge_pct: float, ttk_hours: float | None) -> int:
    """
    Compute priority tier 1-9 from edge % and time-to-kickoff hours.
    Lower number = higher priority. Returns 99 if outside all tiers.
    """
    if ttk_hours is None or ttk_hours > MAX_TTK_HOURS:
        return 99

    # Edge bucket index: 0 = 10%+, 1 = 5-10%, 2 = 2-5%
    edge_idx = -1
    for i, threshold in enumerate(EDGE_THRESHOLDS):
        if edge_pct >= threshold:
            edge_idx = i
            break
    if edge_idx == -1:
        return 99  # Below min edge

    # TTK bucket index: 0 = 0-12h, 1 = 12-24h, 2 = 24-48h
    ttk_idx = -1
    for i, threshold in enumerate(TTK_THRESHOLDS):
        if ttk_hours <= threshold:
            ttk_idx = i
            break
    if ttk_idx == -1:
        return 99

    return edge_idx * len(TTK_THRESHOLDS) + ttk_idx + 1
```

- [ ] **Step 2: Add `priority` field to BatchBet**

Add after `funded` field (line 67):

```python
    # Priority tier (1-9, lower = better; 99 = outside all tiers)
    priority: int = 99
```

- [ ] **Step 3: Verify file still parses**

Run: `cd backend && python -c "from src.services.batch_builder import BatchBuilder, compute_priority; print(compute_priority(12.0, 3.0), compute_priority(6.0, 15.0), compute_priority(3.0, 40.0))"`
Expected: `1 5 9`

- [ ] **Step 4: Commit**

```bash
git add backend/src/services/batch_builder.py
git commit -m "feat(batch): add priority tier constants and compute_priority helper"
```

---

### Task 2: Wire priority into candidate collection and enforce 48h TTK cap

**Files:**
- Modify: `backend/src/services/batch_builder.py:319-502` (_collect_candidates + _make_candidate)

- [ ] **Step 1: Add TTK filter and priority assignment in _make_candidate**

At the top of `_make_candidate` (after line 372 docstring), add TTK check:

```python
        # Enforce 48h TTK cap for soft providers
        if opp.provider1_id not in SHARP_PROVIDERS:
            if event.start_time:
                from datetime import datetime, timezone
                ttk_hours = (event.start_time - datetime.now(timezone.utc)).total_seconds() / 3600
                if ttk_hours > MAX_TTK_HOURS:
                    return None
                if ttk_hours <= 0:
                    return None
            else:
                ttk_hours = None
        else:
            ttk_hours = None
```

Then in the `return BatchBet(...)` call (around line 480), after the `cluster=cluster` line, add:

```python
            funded=not unfunded,
            priority=compute_priority(opp.edge_pct or 0.0, ttk_hours) if provider_id not in SHARP_PROVIDERS else 0,
```

And remove the existing `funded` default (since we're now setting it explicitly). The sharp providers get priority 0 (always highest).

- [ ] **Step 2: Verify candidates have priority set**

Run: `cd backend && python -c "
from src.services.batch_builder import BatchBuilder, compute_priority
print('Priority 1 (10%+, <12h):', compute_priority(15.0, 5.0))
print('Priority 4 (5-10%, <12h):', compute_priority(7.0, 8.0))
print('Priority 9 (2-5%, 24-48h):', compute_priority(3.0, 40.0))
print('Excluded (>48h):', compute_priority(15.0, 50.0))
print('Excluded (<2%):', compute_priority(1.5, 5.0))
"`
Expected: `1, 4, 9, 99, 99`

- [ ] **Step 3: Commit**

```bash
git add backend/src/services/batch_builder.py
git commit -m "feat(batch): enforce 48h TTK cap and assign priority tiers to candidates"
```

---

### Task 3: Sort by priority in the allocation pipeline

**Files:**
- Modify: `backend/src/services/batch_builder.py:150-157` (sorting in `build`)
- Modify: `backend/src/services/batch_builder.py:684-688` (sorting in `_allocate_with_round_robin`)

- [ ] **Step 1: Update main sort in `build` method**

Replace the sorting block at lines 154-157:

```python
        ranked = sorted(
            candidates,
            key=lambda b: (-TIER_PRIORITY.get(b.tier, 0), -b.expected_profit),
        )
```

With:

```python
        ranked = sorted(
            candidates,
            key=lambda b: (-TIER_PRIORITY.get(b.tier, 0), b.priority, -b.expected_profit),
        )
```

This adds `b.priority` as second sort key: sharp providers still come first (TIER_PRIORITY), then within soft, lowest priority number (best tier) comes first, then highest expected_profit within same tier.

- [ ] **Step 2: Update sort in `_allocate_with_round_robin`**

Replace the sorting at line 685-688:

```python
        sorted_keys = sorted(
            best_per_key.keys(),
            key=lambda k: -best_per_key[k].expected_profit,
        )
```

With:

```python
        sorted_keys = sorted(
            best_per_key.keys(),
            key=lambda k: (best_per_key[k].priority, -best_per_key[k].expected_profit),
        )
```

- [ ] **Step 3: Verify sort order with a quick smoke test**

Run the batch builder via API and check that the batch is ordered by priority:

```bash
cd backend && python -c "
from src.db.models import init_db
from sqlalchemy.orm import Session
from src.services.batch_builder import BatchBuilder

db = init_db()
session = Session(db)
builder = BatchBuilder(session)
result = builder.build(profile_id=1)
soft = [b for b in result['batch'] if b['tier'] == 'soft' and b['funded']]
for b in soft[:10]:
    print(f\"P{b.get('priority', '?'):>2}  edge={b['edge_pct']:>5.1f}%  {b['display_home']} vs {b['display_away']}\")
session.close()
"
```
Expected: Bets should be ordered by priority ascending, then edge descending within each priority.

- [ ] **Step 4: Commit**

```bash
git add backend/src/services/batch_builder.py
git commit -m "feat(batch): sort allocation by priority tier, then expected profit"
```

---

### Task 4: Expose priority in the API response

**Files:**
- Modify: `backend/src/services/batch_builder.py:841-866` (_bet_to_dict)

- [ ] **Step 1: Add priority to the serialized dict**

In `_bet_to_dict`, add after `"funded": bet.funded,`:

```python
            "priority": bet.priority,
```

- [ ] **Step 2: Add priority tier breakdown to _build_summary**

In `_build_summary` (line 760), add tier breakdown stats:

```python
    def _build_summary(self, batch: list[BatchBet]) -> dict:
        polymarket_bets = [b for b in batch if b.tier == "polymarket"]
        pinnacle_bets = [b for b in batch if b.tier == "pinnacle"]
        soft_bets = [b for b in batch if b.tier == "soft"]

        # Priority tier breakdown for soft bets (funded only)
        funded_soft = [b for b in soft_bets if b.funded]
        tier_breakdown = {}
        for b in funded_soft:
            p = b.priority
            if p not in tier_breakdown:
                tier_breakdown[p] = {"count": 0, "stake": 0.0, "ev": 0.0}
            tier_breakdown[p]["count"] += 1
            tier_breakdown[p]["stake"] += b.stake
            tier_breakdown[p]["ev"] += b.expected_profit
        # Round values
        for v in tier_breakdown.values():
            v["stake"] = round(v["stake"], 2)
            v["ev"] = round(v["ev"], 2)

        return {
            "total_bets": len(batch),
            "total_stake": round(sum(b.stake for b in batch), 2),
            "total_expected_profit": round(sum(b.expected_profit for b in batch), 2),
            "polymarket_bets": len(polymarket_bets),
            "polymarket_ev": round(sum(b.expected_profit for b in polymarket_bets), 2),
            "pinnacle_bets": len(pinnacle_bets),
            "pinnacle_ev": round(sum(b.expected_profit for b in pinnacle_bets), 2),
            "soft_bets": len(soft_bets),
            "soft_ev": round(sum(b.expected_profit for b in soft_bets), 2),
            "tier_breakdown": tier_breakdown,
        }
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/services/batch_builder.py
git commit -m "feat(batch): expose priority and tier breakdown in API response"
```

---

### Task 5: Align Soft page filters (min edge 2%, TTK 48h)

**Files:**
- Modify: `frontend/src/utils/formatters.ts:86` (MAX_TTK_HOURS constant)
- Modify: `frontend/src/components/Terminal/pages/ValuePage.tsx:417` (min edge)

- [ ] **Step 1: Change MAX_TTK_HOURS from 168 to 48**

In `frontend/src/utils/formatters.ts` line 86, change:

```typescript
export const MAX_TTK_HOURS = 7 * 24; // 168 hours
```

To:

```typescript
export const MAX_TTK_HOURS = 48; // 2 days
```

**Note:** This constant is used by ValuePage, DrainPage, DutchPage, PolymarketPage, and ReversePage. All pages will now filter to 48h max. This is intentional — the batch also caps at 48h so all pages should be aligned.

- [ ] **Step 2: Change ValuePage min edge from 3 to 2**

In `frontend/src/components/Terminal/pages/ValuePage.tsx` line 417, change:

```typescript
    queryFn: () => api.getOpportunities('value', true, undefined, undefined, undefined, undefined, undefined, 3),
```

To:

```typescript
    queryFn: () => api.getOpportunities('value', true, undefined, undefined, undefined, undefined, undefined, 2),
```

- [ ] **Step 3: Verify the frontend compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/utils/formatters.ts frontend/src/components/Terminal/pages/ValuePage.tsx
git commit -m "feat(soft): align Soft page filters to match batch (min edge 2%, TTK 48h)"
```

---

### Task 6: Show tier breakdown in Session Batch summary

**Files:**
- Modify: `frontend/src/components/Terminal/pages/play/SessionBatchPanel.tsx`

- [ ] **Step 1: Read SessionBatchPanel.tsx to find the summary display**

Read the file to locate where `soft_bets` count and `soft_ev` are displayed in the summary header (the line that shows `SOFT 290 (+2927)`).

- [ ] **Step 2: Add tier breakdown display**

After the existing `SOFT {n} (+{ev})` display, add the tier breakdown from `summary.tier_breakdown`. Display as compact inline labels:

```tsx
{summary.tier_breakdown && Object.entries(summary.tier_breakdown)
  .sort(([a], [b]) => Number(a) - Number(b))
  .map(([tier, data]: [string, any]) => (
    <span key={tier} className="ml-2 text-zinc-500">
      T{tier}:{data.count}
    </span>
  ))
}
```

This shows e.g. `SOFT 55 (+650) T1:12 T4:8 T7:35` so you can see how capital is distributed across priority tiers.

- [ ] **Step 3: Verify the frontend compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Terminal/pages/play/SessionBatchPanel.tsx
git commit -m "feat(play): show priority tier breakdown in batch summary"
```
