# Intraday Candle Chart + Gauges Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the multi-panel TradingIntradayPage with a dominant candlestick chart (lightweight-charts) + horizontal gauge strip + compact signal list.

**Architecture:** Backend adds a `/candles` endpoint serving OHLCV data from `DabentoProvider.get_bars()` via `CachedMarketDataProvider`, plus server-side candle aggregation in the SSE stream. Frontend uses `lightweight-charts` v4 for the chart, overlays all levels as `PriceLine`s, and replaces the text panels with a row of compact gauges.

**Tech Stack:** Python/FastAPI, React 19, TypeScript, Tailwind CSS, lightweight-charts v4

**Spec:** `docs/superpowers/specs/2026-03-16-intraday-chart-gauges-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/src/services/market_service.py` | Modify | Add `get_candles()` method |
| `backend/src/api/routes/market.py` | Modify | Add `GET /candles` endpoint, add candle event to SSE stream |
| `backend/src/market_data/stream.py` | Modify | Add `CandleFlow` aggregation, emit `candle` events |
| `frontend/package.json` | Modify | Add `lightweight-charts` dependency |
| `frontend/src/types/market.ts` | Modify | Add `CandleData`, `CandlesResponse` types |
| `frontend/src/services/api.ts` | Modify | Add `getCandles()` function |
| `frontend/src/hooks/useMarketStream.ts` | Modify | Handle `candle` SSE events, expose `lastCandle` |
| `frontend/src/components/Terminal/pages/CandleChart.tsx` | Create | Chart init, level overlays, real-time candle updates |
| `frontend/src/components/Terminal/pages/GaugeStrip.tsx` | Create | 11 gauge definitions + rendering |
| `frontend/src/components/Terminal/pages/TradingIntradayPage.tsx` | Rewrite | New layout: chart + gauges + signals |

---

## Chunk 1: Backend — Candle Endpoint + SSE Candle Events

No pytest suite exists in this project. Backend verification is manual (curl / browser).

---

### Task 1: Add `get_candles()` to MarketService

**Files:**
- Modify: `backend/src/services/market_service.py`

- [ ] **Step 1: Add the `get_candles` method**

Add this method to the `MarketService` class (after the existing `get_indicators` method, around line 280):

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

Note: `_get_provider()` is the module-level singleton that returns a `CachedMarketDataProvider` wrapping `DabentoProvider`. The `get_bars()` method already handles 5m resampling from 1m bars internally.

- [ ] **Step 2: Verify import availability**

Confirm `datetime`, `timedelta`, and `timezone` are already imported at the top of `market_service.py` (they are — line 7). No new imports needed.

---

### Task 2: Add `GET /candles` route

**Files:**
- Modify: `backend/src/api/routes/market.py`

- [ ] **Step 1: Add the candles endpoint**

Add this route after the existing `/session/{date}` route (after line 41):

```python
@router.get("/candles")
async def get_candles(
    symbol: str = Query(default="NQ"),
    interval: str = Query(default="5m", pattern="^(1m|5m|15m)$"),
    date: str = Query(default=None),
    svc: MarketService = Depends(_svc),
):
    """Return OHLCV candles for charting."""
    return await svc.get_candles(symbol, interval, date)
```

Note: Use `pattern=` (not `regex=`) — this project uses Pydantic v2.

- [ ] **Step 2: Verify manually**

Run: `cd backend && python -m src.app serve` (or use the existing dev server)

```bash
curl "http://localhost:8000/api/trading/market/candles?interval=5m"
```

Expected: JSON with `candles` array (may be empty on weekends/holidays), `symbol`, `interval`, `date` fields.

---

### Task 3: Add CandleFlow aggregation to live stream

**Files:**
- Modify: `backend/src/market_data/stream.py`

The live stream (`DatabentoLiveStream`) currently publishes `tick` and `book` events. We need it to also aggregate ticks into running 5-minute OHLCV candles and emit `candle` events every 5 seconds.

- [ ] **Step 1: Add CandleFlow class**

Add this class before the `DatabentoLiveStream` class in `stream.py`:

