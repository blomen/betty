# Intraday Price-Centered Level Table

**Date:** 2026-03-16
**Status:** Draft

## Problem

The intraday page uses a custom monospace level table that doesn't match the betting tab pages (ValuePage, SpecialsPage, etc.) which use the `.sq` table class. Additionally, levels are sorted by absolute distance in a flat list, making it hard to quickly see what's above vs below the current price. Traders think in terms of "what's above me / what's below me" — a price-centered layout matches this mental model.

## Design

### Layout

Replace `LevelMonitorTable` with a price-centered `.sq` table that mirrors the betting page table pattern. Current price sits as a highlighted divider row in the middle. Levels above price stack upward (closest to center first), levels below price stack downward (closest to center first).

```
┌───────────┬──────────────┬─────────┬────────┬────────┐
│ PRICE     │ LEVEL        │ TYPE    │ DIST   │ STATUS │
├───────────┼──────────────┼─────────┼────────┼────────┤
│ 25,180.00 │ monthly_high │ session │ +290   │ WATCH  │  ← furthest above
│ 24,855.25 │ vah          │ session │ +35    │ WATCH  │  ← closest above
├═══════════╧══════════════╧═════════╧════════╧════════┤
│  ● NQ 24890.25  Live  +0.60 SD                      │  ← center divider
├═══════════╤══════════════╤═════════╤════════╤════════┤
│ 24,795.50 │ ib_high      │ session │ -95    │ WATCH  │  ← closest below
│ 213.50    │ poc          │ session │ -854   │ WATCH  │
│ 209.00    │ val          │ session │ -836   │ WATCH  │  ← furthest below
└───────────┴──────────────┴─────────┴────────┴────────┘
```

### Table Styling

Use the exact same `.sq` table pattern from ValuePage:
- `<table className="sq w-full table-fixed">`
- `<colgroup>` with column width percentages
- `<thead className="sticky top-0 z-10 bg-panel">` with uppercase 10px headers
- Standard `.sq` cell padding, borders, hover states, even-row shading

### Columns

| Column | Width | Align | Content |
|--------|-------|-------|---------|
| PRICE | 20% | right | Level price, 2 decimal places, monospace |
| LEVEL | 30% | left | Level name (e.g., `weekly_poc`, `vah`, `VWAP +1SD`) |
| TYPE | 15% | left | Category badge, color-coded (session=blue, band=purple, prior=amber, structure=cyan, overnight=gray) |
| DIST | 15% | right | Distance in ticks from current price, signed (+/-), monospace |
| STATUS | 20% | center | Status badge (WATCH, NEAR, AT LVL, TRIG, REJ), same color scheme as current |

### Center Price Row

A `<tr>` with a single `<td colSpan={5}>` that displays:
- Green dot + "Live" (or red dot if disconnected)
- Symbol + current price (bold, monospace, large-ish)
- VWAP deviation in SD (colored: red >2SD, yellow >1SD, gray otherwise)
- Styled with accent border top/bottom, slightly different background (`bg-zinc-800/50` or similar)

### Sorting Logic

```typescript
const above = levels
  .filter(l => l.price > currentPrice)
  .sort((a, b) => a.price - b.price);  // ascending: furthest at top, closest at bottom

const below = levels
  .filter(l => l.price <= currentPrice)
  .sort((a, b) => b.price - a.price);  // descending: closest at top, furthest at bottom

// Render: [...above, CENTER_ROW, ...below]
```

Distance signs: levels above show `+N`, levels below show `-N`.

### Page Structure

Match the betting page layout:

1. **Header strip** — Same position as FilterBar on betting pages. Contains:
   - Refresh button (triggers `/api/trading/market/compute`)
   - Connection status indicator
   - Any future filters (not needed now, but the pattern is in place)

2. **Scrollable table area** — The `.sq` price-centered table. Scrolls independently. The center price row should stay visible (use `position: sticky` on the center row if feasible, otherwise let it scroll naturally).

3. **BattleScreen** — Renders below the table when `activeBattle` is set, same as current.

4. **PositionManager** — Renders below BattleScreen when positions exist, same as current.

### Status & Category Colors

Identical to current implementation, just rendered as `.sq` cell content:

**Category (TYPE column):**
- `session`: `text-blue-400`
- `band`: `text-purple-400`
- `prior`: `text-amber-400`
- `structure`: `text-cyan-400`
- `overnight`: `text-zinc-400`

**Status (STATUS column):**
- `watching`: `text-zinc-600` — "WATCH"
- `approaching`: `text-amber-400` — "NEAR" (with pulse animation)
- `at_level`: `text-cyan-400 font-bold` — "AT LVL" (with left border highlight)
- `triggered`: `text-emerald-600` — "TRIG"
- `rejected`: `text-zinc-600` — "REJ"

### What Changes

| File | Change |
|------|--------|
| `frontend/src/components/Terminal/pages/TradingIntradayPage.tsx` | Replace inline header + `<LevelMonitorTable>` with header strip + new price-centered `.sq` table |
| `frontend/src/components/Terminal/pages/LevelMonitorTable.tsx` | Delete — functionality absorbed into the new table within TradingIntradayPage |

### What Stays the Same

- `useLevelMonitor` hook — no changes, same data shape
- `useMarketStream` hook — no changes
- `BattleScreen` component — no changes, still renders conditionally
- `GaugeBar` component — no changes
- `PositionManager` component — no changes
- Backend API — no changes
- All SSE events — no changes

### Compact Mode

When `battleActive` is true, the table should limit visible rows (e.g., show only the 3 closest above + 3 closest below + center row = 7 rows total). Show "+N more levels" footer if truncated. This matches the current `compact={battleActive}` behavior.

## Out of Scope

- FilterBar with dropdowns (no filters needed for levels yet)
- Expandable rows with actions (levels don't have per-row actions like bets)
- Column sorting (levels are always sorted by price relative to center)
- Sticky center row (nice-to-have, not required for v1)
