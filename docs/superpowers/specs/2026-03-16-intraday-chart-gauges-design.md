# Intraday Page Redesign — Candle Chart + Gauges

**Date:** 2026-03-16
**Status:** Draft
**Scope:** Replace multi-panel Intraday layout with single candle chart + gauge strip + compact signal list

## Problem

The current TradingIntradayPage splits data across 5 panels (Price Ladder, Context Strip, Orderflow, Volume Profiles, Signals) in a two-column layout. This scatters information that should be unified — price levels belong on a chart, not a text list. Orderflow confirmations are buried in a panel instead of being glanceable at a glance.

## Solution

Replace the entire layout with:
1. **One dominant candle chart** (lightweight-charts) showing 5-min OHLCV bars with all levels overlaid as horizontal price lines
2. **A gauge strip** underneath — one compact gauge per confirmation metric
3. **A compact signal list** beside the gauges for active trading signals

## Layout

```
+---------------------------------------------------------------------+
|  HEADER: NQ 19847.50 Live +0.89 SD                auto 5m [Refresh] |
+---------------------------------------------------------------------+
|                                                                     |
|                                                                     |
|                    CANDLE CHART (full width)                         |
|              5-min candles + VWAP + levels overlay                   |
|              + signal markers (E/S/T lines)                         |
|                                                                     |
|                                                                     |
+----+----+----+----+----+----+----+----+----+----+----+--------------+
| D  |CVD | RF |ASPR|Imb |Val |VIX |Big |VSA |Trap|Stop| Signals (3) |
|+1.2|  ^ | +3 |42pt|buy | ^  |14.2| x2 | ok | -- | -- | [compact   |
| ## | ## | ## | ## | ## | ## | ## | ## | ## | ## | ## |  list]       |
+----+----+----+----+----+----+----+----+----+----+----+--------------+
```

- Chart takes ~75% of viewport height
- Gauge strip is a single row (~55px)
- Signals list sits to the right of gauges (wraps below on narrow screens)
- Price Ladder is removed — all levels are horizontal lines on the chart

---

## 1. Backend: Candle Endpoint

### New Endpoint: `GET /api/trading/market/candles`

**Parameters:**
| Param | Default | Description |
|-------|---------|-------------|
| `symbol` | `NQ` | Instrument |
| `interval` | `5m` | Bar interval: `1m`, `5m`, `15m` |
| `date` | today | Date string `YYYY-MM-DD` |

**Response:**
```json
{
  "candles": [
    {"t": 1710590400, "o": 19820.5, "h": 19835.0, "l": 19815.25, "c": 19830.0, "v": 42150},
    ...
  ],
  "symbol": "NQ",
  "interval": "5m",
  "date": "2026-03-16"
}
```

- `t` is Unix epoch seconds (lightweight-charts native format)
- Data sourced from `DabentoProvider.get_bars()` via `CachedMarketDataProvider`
- Past days served from parquet cache, today re-fetched live
- No new DB tables — bars flow through existing Databento + parquet pipeline

### Implementation in `MarketService`

```python
async def get_candles(self, symbol: str = "NQ", interval: str = "5m", date_str: str | None = None) -> dict:
    """Return OHLCV candle array for charting."""
    provider = _get_provider()
    target = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    target_dt = datetime.strptime(target, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = target_dt + timedelta(days=1)
    bars = await provider.get_bars(f"{symbol}.FUT", interval, target_dt, end_dt)
    return {
        "candles": [
            {"t": int(b.timestamp.timestamp()), "o": b.open, "h": b.high, "l": b.low, "c": b.close, "v": b.volume}
            for b in bars
        ],
        "symbol": symbol,
        "interval": interval,
        "date": target,
    }
```

### Route in `market.py`

```python
@router.get("/candles")
async def get_candles(
    symbol: str = Query(default="NQ"),
    interval: str = Query(default="5m", regex="^(1m|5m|15m)$"),
    date: str = Query(default=None),
    svc: MarketService = Depends(_svc),
):
    return await svc.get_candles(symbol, interval, date)
```

---

## 2. Frontend: Chart Component

### Dependency

Add `lightweight-charts` v4 (TradingView open-source, MIT license, ~40KB gzipped).