```python
class CandleFlow:
    """Aggregates ticks into a running OHLCV candle for the current 5-min bucket."""

    BUCKET_SECONDS = 300  # 5 minutes
    EMIT_INTERVAL = 5.0   # seconds between candle event emissions

    def __init__(self):
        self._bucket_start: int = 0
        self._o = self._h = self._l = self._c = 0.0
        self._v = 0
        self._dirty = False
        self._last_emit: float = 0.0

    def _bucket_for(self, epoch: float) -> int:
        return int(epoch) // self.BUCKET_SECONDS * self.BUCKET_SECONDS

    def update(self, price: float, size: int, epoch: float) -> dict | None:
        """Feed a tick. Returns a candle event dict if it's time to emit, else None."""
        bucket = self._bucket_for(epoch)

        if bucket != self._bucket_start:
            # New bucket — reset
            self._bucket_start = bucket
            self._o = self._h = self._l = self._c = price
            self._v = size
        else:
            self._h = max(self._h, price)
            self._l = min(self._l, price)
            self._c = price
            self._v += size

        self._dirty = True

        now = time.monotonic()  # time is imported at module level
        if now - self._last_emit >= self.EMIT_INTERVAL and self._dirty:
            self._last_emit = now
            self._dirty = False
            return self.snapshot()

        return None

    def snapshot(self) -> dict:
        return {
            "type": "candle",
            "t": self._bucket_start,
            "o": self._o,
            "h": self._h,
            "l": self._l,
            "c": self._c,
            "v": self._v,
        }
```

Also add `import time` to the **module-level** imports at the top of `stream.py` (alongside the existing `import asyncio`, `import logging`, etc.).

- [ ] **Step 2: Integrate CandleFlow into DatabentoLiveStream**

In `DatabentoLiveStream.__init__`, add:

```python
self._candle_flow = CandleFlow()
```

In the `_stream_loop` method, the existing trade-handling code (lines 198-221 of `stream.py`) looks like:

```python
ts = datetime.fromtimestamp(record.ts_event / 1e9, tz=timezone.utc)
# ... then for trade records:
price = record.price / 1e9
size = record.size
# ... builds tick event, then:
self._publish(event)
```

After the `self._publish(event)` call for tick events (line 221), add:

```python
ts_epoch = record.ts_event / 1e9
candle_event = self._candle_flow.update(price, record.size, ts_epoch)
if candle_event:
    self._publish(candle_event)
```

Note: The variable is `record` (not `rec`) — matches the existing `async for record in client:` loop at line 194.

- [ ] **Step 3: Verify manually**

Open a browser to the SSE stream endpoint while the market is open:
```
http://localhost:8000/api/trading/market/stream?symbol=NQ
```

Every ~5 seconds during market hours, you should see `event: candle` SSE events alongside the usual `event: tick` and `event: book` events.

- [ ] **Step 4: Commit**

```bash
git add backend/src/services/market_service.py backend/src/api/routes/market.py backend/src/market_data/stream.py
git commit -m "feat(trading): add candle endpoint and SSE candle events

Add GET /api/trading/market/candles for OHLCV chart data via Databento.
Add CandleFlow aggregation to live stream — emits partial candle events
every 5s for real-time chart updates."
```

---

## Chunk 2: Frontend — Types, API, Hook, Dependency

---

### Task 4: Install lightweight-charts

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: Install the dependency**

```bash
cd frontend && npm install lightweight-charts
```

This adds TradingView's open-source chart library (~40KB gzipped, MIT license).

- [ ] **Step 2: Verify installation**

```bash
cd frontend && npm ls lightweight-charts
```

Expected: Shows `lightweight-charts@4.x.x`

---

### Task 5: Add CandleData types

**Files:**
- Modify: `frontend/src/types/market.ts`

- [ ] **Step 1: Add candle types**

Add at the end of `market.ts` (after the `IndicatorsResponse` interface, line 304):

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

---

### Task 6: Add `getCandles` API function

**Files:**
- Modify: `frontend/src/services/api.ts`

- [ ] **Step 1: Add the API call**

Add inside the `api` object, in the `// ============ Market Data / Scanner ============` section (after `getMarketSession`, around line 963):

```typescript
async getCandles(symbol = 'NQ', interval = '5m', date?: string): Promise<import('@/types/market').CandlesResponse> {
  const params = new URLSearchParams({ symbol, interval });
  if (date) params.set('date', date);
  return fetchJson(`/trading/market/candles?${params}`);
},
```

---

### Task 7: Update useMarketStream hook to handle candle events

**Files:**
- Modify: `frontend/src/hooks/useMarketStream.ts`

- [ ] **Step 1: Add CandleData import and lastCandle state**

Update the import and add state:

```typescript
import { useState, useEffect, useRef } from 'react';
import type { StreamTickEvent, StreamBookEvent, CandleData } from '@/types/market';

export function useMarketStream(symbol: string = 'NQ') {
  const [lastTick, setLastTick] = useState<StreamTickEvent | null>(null);
  const [book, setBook] = useState<StreamBookEvent | null>(null);
  const [lastCandle, setLastCandle] = useState<CandleData | null>(null);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const tickBuffer = useRef<StreamTickEvent[]>([]);
```

- [ ] **Step 2: Add candle event listener**

Add after the existing `es.addEventListener('book', ...)` call:

```typescript
es.addEventListener('candle', (e) => {
  setLastCandle(JSON.parse(e.data));
});
```

