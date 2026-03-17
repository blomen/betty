# Trading Intraday Page — Grid Panel Layout Redesign

**Date:** 2026-03-14
**Status:** Reviewed

## Problem

The current TradingIntradayPage dumps all data sections into a single scrolling right column. This feels messy — data types blend together, there's no visual hierarchy, and you can't quickly glance at one section without scanning past others.

## Solution

Restructure the right column into a **fixed 2x2 grid** of bordered panels, each with its own purpose and scroll context. The price ladder remains as the tall left column.

## Layout

```
┌──────────────────────────────────────────────────────────────────────┐
│  HEADER: NQ 19847.50 ●Live +0.89 SD                  auto 5m [Ref] │
├────────────┬─────────────────────────────────────────────────────────┤
│            │  CONTEXT STRIP (full width)                            │
│            │  MACRO: risk_on VIX 14.2 │ SESSION: OTD IB 42pt │ ... │
│   PRICE    ├────────────────────┬────────────────────────────────────┤
│   LADDER   │  ORDERFLOW         │  VOLUME PROFILES                  │
│            │  Δ +1200  CVD ↑    │  Sess  19820  19780-19865         │
│  (scrolls  │  ✓Delta ✓VSA ✗Div  │  Wkly  19750  19700-19800        │
│   independ │  Live: 19847 Δ1m+5 │  Leg/Macro anchors: [date pickers]│
│   ently)   ├────────────────────┴────────────────────────────────────┤
│            │  SIGNALS (3)                            auto 5m thr 70 │
│            │  ┌ 85 spring LONG  E 19850 S 19830 T 19900  2.5R      │
│            │  └ [expandable detail + Take Trade]                    │
└────────────┴─────────────────────────────────────────────────────────┘
```

## Panel Specifications

### Header (unchanged)
- Symbol, live price, connection status, SD deviation, auto-refresh timer, Refresh button
- No structural changes needed

### Panel 1: Price Ladder (left column, full height)
- **Width:** 340px fixed, flex-shrink-0
- **Height:** Full panel height, independent scroll
- **Content:** Sorted levels with color-coded dots, price marker with dashed line
- **Change:** Make current price row sticky (always visible mid-scroll)
- No data or logic changes

### Panel 2: Context Strip (top-right, spans full width of right column)
- **Height:** Auto (no scroll, content must fit in ~2-3 lines max)
- **Layout:** Single horizontal strip with inline groups separated by `│` dividers
- **Groups:**
  1. **MACRO** — regime badge (color-coded), VIX (with % change), DXY, 10Y, 2s10s spread
  2. **SESSION** — market_type, opening_type, IB range, RF, ASPR + percentile, distribution, value migration, poor extremes, single prints
  3. **STRUCTURE** — trend direction (HH/HL ↑ or LH/LL ↓ or Ranging ↔), ML day type + confidence
- **Styling:** 10px uppercase labels per group, 11px monospace values, `border-r border-zinc-800` between groups
- **Rationale:** This is pure context — you glance at it, you don't interact. Horizontal = less vertical space wasted.

### Panel 3: Orderflow (middle-left of the 2x2)
- **Height:** Shares grid row with Volume Profiles (sized by `2fr` — smaller than Signals)
- **Content (unchanged):**
  - Connection indicator (● Live / ● Off)
  - Delta value (colored +/-) + CVD value (colored by trend) + Big trades count
  - Signal flags row: Delta, VSA, Diverg, Unwind, TickVol, Trapped, StopRun (✓/✗ badges)
  - Live tick strip: price, Δ1m, CVD
- **Panel header:** "ORDERFLOW" label + connection dot