```bash
npm install lightweight-charts
```

### Chart Setup

Create a `CandleChart` component in `pages/CandleChart.tsx` (see Section 10c for extraction rationale).

```tsx
function CandleChart({ candles, levels, signals, lastTick }: {
  candles: CandleData[];
  levels: LadderLevel[];
  signals: TradingSignal[];
  lastTick: StreamTickEvent | null;
}) { ... }
```

**Initialization:**
- Create `chart` via `createChart(containerRef, options)` with dark theme matching existing palette
- Add candlestick series with OHLCV data
- Add volume series as histogram at bottom (overlay, 20% height)

**Chart options (matching retro terminal aesthetic):**
```typescript
{
  layout: { background: { color: '#0a0e0a' }, textColor: '#d4e0d4' },
  grid: { vertLines: { color: '#1a231a' }, horzLines: { color: '#1a231a' } },
  crosshair: { mode: CrosshairMode.Normal },
  timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#2a3a2a' },
  rightPriceScale: { borderColor: '#2a3a2a' },
}
```

**Candlestick colors:**
- Up: `#4CAF50` (body), `#4CAF50` (wick)
- Down: `#EF5350` (body), `#EF5350` (wick)

### Level Overlays

All levels from the current `buildLadder()` function rendered as horizontal `PriceLine`s on the candlestick series:

| Level Type | Color | Line Style | Line Width |
|-----------|-------|------------|------------|
| VWAP | `#60A5FA` (blue-400) | Solid | 2 |
| +/-1 SD | `#60A5FA` | Dashed | 1 |
| +/-2 SD | `#3B82F6` | Dashed | 1 |
| +/-3 SD | `#2563EB` | Dotted | 1 |
| Session POC | `#FACC15` (yellow-400) | Solid | 2 |
| VAH/VAL | `#FACC15` | Dashed | 1 |
| Weekly POC | `#EAB308` | Dashed | 1 |
| Leg POC | `#CA8A04` | Dotted | 1 |
| Macro POC | `#A16207` | Dotted | 1 |
| Naked POC | `#FB923C` (orange-400) | Dotted | 1 |
| IB High/Low | `#22D3EE` (cyan-400) | Dashed | 1 |
| PDH | `#4ADE80` (green-400) | Dotted | 1 |
| PDL | `#F87171` (red-400) | Dotted | 1 |
| Swing High/Low | `#A78BFA` (purple-400) | Dotted | 1 |
| ON High/Low | `#71717A` (zinc-500) | Dotted | 1 |
| Order Block | `#FB923C` (orange-400) | Dashed | 1 |
| FVG | `#FBBF24` (amber-400) | Dashed | 1 |

Each price line has a label on the right axis (e.g., "VWAP", "POC", "IB H").

### Signal Markers on Chart

When signals are active, their E/S/T levels appear as temporary price lines:
- **Entry**: `#06B6D4` (cyan-500), solid, 1px
- **Stop**: `#EF5350` (red), dashed, 1px
- **Target**: `#4CAF50` (green), dashed, 1px

Labels: `"E 19860"`, `"S 19818"`, `"T 19920"`

Lines are added/removed as signals appear/expire.

### Zone/Range Levels (OB, FVG)

`buildLadder()` produces levels with `zone: true` and `priceHigh` for order blocks and fair value gaps. Since lightweight-charts `PriceLine` only renders single-price lines, zones are rendered as **two dashed lines** (top and bottom of the range) with matching colors:

| Level Type | Color | Line Style |
|-----------|-------|------------|
| Order Block (top+bottom) | `#FB923C` (orange-400) | Dashed, 1px |
| FVG (top+bottom) | `#FBBF24` (amber-400) | Dashed, 1px |

Labels: `"OB Top"` / `"OB Bot"`, `"FVG Top"` / `"FVG Bot"`

### Real-Time Updates

The existing SSE stream (`useMarketStream`) provides tick events. The hook currently exposes only the `lastTick` (latest tick per 200ms flush), discarding intermediate ticks. This loses high/low accuracy for candle updates.

**Solution: Server-side partial candle events.** Add a `candle` event type to the SSE stream that emits the current partial candle (aggregated OHLCV) every 5 seconds. This is more accurate than client-side aggregation because the server sees every tick.