- [ ] **Step 3: Update the return value**

Change the return statement:

```typescript
return { lastTick, book, lastCandle, connected };
```

- [ ] **Step 4: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/types/market.ts frontend/src/services/api.ts frontend/src/hooks/useMarketStream.ts
git commit -m "feat(trading): add candle types, API, and SSE candle handling

Install lightweight-charts v4. Add CandleData/CandlesResponse types.
Add getCandles() API function. Extend useMarketStream to handle candle
SSE events and expose lastCandle."
```

---

## Chunk 3: CandleChart Component

---

### Task 8: Create CandleChart component

**Files:**
- Create: `frontend/src/components/Terminal/pages/CandleChart.tsx`

This component initializes a lightweight-charts candlestick chart, overlays levels as price lines, and updates the last candle in real-time from SSE data.

- [ ] **Step 1: Write the CandleChart component**

Create `frontend/src/components/Terminal/pages/CandleChart.tsx`:

```tsx
import { useEffect, useRef } from 'react';
import { createChart, CrosshairMode, type IChartApi, type ISeriesApi, LineStyle } from 'lightweight-charts';
import type { CandleData } from '@/types/market';
import type { LadderLevel } from './TradingIntradayPage';

// ─── Level color/style mapping ───────────────────────────────────────────────

interface SignalLevels {
  entry?: number;
  stop?: number;
  target?: number;
}

const LEVEL_STYLE: Record<string, { color: string; lineStyle: LineStyle; lineWidth: number }> = {
  vwap:      { color: '#60A5FA', lineStyle: LineStyle.Solid,  lineWidth: 2 },
  sd:        { color: '#60A5FA', lineStyle: LineStyle.Dashed, lineWidth: 1 },
  poc:       { color: '#FACC15', lineStyle: LineStyle.Solid,  lineWidth: 2 },
  vah:       { color: '#FACC15', lineStyle: LineStyle.Dashed, lineWidth: 1 },
  val:       { color: '#FACC15', lineStyle: LineStyle.Dashed, lineWidth: 1 },
  ib:        { color: '#22D3EE', lineStyle: LineStyle.Dashed, lineWidth: 1 },
  pdh:       { color: '#4ADE80', lineStyle: LineStyle.Dotted, lineWidth: 1 },
  pdl:       { color: '#F87171', lineStyle: LineStyle.Dotted, lineWidth: 1 },
  swing:     { color: '#A78BFA', lineStyle: LineStyle.Dotted, lineWidth: 1 },
  ob:        { color: '#FB923C', lineStyle: LineStyle.Dashed, lineWidth: 1 },
  fvg:       { color: '#FBBF24', lineStyle: LineStyle.Dashed, lineWidth: 1 },
  overnight: { color: '#71717A', lineStyle: LineStyle.Dotted, lineWidth: 1 },
  naked:     { color: '#FB923C', lineStyle: LineStyle.Dotted, lineWidth: 1 },
  session:   { color: '#71717A', lineStyle: LineStyle.Dotted, lineWidth: 1 },
  default:   { color: '#52525B', lineStyle: LineStyle.Dotted, lineWidth: 1 },
};

function getLevelStyle(category: string) {
  return LEVEL_STYLE[category] ?? LEVEL_STYLE.default;
}

// ─── Component ───────────────────────────────────────────────────────────────

