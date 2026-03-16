# Trading Intraday Page Redesign — Design Spec

## Context

The Trading Intraday page has gone through multiple design iterations (grid layout, gauge dashboard, candle chart, price-centered table) without a clear resolution. The user needs a page that adapts to their 3-phase trading workflow: scanning for setups, battling at levels, and managing trades. This spec finalizes the layout as a **two-mode page** that morphs between scanning and battle modes based on real-time level proximity events.

## Design Decisions

| Question | Answer |
|----------|--------|
| Primary focus when scanning | Level table + session context equally |
| Primary focus at a level | Orderflow/structure gauges (dominant) |
| Chart role | Small reference chart, not dominant |
| Tab structure | Keep Bankroll + Stats as separate tabs |
| Battle → level table | Minimal horizontal price strip only |

## Layout: Two-Column + Chart Strip (Layout B)

### Scanning Mode

```
┌─────────────────────────────────────────────┐
│ IntradayHeader                              │
│ Title · NQ Price · Change · LIVE · Refresh  │
├─────────────────────────────────────────────┤
│ MiniChart (60px) + key level labels         │
│ POC 21,442 · VWAP 21,451 · IB 440-470      │
├──────────────────────────┬──────────────────┤
│  LevelTable (70%)        │ ContextSidebar   │
│  Price-centered .sq      │ (30%)            │
│                          │                  │
│  21,520  PDH    63 WATCH │ Type: Balanced   │
│  21,498  +1SD   41 WATCH │ Open: OTD        │
│  21,475  ON Hi  18 NEAR  │ IB: 30pt         │
│  ═══ NQ 21,456.75 ═══   │ POC: 21,442      │
│  21,442  POC    15 WATCH │ VWAP: 21,451     │
│  21,415  -1SD   42 WATCH │ Macro: Risk On   │
│  21,380  PDL    77 WATCH │ VIX: 14.2        │
│                          │ Day: Normal 82%  │
│                          │ Poor: High       │
└──────────────────────────┴──────────────────┘
```

### Battle Mode (triggered on level_touched SSE event)

```
┌─────────────────────────────────────────────┐
│ IntradayHeader + BattleBanner               │
│ ⚔ ON High · 21,475  Confluence: 2  [✕]     │
├─────────────────────────────────────────────┤
│ MiniChart (60px, stays visible)             │
├─────────────────────┬───────────────────────┤
│   ORDERFLOW (8)     │  STRUCTURE (6)        │
│                     │  + ML & MACRO (4)     │
│  DELTA    ████░ 65% │  MKT TYPE   Balanced  │
│  CVD      █████ 80% │  OPEN       OTD       │
│  ABSORB   █████▊90% │  DISTRIB    P-shape   │
│  IMBAL    ██░░░ 40% │  POOR H/L   Poor High │
│  BIG      ████░ 70% │  SWING      HH/HL    │
│  TRAPPED  ███░░ 55% │  SINGLES    None      │
│  STOP RUN █░░░░ 20% │  ── ML & Macro ──     │
│  PA RATIO ████░ 75% │  DAY TYPE   Normal82% │
│                     │  VIX        14.2 Low  │
│                     │  REGIME     Risk On   │
│                     │  CONFLNC    2 levels  │
├─────────────────────┴───────────────────────┤
│ ↑ +1SD 498 · ▸ON Hi 475 · NQ 456 · POC 442│
├─────────────────────────────────────────────┤
│ E:21,474  S:21,480  T1:456 T2:442  [SHORT] │
└─────────────────────────────────────────────┘
```

## Component Architecture

### Component Tree

```
TradingIntradayPage (orchestrator — data fetching, mode switching, layout)
├── IntradayHeader (always)
│     In scanning: title, live price, change, status, refresh
│     In battle: + battle banner (level, price, confluence, dismiss)
├── MiniChart (always, 60px)
│     lightweight-charts candlestick, level price lines
├── [SCANNING: grid-cols-[7fr_3fr]]
│   ├── LevelTable (inline, price-centered .sq table)
│   └── ContextSidebar (session context)
├── [BATTLE: flex-col]
│   ├── GaugeGrid (grid-cols-2, flex-1)
│   │   ├── OrderflowGauges (8 GaugeBar: DELTA, CVD, ABSORB, IMBAL, BIG, TRAPPED, STOP RUN, PA RATIO)
│   │   └── StructureMLGauges (10 items: 6 structure + 4 ML/macro, rendered as labeled text values)
│   ├── NearbyLevelStrip (horizontal one-liner)
│   └── TradeActionBar (entry, stop, targets, trade buttons)
└── PositionManager (conditional, bottom, both modes)
```

### New Files

| File | Purpose |
|------|---------|
| `pages/IntradayHeader.tsx` | Header strip for both modes |
| `pages/MiniChart.tsx` | 60px candlestick chart (lightweight-charts) |
| `pages/ContextSidebar.tsx` | Session context panel |
| `pages/NearbyLevelStrip.tsx` | Compact horizontal level strip |
| `pages/TradeActionBar.tsx` | Entry/stop/targets + trade buttons |
| `pages/gaugeHelpers.ts` | Extracted from BattleScreen: `orderflowToGauges`, `structureToGauges`, `mlToGauges` |

### Modified Files

| File | Change |
|------|--------|
| `TradingIntradayPage.tsx` | Major rewrite: two-mode layout orchestrator with grid scanning / flex battle |
| `BattleScreen.tsx` | Delete — gauge functions extracted to gaugeHelpers.ts, layout absorbed into page |

### Unchanged Files