**New SSE event:**
```json
{"type": "candle", "t": 1710590700, "o": 19830.0, "h": 19838.5, "l": 19828.0, "c": 19835.25, "v": 8420}
```

The frontend updates the chart's last candle via `series.update()` on each `candle` event. When `t` changes (new 5-min bucket), a new candle is appended.

**Backend change:** In the SSE stream generator (`market.py` `/stream` endpoint), aggregate incoming ticks into a running `CandleFlow` for the current 5-min bucket. Emit a `candle` event every 5 seconds with the current OHLCV state. Reset on bucket boundary.

**SSE reconnection:** When SSE reconnects (`connected` transitions false -> true in `useMarketStream`), re-fetch candles from the `/candles` endpoint to resync chart state since ticks were missed.

**Hook integration:** Extend the existing `useMarketStream` hook to handle `candle` events and expose a `lastCandle` state alongside `lastTick`. Do NOT create a second `EventSource` — the single SSE connection already receives all event types. The `CandleChart` component receives `lastCandle` as a prop.

**Aggregation placement:** The `CandleFlow` aggregation runs inside `DatabentoLiveStream` (the stream publisher), not in the per-client SSE generator. The stream publisher emits `candle` events alongside `tick` and `book` events. This way, aggregation happens once regardless of client count.

**Pydantic note:** The `regex` parameter on `Query()` may need to be `pattern` in Pydantic v2. Verify during implementation.

---

## 3. Gauge Strip

### Layout

Horizontal row of gauges below the chart. Uses CSS flex with `gap-1`, wraps on narrow screens.

Each gauge is a small bordered box:
```
+----------+
|  DELTA   |  <- 9px uppercase muted label
|  +1,240  |  <- 12px mono value, colored
|  ####--  |  <- 4px mini bar (optional, for numeric gauges)
+----------+
```

- Numeric gauges: ~80px wide, ~50px tall
- Boolean gauges: ~55px wide (just icon + label)
- All gauges: `border border-border bg-panel`, square corners (retro spec)

### Gauge Definitions

| # | Key | Label | Value Format | Color Logic | Data Source |
|---|-----|-------|-------------|-------------|-------------|
| 1 | delta | DELTA | +/-N formatted | green if +, red if - | `orderflow.delta` |
| 2 | cvd | CVD | +/-N + trend icon (^/v/-) | green=rising, red=falling, gray=flat | `orderflow.cvd` + `orderflow.cvd_trend` |
| 3 | rf | ROT.F | +/-N | green +, red -, gray 0 | `session.rotation_factor` |
| 4 | aspr | ASPR | Npt P% | green <P30, yellow P30-70, red >P70 | `session.aspr` + `session.aspr_percentile` |
| 5 | imbalance | IMBAL | buy/sell/-- + xN count | green=buy, red=sell, gray=neutral | `orderflow.stacked_imbalance_direction` + `stacked_imbalance_count` |
| 6 | value | VALUE | ^/v/-- | green=up, red=down, gray=neutral | `session.value_migration` |
| 7 | vix | VIX | N.N | green <18, yellow 18-25, red >25 | `macro.vix` |
| 8 | big | BIG | xN (net delta direction) | green if net +, red if net - | `orderflow.big_trades_count` + `big_trades_net_delta` |
| 9 | vsa | VSA | checkmark or X | green if true, dim gray if false | `orderflow.vsa_absorption` |
| 10 | trapped | TRAP | checkmark or X | orange if true, dim gray if false | `orderflow.trapped_traders` |
| 11 | stoprun | STOP | checkmark or X | red if true, dim gray if false | `orderflow.stop_run_detected` |

### Gauge Display Rules

