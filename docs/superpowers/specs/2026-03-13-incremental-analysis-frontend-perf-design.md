# Incremental Analysis + Frontend Performance + Real-Time Odds Updates

**Date**: 2026-03-13
**Status**: Draft
**Scope**: Backend incremental opportunity analysis, frontend TanStack Query migration, render performance, real-time SSE odds streaming

## Problem Statement

Three interconnected performance issues degrade the user experience:

1. **Backend**: After each extraction cycle, the analyzer deactivates ALL opportunities and rescans every event/market from scratch (~1800+ events x 3 market types). ~90% of odds don't change between cycles, making this work redundant.

2. **Frontend data fetching**: 4 betting pages independently fetch the same data (getBets x4, getOpportunities x4 per type). No caching, no deduplication. `useBettingContext` fetches globally but pages ignore it and refetch independently.

3. **Frontend rendering**: ValuePage has 35 useState calls. Search triggers full opportunity re-grouping on every keystroke. No table virtualization for 50+ row tables. Trading SSE ticks cause per-tick re-renders (100+/sec potential).

4. **Full-page refresh model**: Odds updates require refetching entire opportunity lists. Users see "Loading..." or stale data until refetch completes. No visual feedback when individual odds change.

## Design

### 1. Backend: Incremental Opportunity Analysis

#### 1.1 Track Changed Events During Upsert

The bulk upsert path is `_flush_inner()` (not the standalone `upsert_odds()`). Add change detection there:

- In `_flush_inner()`, before overwriting `existing.odds = record["odds"]`, compare old vs new: if `abs(existing.odds - record["odds"]) >= 0.01`, mark as changed
- New odds rows (INSERT path) always count as changed
- `OddsBatchProcessor` accumulates a `changed_event_ids: set[str]` during flush
- Property `OddsBatchProcessor.changed_event_ids` exposes the set after flush

**Aggregation across batch processors**: Each provider+sport combination creates its own `OddsBatchProcessor` with a per-sport session. The orchestrator maintains a shared `changed_event_ids: set[str]` on `self` and merges each batch processor's changes after it flushes:

```python
# In orchestrator, after each provider/sport extraction:
self._changed_event_ids |= batch_processor.changed_event_ids
```

This shared set accumulates across all providers and sports within a tier run.

#### 1.2 Incremental Analyzer

`OpportunityAnalyzer.run()` accepts optional `changed_event_ids: set[str] | None`:

**When `changed_event_ids` is provided (normal extraction)**:
- Skip `cleanup_stale()` entirely (no global deactivation)
- Instead, deactivate only opportunities for events in the changed set:
  ```python
  self.db.query(Opportunity).filter(
      Opportunity.event_id.in_(changed_event_ids),
      Opportunity.is_active == True
  ).update({"is_active": False}, synchronize_session=False)
  ```
- Only query events/odds for changed event IDs
- Only run scan_value(), scan_dutch(), scan_reverse() on changed events
- Re-activate/create opportunities that still have value (set `is_active=True`)
- Opportunities for unchanged events remain untouched (still `is_active=True`)
- Return both updated opportunities and removed opportunity IDs (for SSE)

**When `changed_event_ids` is None (full rescan)**:
- Current behavior: call `cleanup_stale()` which deactivates all, rescan everything
- Triggered by: 6-hour cleanup cycle, manual full scan, first run after startup

#### 1.2.1 Disappeared Events (Zombie Prevention)

