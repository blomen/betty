# Trading Intraday — Gauge Dashboard Redesign

**Date:** 2026-03-15
**Status:** Draft

## Problem

The current grid panel layout still presents data as text tables. Traders need to read numbers and mentally interpret state. A gauge-based dashboard communicates state visually at a glance — like a cockpit instrument panel.

## Solution

Replace the right column's text panels with **4 visual gauge windows** arranged in a 2x2 grid, plus a **Signals list** below. Keep the **Price Strip** as a narrow left column showing a mini chart with level overlays. All charts rendered with inline SVG (no chart library dependency).

## Layout

```
┌──────────────────────────────────────────────────────────────┐
│  HEADER: NQ 19847.50 ●Live +0.89 SD              auto 5m [R]│
├──────────┬───────────────────────────────────────────────────┤
│          │  ┌─ DISTRIBUTION ──────┬─ ORDERFLOW ──────────┐  │
│  PRICE   │  │ TPO hist │ Vol hist │ CVD sparkline         │  │
│  STRIP   │  │ RF dial  │ VAL-VAH  │ Delta/CVD/Big gauges  │  │
│          │  │ shape    │ POC line │ Signal flags           │  │
│ (mini    │  ├──────────────────────┼──────────────────────┤  │
│  chart   │  │ MACRO                │ SIGNALS              │  │
│  + level │  │ Regime/VIX/YC dials  │ (list, unchanged)    │  │
│  markers)│  │ DXY, 10Y values     │                       │  │
│          │  │ Session/Structure    │                       │  │
│          │  └──────────────────────┴──────────────────────┘  │
└──────────┴───────────────────────────────────────────────────┘
```

## Dependencies

**No new npm packages.** All gauges rendered with inline SVG elements in React. SVG is the right choice because:
- Gauges are low-element-count (arcs, lines, circles) — SVG handles this perfectly
- No animation frame loops needed (data updates at 200ms tick flush interval)
- Tailwind classes work on SVG elements for colors
- No bundle size increase

## Data Flow

### Tick History Buffer

The current `useMarketStream` hook only exposes the latest tick. The Price Strip and CVD sparkline need a rolling history.

**Change:** Add a `tickHistory` array (last 100 ticks, ~20 seconds at 200ms flush) to `useMarketStream`:

```ts
// In useMarketStream.ts
const [tickHistory, setTickHistory] = useState<StreamTickEvent[]>([]);

// In flush interval:
setTickHistory(prev => {
  const next = [...prev, ...tickBuffer.current];
  return next.slice(-100); // keep last 100
});
```

Return `tickHistory` alongside `lastTick`, `book`, `connected`.

### All Other Data

Unchanged — `session`, `indicators`, `signals` fetched via existing API calls in `TradingIntradayPage`.

## Panel Specifications

### Panel 1: Price Strip (left column, 200px wide, full height)

**Gauge type:** Vertical mini line chart with horizontal level markers

**SVG structure:**
- `<svg>` sized to panel (200 x full height)
- Y-axis maps price range (min level - 0.5% to max level + 0.5%)
- Price line: `<polyline>` from `tickHistory` prices, green if last > first, red otherwise
- Level markers: `<line>` + `<text>` for each level, colored by category (same `LEVEL_COLORS` map)
- Zone markers (OB, FVG): `<rect>` with 10% opacity fill
- Current price: `<circle>` with CSS pulse animation at the latest price Y position

**Data source:** `tickHistory` (price line) + `buildLadder(session)` (level markers)

**Replaces:** Current `PriceLadder` text list

### Panel 2: Distribution (top-left of 2x2)

**Gauge type:** Dual horizontal histogram + radial dial

**Content — TPO Histogram (left half):**
- Horizontal bars showing time-at-price distribution
- Data source: `session.session.tpo_distribution` (array of `{price, letters}`)
- Each bar width proportional to letter count
- Fair value zone (VAL-VAH) highlighted with shaded band
- POC row highlighted in yellow

**Content — Volume Histogram (right half):**
- Horizontal bars showing volume-at-price
- Data source: `session.profiles.session` (poc, val, vah) + `session.profiles.volume_distribution` if available
- If no volume distribution data, show summary only: POC/VAL/VAH/Dev POC as labeled lines on a vertical scale
- Fair value zone highlighted