| File | Why |
|------|-----|
| `GaugeBar.tsx` | Reused as-is for battle gauges |
| `PositionManager.tsx` | Renders identically in both modes |
| `useLevelMonitor.ts` | Already provides battleActive/activeBattle/dismissBattle |
| `useMarketStream.ts` | Already provides lastTick/lastCandle/connected |
| `useSound.ts` | Already plays alert on battle activation |
| `market.ts` (types) | All types already defined |

## State Management

All state lives in `TradingIntradayPage`. No new contexts or stores.

### Key State

```typescript
// Existing
const [session, setSession] = useState<ExpandedSession | null>(null);
const [loading, setLoading] = useState(true);
const [isRefreshing, setIsRefreshing] = useState(false);
const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

// Existing hooks (add lastCandle destructuring — already returned by hook but not currently used)
const { lastTick, lastCandle, connected, esRef } = useMarketStream();
const { levels, activeBattle, battleActive, dismissBattle, switchBattleLevel, seedLevels } = useLevelMonitor(esRef, session);

// New: candle history for MiniChart
const [candles, setCandles] = useState<CandleData[]>([]);
```

### Mode Switch

`battleActive` (boolean from `useLevelMonitor`) is the **sole mode toggle**. No additional state needed.

- `level_touched` SSE → `battleActive = true` → page renders battle layout
- `dismissBattle()` or `level_rejected` SSE → `battleActive = false` → page renders scanning layout
- Sound plays via existing `useEffect` on `battleActive` transition

## Data Flow

### On Mount

```
Promise.all([
  api.getExpandedSession()  → session
  api.getLiveLevels()       → seedLevels()
  api.getCandles()          → candles (for MiniChart)
])
```

### Real-time (SSE)

- `tick` → `lastTick` → current price in header + level distances
- `candle` → `lastCandle` → MiniChart real-time bar update
- `level_approaching` → level status update in table
- `level_touched` → battle mode activation
- `level_rejected` → battle mode dismissal
- `orderflow_update` → live gauge updates in battle mode
- `level_context` → async ML + macro data for battle

### Auto-refresh

Session re-fetched every 30s (existing pattern). Candles update via SSE, no polling needed.

## Component Specs

### MiniChart

- `lightweight-charts` v5 (already installed)
- 60px container, `autoSize: true`
- `timeScale: { visible: false }`, `rightPriceScale: { visible: false }` — maximize chart area
- Level reference prices as small text labels to the right of chart (POC, VWAP, IB)
- Candle colors: green `#10b981`, red `#ef5350`
- Background: `#09090b`, grid: `#1c1c22`
- Empty state: when `candles.length === 0` (weekends, pre-market), show "Market closed" text + level reference labels only
- Candle interval: 5-minute bars (matches the existing `/candles` endpoint default)

### ContextSidebar

**Props:** Takes full `ExpandedSession` (not just `session` sub-field), since it needs `profiles.developing_poc_direction` and `macro`.

Stacked labeled values, grouped:
1. **Session**: market_type, opening_type, IB range, distribution_type
2. **Volume Profile**: POC, VAH, VAL, developing POC direction (from `profiles.developing_poc_direction`)
3. **VWAP**: value + SD deviation
4. **Macro**: regime, VIX, DXY
5. **ML**: day_type + confidence %
6. **Extremes**: poor_high/poor_low badges

Color coding: green for bullish values (balanced, risk on), amber for neutral/caution (OTD, developing), red for bearish/warnings (poor high/low, trending down). `text-[10px]` labels, `text-xs font-mono` values. Note: `CATEGORY_COLORS` from the level table maps level categories (session/band/prior/structure/overnight) — sidebar uses its own semantic coloring.

### NearbyLevelStrip

Single horizontal line: `↑ name price · name price | ↓ name price · name price`
Max 3 above + 3 below current price. `text-[10px] font-mono text-zinc-500`.

### TradeActionBar

Extracted from existing BattleScreen (lines 195-217). Entry, Stop, T1/T2/T3, TRADE LONG / TRADE SHORT buttons. No design change, just extraction.

## CSS Strategy

- **Scanning**: `grid grid-cols-[7fr_3fr] gap-2` for main body
- **Battle**: `flex flex-col` with `grid grid-cols-2 gap-4` for gauge area
- Chart always `h-[60px] flex-shrink-0`
- Header always `flex-shrink-0`
- Level table gets `overflow-y-auto flex-1`
- All panels: `bg-panel border border-border` (existing pattern)

## Implementation Phases

### Phase 1: Extract Components
1. Create `IntradayHeader.tsx` — extract header from TradingIntradayPage
2. Create `ContextSidebar.tsx` — pure presentational, takes session prop
3. Create `NearbyLevelStrip.tsx` — horizontal level display
4. Create `TradeActionBar.tsx` — extract from BattleScreen
5. Create `gaugeHelpers.ts` — extract gauge transform functions from BattleScreen

### Phase 2: Add MiniChart
6. Create `MiniChart.tsx` — lightweight-charts 60px candlestick
7. Add candle fetching to TradingIntradayPage

### Phase 3: Rewrite Page Layout
8. Implement scanning mode (grid two-column)
9. Implement battle mode (conditional on `battleActive`)
10. Wire all props and verify transitions

### Phase 4: Cleanup
11. Delete `BattleScreen.tsx` (absorbed into page)
12. Visual verification via browser

## Verification

1. **Scanning mode renders**: Level table + context sidebar visible, mini chart shows candles
2. **Battle mode activates**: Simulate level touch → gauges appear, level strip + trade bar visible
3. **Dismiss returns to scanning**: Click dismiss → back to level table
4. **Sound plays**: Battle activation triggers audio alert
5. **Real-time updates**: Tick events update price in header + level distances
6. **SSE reconnection**: Disconnect/reconnect → data re-fetches correctly
7. **Responsive**: Layout works on standard monitor widths (1280px+)
