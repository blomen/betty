# Incremental Analysis + Frontend Performance + Real-Time Odds Updates — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the betting platform feel real-time by making analysis incremental (only rescan changed events), replacing per-page data fetching with TanStack Query, adding table virtualization, and streaming odds updates via SSE with visual feedback.

**Architecture:** Backend tracks which odds actually changed during upsert, passes only changed event IDs to the analyzer, and broadcasts deltas via SSE. Frontend uses TanStack Query for shared cached data, applies SSE patches to the cache in-place, and uses React.memo + virtualization for render performance.

**Tech Stack:** Python/FastAPI (backend SSE + incremental analyzer), React 19 + TanStack Query + TanStack Virtual (frontend), SSE EventSource (real-time streaming)

**Spec:** `docs/superpowers/specs/2026-03-13-incremental-analysis-frontend-perf-design.md`

---

## Chunk 1: Backend — Change Tracking + Incremental Analysis

### Task 1: Add change detection to OddsBatchProcessor._flush_inner

**Files:**
- Modify: `backend/src/pipeline/storage.py:1016-1176`

- [ ] **Step 1: Add `changed_event_ids` set to OddsBatchProcessor.__init__**

In `backend/src/pipeline/storage.py`, find `OddsBatchProcessor.__init__` (around line 1016). Add:

```python
self.changed_event_ids: set[str] = set()
```

- [ ] **Step 2: Add change detection in `_flush_inner` UPDATE path**

In `_flush_inner()` (line ~1141), before `existing.odds = record["odds"]`, add comparison:

```python
# Before overwriting, detect if odds actually changed
if abs(existing.odds - record["odds"]) >= 0.01:
    self.changed_event_ids.add(record["event_id"])
existing.odds = record["odds"]
```

- [ ] **Step 3: Mark new inserts as changed in `_flush_inner` INSERT path**

In the INSERT branch of `_flush_inner()` (where new Odds rows are created), add:

```python
self.changed_event_ids.add(record["event_id"])
```

- [ ] **Step 4: Verify manually**

Run a sharp extraction and check logs. Add a temporary log line after `_flush_inner` returns:

```python
logger.info(f"Batch flush: {len(self.changed_event_ids)} events with changed odds")
```

Run: `cd backend && python -m src.app extract pinnacle`

Expected: Should see a count < total events (most Pinnacle odds are stable between runs).

- [ ] **Step 5: Commit**

```bash
git add backend/src/pipeline/storage.py
git commit -m "feat(storage): track changed_event_ids in OddsBatchProcessor"
```

---

### Task 2: Add shared changed_event_ids aggregation to Orchestrator

**Files:**
- Modify: `backend/src/pipeline/orchestrator.py:928-930,1218,1396-1399`

- [ ] **Step 1: Initialize shared set in orchestrator**

In the orchestrator class `__init__` method, add:

```python
self._changed_event_ids: set[str] = set()
```

- [ ] **Step 2: Reset at start of each run**

At the top of the orchestrator's `run()` method (or `_run_extraction()`), clear the set:

```python
self._changed_event_ids = set()
```

- [ ] **Step 3: Merge after each OddsBatchProcessor exits**

At **both** OddsBatchProcessor usage sites:

**Polymarket path** (line ~1218): After the `with OddsBatchProcessor(...) as odds_batch:` block exits, add:

```python
self._changed_event_ids |= odds_batch.changed_event_ids
```

**Provider extraction path** (per-sport loop, line ~1432): This path creates one `OddsBatchProcessor` per sport iteration inside a per-sport loop. The merge must happen **inside the per-sport loop**, right after `odds_batch.get_stats()` (around line 1432), before `sport_session.close()`:

```python
# After odds_batch.get_stats() and before sport_session.close():
self._changed_event_ids |= odds_batch.changed_event_ids
```

This ensures changes from every provider + sport combination are aggregated.

- [ ] **Step 4: Pass to analyzer**

At line ~929-930 where `OpportunityAnalyzer` is called, change:

```python
# Before:
analyzer = OpportunityAnalyzer(self.session)
analysis_results = analyzer.run()

# After:
analyzer = OpportunityAnalyzer(self.session)
changed_ids = self._changed_event_ids if self._changed_event_ids else None
analysis_results = analyzer.run(changed_event_ids=changed_ids)
```

- [ ] **Step 5: Commit**

```bash
git add backend/src/pipeline/orchestrator.py
git commit -m "feat(orchestrator): aggregate changed_event_ids and pass to analyzer"
```

---

### Task 3: Make OpportunityAnalyzer incremental

**Files:**
- Modify: `backend/src/pipeline/analyzer.py:82-153`
- Modify: `backend/src/repositories/opportunity_repo.py:333-394`

- [ ] **Step 1: Update `cleanup_stale()` to accept optional event filter**

In `backend/src/repositories/opportunity_repo.py`, modify `cleanup_stale()` signature:

```python
def cleanup_stale(self, changed_event_ids: set[str] | None = None) -> dict:
```

When `changed_event_ids` is provided, replace ONLY the global deactivation step (line ~390-392). Keep the DELETE steps (inactive, orphaned, past-event cleanup) — they're fast and prevent data accumulation:

```python
def cleanup_stale(self, changed_event_ids: set[str] | None = None) -> dict:
    stats = {}

    # Step 1-3: Always run — delete inactive, orphaned, past-event opportunities
    # (These are fast and prevent zombie accumulation between full rescans)
    stats["inactive"] = self.db.query(Opportunity).filter(
        Opportunity.is_active == False
    ).delete()
    # ... existing orphaned and past-event DELETE logic stays unchanged ...

    # Step 4: Deactivation — incremental vs full
    if changed_event_ids is not None:
        # Incremental: only deactivate opportunities for changed events
        stats["deactivated"] = self.db.query(Opportunity).filter(
            Opportunity.event_id.in_(changed_event_ids),
            Opportunity.is_active == True
        ).update({"is_active": False}, synchronize_session=False)
    else:
        # Full: deactivate all (existing behavior)
        stats["deactivated"] = self.db.query(Opportunity).filter(
            Opportunity.is_active == True
        ).update({"is_active": False})

    # Step 5: Delete past events + odds (existing behavior, always runs)
    # ... existing logic stays unchanged ...

    return stats
```

- [ ] **Step 2: Update `OpportunityAnalyzer.run()` to accept and use `changed_event_ids`**

In `backend/src/pipeline/analyzer.py`, modify `run()`:

```python
def run(self, changed_event_ids: set[str] | None = None) -> dict:
```

At line ~105 where `cleanup_stale()` is called:

```python
# Before:
cleanup_stats = self.opp_repo.cleanup_stale()

# After:
cleanup_stats = self.opp_repo.cleanup_stale(changed_event_ids=changed_event_ids)
```

At line ~108 where events are queried, filter when incremental:

```python
# Before:
events = self.scanner.get_multi_provider_events(min_providers=2)

# After:
events = self.scanner.get_multi_provider_events(min_providers=2)
if changed_event_ids is not None:
    events = [e for e in events if e.id in changed_event_ids]
```

- [ ] **Step 3: Track analysis deltas for SSE**

After the scanner loops that create/update opportunities, collect results:

```python
# Add to run() return value:
results["changed_event_ids"] = changed_event_ids
results["updated_opportunity_ids"] = [...]  # IDs that were upserted
results["removed_opportunity_ids"] = [...]  # IDs that were deactivated but not re-created
```

The exact collection depends on what the upsert methods return. The upsert methods (`upsert_value`, `upsert_dutch`, etc.) already return the opportunity object — collect their IDs.

- [ ] **Step 4: Verify incremental mode**

Run extraction twice in a row:

```bash
cd backend && python -m src.app extract pinnacle
cd backend && python -m src.app extract pinnacle
```

Second run should log significantly fewer events being analyzed (most Pinnacle odds won't change in the interval).

- [ ] **Step 5: Commit**

```bash
git add backend/src/pipeline/analyzer.py backend/src/repositories/opportunity_repo.py
git commit -m "feat(analyzer): incremental analysis — only rescan events with changed odds"
```

---

### Task 4: SSE Broadcast Channel

**Files:**
- Create: `backend/src/pipeline/broadcast.py`

- [ ] **Step 1: Create broadcast module**

```python
"""SSE broadcast channel for real-time odds/opportunity updates."""
import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class Broadcaster:
    """Fan-out broadcaster: one producer, many SSE consumers."""

    def __init__(self):
        self._clients: dict[int, asyncio.Queue] = {}
        self._counter = 0

    def subscribe(self) -> tuple[int, asyncio.Queue]:
        """Register a new SSE client. Returns (client_id, queue)."""
        self._counter += 1
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._clients[self._counter] = q
        logger.info(f"SSE client {self._counter} connected ({len(self._clients)} total)")
        return self._counter, q

    def unsubscribe(self, client_id: int) -> None:
        """Remove an SSE client."""
        self._clients.pop(client_id, None)
        logger.info(f"SSE client {client_id} disconnected ({len(self._clients)} remaining)")

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        """Push an event to all connected clients. Non-blocking; drops if queue full."""
        message = {"event": event_type, "data": data}
        dead = []
        for cid, q in self._clients.items():
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                dead.append(cid)
        for cid in dead:
            logger.warning(f"SSE client {cid} queue full, disconnecting")
            self._clients.pop(cid, None)

    @property
    def client_count(self) -> int:
        return len(self._clients)


# Singleton instance — imported by orchestrator (publish) and extraction routes (subscribe)
odds_broadcaster = Broadcaster()
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/pipeline/broadcast.py
git commit -m "feat(broadcast): SSE broadcast channel for real-time odds updates"
```

---

### Task 5: SSE Streaming Endpoint

**Files:**
- Modify: `backend/src/api/routes/extraction.py` (add endpoint after line ~400)

- [ ] **Step 1: Add SSE stream endpoint**

Add to extraction routes:

```python
import asyncio
import json
from sse_starlette.sse import EventSourceResponse
from ..pipeline.broadcast import odds_broadcaster


@router.get("/extraction/stream")
async def extraction_stream(request: Request):
    """SSE endpoint streaming real-time odds and opportunity updates."""
    client_id, queue = odds_broadcaster.subscribe()

    async def event_generator():
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield {
                        "event": msg["event"],
                        "data": json.dumps(msg["data"]),
                    }
                except asyncio.TimeoutError:
                    # Heartbeat to keep connection alive
                    yield {"event": "heartbeat", "data": ""}
        except asyncio.CancelledError:
            pass
        finally:
            odds_broadcaster.unsubscribe(client_id)

    return EventSourceResponse(event_generator())
```

- [ ] **Step 2: Install sse-starlette if not already present**

```bash
cd backend && pip install sse-starlette
```

Add to `requirements.txt` if it exists, or `pyproject.toml`.

- [ ] **Step 3: Wire orchestrator to publish events**

In `backend/src/pipeline/orchestrator.py`, wire SSE broadcasts at two points:

**A) After each OddsBatchProcessor flush (raw odds changes):**