If a provider stops offering odds on an event, no odds change is detected (the event simply isn't in the extraction response), so it won't appear in `changed_event_ids`. This means stale opportunities could persist until the 6-hour full rescan.

**Accepted trade-off**: The 6-hour cleanup cycle handles this. Additionally, opportunities have a `MAX_ODDS_AGE_HOURS=2` filter in the scanner — if odds aren't refreshed within 2 hours, they're filtered out of scan results naturally. The worst case is a zombie opportunity visible for up to 2 hours, which is already the current staleness window.

**Future enhancement** (not in this spec): Track "seen event IDs" per provider per extraction run and compute disappeared events by diffing against previous run.

#### 1.3 Wire Through Orchestrator

After extraction completes for a tier:
1. Collect `changed_event_ids` from `OddsBatchProcessor`
2. Pass to `OpportunityAnalyzer.run(changed_event_ids=changed_ids)`
3. Collect analysis results (updated + removed opportunities)
4. Emit SSE events for each change (see Section 5)

#### 1.4 Expected Impact

- If 10% of odds change per cycle, analyzer work drops ~90%
- Sharp tier (every 3min) benefits most since Pinnacle odds are highly stable
- Full rescan every 6h as safety net ensures no drift

### 2. Frontend: TanStack Query Data Layer

#### 2.1 Setup

Install `@tanstack/react-query`. Wrap App in `QueryClientProvider` with default config:

```typescript
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

#### 2.2 Query Key Schema

| Query Key | Fetcher | staleTime | Notes |
|-----------|---------|-----------|-------|
| `['opportunities', 'value']` | `getOpportunities('value', ...)` | 30s | Betting value bets |
| `['opportunities', 'dutch']` | `getOpportunities('dutch', ...)` | 30s | Dutch opportunities |
| `['opportunities', 'reverse']` | `getOpportunities('reverse_value', ...)` | 30s | Reverse (Pinnacle) |
| `['opportunities', 'polymarket']` | `getPolymarketValue(...)` | 30s | Polymarket value |
| `['bets', 'pending']` | `getBets('pending', 500)` | 60s | Shared across ALL pages |
| `['specials']` | `getSpecials({})` | 60s | Boosts/specials |
| `['providers']` | `getProviders()` | 120s | Provider list + balances |
| `['bankroll']` | `getBankroll()` | 120s | Total bankroll |
| `['market-session']` | `getMarketSession()` | Infinity | Invalidated by Compute |
| `['market-signals']` | `getMarketSignals()` | 60s | Invalidated by Scan |
| `['confirmations']` | `getConfirmations()` | 30s | Invalidated by context change |
| `['macro']` | `getMacroSnapshot()` | 300s | Daily macro data |
| `['cot']` | `getCotData(2)` | 3600s | Weekly COT reports |
| `['market-levels']` | `getMarketLevels()` | Infinity | Invalidated by Compute |

#### 2.3 Invalidation Strategy

**On extraction complete** (replaces `useRefreshOnExtraction`):
```typescript
queryClient.invalidateQueries({ queryKey: ['opportunities'] })  // all types
queryClient.invalidateQueries({ queryKey: ['bets'] })
queryClient.invalidateQueries({ queryKey: ['providers'] })
```

**On trading Compute mutation**:
```typescript
queryClient.invalidateQueries({ queryKey: ['market-session'] })
queryClient.invalidateQueries({ queryKey: ['market-signals'] })
queryClient.invalidateQueries({ queryKey: ['confirmations'] })
queryClient.invalidateQueries({ queryKey: ['market-levels'] })
```

**On trading Scan mutation**:
```typescript
queryClient.invalidateQueries({ queryKey: ['market-signals'] })
queryClient.invalidateQueries({ queryKey: ['confirmations'] })
```

**On context update mutation**:
```typescript
queryClient.invalidateQueries({ queryKey: ['confirmations'] })
```

#### 2.4 Remove useBettingContext and Prop Drilling

`useBettingContext` currently fetches all opportunities + events + providers + bankroll globally every 60s. Pages ignore it and refetch independently. Remove it entirely:

1. Delete `useBettingContext.ts`
2. Remove `const { context, refresh } = useBettingContext()` from `App.tsx`
3. Remove `context` and `onRefresh` props from `TerminalWindow` and `TerminalWindowProps`
4. Pages that received `context.providers` via props (BankrollPage, ProfilePage) switch to their own `useQuery({ queryKey: ['providers'] })` call — shared cache means no extra network request
5. `ProfilePage.onRefresh` callback replaced by `queryClient.invalidateQueries({ queryKey: ['providers'] })` after profile mutations
6. Delete the `BettingContext` type if no longer referenced

#### 2.5 Page Migration Pattern

Each page replaces its `fetchData()` + `useEffect` + `useRefreshOnExtraction` with:

```typescript
// Before (ValuePage):
const [opportunities, setOpportunities] = useState([]);
const fetchData = useCallback(async () => {
  const [res, boostRes, , betsRes] = await Promise.all([...]);
  setOpportunities(res.opportunities);
  // ...
}, []);
useEffect(() => { fetchData(); }, [fetchData]);
useRefreshOnExtraction(fetchData);

// After:
const { data: opportunities = [] } = useQuery({
  queryKey: ['opportunities', 'value'],
  queryFn: () => api.getOpportunities('value', true, ...),
});
const { data: betsData } = useQuery({
  queryKey: ['bets', 'pending'],
  queryFn: () => api.getBets('pending', 500),
});
```

### 3. Frontend: Render Performance

#### 3.1 Deferred Search Filter

Replace direct `search` state with `useDeferredValue`:

```typescript
const [searchInput, setSearchInput] = useState('');
const search = useDeferredValue(searchInput);
```

Typing stays instant. Table re-group defers to next idle frame. Built into React 19, no library needed. Apply to all pages with search inputs.

#### 3.2 Memoized Row Components

Extract opportunity table rows into `React.memo` components:

```typescript
const OpportunityRow = React.memo(function OpportunityRow({
  opportunity, isExpanded, onToggle, onPlace, placedKeys
}: OpportunityRowProps) {
  // Row render logic
});
```

Row only re-renders when its own props change. When a sibling's odds change or a filter toggles, unaffected rows skip rendering entirely.

#### 3.3 Table Virtualization

Install `@tanstack/react-virtual`. Apply to all opportunity tables:

```typescript
const virtualizer = useVirtualizer({
  count: sortedRows.length,
  getScrollElement: () => scrollRef.current,
  estimateSize: (index) => expandedRows.has(index) ? 120 : 40,
  overscan: 5,
});
```

Renders only visible rows (~20-30) instead of all 50+. Works with existing `<table>` markup via absolute positioning on `<tr>` elements.

**Expandable rows**: The existing UI has click-to-expand rows showing provider details and bet placement UI. These are taller than collapsed rows. Use dynamic `estimateSize` based on expansion state, and call `virtualizer.measureElement` on expanded row refs for accurate measurement after expansion.

#### 3.4 SSE Tick Batching for Trading

Replace per-tick `setLastTick()` in `useMarketStream` with 200ms batched flush:

```typescript
const tickBuffer = useRef<StreamTickEvent[]>([]);

useEffect(() => {
  const es = new EventSource(`/api/trading/market/stream?symbol=${symbol}`);

  es.addEventListener('tick', (e) => {
    tickBuffer.current.push(JSON.parse(e.data));
  });

  const flushId = setInterval(() => {
    if (tickBuffer.current.length > 0) {
      setLastTick(tickBuffer.current[tickBuffer.current.length - 1]);
      tickBuffer.current = [];
    }
  }, 200);

  return () => { es.close(); clearInterval(flushId); tickBuffer.current = []; };
}, [symbol]);
```

Reduces 100+ renders/sec to 5 renders/sec. Always shows latest tick.

#### 3.5 Local Inline Edit State

Move `oddsOverride` and `stakeOverride` useState into the row component. Parent only receives the final value on blur/confirm, not on every keystroke. Eliminates cascade re-renders of all rows when editing one.

### 4. Not Changing

- **Provider extraction logic**: Still full re-extract per tier, no conditional fetching from provider APIs
- **Event/odds upsert in storage.py**: Already incremental, keeping as-is
- **Specials full-replace**: Boost data is ephemeral, full DELETE + INSERT is correct for this use case
- **6-hour cleanup cycle**: Still runs full `cleanup_stale()` as safety net for data integrity
- **useMarketStream SSE architecture**: Keeping EventSource pattern for trading, only batching tick updates
- **Component splitting of ValuePage**: Deferred — TanStack Query + memo + virtualization solves the acute performance pain without a 1800-line refactor

### 5. Real-Time In-Place Odds Updates via SSE

#### 5.1 Backend SSE Endpoint

New endpoint `GET /api/extraction/stream` using FastAPI's `EventSourceResponse`:

**Event types**:

```
event: odds_update
data: {"event_id":"abc","provider":"unibet","market":"1x2","outcome":"home","point":null,"odds":2.45,"prev_odds":2.30}

event: opportunity_update
data: {"id":"opp123","type":"value","edge_pct":5.2,"odds1":2.45,"fair_odds":2.33,"stake":150}

event: opportunity_added
data: {"id":"opp456","type":"value",...full opportunity object...}

event: opportunity_removed
data: {"id":"opp789","type":"value","reason":"edge_below_threshold"}

event: tier_complete
data: {"tier":"api_soft","changed_events":42,"total_events":1800}
```

**Implementation**:
- Backed by an `asyncio.Queue` per connected client
- Orchestrator pushes events to a broadcast channel after each upsert batch and after analyzer completes
- Clients receive only events that occurred after connection
- Heartbeat every 15s to keep connection alive
- Auto-cleanup of disconnected client queues

#### 5.2 Frontend SSE Hook: useOddsStream

```typescript
function useOddsStream(queryClient: QueryClient) {
  useEffect(() => {
    const es = new EventSource('/api/extraction/stream');

    es.addEventListener('opportunity_update', (e) => {
      const update = JSON.parse(e.data);
      const queryKey = ['opportunities', update.type];
      queryClient.setQueryData(queryKey, (old: Opportunity[] | undefined) =>
        old?.map(opp => opp.id === update.id ? { ...opp, ...update } : opp)
      );
    });

    es.addEventListener('opportunity_added', (e) => {
      const opp = JSON.parse(e.data);
      const queryKey = ['opportunities', opp.type];
      queryClient.setQueryData(queryKey, (old: Opportunity[] | undefined) =>
        old ? [...old, opp] : [opp]
      );
    });

    es.addEventListener('opportunity_removed', (e) => {
      const { id, type } = JSON.parse(e.data);
      const queryKey = ['opportunities', type];
      queryClient.setQueryData(queryKey, (old: Opportunity[] | undefined) =>
        old?.filter(opp => opp.id !== id)
      );
    });

    es.addEventListener('tier_complete', () => {
      // Only invalidate non-opportunity queries (providers, bankroll).
      // Opportunity data is kept in sync by individual SSE events.
      // Full opportunity invalidation only happens on SSE reconnect (see below).
      queryClient.invalidateQueries({ queryKey: ['providers'] });
      queryClient.invalidateQueries({ queryKey: ['bankroll'] });
      queryClient.invalidateQueries({ queryKey: ['bets'] });
    });

    es.onerror = () => {
      // On reconnect after disconnect, do full invalidation as safety net
      // (we may have missed SSE events during the disconnect window)
      queryClient.invalidateQueries({ queryKey: ['opportunities'] });
      queryClient.invalidateQueries({ queryKey: ['providers'] });
    };

    return () => es.close();
  }, [queryClient]);
}
```

#### 5.3 Visual Feedback on Number Change

CSS transitions on odds/edge cells:

```css
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
```

Row component detects change by comparing the incoming SSE update against the value already in TanStack Query cache (no `prev_*` fields needed in the SSE payload — avoids extra SELECT queries on backend):

```typescript
// Inside OpportunityRow, via useRef to track previous value:
const prevOdds = useRef(opportunity.odds1);
useEffect(() => {
  if (opportunity.odds1 !== prevOdds.current) {
    setFlash(opportunity.odds1 > prevOdds.current ? 'up' : 'down');
    prevOdds.current = opportunity.odds1;
    const timer = setTimeout(() => setFlash(null), 1500);
    return () => clearTimeout(timer);
  }
}, [opportunity.odds1]);
```

- `odds > prev` → apply `flash-up` class for 1.5s
- `odds < prev` → apply `flash-down` class for 1.5s
- New rows (opportunity_added) → fade-in animation (opacity 0 → 1 over 0.3s)
- Removed rows (opportunity_removed) → fade-out animation (opacity 1 → 0 over 0.3s), then remove from DOM

#### 5.4 Fallback Behavior

If SSE disconnects (network issue, backend restart):
- `EventSource` auto-reconnects (browser built-in)
- On reconnect, `tier_complete` forces full invalidation via TanStack Query
- During disconnect, TanStack Query's normal stale-time refetch keeps data reasonably fresh
- No user-visible error unless disconnect exceeds 30s (show subtle "reconnecting..." indicator)

#### 5.5 Extraction Status Integration

Replace the current extraction polling model for freshness:
- `useExtractionStatus` still polls `/api/extraction/tiers/progress` for tier running/idle state (progress bars, freshness indicators)
- But data refresh is now driven by SSE events, not by polling + refetching entire datasets
- `tier_complete` SSE event replaces the `EXTRACTION_COMPLETE_EVENT` window dispatch for data refresh
- Extraction progress UI (progress bars, provider counts) remains poll-based since it needs percentage/provider granularity

## File Change Summary

### Backend (modified)
- `backend/src/pipeline/storage.py` — `upsert_odds()` returns changed flag
- `backend/src/pipeline/storage.py` — `OddsBatchProcessor` tracks `changed_event_ids`
- `backend/src/pipeline/orchestrator.py` — pass `changed_event_ids` to analyzer, emit SSE events
- `backend/src/pipeline/analyzer.py` — accept `changed_event_ids` filter, return deltas
- `backend/src/repositories/opportunity_repo.py` — `cleanup_stale()` accepts event ID filter
- `backend/src/api/routes/extraction.py` — new SSE stream endpoint

### Backend (new)
- `backend/src/pipeline/broadcast.py` — SSE broadcast channel (asyncio.Queue per client)

### Frontend (new)
- `frontend/src/hooks/useOddsStream.ts` — SSE hook for real-time odds updates
- `frontend/src/components/Terminal/VirtualTable.tsx` — shared virtualized table wrapper

### Frontend (modified)
- `frontend/src/App.tsx` — add QueryClientProvider, remove useBettingContext
- `frontend/src/hooks/useBettingContext.ts` — delete
- `frontend/src/hooks/useMarketStream.ts` — add tick batching
- `frontend/src/hooks/useExtractionStatus.ts` — simplify (SSE handles data refresh)
- `frontend/src/components/Terminal/pages/ValuePage.tsx` — TanStack Query, memo rows, virtual table, deferred search, local edit state
- `frontend/src/components/Terminal/pages/DutchPage.tsx` — same pattern
- `frontend/src/components/Terminal/pages/ReversePage.tsx` — same pattern
- `frontend/src/components/Terminal/pages/PolymarketPage.tsx` — same pattern
- `frontend/src/components/Terminal/pages/TradingIntradayPage.tsx` — TanStack Query, mutations
- `frontend/src/components/Terminal/pages/TradingBankrollPage.tsx` — TanStack Query
- `frontend/src/components/Terminal/pages/TradingStatsPage.tsx` — TanStack Query
- `frontend/src/services/api.ts` — no changes (fetcher functions stay the same)

### Dependencies
- `@tanstack/react-query` (frontend)
- `@tanstack/react-virtual` (frontend)