**Content — Rotation Factor Dial (bottom-left corner):**
- Semicircle gauge, range -6 to +6
- SVG `<path>` arc with needle `<line>` pointing to current RF value
- Color: green >0, red <0, zinc =0
- Label: "RF +4" below

**Content — Summary Text (bottom-right):**
- Distribution shape (p_shape, b_shape, etc.)
- Value migration direction
- Poor extremes (PoorH, PoorL)
- Single prints count
- Font: 10px mono, same as current

**Data source:** `session.session` (tpo fields, distribution_type, value_migration, poor_high/low, single_prints, rotation_factor), `session.profiles`

### Panel 3: Orderflow (top-right of 2x2)

**Gauge type:** Sparkline + linear bar gauges

**Content — CVD Sparkline (top 60% of panel):**
- Line chart of `tickHistory.map(t => t.cvd)` over time
- SVG `<polyline>` with gradient fill below (green if rising, red if falling)
- Y-axis auto-scales to data range
- X-axis shows last ~20 seconds
- Horizontal zero line if CVD crosses zero

**Content — Three Linear Bar Gauges (bottom 25%):**

```
Delta:    [████████░░░░░░░░] +1,200
CVD:      [░░░░░░░█████████] rising ↑
Big:      [██░░░░░░░░░░░░░░] x3
```

- Each gauge: SVG `<rect>` background + `<rect>` fill
- Delta: green fill right of center if positive, red fill left of center if negative. Range auto-scales.
- CVD trend: fill direction indicates rising/falling. Color: green=rising, red=falling, zinc=flat
- Big trades: fill proportional to count (0-10 range), colored by net delta direction

**Content — Signal Flags (bottom 15%):**
- Same badge row as current (text-based, not SVG)
- `✓Delta ✓VSA ✗Diverg` etc.

**Data source:** `tickHistory` (sparkline), `indicators.orderflow` (delta, cvd, big trades, signal flags)

### Panel 4: Macro (bottom-left of 2x2)

**Gauge type:** Three semicircle radial gauges + text

**Content — Gauge Row (top 50%):**

Three semicircle dials side by side:

1. **Regime gauge** — 3 zones: risk_off (red, left), mixed (yellow, center), risk_on (green, right). Needle points to current regime.
2. **VIX gauge** — range 10-40, zones: <18 green, 18-25 yellow, >25 red. Needle at current VIX. Value label: "14.2 (-3.1%)"
3. **Yield Curve gauge** — range -50bp to +50bp. Green >0, red <0. Needle at current spread. Label: "+33bp"

Each gauge: SVG semicircle arc (`<path>`) with zone coloring + needle (`<line>` rotated to value angle) + value text below.

**Content — Values (middle):**
- DXY and 10Y as plain monospace text (not enough dynamic range for gauges)

**Content — Session/Structure Summary (bottom):**
- Compact text: market type, opening type, IB range, ASPR + percentile
- Structure: trend direction + ML day type
- Same data as current ContextStrip, just placed inside this panel

**Data source:** `session.macro`, `session.session`, `session.structure`, `session.ml_day_type`

### Panel 5: Signals (bottom-right of 2x2)

**Unchanged.** Signal rows with expand/collapse, score, setup name, direction, E/S/T levels, conditions grid, Take Trade button. This is inherently text/list data — gauges don't apply.

## SVG Gauge Components

### `SemiGauge` — Reusable semicircle dial

```tsx
interface SemiGaugeProps {
  value: number;
  min: number;
  max: number;
  zones?: { from: number; to: number; color: string }[];
  label?: string;
  size?: number; // diameter in px, default 80
}
```

- Renders a 180-degree arc (bottom half) with colored zone segments
- Needle rotated from `min` angle (left, 180deg) to `max` angle (right, 0deg)
- Value label centered below the arc

### `SparkLine` — Reusable line chart

```tsx
interface SparkLineProps {
  data: number[];
  width: number;
  height: number;
  color?: string;        // line color
  fillGradient?: boolean; // gradient fill below line
  zeroLine?: boolean;     // show horizontal line at y=0
}
```

- Auto-scales Y to data range
- `<polyline>` for the line, optional `<linearGradient>` + `<polygon>` for fill
- `<line>` for zero reference