In both batch processor usage sites (same locations as Task 2 Step 3), after the merge line, add:

```python
from .broadcast import odds_broadcaster

# Emit odds_update events for changed odds (only if clients connected)
if odds_broadcaster.client_count > 0:
    for record in odds_batch.get_changed_records():
        odds_broadcaster.publish("odds_update", {
            "event_id": record["event_id"],
            "provider": record["provider"],
            "market": record["market"],
            "outcome": record["outcome"],
            "point": record.get("point"),
            "odds": record["odds"],
            "prev_odds": record.get("prev_odds"),
        })
```

This requires adding a `get_changed_records()` method to `OddsBatchProcessor` that returns the records which triggered a change (collected alongside `changed_event_ids` in Task 1). Add to `OddsBatchProcessor`:

```python
self._changed_records: list[dict] = []

# In _flush_inner UPDATE path, when change detected:
if abs(existing.odds - record["odds"]) >= 0.01:
    self.changed_event_ids.add(record["event_id"])
    self._changed_records.append({**record, "prev_odds": existing.odds})

def get_changed_records(self) -> list[dict]:
    return self._changed_records
```

**B) After analyzer.run() returns (opportunity deltas):**

```python
# After analyzer.run() returns:
if odds_broadcaster.client_count > 0 and analysis_results:
    # New opportunities (not previously active)
    for opp in analysis_results.get("added_opportunities", []):
        odds_broadcaster.publish("opportunity_added", {
            "id": opp.id,
            "type": opp.type,
            "edge_pct": opp.edge_pct,
            "odds1": opp.odds1,
            "fair_odds": opp.fair_odds,
            "stake": opp.stake,
            # Include full opportunity fields for frontend cache insertion
            "event_id": opp.event_id,
            "provider1": opp.provider1_id,
            "outcome1": opp.outcome1,
            "market": opp.market,
            "starts_at": opp.starts_at.isoformat() if opp.starts_at else None,
        })
    # Updated opportunities (already existed, values changed)
    for opp in analysis_results.get("updated_opportunities", []):
        odds_broadcaster.publish("opportunity_update", {
            "id": opp.id,
            "type": opp.type,
            "edge_pct": opp.edge_pct,
            "odds1": opp.odds1,
            "fair_odds": opp.fair_odds,
            "stake": opp.stake,
        })
    # Removed opportunities (deactivated, edge dropped)
    for opp_id, opp_type in analysis_results.get("removed_opportunities", []):
        odds_broadcaster.publish("opportunity_removed", {
            "id": opp_id,
            "type": opp_type,
            "reason": "edge_below_threshold",
        })
    # Tier complete
    odds_broadcaster.publish("tier_complete", {
        "tier": tier_name,
        "changed_events": len(self._changed_event_ids),
    })
```

Note: The analyzer's `run()` method (Task 3) must distinguish between newly created vs updated opportunities. Track this by checking whether the upsert was an INSERT or UPDATE in the repository methods.

- [ ] **Step 4: Commit**

```bash
git add backend/src/api/routes/extraction.py backend/src/pipeline/orchestrator.py
git commit -m "feat(sse): add /extraction/stream endpoint and wire orchestrator broadcasts"
```

---

## Chunk 2: Frontend — TanStack Query Migration

### Task 6: Install dependencies and set up QueryClient

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Install TanStack Query and Virtual**

```bash
cd frontend && npm install @tanstack/react-query @tanstack/react-virtual
```

- [ ] **Step 2: Create QueryClient and wrap App**

In `frontend/src/App.tsx`, add QueryClientProvider. Remove `useBettingContext`:

```typescript
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 30_000,
    },
  },
});
```

Wrap the return JSX in `<QueryClientProvider client={queryClient}>...</QueryClientProvider>`.

Remove `const { context, refresh } = useBettingContext();` and the `context`/`onRefresh` props from `<TerminalWindow>`.

Also update the `useExtractionStatus` call: remove the `onExtractionComplete` callback parameter since SSE now handles data refresh:

```typescript
// Before:
const onExtractionComplete = useCallback(() => { refresh(); }, [refresh]);
useExtractionStatus(onExtractionComplete);

// After:
useExtractionStatus();  // Still polls for progress bars/freshness UI, but no data refresh callback
```

Delete the `onExtractionComplete` callback entirely.

- [ ] **Step 3: Update TerminalWindow to not require context/onRefresh props**

In `frontend/src/components/Terminal/TerminalWindow.tsx`:
- Remove `context` and `onRefresh` from `TerminalWindowProps` interface (lines 24-27)
- Remove `context.providers` prop being passed to child pages
- Pages that need providers will use `useQuery({ queryKey: ['providers'] })` directly (done in later tasks)

- [ ] **Step 4: Verify app still compiles**

```bash
cd frontend && npm run build
```

Fix any TypeScript errors from removed props. Pages may temporarily lose provider data — that's OK, they'll be migrated in subsequent tasks.

- [ ] **Step 5: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/App.tsx frontend/src/components/Terminal/TerminalWindow.tsx
git commit -m "feat(frontend): install TanStack Query/Virtual, set up QueryClient, remove useBettingContext"
```

---

### Task 7: Create useOddsStream SSE hook

**Files:**
- Create: `frontend/src/hooks/useOddsStream.ts`

- [ ] **Step 1: Create the SSE hook**

```typescript
import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';

export function useOddsStream() {
  const queryClient = useQueryClient();

  useEffect(() => {
    const es = new EventSource('/api/extraction/stream');

    es.addEventListener('opportunity_update', (e) => {
      const update = JSON.parse(e.data);
      const queryKey = ['opportunities', update.type];
      queryClient.setQueryData(queryKey, (old: any[] | undefined) =>
        old?.map((opp: any) => opp.id === update.id ? { ...opp, ...update } : opp)
      );
    });

    es.addEventListener('opportunity_added', (e) => {
      const opp = JSON.parse(e.data);
      const queryKey = ['opportunities', opp.type];
      queryClient.setQueryData(queryKey, (old: any[] | undefined) =>
        old ? [...old, opp] : [opp]
      );
    });

    es.addEventListener('opportunity_removed', (e) => {
      const { id, type } = JSON.parse(e.data);
      const queryKey = ['opportunities', type];
      queryClient.setQueryData(queryKey, (old: any[] | undefined) =>
        old?.filter((opp: any) => opp.id !== id)
      );
    });

    es.addEventListener('tier_complete', () => {
      queryClient.invalidateQueries({ queryKey: ['providers'] });
      queryClient.invalidateQueries({ queryKey: ['bankroll'] });
      queryClient.invalidateQueries({ queryKey: ['bets'] });
    });

    es.onerror = () => {
      queryClient.invalidateQueries({ queryKey: ['opportunities'] });
      queryClient.invalidateQueries({ queryKey: ['providers'] });
    };

    return () => es.close();
  }, [queryClient]);
}
```

- [ ] **Step 2: Wire into App.tsx**

In `frontend/src/App.tsx`, add after QueryClientProvider setup:

```typescript
import { useOddsStream } from './hooks/useOddsStream';

// Inside the App component:
useOddsStream();
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useOddsStream.ts frontend/src/App.tsx
git commit -m "feat(frontend): add useOddsStream SSE hook for real-time opportunity updates"
```

---

### Task 8: Migrate ValuePage to TanStack Query

**Files:**
- Modify: `frontend/src/components/Terminal/pages/ValuePage.tsx`

- [ ] **Step 1: Replace data fetching with useQuery**

Remove `fetchData` callback, the `useEffect` that calls it, and `useRefreshOnExtraction(fetchData)`.

Replace with:

```typescript
import { useQuery } from '@tanstack/react-query';

const { data: opportunitiesData, isLoading } = useQuery({
  queryKey: ['opportunities', 'value'],
  queryFn: () => api.getOpportunities('value', true, undefined, undefined, undefined, undefined, undefined, 3),
});
const opportunities = opportunitiesData?.opportunities ?? [];

const { data: specialsData } = useQuery({
  queryKey: ['specials'],
  queryFn: () => api.getSpecials({}),
  staleTime: 60_000,
});
const specials = specialsData ?? [];

const { data: betsData } = useQuery({
  queryKey: ['bets', 'pending'],
  queryFn: () => api.getBets('pending', 500),
  staleTime: 60_000,
});
```

Remove `useState` for `opportunities`, `specials`, `isLoading` — they're now from `useQuery`.

- [ ] **Step 2: Add deferred search**

Replace:
```typescript
const [search, setSearch] = useState('');
```

With:
```typescript
import { useDeferredValue } from 'react';