### Panel 4: Volume Profiles (middle-right of the 2x2)
- **Height:** Same grid row as Orderflow
- **Content (merged from current Profiles + VP Anchors sections):**
  - POC table: Session, Weekly, Leg, Dev POC (with direction arrow), each showing POC + VAL-VAH range
  - **NEW: Macro POC row** — data exists in `session.profiles.macro` but is not currently rendered. Add a row matching the existing Session/Weekly/Leg pattern: `Macro  {poc}  {val}-{vah}`
  - Naked POCs: remain only in the Price Ladder (no change needed here — they're level data, not profile summaries)
  - VP Anchor date pickers (Leg + Macro) pinned at bottom
- **Internal layout:** `flex flex-col` with POC table at top, date pickers at bottom via `mt-auto`
- **Panel header:** "VOLUME PROFILES" label

### Panel 5: Signals (bottom-right, spans full width of right column)
- **Height:** `3fr` grid row — gets more space than the Orderflow/Profiles row since signal rows are expandable and variable-length
- **Scroll:** Independent overflow-y-auto
- **Content (unchanged):**
  - Header: "Signals" count + auto-scan info
  - SignalRow components with expand/collapse
  - Each row: score, setup name, direction badge, E/S/T levels, R:R
  - Expanded: conditions grid, live orderflow strip, Take Trade button
- **Empty state:** "No signals above threshold (70). Auto-scanning every 5 min."

## CSS Grid Structure

```tsx
// Right column layout
<div className="flex-1 min-w-0 grid grid-rows-[auto_minmax(160px,2fr)_minmax(200px,3fr)] gap-2 overflow-hidden">
  {/* Row 1: Context Strip (auto height, ~2-3 lines) */}
  <ContextStrip />

  {/* Row 2: Orderflow + Volume Profiles (2fr — smaller share) */}
  <div className="grid grid-cols-2 gap-2 min-h-0">
    <OrderflowPanel />
    <VolumeProfilesPanel />
  </div>

  {/* Row 3: Signals (3fr — larger share, scrollable) */}
  <SignalsPanel />
</div>
```

The outer grid uses `grid-rows-[auto_minmax(160px,2fr)_minmax(200px,3fr)]`:
- Row 1 (`auto`): Context strip sizes to its content (~2-3 lines)
- Row 2 (`minmax(160px, 2fr)`): Orderflow + Profiles split 50/50 horizontally, minimum 160px height
- Row 3 (`minmax(200px, 3fr)`): Signals gets ~60% of remaining space, minimum 200px height

The `minmax()` values prevent panels from becoming unreadably small on shorter viewports. If the viewport is too short to fit all minimums, the outer container scrolls as a whole (via `overflow-y-auto` on the parent flex container).

## Component Changes

### New Component: `ContextStrip`
- Extracts macro/session/structure rendering from current `SessionPanel`
- Renders horizontally with dividers instead of vertically with headers
- No interactivity, pure display

### Modified: `SessionPanel` → deleted
- Content absorbed into `ContextStrip`

### Modified: `OrderflowPanel`
- Wrap in panel chrome (border, header, padding)
- No logic changes

### Modified: Volume Profiles section
- Merge current "Volume Profiles" and "VP Anchors" sections into single panel
- No logic changes, just layout consolidation

### Modified: `TradingIntradayPage`
- Replace single scrolling column with CSS grid layout
- Wire up new `ContextStrip` component
- Remove old section ordering

## What Does NOT Change

- Price ladder component and logic
- Data fetching (fetchData, auto-refresh, useMarketStream)
- Signal row expand/collapse and Take Trade flow
- All API calls and types
- Header bar
- Color scheme and typography
- No new dependencies

## File Impact

| File | Change |
|------|--------|
| `TradingIntradayPage.tsx` | Restructure layout to grid, extract ContextStrip, merge VP sections |

Single file change. All sub-components (`PriceLadder`, `OrderflowPanel`, `SignalRow`) stay in the same file — they're not large enough to warrant extraction.

## Testing

- Visual verification via Claude Preview or manual browser check
- Verify all panels render with data and without data (empty states)
- Verify price ladder scroll independence
- Verify signals expand/collapse still works
- Verify VP anchor date pickers still trigger updates
- Check responsive behavior at different window heights (panels should compress gracefully)
