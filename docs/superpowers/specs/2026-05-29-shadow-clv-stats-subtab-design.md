# Shadow CLV — Stats Sub-Tab

**Status:** Design approved 2026-05-29. Follow-up to the opp-snapshots foundation.

## Problem

`opp_snapshots` is now collecting CLV for every detected opp (~3k/day, ~160 with computed CLV after a few hours). There's no UI surfacing it yet. Need a way to see at a glance "are all strategies × providers actually finding edge, or is something noisy?"

## Goal

Add a **Shadow CLV** sub-tab to the Stats page. One screen that answers both "is the scanner sharp in general" (chart) and "which provider × strategy × market combos are dragging the average" (table).

## Non-Goals

- Per-event drill-down / event detail panels.
- Cross-table joins to bets ("did my placed bets outperform the unplayed opps?"). Future iteration.
- Time-to-start bucketing, sport breakdown — single dimension at a time keeps it readable.
- Charts beyond the one cumulative-line chart.

## Sub-tab structure

`StatsPage` becomes two sub-tabs (mirrors `PlayPage`'s value/arb pattern):

| Sub-tab | Contents |
|---|---|
| **Bets** (default) | Existing Stats content (KPI cards, BankrollChart, CLVChart, BonusArbTracker, edge analytics, bets table) |
| **Shadow CLV** | New view, described below |

Sub-tab state: `useState<'bets' | 'shadow'>('bets')` at the top of `BetsPage`. No URL persistence in v1 (the existing page also doesn't persist scroll/sort).

## Shadow CLV view layout

**Top row — summary KPIs** (4-column grid matching the Bets KPI row):
- Total snapshots (rows with `clv_computed_at IS NOT NULL`)
- Distinct events
- Mean `pinnacle_clv_pct` (colored ± / muted if n=0)
- % beat close (rows where `pinnacle_clv_pct >= 0`)

**Middle — multi-line cumulative CLV chart**:
- One SVG, three colored cumulative-average lines (value = green, arb = blue, reverse_value = amber).
- X-axis: detection sequence (matches existing `CLVChart` so clustering doesn't squash to one edge).
- Y-axis: cumulative mean `pinnacle_clv_pct`.
- Dotted zero line, gradient fill under each line.
- Header pills: total n + "X% beat close" per type.

**Bottom — breakdown table**:
- Rows: every `(provider1_id, type, market)` combo with `n >= 3` computed CLV rows.
- Columns: provider, type, market, n, mean pin_clv (colored), mean prov_clv (colored), mean edge@detection.
- Sortable by every column. Default sort: n desc.
- Header CSS matches the existing bets table.

## Backend

One new route file: `backend/src/api/routes/opp_snapshots.py`.

**Endpoint:** `GET /api/opp-snapshots/stats?days=30`

**Returns:**
```json
{
  "summary": {
    "total": int,
    "distinct_events": int,
    "mean_pinnacle_clv_pct": float | null,
    "beat_close_pct": float | null
  },
  "history": [
    { "detected_at": iso8601, "type": "value"|"arb"|"reverse_value", "pinnacle_clv_pct": float }
  ],
  "breakdown": [
    {
      "provider_id": str, "type": str, "market": str,
      "n": int,
      "mean_pinnacle_clv_pct": float | null,
      "mean_provider_clv_pct": float | null,
      "mean_edge_at_detection": float | null
    }
  ]
}
```

All aggregations filtered to `clv_computed_at IS NOT NULL AND first_detected_at > now() - interval 'N days'`. `days` is a query param (default 30, max 365). `history` rows sorted by `first_detected_at` asc. `breakdown` only includes combos with `n >= 3` to avoid noise.

Auth: same as other `/api/*` routes (nginx basic auth at the edge, no per-route check).

## Frontend

**Files:**
- `frontend/src/pages/StatsPage.tsx` — add sub-tab bar + new `ShadowCLVView` component (inline, same file, mirrors existing `CLVChart` / `BankrollChart` pattern).
- `frontend/src/services/api.ts` — add `getOppSnapshotStats(days?: number)`.

**New component shape:**
```tsx
function ShadowCLVView() {
  const { data } = useQuery({ queryKey: ['opp-snapshot-stats', 30], queryFn: () => api.getOppSnapshotStats(30) });
  if (!data) return <div className="text-muted text-xs p-3">Loading…</div>;
  if (data.history.length === 0) return <div className="text-muted text-xs p-3">No backfilled snapshots yet — wait for events to start.</div>;
  return (
    <div className="space-y-3">
      <SummaryCards summary={data.summary} />
      <MultiLineCLVChart history={data.history} />
      <BreakdownTable rows={data.breakdown} />
    </div>
  );
}
```

`MultiLineCLVChart` and `BreakdownTable` are local helpers — copy structure from `CLVChart` for the chart; for the table reuse the existing sortable-header pattern in `SortHeader` (already in StatsPage.tsx).

## Out of scope (deferred)

- Bet ↔ snapshot join column (`bets.opp_snapshot_id`).
- "If I had played every value opp" simulated PnL.
- Per-sport breakdown.
- Date-range picker (fixed at 30 days for v1; user can override via URL `?days=`).
- Polling — react-query staleTime + manual refresh is enough.

## Files touched (anticipated)

| File | Change |
|---|---|
| `backend/src/api/routes/opp_snapshots.py` | Create — one GET endpoint |
| `backend/src/api/__init__.py` (or equiv) | Register router |
| `frontend/src/services/api.ts` | Add `getOppSnapshotStats` |
| `frontend/src/pages/StatsPage.tsx` | Add sub-tab bar, `ShadowCLVView`, `MultiLineCLVChart`, `BreakdownTable` |