const [searchInput, setSearchInput] = useState('');
const search = useDeferredValue(searchInput);
```

Update the search input's `onChange` to use `setSearchInput`. The `grouped` memo already uses `search` as a dependency — it will now defer re-computation.

- [ ] **Step 3: Remove useRefreshOnExtraction**

Delete the `useRefreshOnExtraction(fetchData)` call. SSE + TanStack Query cache patching handles refresh now.

Also remove the 60s polling interval (`setInterval(fetchData, 60_000)`) — TanStack Query's `staleTime` handles this.

- [ ] **Step 4: Verify page loads and shows data**

```bash
cd frontend && npm run build
```

Start dev server, navigate to Soft tab, verify opportunities load.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Terminal/pages/ValuePage.tsx
git commit -m "feat(value-page): migrate to TanStack Query, add deferred search"
```

---

### Task 9: Migrate DutchPage, ReversePage, PolymarketPage to TanStack Query

**Files:**
- Modify: `frontend/src/components/Terminal/pages/DutchPage.tsx`
- Modify: `frontend/src/components/Terminal/pages/ReversePage.tsx`
- Modify: `frontend/src/components/Terminal/pages/PolymarketPage.tsx`

- [ ] **Step 1: Migrate DutchPage**

Same pattern as ValuePage Task 8:
- Replace `fetchData` + `useEffect` + `useRefreshOnExtraction` with `useQuery({ queryKey: ['opportunities', 'dutch'] })`
- Replace bets fetch with shared `useQuery({ queryKey: ['bets', 'pending'] })`
- Add `useDeferredValue` for search if present
- Remove 60s polling interval

- [ ] **Step 2: Migrate ReversePage**

Same pattern:
- `useQuery({ queryKey: ['opportunities', 'reverse'] })` with `queryFn: () => api.getOpportunities('reverse_value', ...)`
- Shared bets query

- [ ] **Step 3: Migrate PolymarketPage**

Same pattern:
- `useQuery({ queryKey: ['opportunities', 'polymarket'] })` with `queryFn: () => api.getPolymarketValue(...)`
- Remove `autoSettleBets()` from every refresh — move to a `useMutation` called explicitly or on first load only
- Shared bets query

- [ ] **Step 4: Verify all pages compile and load**

```bash
cd frontend && npm run build
```

Navigate to each tab and verify data loads.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Terminal/pages/DutchPage.tsx frontend/src/components/Terminal/pages/ReversePage.tsx frontend/src/components/Terminal/pages/PolymarketPage.tsx
git commit -m "feat(pages): migrate Dutch, Reverse, Polymarket to TanStack Query"
```

---

### Task 10: Migrate Trading Pages to TanStack Query

**Files:**
- Modify: `frontend/src/components/Terminal/pages/TradingIntradayPage.tsx`
- Modify: `frontend/src/components/Terminal/pages/TradingBankrollPage.tsx`
- Modify: `frontend/src/components/Terminal/pages/TradingStatsPage.tsx`

- [ ] **Step 1: Migrate TradingIntradayPage**

Replace `fetchData` with separate `useQuery` calls:

```typescript
const { data: session } = useQuery({
  queryKey: ['market-session'],
  queryFn: () => api.getMarketSession(),
  staleTime: Infinity,
});

const { data: signals } = useQuery({
  queryKey: ['market-signals'],
  queryFn: () => api.getMarketSignals(),
  staleTime: 60_000,
});

const { data: confirmations } = useQuery({
  queryKey: ['confirmations'],
  queryFn: () => api.getConfirmations(),
  staleTime: 30_000,
});
```

Replace `handleCompute` and `handleScan` with `useMutation`:

```typescript
import { useMutation, useQueryClient } from '@tanstack/react-query';

const queryClient = useQueryClient();

const computeMutation = useMutation({
  mutationFn: () => api.triggerMarketCompute(),
  onSuccess: () => {
    queryClient.invalidateQueries({ queryKey: ['market-session'] });
    queryClient.invalidateQueries({ queryKey: ['market-signals'] });
    queryClient.invalidateQueries({ queryKey: ['confirmations'] });
    queryClient.invalidateQueries({ queryKey: ['market-levels'] });
  },
});