- **Imbalance gauge (#5):** When `stacked_imbalance_direction === 'neutral'` OR `stacked_imbalance_count === 0`, display `"--"`. Otherwise display direction + `"x{count}"` (e.g., `"buy x3"`).
- **Boolean gauges (#9-11):** Display checkmark when active, `"--"` when inactive.

### Mini Bar (Numeric Gauges Only)

For gauges 1-4, a 4px-tall horizontal bar shows relative magnitude using fixed normalization scales:
- **Delta:** +/-5,000 = full bar
- **CVD:** +/-10,000 = full bar
- **Rotation Factor:** +/-6 = full bar
- **ASPR percentile:** 0-100 maps directly to 0-1

Uses the gauge's accent color at reduced opacity (e.g., `bg-green-500/30`). Clamped at 0 and 1 (values beyond scale show full bar). Skipped for boolean gauges (5-11).

### Implementation

Single `GaugeStrip` component in `TradingIntradayPage.tsx`:

```tsx
function GaugeStrip({ session, orderflow, macro }: {
  session: MarketSession | undefined;
  orderflow: OrderflowIndicators | undefined;
  macro: MacroSnapshot | undefined;
}) { ... }
```

Each gauge rendered by a small `Gauge` helper:

```tsx
function Gauge({ label, value, color, bar }: {
  label: string;
  value: string;
  color: string;  // tailwind text color class
  bar?: number;   // 0-1 fill fraction, omit for boolean gauges
}) { ... }
```

---

## 4. Signals Panel

### Layout

Sits to the right of the gauge strip. On wide viewports (>1200px), shares the same row. On narrow viewports, wraps below.

### Compact List

Each signal row is a single line:
```
85  IB Extension Long   E 19860 S 19818 T 19920 1.4R  >
```

- Score (colored by threshold), setup name, direction badge, E/S/T, R:R, expand toggle
- Same `SignalRow` component as current, just with tighter padding (`py-1.5` instead of `py-2`)

### Expand Behavior

On expand:
- Signal's E/S/T lines appear on the chart (as described in Section 2)
- Conditions grid + Take Trade form show below the row (unchanged from current)
- Live orderflow strip within expanded signal is removed — redundant with gauges

### Empty State

```
No signals above threshold (70)
```

Single line, centered, muted text.

---

## 5. VP Anchor Date Pickers

Currently in `VolumeProfilesPanel`. Since that panel is removed:

- Move to a small toolbar row at the top-right of the chart container
- Two date inputs: Leg anchor, Macro anchor
- Same `onBlur` update behavior
- Rendered as compact inline inputs: `Leg: [2026-03-01]  Macro: [2026-02-15]`
- Styled to match chart chrome (dark bg, muted text, square corners)

---

## 6. Data Flow

```
User opens Intraday tab
  |
  v
Parallel fetch:
  GET /candles?interval=5m     -> OHLCV array
  GET /session                 -> ExpandedSession (levels, macro, session metrics)
  GET /signals                 -> TradingSignal[]
  GET /indicators              -> OrderflowIndicators
  |
  v
Render:
  CandleChart <- candles + levels (from session) + signal markers
  GaugeStrip  <- session metrics + orderflow + macro
  SignalsList <- signals
  |
  v
Auto-refresh every 30s:
  Re-fetch session + signals + indicators
  Update chart levels + gauges + signal list
  (candles NOT re-fetched — updated via SSE stream)
  |
  v
SSE stream (continuous):
  Tick events -> update last candle on chart in real-time
```

---

## 7. Components Removed

| Component | Replacement |
|-----------|-------------|
| `PriceLadder` | Chart level overlays (horizontal price lines) |
| `ContextStrip` | Gauge strip (macro/session metrics as individual gauges) |
| `OrderflowPanel` | Gauge strip (delta, CVD, VSA, etc. as individual gauges) |
| `VolumeProfilesPanel` | Chart level overlays (POCs as price lines) + VP anchor toolbar |

All removed components are defined inline in `TradingIntradayPage.tsx` — no external file cleanup needed.

---

## 8. Existing `buildLadder()` Function

The `buildLadder()` helper that aggregates levels from session data is **kept and reused**. It currently returns `LadderLevel[]` which the Price Ladder renders as text rows. The chart component uses the same array to create `PriceLine` overlays. The `classifyLevel()` and `LEVEL_COLORS` mappings drive the line color/style selection.

---

## 9. Types

### New Types in `market.ts`

```typescript
/** Single OHLCV candle for chart rendering */
export interface CandleData {
  t: number;  // Unix epoch seconds
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

/** Response from GET /api/trading/market/candles */
export interface CandlesResponse {
  candles: CandleData[];
  symbol: string;
  interval: string;
  date: string;
}
```

### New API Function in `api.ts`

```typescript
getCandles: (symbol = 'NQ', interval = '5m', date?: string) =>
  fetchJson<CandlesResponse>(`/api/trading/market/candles?symbol=${symbol}&interval=${interval}${date ? `&date=${date}` : ''}`),
```

---

## 10. Error & Empty States

| Scenario | Behavior |
|----------|----------|
| No candle data (weekend, holiday, pre-compute) | Chart area shows centered message: "No candle data for {date}. Click Compute to load." Level overlays still render if session data exists. |
| Candle fetch fails (network, API key) | Chart area shows: "Failed to load candles" with a Retry button. |
| No session data | Gauges show `"--"` for all values. Chart has no level overlays. |
| No orderflow data | Orderflow gauges (Delta, CVD, etc.) show `"--"`. |
| SSE disconnected | Header shows red dot + "Offline". Chart freezes on last known state. On reconnect, re-fetch candles to resync. |
| Zero signals | Signal panel shows single line: "No signals above threshold (70)" |

## 10a. Chart Lifecycle

The `CandleChart` component must:
- Store the `IChartApi` instance in a `useRef`
- Call `chart.remove()` in the `useEffect` cleanup to prevent memory leaks
- Re-create the chart only when the container ref changes (not on data updates)
- Use `series.setData()` for initial load, `series.update()` for incremental updates

## 10b. Interval Selection

The interval is fixed at `5m` — there is no UI to change it. The "5m" shown in the header is a static label, not interactive. Interval selection is deferred to a future enhancement.

## 10c. Component Extraction

Despite the note in Section 2 about keeping `CandleChart` in the page file, `CandleChart` and `GaugeStrip` should be extracted into sibling files in `pages/`:
- `pages/CandleChart.tsx` — chart initialization, level overlays, real-time updates
- `pages/GaugeStrip.tsx` — gauge definitions and rendering

This keeps `TradingIntradayPage.tsx` focused on data fetching and layout orchestration.

---

## 11. What Does NOT Change

- Backend analysis pipeline (AMT, orderflow, scanner, Databento provider)
- Signal scoring, detector logic, setup definitions
- SSE stream implementation
- Auto-refresh interval (30s) and scan interval (5m)
- Take Trade flow (create trade via API)
- Other trading pages (Bankroll, Stats)
- All existing API endpoints (session, signals, indicators, etc.)
- Database schema — no new tables, no migrations

---

## 12. File Impact

| File | Change |
|------|--------|
| `backend/src/api/routes/market.py` | Add `GET /candles` endpoint, add `candle` event to SSE stream |
| `backend/src/services/market_service.py` | Add `get_candles()` method |
| `frontend/package.json` | Add `lightweight-charts` dependency |
| `frontend/src/components/Terminal/pages/TradingIntradayPage.tsx` | Full rewrite: chart + gauges + signals layout |
| `frontend/src/components/Terminal/pages/CandleChart.tsx` | New: chart initialization, level overlays, real-time updates |
| `frontend/src/components/Terminal/pages/GaugeStrip.tsx` | New: gauge definitions and rendering |
| `frontend/src/services/api.ts` | Add `getCandles()` function |
| `frontend/src/types/market.ts` | Add `CandleData`, `CandlesResponse` types |
| `frontend/src/hooks/useMarketStream.ts` | Add `candle` event handling, expose `lastCandle` state |
| `backend/src/market_data/databento_live.py` (or equivalent) | Add `CandleFlow` aggregation, emit `candle` events |

**10 files total.** Backend: 3 files. Frontend: 7 files.

---

## 13. Testing

- Verify chart renders with candle data and without (empty state: "No session data. Click Compute to load.")
- Verify all level overlays appear with correct colors/styles
- Verify gauges update on 30s auto-refresh
- Verify SSE stream updates last candle in real-time
- Verify signal expand/collapse still works
- Verify signal E/S/T lines appear on chart when signal is expanded
- Verify VP anchor date pickers still trigger updates
- Verify Take Trade flow still works end-to-end
- Visual verification via Claude Preview