export function CandleChart({ candles, levels, signalLevels, lastCandle }: {
  candles: CandleData[];
  levels: LadderLevel[];
  signalLevels: SignalLevels | null;
  lastCandle: CandleData | null;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);

  // ── Create chart on mount ──
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: '#09090b' },
        textColor: '#a1a1aa',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: '#1c1c22' },
        horzLines: { color: '#1c1c22' },
      },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        borderColor: '#27272a',
      },
      rightPriceScale: {
        borderColor: '#27272a',
      },
      autoSize: true,
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#4CAF50',
      downColor: '#EF5350',
      wickUpColor: '#4CAF50',
      wickDownColor: '#EF5350',
      borderVisible: false,
    });

    const volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    });

    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;

    return () => {
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
    };
  }, []);

  // ── Load candle data ──
  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current || candles.length === 0) return;

    const candleData = candles.map(c => ({
      time: c.t as any,
      open: c.o,
      high: c.h,
      low: c.l,
      close: c.c,
    }));

    const volumeData = candles.map(c => ({
      time: c.t as any,
      value: c.v,
      color: c.c >= c.o ? 'rgba(76,175,80,0.3)' : 'rgba(239,83,80,0.3)',
    }));

    candleSeriesRef.current.setData(candleData);
    volumeSeriesRef.current.setData(volumeData);
  }, [candles]);

  // ── Real-time candle updates from SSE ──
  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current || !lastCandle) return;

    candleSeriesRef.current.update({
      time: lastCandle.t as any,
      open: lastCandle.o,
      high: lastCandle.h,
      low: lastCandle.l,
      close: lastCandle.c,
    });

    volumeSeriesRef.current.update({
      time: lastCandle.t as any,
      value: lastCandle.v,
      color: lastCandle.c >= lastCandle.o ? 'rgba(76,175,80,0.3)' : 'rgba(239,83,80,0.3)',
    });
  }, [lastCandle]);

  // ── Level overlays ──
  useEffect(() => {
    if (!candleSeriesRef.current) return;
    const series = candleSeriesRef.current;

    // Clear existing price lines
    const existingLines = (series as any).priceLines?.() ?? [];
    // lightweight-charts doesn't expose a removeAllPriceLines; we track and remove manually
    // Simplest approach: remove series and re-add (but that loses data).
    // Instead, we'll create price lines fresh each time levels change.
    // The series.createPriceLine returns a reference we can remove.

    // Store refs for cleanup
    const lineRefs: any[] = [];

    for (const level of levels) {
      const style = getLevelStyle(level.category);

      if (level.zone && level.priceHigh != null) {
        // Zone levels: two lines (top + bottom)
        lineRefs.push(series.createPriceLine({
          price: level.priceHigh,
          color: style.color,
          lineWidth: style.lineWidth as any,
          lineStyle: style.lineStyle,
          axisLabelVisible: true,
          title: `${level.label} Top`,
        }));
        lineRefs.push(series.createPriceLine({
          price: level.price,
          color: style.color,
          lineWidth: style.lineWidth as any,
          lineStyle: style.lineStyle,
          axisLabelVisible: false,
          title: `${level.label} Bot`,
        }));
      } else {
        lineRefs.push(series.createPriceLine({
          price: level.price,
          color: style.color,
          lineWidth: style.lineWidth as any,
          lineStyle: style.lineStyle,
          axisLabelVisible: true,
          title: level.label,
        }));
      }
    }

    return () => {
      // Cleanup: remove all price lines we added
      for (const line of lineRefs) {
        try { series.removePriceLine(line); } catch { /* already removed */ }
      }
    };
  }, [levels]);

  // ── Signal E/S/T overlays ──
  useEffect(() => {
    if (!candleSeriesRef.current || !signalLevels) return;
    const series = candleSeriesRef.current;
    const lineRefs: any[] = [];

    if (signalLevels.entry != null) {
      lineRefs.push(series.createPriceLine({
        price: signalLevels.entry,
        color: '#06B6D4',
        lineWidth: 1,
        lineStyle: LineStyle.Solid,
        axisLabelVisible: true,
        title: `E ${signalLevels.entry.toFixed(0)}`,
      }));
    }
    if (signalLevels.stop != null) {
      lineRefs.push(series.createPriceLine({
        price: signalLevels.stop,
        color: '#EF5350',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: `S ${signalLevels.stop.toFixed(0)}`,
      }));
    }
    if (signalLevels.target != null) {
      lineRefs.push(series.createPriceLine({
        price: signalLevels.target,
        color: '#4CAF50',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: `T ${signalLevels.target.toFixed(0)}`,
      }));
    }

    return () => {
      for (const line of lineRefs) {
        try { series.removePriceLine(line); } catch { /* already removed */ }
      }
    };
  }, [signalLevels]);

  return (
    <div ref={containerRef} className="w-full h-full" />
  );
}
```

**Key design decisions:**
- Chart created once on mount, data loaded via `setData()`, real-time via `update()`
- `chart.remove()` in cleanup prevents memory leaks
- Level price lines are recreated when `levels` changes (cleanup removes old ones)
- Signal E/S/T lines are separate effect tied to `signalLevels` (added/removed when signal expands/collapses)
- `autoSize: true` handles responsive resizing
- Volume series on separate price scale (`volume`) pinned to bottom 20%

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/Terminal/pages/CandleChart.tsx
git commit -m "feat(trading): add CandleChart component with level overlays

lightweight-charts candlestick chart with volume histogram, horizontal
price lines for all session levels (VWAP, POC, IB, etc.), signal E/S/T
overlays, and real-time candle updates from SSE stream."
```

---

## Chunk 4: GaugeStrip Component

---

### Task 9: Create GaugeStrip component

**Files:**
- Create: `frontend/src/components/Terminal/pages/GaugeStrip.tsx`

- [ ] **Step 1: Write the GaugeStrip component**

Create `frontend/src/components/Terminal/pages/GaugeStrip.tsx`:

```tsx
import type { OrderflowIndicators, MacroSnapshot, MarketSession } from '@/types/market';

// ─── Gauge helper ────────────────────────────────────────────────────────────

function Gauge({ label, value, color, bar }: {
  label: string;
  value: string;
  color: string;   // tailwind text color class
  bar?: number;     // 0-1 fill fraction, omit for non-numeric gauges
}) {
  return (
    <div className="border border-zinc-700 bg-zinc-900/50 px-2 py-1.5 min-w-[55px] text-center">
      <div className="text-[9px] text-zinc-500 uppercase tracking-wider">{label}</div>
      <div className={`text-xs font-mono font-medium ${color}`}>{value}</div>
      {bar != null && (
        <div className="mt-0.5 h-1 bg-zinc-800 rounded-sm overflow-hidden">
          <div
            className={`h-full rounded-sm ${color.replace('text-', 'bg-')}/30`}
            style={{ width: `${Math.min(Math.max(bar, 0), 1) * 100}%` }}
          />
        </div>
      )}
    </div>
  );
}

// ─── Color helpers ───────────────────────────────────────────────────────────

function signColor(val: number): string {
  return val > 0 ? 'text-green-400' : val < 0 ? 'text-red-400' : 'text-zinc-400';
}

function fmtSigned(val: number | null | undefined): string {
  if (val == null) return '--';
  return `${val > 0 ? '+' : ''}${val.toLocaleString()}`;
}

// ─── Main ────────────────────────────────────────────────────────────────────

export function GaugeStrip({ session, orderflow, macro }: {
  session: MarketSession | undefined;
  orderflow: OrderflowIndicators | undefined;
  macro: MacroSnapshot | undefined;
}) {
  // Delta
  const delta = orderflow?.delta;
  const deltaColor = delta != null ? signColor(delta) : 'text-zinc-500';
  const deltaBar = delta != null ? Math.abs(delta) / 5000 : undefined;

  // CVD
  const cvd = orderflow?.cvd;
  const cvdTrend = orderflow?.cvd_trend ?? 'flat';
  const cvdIcon = cvdTrend === 'rising' ? ' ^' : cvdTrend === 'falling' ? ' v' : '';
  const cvdColor = cvdTrend === 'rising' ? 'text-green-400' : cvdTrend === 'falling' ? 'text-red-400' : 'text-zinc-400';
  const cvdBar = cvd != null ? Math.abs(cvd) / 10000 : undefined;

  // Rotation Factor
  const rf = session?.rotation_factor;
  const rfColor = rf != null ? signColor(rf) : 'text-zinc-500';
  const rfBar = rf != null ? Math.abs(rf) / 6 : undefined;

  // ASPR
  const aspr = session?.aspr;
  const asprPct = session?.aspr_percentile;
  const asprColor = asprPct != null
    ? asprPct < 0.3 ? 'text-green-400' : asprPct > 0.7 ? 'text-red-400' : 'text-yellow-400'
    : 'text-zinc-400';
  const asprBar = asprPct ?? undefined;

  // Imbalance
  const imbDir = orderflow?.stacked_imbalance_direction;
  const imbCount = orderflow?.stacked_imbalance_count ?? 0;
  const imbVal = imbDir && imbDir !== 'neutral' && imbCount > 0 ? `${imbDir} x${imbCount}` : '--';
  const imbColor = imbDir === 'buy' ? 'text-green-400' : imbDir === 'sell' ? 'text-red-400' : 'text-zinc-500';

  // Value migration
  const valMig = session?.value_migration;
  const valIcon = valMig === 'up' ? '^' : valMig === 'down' ? 'v' : '--';
  const valColor = valMig === 'up' ? 'text-green-400' : valMig === 'down' ? 'text-red-400' : 'text-zinc-500';

  // VIX
  const vix = macro?.vix;
  const vixColor = vix != null
    ? vix < 18 ? 'text-green-400' : vix > 25 ? 'text-red-400' : 'text-yellow-400'
    : 'text-zinc-500';

  // Big trades
  const bigCount = orderflow?.big_trades_count ?? 0;
  const bigNet = orderflow?.big_trades_net_delta ?? 0;
  const bigColor = bigCount > 0 ? (bigNet > 0 ? 'text-green-400' : 'text-red-400') : 'text-zinc-500';

  // Boolean gauges
  const vsaActive = orderflow?.vsa_absorption ?? false;
  const trappedActive = orderflow?.trapped_traders ?? false;
  const stopRunActive = orderflow?.stop_run_detected ?? false;

  return (
    <div className="flex flex-wrap gap-1">
      <Gauge label="DELTA" value={fmtSigned(delta)} color={deltaColor} bar={deltaBar} />
      <Gauge label="CVD" value={cvd != null ? `${cvd.toLocaleString()}${cvdIcon}` : '--'} color={cvdColor} bar={cvdBar} />
      <Gauge label="ROT.F" value={rf != null ? fmtSigned(rf) : '--'} color={rfColor} bar={rfBar} />
      <Gauge label="ASPR" value={aspr != null ? `${aspr.toFixed(0)}pt${asprPct != null ? ` P${(asprPct * 100).toFixed(0)}` : ''}` : '--'} color={asprColor} bar={asprBar} />
      <Gauge label="IMBAL" value={imbVal} color={imbColor} />
      <Gauge label="VALUE" value={valIcon} color={valColor} />
      <Gauge label="VIX" value={vix != null ? vix.toFixed(1) : '--'} color={vixColor} />
      <Gauge label="BIG" value={bigCount > 0 ? `x${bigCount}` : '--'} color={bigColor} />
      <Gauge label="VSA" value={vsaActive ? '✓' : '--'} color={vsaActive ? 'text-green-400' : 'text-zinc-600'} />
      <Gauge label="TRAP" value={trappedActive ? '✓' : '--'} color={trappedActive ? 'text-orange-400' : 'text-zinc-600'} />
      <Gauge label="STOP" value={stopRunActive ? '✓' : '--'} color={stopRunActive ? 'text-red-400' : 'text-zinc-600'} />
    </div>
  );
}
```