const scanMutation = useMutation({
  mutationFn: (threshold: number) => api.triggerMarketScan(threshold),
  onSuccess: () => {
    queryClient.invalidateQueries({ queryKey: ['market-signals'] });
    queryClient.invalidateQueries({ queryKey: ['confirmations'] });
  },
});
```

Also add queries for macro, COT, context, levels with appropriate staleTime per spec.

- [ ] **Step 2: Migrate TradingBankrollPage**

Replace `fetchAccounts` with:
```typescript
const { data: accountsData } = useQuery({
  queryKey: ['trading-accounts'],
  queryFn: () => api.getTradingAccounts(),
});
```

Replace mutation handlers (adjust, reset) with `useMutation` + `invalidateQueries({ queryKey: ['trading-accounts'] })`.

- [ ] **Step 3: Migrate TradingStatsPage**

Replace `fetchData` with:
```typescript
const { data: analytics } = useQuery({
  queryKey: ['trading-analytics'],
  queryFn: () => api.getTradingAnalytics({}),
});
const { data: trades } = useQuery({
  queryKey: ['trading-trades'],
  queryFn: () => api.getTrades({}),
});
```

- [ ] **Step 4: Verify all trading pages compile and load**

```bash
cd frontend && npm run build
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Terminal/pages/TradingIntradayPage.tsx frontend/src/components/Terminal/pages/TradingBankrollPage.tsx frontend/src/components/Terminal/pages/TradingStatsPage.tsx
git commit -m "feat(trading): migrate all trading pages to TanStack Query with mutations"
```

---

### Task 11: Clean up removed hooks

**Files:**
- Delete: `frontend/src/hooks/useBettingContext.ts`
- Modify: `frontend/src/hooks/useExtractionStatus.ts`

- [ ] **Step 1: Delete useBettingContext.ts**

```bash
rm frontend/src/hooks/useBettingContext.ts
```

Remove any remaining imports of `useBettingContext` across the codebase.

- [ ] **Step 2: Simplify useExtractionStatus**

`useExtractionStatus` still polls tier progress for UI indicators (progress bars, freshness). But the `onComplete` callback and `EXTRACTION_COMPLETE_EVENT` dispatch are no longer needed for data refresh (SSE handles that).

Remove:
- The `onComplete?.()` call
- The `EXTRACTION_COMPLETE_EVENT` window dispatch
- The `useRefreshOnExtraction` hook export

Keep:
- `useExtractionProgress()` — still used for progress bars
- `useTiersProgress()` — still used for freshness indicators
- `useExtractionFreshness()` — still used for age display
- The polling logic (10s/30s) — still needed for UI indicators

- [ ] **Step 3: Migrate remaining pages that use useRefreshOnExtraction**

Search for all remaining usages:
```bash
grep -rn "useRefreshOnExtraction" frontend/src/
```

Pages NOT covered by Tasks 8-10 (e.g., `StatsPage.tsx`, `DrainPage.tsx`, or any others found) must also be migrated:
- Remove `useRefreshOnExtraction(fetchData)` calls
- Replace with TanStack Query `useQuery` (data will auto-refresh via SSE-driven invalidation)
- If the page has simple data fetching, convert to `useQuery` with appropriate query key
- If the page only needs a refresh signal, use `queryClient.invalidateQueries` triggered by SSE `tier_complete`

Verify none remain after migration.

- [ ] **Step 4: Verify build**

```bash
cd frontend && npm run build
```

- [ ] **Step 5: Commit**

```bash
git add -u frontend/src/hooks/
git commit -m "refactor(hooks): remove useBettingContext and useRefreshOnExtraction"
```

---

## Chunk 3: Frontend — Render Performance

### Task 12: Add SSE tick batching to useMarketStream

**Files:**
- Modify: `frontend/src/hooks/useMarketStream.ts`

- [ ] **Step 1: Replace per-tick setState with batched flush**

Replace the current EventSource tick listener with buffered approach:

```typescript
import { useState, useEffect, useRef } from 'react';
import type { StreamTickEvent, StreamBookEvent } from '../types/market';

export function useMarketStream(symbol: string = 'NQ') {
  const [lastTick, setLastTick] = useState<StreamTickEvent | null>(null);
  const [book, setBook] = useState<StreamBookEvent | null>(null);
  const [connected, setConnected] = useState(false);
  const tickBuffer = useRef<StreamTickEvent[]>([]);

  useEffect(() => {
    const es = new EventSource(`/api/trading/market/stream?symbol=${symbol}`);

    es.addEventListener('tick', (e) => {
      tickBuffer.current.push(JSON.parse(e.data));
    });

    es.addEventListener('book', (e) => {
      setBook(JSON.parse(e.data));
    });

    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);

    const flushId = setInterval(() => {
      if (tickBuffer.current.length > 0) {
        setLastTick(tickBuffer.current[tickBuffer.current.length - 1]);
        tickBuffer.current = [];
      }
    }, 200);

    return () => {
      es.close();
      clearInterval(flushId);
      tickBuffer.current = [];
      setConnected(false);
    };
  }, [symbol]);

  return { lastTick, book, connected };
}
```

- [ ] **Step 2: Verify trading page still receives ticks**

Start dev servers, navigate to trading page, confirm tick data still flows.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useMarketStream.ts
git commit -m "perf(market-stream): batch SSE ticks to 200ms flush interval"
```

---

### Task 13: Add flash animation CSS

**Files:**
- Modify: `frontend/src/index.css` (or wherever global styles live)