### `BarGauge` — Reusable horizontal bar

```tsx
interface BarGaugeProps {
  value: number;
  min: number;
  max: number;
  label: string;
  valueLabel: string;
  color: string;
  centered?: boolean; // if true, fill extends from center (for +/- values)
}
```

- Background `<rect>` in zinc-800
- Fill `<rect>` proportional to value position in range
- If `centered`, fill goes left (negative) or right (positive) from midpoint

### `PriceChart` — Vertical price with levels

```tsx
interface PriceChartProps {
  tickHistory: StreamTickEvent[];
  levels: LadderLevel[];
  currentPrice: number | null;
}
```

- Vertical orientation: Y = price, X = time (left to right)
- Level markers as horizontal `<line>` + `<text>` labels
- Price line as `<polyline>`
- Current price as pulsing `<circle>`

## CSS Grid Structure

```tsx
<div className="flex gap-3 flex-1 min-h-0 px-1">
  {/* LEFT: Price Strip */}
  <div className="w-[200px] flex-shrink-0 border border-zinc-800 rounded bg-zinc-900/30">
    <PriceChart tickHistory={tickHistory} levels={ladderLevels} currentPrice={currentPrice} />
  </div>

  {/* RIGHT: 2x2 gauge grid + signals */}
  <div className="flex-1 min-w-0 grid grid-rows-[1fr_1fr] gap-2 overflow-hidden">
    {/* Top row */}
    <div className="grid grid-cols-2 gap-2 min-h-0">
      <DistributionPanel session={session} />
      <OrderflowPanel tickHistory={tickHistory} of={indicators?.orderflow} connected={connected} lastTick={lastTick} />
    </div>
    {/* Bottom row */}
    <div className="grid grid-cols-2 gap-2 min-h-0">
      <MacroPanel session={session} />
      <SignalsPanel signals={signals} expandedSignal={expandedSignal} ... />
    </div>
  </div>
</div>
```

Grid uses `grid-rows-[1fr_1fr]` — equal height rows. Each panel has `overflow-hidden` with internal scroll only where needed (Signals).

## Component Changes

### New Components (in `TradingIntradayPage.tsx`):

| Component | Type | Purpose |
|-----------|------|---------|
| `SemiGauge` | SVG gauge | Reusable semicircle dial |
| `SparkLine` | SVG chart | Reusable line sparkline |
| `BarGauge` | SVG gauge | Reusable horizontal bar gauge |
| `PriceChart` | SVG chart | Vertical price line + level markers |
| `DistributionPanel` | Panel | TPO + Volume histograms + RF dial |
| `MacroPanel` | Panel | Regime/VIX/YC gauges + session text |

### Modified Components:

| Component | Change |
|-----------|--------|
| `OrderflowPanel` | Add CVD sparkline + bar gauges, keep signal flags |
| `useMarketStream` | Add `tickHistory` array (last 100 ticks) |

### Deleted Components:

| Component | Reason |
|-----------|--------|
| `PriceLadder` | Replaced by `PriceChart` |
| `ContextStrip` | Absorbed into `MacroPanel` |
| `VolumeProfilesPanel` | Absorbed into `DistributionPanel` |

### VP Anchor Date Pickers

Moved into `DistributionPanel` — pinned at bottom of the panel, same `onBlur` → `handleAnchorUpdate` pattern.

## What Does NOT Change

- Data fetching (fetchData, auto-refresh, handleRefresh)
- Signal row expand/collapse and Take Trade flow
- All API calls and types (except `useMarketStream` adding `tickHistory`)
- Header bar
- Color scheme (same Tailwind palette)
- No new npm dependencies

## File Impact

| File | Change |
|------|--------|
| `TradingIntradayPage.tsx` | Major rewrite — new gauge components, new layout |
| `useMarketStream.ts` | Add `tickHistory` to return value |

## Testing

- Visual verification via browser — each gauge renders with mock/real data
- Verify tick history populates and sparklines update live
- Verify SVG scales correctly at different panel sizes
- Verify Signals expand/collapse still works
- Verify VP anchor date pickers still trigger updates
- Verify Refresh button still works
- Check that SVG elements don't cause layout overflow