**Key design decisions:**
- Each gauge is a small bordered box with label, value, and optional mini bar
- Normalization scales: Delta ±5k, CVD ±10k, RF ±6, ASPR uses percentile directly
- Boolean gauges (VSA, Trap, Stop) show checkmark when active, `--` when inactive
- No SVG — pure CSS bars for simplicity

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/Terminal/pages/GaugeStrip.tsx
git commit -m "feat(trading): add GaugeStrip component with 11 indicator gauges

Compact horizontal gauge strip showing Delta, CVD, Rotation Factor,
ASPR, Imbalance, Value migration, VIX, Big trades, VSA, Trapped
traders, and Stop run indicators."
```

---

## Chunk 5: TradingIntradayPage Rewrite + Integration

---

### Task 10: Rewrite TradingIntradayPage layout

**Files:**
- Modify: `frontend/src/components/Terminal/pages/TradingIntradayPage.tsx`

This is a full rewrite of the page layout. We keep all data-fetching logic, `buildLadder()`, `classifyLevel()`, `LEVEL_COLORS`, `SignalRow`, and handler functions. We replace the two-column layout with: chart (full width, ~75% height) + gauge strip + signal list.

- [ ] **Step 1: Update imports**

Replace the existing imports at the top of the file:

```tsx
import { useState, useEffect, useCallback, useMemo } from 'react';
import { api } from '@/services/api';
import { TabIcon, TAB_COLORS } from '../TabBar';
import { useMarketStream } from '@/hooks/useMarketStream';
import { CandleChart } from './CandleChart';
import { GaugeStrip } from './GaugeStrip';
import type { ExpandedSession, IndicatorsResponse, TradingSignal, ScanCondition, CandleData, CandlesResponse } from '@/types/market';
```

- [ ] **Step 2: Add candle state and fetching**

In the `TradingIntradayPage` component, add candle-related state alongside the existing state:

```tsx
const [candles, setCandles] = useState<CandleData[]>([]);
const [candleError, setCandleError] = useState(false);
```

Update the `useMarketStream` destructure to include `lastCandle`:

```tsx
const { lastTick, lastCandle, connected } = useMarketStream();
```

In `fetchData`, add candle fetch to the `Promise.all`:

```tsx
const [sessionRes, signalsRes, indicRes, candleRes] = await Promise.all([
  api.getExpandedSession().catch(() => null),
  api.getMarketSignals().catch(() => ({ signals: [] })),
  api.getIndicators().catch(() => null),
  api.getCandles().catch(() => ({ candles: [], _error: true })),
]);
if (sessionRes) setSession(sessionRes);
setSignals((signalsRes as { signals: TradingSignal[] }).signals || []);
if (indicRes) setIndicators(indicRes);
if (candleRes && !(candleRes as any)._error) {
  setCandles(candleRes.candles);
  setCandleError(false);
} else if ((candleRes as any)?._error) {
  setCandleError(true);
}
```

Do NOT add candle re-fetch to the 30s auto-refresh — candles are updated via SSE stream. Only re-fetch candles on manual Refresh or SSE reconnect.

In `handleRefresh`, add candle re-fetch:

```tsx
const [sessionRes, signalsRes, indicRes, candleRes] = await Promise.all([
  api.getExpandedSession().catch(() => null),
  api.triggerMarketScan(70).catch(() => ({ signals: [] })),
  api.getIndicators().catch(() => null),
  api.getCandles().catch(() => null),
]);
// ... existing setters ...
if (candleRes) setCandles(candleRes.candles);
```

- [ ] **Step 3: Add SSE reconnect re-fetch**

Add a `useEffect` that re-fetches candles when SSE reconnects (to resync after missed ticks):

```tsx
const prevConnected = useRef(false);
useEffect(() => {
  if (connected && !prevConnected.current) {
    // SSE reconnected — re-fetch candles to resync
    api.getCandles().then(res => { if (res) setCandles(res.candles); }).catch(() => {});
  }
  prevConnected.current = connected;
}, [connected]);
```

Add `useRef` to the imports if not already there. It's not currently imported — add it:

```tsx
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
```

- [ ] **Step 4: Compute signal levels for chart overlay**

Add a `useMemo` to derive signal levels from the currently expanded signal:

```tsx
const signalLevels = useMemo(() => {
  if (expandedSignal == null) return null;
  const sig = signals.find(s => s.id === expandedSignal);
  if (!sig) return null;
  return {
    entry: sig.suggested_entry ?? undefined,
    stop: sig.suggested_stop ?? undefined,
    target: sig.suggested_target ?? undefined,
  };
}, [expandedSignal, signals]);
```

- [ ] **Step 5: Replace the JSX layout**

Replace everything from `return (` to the end of the component with this new layout:

```tsx
if (loading) return <div className="text-zinc-500 text-sm p-4">Loading scanner...</div>;

return (
  <div className="flex flex-col h-full">

    {/* ─── Header ─── */}
    <div className="flex items-center gap-3 flex-wrap border-b border-zinc-800 pb-2 mb-2 px-1">
      <TabIcon name="tradingIntraday" color={TAB_COLORS.tradingIntraday} size={16} />
      <span className="text-sm font-semibold text-text">Intraday</span>

      {currentPrice != null && (
        <span className="font-mono text-sm text-tabTradingScanner font-bold">
          NQ {currentPrice.toFixed(2)}
        </span>
      )}

      <div className="flex items-center gap-1 text-[10px]">
        <span className={connected ? 'text-green-400' : 'text-red-400'}>●</span>
        <span className="text-zinc-500">{connected ? 'Live' : 'Offline'}</span>
      </div>

      {pricePos?.vwap_deviation_sd != null && (
        <span className={`text-[10px] font-mono ${
          Math.abs(pricePos.vwap_deviation_sd) > 2 ? 'text-red-400' :
          Math.abs(pricePos.vwap_deviation_sd) > 1 ? 'text-yellow-400' : 'text-zinc-400'
        }`}>
          {pricePos.vwap_deviation_sd > 0 ? '+' : ''}{pricePos.vwap_deviation_sd.toFixed(2)} SD
        </span>
      )}

      <div className="flex-1" />

      {/* VP Anchor date pickers */}
      {session?.profiles && (
        <div className="flex gap-2 text-[10px]">
          <div className="flex items-center gap-1">
            <span className="text-zinc-500">Leg:</span>
            <input type="date"
              className="bg-zinc-800 border border-zinc-700 rounded px-1 py-0.5 text-[10px] text-zinc-300 w-28"
              defaultValue={session.profiles.leg?.anchor ?? ''}
              onBlur={e => handleAnchorUpdate('vp_leg_start', e.target.value)} />
          </div>
          <div className="flex items-center gap-1">
            <span className="text-zinc-500">Macro:</span>
            <input type="date"
              className="bg-zinc-800 border border-zinc-700 rounded px-1 py-0.5 text-[10px] text-zinc-300 w-28"
              defaultValue={session.profiles.macro?.anchor ?? ''}
              onBlur={e => handleAnchorUpdate('vp_ongoing_macro_start', e.target.value)} />
          </div>
        </div>
      )}

      <span className="text-[9px] text-zinc-600 font-mono">
        5m{lastRefresh && ` · ${lastRefresh.toLocaleTimeString()}`}
      </span>
      <button onClick={handleRefresh} disabled={isRefreshing}
        className="text-[10px] px-2.5 py-1 border border-zinc-700 text-zinc-400 rounded hover:bg-zinc-800 hover:text-zinc-200 disabled:opacity-40 transition-colors">
        {isRefreshing ? 'Refreshing...' : 'Refresh'}
      </button>
    </div>

    {/* ─── Chart (dominant, ~75% height) ─── */}
    <div className="flex-1 min-h-0 border border-zinc-800 rounded bg-zinc-900/30">
      {candles.length > 0 ? (
        <CandleChart
          candles={candles}
          levels={ladderLevels}
          signalLevels={signalLevels}
          lastCandle={lastCandle}
        />
      ) : candleError ? (
        <div className="flex flex-col items-center justify-center h-full gap-2">
          <span className="text-zinc-500 text-sm">Failed to load candles</span>
          <button onClick={handleRefresh} className="text-[10px] px-3 py-1 border border-zinc-700 text-zinc-400 rounded hover:bg-zinc-800">
            Retry
          </button>
        </div>
      ) : (
        <div className="flex items-center justify-center h-full text-zinc-500 text-sm">
          No candle data. Click Refresh to load.
        </div>
      )}
    </div>

    {/* ─── Bottom: Gauge Strip + Signals ─── */}
    <div className="flex gap-2 mt-2 min-h-[120px] max-h-[200px]">

      {/* Gauges */}
      <div className="flex-shrink-0">
        <GaugeStrip
          session={session?.session}
          orderflow={indicators?.orderflow}
          macro={session?.macro}
        />
      </div>

      {/* Signals */}
      <div className="flex-1 min-w-0 border border-zinc-800 rounded bg-zinc-900/30 overflow-y-auto">
        <div className="sticky top-0 bg-zinc-900 border-b border-zinc-800 px-3 py-1 flex items-center justify-between">
          <span className="text-[10px] font-semibold text-text">
            Signals <span className="text-tabTradingScanner">{signals.length}</span>
          </span>
        </div>
        {signals.length === 0 ? (
          <div className="px-3 py-2 text-center text-zinc-600 text-[10px]">
            No signals above threshold (70)
          </div>
        ) : (
          signals.map(sig => (
            <SignalRow
              key={sig.id}
              sig={sig}
              expanded={expandedSignal === sig.id}
              onToggle={() => setExpandedSignal(expandedSignal === sig.id ? null : sig.id)}
              onTakeTrade={handleTakeTrade}
              connected={connected}
              lastTick={lastTick}
            />
          ))
        )}
      </div>
    </div>
  </div>
);
```

- [ ] **Step 6: Delete removed components**

Delete these component functions from the file (they are no longer used):
- `PriceLadder` (function starting with `function PriceLadder`)
- `ContextStrip` (function starting with `function ContextStrip`)
- `OrderflowPanel` (function starting with `function OrderflowPanel`)
- `VolumeProfilesPanel` (function starting with `function VolumeProfilesPanel`)

In `SignalRow`, delete the "Live orderflow" section inside the expanded view (the `<div>` with `bg-zinc-900/50` that shows `● Price ... Δ1m ... CVD ...`). This is redundant with the gauge strip. The section is approximately lines 584-591 in the current file.

Keep these (still used):
- `SETUP_COLORS`, `LEVEL_COLORS`, `LadderLevel`, `classifyLevel`, `getColors`, `buildLadder` — used by CandleChart via props
- `SignalRow` — used in the signal list (minus the live orderflow strip)

Also keep `LEVEL_COLORS` even though `CandleChart` has its own `LEVEL_STYLE` map — `LEVEL_COLORS` is used by `buildLadder` → `classifyLevel` for the `category` field that `CandleChart` consumes.

- [ ] **Step 7: Export LadderLevel type**

`CandleChart` already imports `LadderLevel` from `TradingIntradayPage`. Add `export` to the existing interface in the page file:

```tsx
export interface LadderLevel {
  price: number;
  label: string;
  category: string;
  zone?: boolean;
  priceHigh?: number;
}
```

---

### Task 11: Visual verification

- [ ] **Step 1: Start dev servers**

```bash
cd backend && python -m src.app serve &
cd frontend && npm run dev
```

Or use the configured launch from `.claude/launch.json`.

- [ ] **Step 2: Verify in browser**

Open `http://localhost:5173`, navigate to the Intraday tab. Check:

1. **Chart renders** — Candlestick chart with volume histogram at bottom
2. **Level overlays** — VWAP (blue solid), POC (yellow solid), IB (cyan dashed), etc. appear as horizontal lines
3. **Empty state** — If no candle data (weekend), shows "No candle data. Click Refresh to load."
4. **Gauges** — 11 gauges show below chart with correct values and colors
5. **Signals** — Signal list to the right of gauges, expand/collapse works
6. **Signal E/S/T on chart** — When a signal is expanded, Entry/Stop/Target lines appear on chart
7. **VP anchors** — Date pickers in header bar trigger VP re-computation
8. **Refresh** — Button triggers compute + data reload + candle re-fetch
9. **SSE live updates** — During market hours, last candle updates in real-time
10. **Responsive** — Page doesn't overflow, chart fills available space

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Terminal/pages/TradingIntradayPage.tsx frontend/src/components/Terminal/pages/CandleChart.tsx
git commit -m "feat(trading): rewrite intraday page with candle chart + gauge strip

Replace multi-panel layout with dominant candlestick chart (lightweight-charts),
horizontal gauge strip (11 indicators), and compact signal list. All session
levels rendered as chart price lines. Signal E/S/T overlays on expand.
VP anchor pickers moved to header toolbar."
```