- [ ] **Step 1: Add flash animation classes**

Find the main CSS file (likely `frontend/src/index.css` or `frontend/src/App.css`). Add:

```css
/* Real-time odds update flash animations */
.odds-cell {
  transition: color 0.3s ease, background-color 0.3s ease;
}
.odds-cell.flash-up {
  color: #4ade80;
  background-color: rgba(74, 222, 128, 0.1);
}
.odds-cell.flash-down {
  color: #f87171;
  background-color: rgba(248, 113, 113, 0.1);
}

/* Row fade-in/out for added/removed opportunities */
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes fadeOut {
  from { opacity: 1; }
  to { opacity: 0; }
}
.row-enter {
  animation: fadeIn 0.3s ease;
}
.row-exit {
  animation: fadeOut 0.3s ease;
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/index.css
git commit -m "feat(css): add odds flash and row transition animations"
```

---

### Task 14: Extract memoized OpportunityRow component for ValuePage

**Files:**
- Modify: `frontend/src/components/Terminal/pages/ValuePage.tsx`

- [ ] **Step 1: Extract row component with React.memo**

Find the table row rendering in ValuePage (around line 834, inside `sortedGroups.map`). Extract the row JSX into a separate `React.memo` component defined above the main component:

```typescript
interface OpportunityRowProps {
  group: GroupedOpp;
  idx: number;
  isExpanded: boolean;
  onToggle: (idx: number) => void;
  placedKeys: Set<string>;
  // ... other needed props from the row render
}

const OpportunityRow = React.memo(function OpportunityRow({
  group, idx, isExpanded, onToggle, placedKeys, ...rest
}: OpportunityRowProps) {
  // Flash detection via useRef
  const prevOdds = useRef(group.odds);
  const [flash, setFlash] = useState<'up' | 'down' | null>(null);

  useEffect(() => {
    if (group.odds !== prevOdds.current) {
      setFlash(group.odds > prevOdds.current ? 'up' : 'down');
      prevOdds.current = group.odds;
      const timer = setTimeout(() => setFlash(null), 1500);
      return () => clearTimeout(timer);
    }
  }, [group.odds]);

  // Move oddsOverride/stakeOverride state LOCAL to this row
  const [localOddsOverride, setLocalOddsOverride] = useState<string>('');
  const [localStakeOverride, setLocalStakeOverride] = useState<string>('');

  return (
    // ... existing row JSX, but with:
    // - odds cell gets className={`odds-cell ${flash ? `flash-${flash}` : ''}`}
    // - inline edit inputs use localOddsOverride/localStakeOverride
    // - onBlur calls parent callback with final value
  );
});
```

The exact props interface depends on what the current row render uses. Read the row JSX carefully and extract all referenced variables as props.

- [ ] **Step 2: Remove parent-level oddsOverride/stakeOverride useState**

Since edit state is now local to each row, remove from the parent:
```typescript
// Remove these from ValuePage:
const [oddsOverride, setOddsOverride] = useState({});
const [editingOdds, setEditingOdds] = useState(null);
const [stakeOverride, setStakeOverride] = useState({});
const [editingStake, setEditingStake] = useState(null);
```

Add a callback prop to OpportunityRow for when the user confirms an edit (onBlur/Enter):
```typescript
onOddsConfirm?: (groupIdx: number, odds: number) => void;
onStakeConfirm?: (groupIdx: number, stake: number) => void;
```

- [ ] **Step 3: Verify ValuePage still works**

```bash
cd frontend && npm run build
```

Test: expand a row, edit odds/stake, confirm bet placement still works.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Terminal/pages/ValuePage.tsx
git commit -m "perf(value-page): extract memoized OpportunityRow with local edit state and flash"
```

---

### Task 15: Add table virtualization to ValuePage

**Files:**
- Modify: `frontend/src/components/Terminal/pages/ValuePage.tsx`

- [ ] **Step 1: Add virtualizer to opportunity table**

```typescript
import { useVirtualizer } from '@tanstack/react-virtual';

// Inside ValuePage, after sortedGroups is computed:
const scrollRef = useRef<HTMLDivElement>(null);
const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set());

const virtualizer = useVirtualizer({
  count: sortedGroups.length,
  getScrollElement: () => scrollRef.current,
  estimateSize: (index) => expandedRows.has(index) ? 120 : 40,
  overscan: 5,
});
```

- [ ] **Step 2: Wrap table in scroll container**

Replace the current table wrapper with:

```tsx
<div ref={scrollRef} style={{ height: '600px', overflow: 'auto' }}>
  <table className="sq" style={{ height: `${virtualizer.getTotalSize()}px`, position: 'relative' }}>
    <thead>...</thead>
    <tbody>
      {virtualizer.getVirtualItems().map((virtualRow) => {
        const group = sortedGroups[virtualRow.index];
        return (
          <OpportunityRow
            key={group.id ?? virtualRow.index}
            ref={virtualizer.measureElement}
            data-index={virtualRow.index}
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: '100%',
              transform: `translateY(${virtualRow.start}px)`,
            }}
            group={group}
            idx={virtualRow.index}
            isExpanded={expandedRows.has(virtualRow.index)}
            onToggle={(idx) => {
              setExpandedRows(prev => {
                const next = new Set(prev);
                next.has(idx) ? next.delete(idx) : next.add(idx);
                return next;
              });
            }}
            // ... other props
          />
        );
      })}
    </tbody>
  </table>
</div>
```

Note: `OpportunityRow` needs `React.forwardRef` to accept the `ref` from `measureElement`. Update the component:

```typescript
const OpportunityRow = React.memo(React.forwardRef<HTMLTableRowElement, OpportunityRowProps>(
  function OpportunityRow(props, ref) {
    // ... row render with ref on the outer <tr>
  }
));
```

- [ ] **Step 3: Verify scroll and expand work**

Start dev server, scroll through opportunity table, expand rows, verify smooth scrolling and correct row heights.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Terminal/pages/ValuePage.tsx
git commit -m "perf(value-page): add table virtualization with dynamic row heights"
```

---

### Task 16: Apply same patterns to DutchPage, ReversePage, PolymarketPage

**Files:**
- Modify: `frontend/src/components/Terminal/pages/DutchPage.tsx`
- Modify: `frontend/src/components/Terminal/pages/ReversePage.tsx`
- Modify: `frontend/src/components/Terminal/pages/PolymarketPage.tsx`

- [ ] **Step 1: DutchPage — extract memoized row, add deferred search, add virtualization**

Same patterns as Tasks 14-15:
- Extract `DutchRow` with `React.memo` + `forwardRef`
- Move `oddsOverride`/`stakeOverride` into row component
- Add `useDeferredValue` for search
- Add `useVirtualizer` with dynamic row heights

- [ ] **Step 2: ReversePage — same pattern**

- Extract `ReverseRow` with `React.memo` + flash detection
- Add `useDeferredValue` for search
- Add virtualization (ReversePage has simpler rows, fixed height OK)

- [ ] **Step 3: PolymarketPage — same pattern**

- Extract `PolyRow` with `React.memo` + flash detection
- Add `useDeferredValue` for search
- Add virtualization

- [ ] **Step 4: Verify all pages**

```bash
cd frontend && npm run build
```

Navigate to each tab, verify scroll, expand, search, flash all work.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Terminal/pages/DutchPage.tsx frontend/src/components/Terminal/pages/ReversePage.tsx frontend/src/components/Terminal/pages/PolymarketPage.tsx
git commit -m "perf(pages): add memoized rows, deferred search, virtualization to all betting pages"
```

---

## Chunk 4: Integration + Cleanup

### Task 17: End-to-end verification

**Files:** None (testing only)

- [ ] **Step 1: Start backend**

```bash
cd backend && python run_dev.py
```

- [ ] **Step 2: Start frontend**

```bash
cd frontend && npm run dev
```

- [ ] **Step 3: Trigger sharp extraction**

```bash
curl -X POST "http://localhost:8000/api/extraction/tier/sharp/start"
```

Open browser DevTools → Network tab → filter by EventSource. Verify:
- `/api/extraction/stream` connects and receives heartbeats
- During extraction, `opportunity_update` events appear
- After extraction, `tier_complete` event fires
- TanStack Query devtools (if installed) show cache being patched

- [ ] **Step 4: Verify odds flash in UI**

While extraction runs, watch the Soft tab. Numbers should update in-place with green/red flash. No full page reload, no "Loading..." spinner.

- [ ] **Step 5: Trigger api_soft extraction**

```bash
curl -X POST "http://localhost:8000/api/extraction/tier/api_soft/start"
```

Verify same behavior with more providers. Check that:
- Only changed odds trigger row updates
- New opportunities fade in
- Removed opportunities fade out

- [ ] **Step 6: Verify incremental analysis**

Check backend logs. Second extraction of same tier should show:
- `changed_event_ids` count << total events
- Analyzer processes only changed events
- Full scan still runs on 6-hour cleanup

- [ ] **Step 7: Commit any fixes**

```bash
# Add only the specific files that were fixed during integration testing
git add <specific-files-that-changed>
git commit -m "fix: integration fixes from e2e testing"
```

---

### Task 18: Remove temporary debug logging

**Files:**
- Modify: `backend/src/pipeline/storage.py` (remove Task 1 debug log)

- [ ] **Step 1: Remove debug log**

Remove the temporary `logger.info(f"Batch flush: {len(self.changed_event_ids)} ...")` added in Task 1 Step 4.

- [ ] **Step 2: Final build verification**

```bash
cd frontend && npm run build
cd backend && python -c "from src.pipeline.storage import OddsBatchProcessor; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/pipeline/storage.py
git commit -m "chore: remove debug logging from OddsBatchProcessor"
```
