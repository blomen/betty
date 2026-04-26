# TradingView Desktop Overlay (Userscript) + Stocks UI Refactor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the local NQ chart UI with a Signals & Values console paired with a Tampermonkey userscript that draws zones / open positions on TradingView (Desktop or web).

**Architecture:** A small `.user.js` userscript installed in Tampermonkey opens a WebSocket back to a new local endpoint `/stocks/ws/tv-overlay`. When the user opens any NQ chart on TradingView, the script attaches once the chart is ready, receives zone / position / signal events, and draws shapes via TradingView's internal `window.TradingView` chart APIs (`createMultipointShape`, `createPositionLine`-equivalent). The Arnold local server reads from the existing in-process `_state` dict and broadcasts deltas to the userscript WebSocket. Frontend deletes `CandleChart.tsx` + chart helpers and replaces the Stocks tab with a card-based `SignalsPage`.

**Tech Stack:** Python 3.10+ asyncio, FastAPI WebSocket, React 19 + TypeScript + Vite + Tailwind, vanilla JS userscript (Tampermonkey or Violentmonkey, in either TV Desktop's Electron or any modern browser on tradingview.com).

**Reference spec:** `C:\Users\rasmu\.claude\plans\i-was-just-thinking-goofy-blanket.md`

---

## File Structure

**New (Python — local Arnold):**
- `arnold/tv_overlay/__init__.py` — public exports.
- `arnold/tv_overlay/router.py` — FastAPI router exposing `/stocks/ws/tv-overlay` (WS) and `/stocks/api/tv-overlay/status` + `/stocks/api/tv-overlay/userscript` (serves the latest `.user.js`).
- `arnold/tv_overlay/broadcaster.py` — diff-and-broadcast loop reading from `src.stocks.dashboard._state`, sending typed JSON messages to attached overlay clients.
- `arnold/tv_overlay/status.py` — thread-safe status snapshot (mirror of the design we had for the bridge).

**New (userscript):**
- `arnold/tv_overlay/userscript/arnold-overlay.user.js` — the file the user installs in Tampermonkey. Self-contained: WebSocket client, draw queue, idempotent shape registry keyed by stable id, reconnect-with-backoff, attach-when-chart-ready, integration with `window.TradingView.activeChart()` drawing API.

**New (Python — server):**
- `backend/src/stocks/dashboard.py` — add `record_depth`, broadcast `depth` event (modify, not new).

**New (frontend):**
- `arnold/frontend/src/pages/stocks/SignalsPage.tsx`
- `arnold/frontend/src/components/stocks/TVOverlayStatus.tsx`
- `arnold/frontend/src/components/stocks/ModelStateCard.tsx`
- `arnold/frontend/src/components/stocks/ZonesTable.tsx`
- `arnold/frontend/src/components/stocks/PositionCard.tsx`
- `arnold/frontend/src/components/stocks/EventLog.tsx`
- `arnold/frontend/src/components/stocks/L2Ladder.tsx`

**Modified:**
- `arnold/frontend/src/hooks/useDashboardWS.ts` — add `depth` state + `case 'depth'` handler.
- `arnold/frontend/src/types/stocks.ts` — add `DepthLevel`, `DepthSnapshot`, `TVOverlayStatus` types.
- `arnold/frontend/src/App.tsx` — drop `ChartPage` import + route, wire `SignalsPage`.
- `arnold/server.py` — register `tv_overlay.router`, start broadcaster task in startup, await its shutdown.
- `arnold/stocks_runtime.py` — wire `stream.on_depth = record_depth` (active mode + passive listener fallback).

**Deleted (frontend):**
- `arnold/frontend/src/pages/stocks/CandleChart.tsx`
- `arnold/frontend/src/pages/stocks/ChartPage.tsx`
- `arnold/frontend/src/pages/stocks/DQNPage.tsx`
- `arnold/frontend/src/pages/stocks/NeuralNetworkSVG.tsx`
- `arnold/frontend/src/lib/indicators.ts`

**No new runtime deps.** No Node subprocess, no `mcp` library, no jackson submodule.

---

## Phase 0: Discover TradingView's chart drawing API surface

Before writing any code, we need to confirm exactly which `window.TradingView` methods exist on a real, signed-in session, what arguments they accept, and that they survive a chart switch. This is the equivalent of the CDP smoke test in the prior plan but for in-page JS.

### Task 0.1: Discover chart-drawing API in DevTools

**Files:** none (sandbox)

- [ ] **Step 1: Open TradingView Desktop, log in, load the NQ 15m chart**

If you don't already have TV Desktop running, launch it normally (no `--remote-debugging-port=9222` needed for this path). Make sure NQ is selected.

- [ ] **Step 2: Open DevTools**

In TV Desktop, press `Ctrl+Shift+I` (Electron exposes DevTools). If that's blocked on your build, alternatively open https://www.tradingview.com/chart/ in a fresh Chrome window and use that — the API surface is identical because Desktop is the same web app.

- [ ] **Step 3: Probe the public chart object**

In the DevTools Console, run:

```javascript
const w = window.TradingViewApi || window.tvWidget || window;
const chart = (w.chart && w.chart()) || (w.activeChart && w.activeChart());
console.log("chart object:", chart);
console.log("methods:", chart && Object.keys(chart).filter(k => typeof chart[k] === 'function').sort());
```

Expected: a chart-like object with methods including some of `createMultipointShape`, `createShape`, `createOrderLine`, `createPositionLine`, `removeEntity`, `setSymbol`, `getVisibleRange`, etc.

- [ ] **Step 4: Test the rectangle draw**

Once you have a chart reference, run:

```javascript
const now = Math.floor(Date.now() / 1000);
const id = chart.createMultipointShape(
  [
    { time: now - 3600, price: chart.priceScale().getVisiblePriceRange ? null : 27400 },
    { time: now,         price: 27450 },
  ],
  { shape: 'rectangle', text: 'arnold-test', overrides: { color: 'rgba(168,85,247,0.5)', backgroundColor: 'rgba(168,85,247,0.18)' } }
);
console.log('drawn id:', id);
```

(Adjust the prices to be inside the visible range of your NQ chart so you can see it.)

Expected: a translucent purple rectangle appears on the chart. The function returns an entity id.

- [ ] **Step 5: Test removing it**

```javascript
chart.removeEntity(id);
```

Expected: rectangle disappears.

- [ ] **Step 6: Test horizontal line for position drawing**

```javascript
const line = chart.createMultipointShape(
  [{ time: now - 3600, price: 27420 }],
  { shape: 'horizontal_line', text: 'entry', overrides: { linecolor: '#10b981' } }
);
```

Expected: a green horizontal line at 27420 with label "entry".

- [ ] **Step 7: Record the actual surface in the plan file**

Append a "Phase 0 verification" section to this plan documenting:
- exact path used to reach the chart object (e.g. `window.TradingViewApi.activeChart()` vs `tvWidget.activeChart()`)
- exact method names that worked (`createMultipointShape` vs alternatives)
- shape names accepted (`rectangle`, `horizontal_line`, `text`, etc.)
- the override key naming (`color` vs `linecolor` vs `backgroundColor` — these matter)
- TV Desktop version + (if web) browser/version

Commit:

```bash
git add docs/superpowers/plans/2026-04-26-tradingview-bridge.md
git commit -m "docs(tv-overlay): record Phase 0 in-page API surface"
```

If the API surface is meaningfully different from what's assumed below (different method names, different argument shape), the userscript in Phase 4 must be updated to match before continuing. The rest of the plan still holds.

### Phase 0 verification (recorded 2026-04-26)

Probed against `https://www.tradingview.com/chart/?symbol=CME_MINI%3ANQ1!` via Playwright (Chromium, unauthenticated). Manual visual confirmation of horizontal-line draw in user's signed-in TradingView Desktop session.

**Confirmed surface — pin these values into the userscript in Phase 4:**

- **Chart path:** `window.TradingViewApi.activeChart()`. (`window.tvWidget` was undefined on a fresh, unauthenticated session. `window.TradingView` exists but is the namespace, not the chart.)
- **Rectangle draw:** `chart.createMultipointShape([{time, price}, {time, price}], { shape: 'rectangle', text, overrides: { color, backgroundColor } })` — succeeds, returns entity id.
- **Horizontal line draw:** `chart.createMultipointShape([{time, price}], { shape: 'horizontal_line', text, overrides: { linecolor, showLabel } })` — succeeds, returns entity id. Visually confirmed in user's TV Desktop (screenshot showed green line at 27420 labeled "entry").
- **Removal:** `chart.removeEntity(id)` — succeeds.

**Quirks noted:**
- Returned entity IDs are objects, not primitives — `JSON.stringify` flattens them to `{}`. They remain valid references in JS scope. The userscript holds them in a `Map`, so this is fine — but **never round-trip an entity ID through the WebSocket as JSON.** Use stable string keys (`zone:27400.0`, `pos:current:entry`) for cross-process identity.
- `window.tvWidget` only exists on certain entry paths (likely authenticated chart sessions or the embedded widget). The userscript must use `TradingViewApi` first; fall back to `tvWidget`/`TradingView` only if `TradingViewApi.activeChart()` returns nullish.

---

## Phase 1: Server-side L2 depth broadcast

(Identical to the prior plan — depth wiring is independent of the overlay transport.)

### Task 1.1: Add `record_depth` + `depth` broadcast type

**Files:**
- Modify: `backend/src/stocks/dashboard.py:64-79` (state dict) + append below `update_status` (~line 592)
- Test: `backend/tests/test_dashboard_depth.py` (new)

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_dashboard_depth.py`:

```python
"""Regression tests for the L2 depth path in the local dashboard module."""
from __future__ import annotations

import asyncio

import pytest

from src.stocks import dashboard as dash


@pytest.mark.asyncio
async def test_record_depth_appends_and_broadcasts():
    """A depth tick should be coalesced into the latest snapshot and broadcast as `depth`."""
    received: list[dict] = []

    async def fake_broadcast(event: dict) -> None:
        received.append(event)

    dash.bind_loop(asyncio.get_running_loop())
    orig = dash.broadcast
    dash.broadcast = fake_broadcast  # type: ignore[assignment]
    try:
        dash.record_depth({"price": 27400.0, "currentVolume": 5, "type": 1})
        dash.record_depth({"price": 27400.25, "currentVolume": 7, "type": 2})
        dash.record_depth({"price": 27400.0, "currentVolume": 9, "type": 1})

        # Force a flush regardless of throttle.
        dash._last_depth_emit = 0
        dash.record_depth({"price": 27400.5, "currentVolume": 4, "type": 2})

        await asyncio.sleep(0.05)
    finally:
        dash.broadcast = orig  # type: ignore[assignment]

    assert any(e["type"] == "depth" for e in received), "expected at least one depth broadcast"
    last = next(e for e in reversed(received) if e["type"] == "depth")
    bids = {lvl["price"]: lvl["size"] for lvl in last["bids"]}
    asks = {lvl["price"]: lvl["size"] for lvl in last["asks"]}
    assert bids[27400.0] == 9, "second bid update at 27400 should overwrite first"
    assert asks[27400.25] == 7
    assert asks[27400.5] == 4
```

- [ ] **Step 2: Run test, confirm failure**

Run: `cd backend && pytest tests/test_dashboard_depth.py -v`
Expected: FAIL with `AttributeError: module 'src.stocks.dashboard' has no attribute 'record_depth'`.

- [ ] **Step 3: Add `depth` to state dict**

Edit `backend/src/stocks/dashboard.py` lines 63-79 — replace the `_state` literal with:

```python
_state = {
    "ticks": deque(maxlen=2000),
    "signals": deque(maxlen=100),
    "quotes": deque(maxlen=1),
    "zones": [],
    "depth": {"bids": {}, "asks": {}, "ts": 0.0},
    "account": {},
    "positions": [],
    "stats": {
        "tick_count": 0,
        "signal_count": 0,
        "trade_count": 0,
        "session_start": None,
        "relay_connected": False,
        "stream_running": False,
    },
}
```

- [ ] **Step 4: Implement `record_depth`**

Append below `update_status` (~line 592):

```python
_DEPTH_THROTTLE_S = 0.2
_last_depth_emit = 0.0


def record_depth(level: dict) -> None:
    """Called from TopstepXStream.on_depth.

    `level` shape (GatewayDepth, see backend/src/stocks/topstepx_stream.py:276-289):
      {"price": float, "currentVolume": int, "type": 1|2}  (1 = bid, 2 = ask)
    Maintains a price→size dict per side; size 0 removes the level.
    Broadcasts a top-20 snapshot at most every _DEPTH_THROTTLE_S seconds.
    """
    global _last_depth_emit
    price = float(level.get("price", 0))
    if price == 0:
        return
    size = int(level.get("currentVolume", 0))
    side = level.get("type", 0)
    book = _state["depth"]["bids"] if side == 1 else _state["depth"]["asks"]
    if size <= 0:
        book.pop(price, None)
    else:
        book[price] = size

    now = _time.time()
    if now - _last_depth_emit < _DEPTH_THROTTLE_S:
        return
    _last_depth_emit = now
    _state["depth"]["ts"] = now

    bids_sorted = sorted(_state["depth"]["bids"].items(), key=lambda kv: -kv[0])[:20]
    asks_sorted = sorted(_state["depth"]["asks"].items(), key=lambda kv: kv[0])[:20]
    _emit(
        {
            "type": "depth",
            "bids": [{"price": p, "size": s} for p, s in bids_sorted],
            "asks": [{"price": p, "size": s} for p, s in asks_sorted],
            "ts": now,
        }
    )
```

- [ ] **Step 5: Run test, confirm pass**

Run: `cd backend && pytest tests/test_dashboard_depth.py -v`
Expected: PASS.

- [ ] **Step 6: Add `depth` to GET /api/state response**

In `create_dashboard_router` `get_state` (line 100-109):

```python
    @router.get("/api/state")
    async def get_state():
        depth = _state["depth"]
        bids = sorted(depth["bids"].items(), key=lambda kv: -kv[0])[:20]
        asks = sorted(depth["asks"].items(), key=lambda kv: kv[0])[:20]
        return {
            "ticks": list(_state["ticks"])[-200:],
            "signals": list(_state["signals"]),
            "quote": list(_state["quotes"])[-1] if _state["quotes"] else None,
            "zones": _state["zones"],
            "depth": {
                "bids": [{"price": p, "size": s} for p, s in bids],
                "asks": [{"price": p, "size": s} for p, s in asks],
                "ts": depth["ts"],
            },
            "account": _state["account"],
            "positions": _state["positions"],
            "stats": _state["stats"],
        }
```

- [ ] **Step 7: Commit**

```bash
git add backend/src/stocks/dashboard.py backend/tests/test_dashboard_depth.py
git commit -m "feat(stocks): add L2 depth state + broadcast in dashboard module"
```

### Task 1.2: Wire `record_depth` from `arnold/stocks_runtime.py`

**Files:**
- Modify: `arnold/stocks_runtime.py:210-225` (active imports), `arnold/stocks_runtime.py:299-301` (callback wiring), `arnold/stocks_runtime.py:128-180` (passive listener)

- [ ] **Step 1: Add `record_depth` import in active bootstrap**

Edit lines 210-222:

```python
    from src.stocks.dashboard import (
        bind_loop,
        record_depth,
        record_dqn_inference,
        record_fill,
        record_quote,
        record_signal,
        record_tick,
        update_status,
        update_zones,
    )
```

- [ ] **Step 2: Wire `stream.on_depth`**

Edit line 299-301:

```python
    stream.on_tick = on_tick
    stream.on_fill = on_fill
    stream.on_quote = record_quote
    stream.on_depth = record_depth
```

- [ ] **Step 3: Mirror in passive autonomous listener (best-effort)**

In `_passive_dashboard_listener` (~line 128-180), check whether the server's `/ws/signals` already broadcasts depth:

Run: `grep -n "depth" backend/src/api/routes/signals_ws.py`

- If it does: add `elif t == "depth": record_depth(msg)` next to the zone branch, mirroring the format the server emits. Note that the *server-side* aggregation may already be top-20 dicts rather than raw level updates; in that case the passive listener should pass through directly with `_state["depth"]["bids"] = msg.get("bids", [])` rather than calling `record_depth` per level.
- If it doesn't: add a single comment `# NOTE: server /ws/signals does not yet broadcast L2 depth in autonomous mode; passive listener gets ticks/zones/signals only.` next to the existing message dispatch and proceed.

- [ ] **Step 4: Smoke check**

Start `arnold.bat` with active mode (TopstepX configured). In another terminal:

```bash
curl http://127.0.0.1:8000/stocks/api/state | python -m json.tool | head -50
```

Expected: `depth.bids` and `depth.asks` arrays populated with 1+ levels.

- [ ] **Step 5: Commit**

```bash
git add arnold/stocks_runtime.py
git commit -m "feat(stocks): wire TopstepX GatewayDepth to local dashboard record_depth"
```

---

## Phase 2: Frontend depth state in `useDashboardWS`

(Identical to prior plan — same hook changes regardless of overlay transport.)

### Task 2.1: Extend `DashboardState` and types

**Files:**
- Modify: `arnold/frontend/src/types/stocks.ts:323` (append types)
- Modify: `arnold/frontend/src/hooks/useDashboardWS.ts`

- [ ] **Step 1: Add depth + overlay-status types**

Append to `arnold/frontend/src/types/stocks.ts`:

```typescript
export interface DepthLevel {
  price: number
  size: number
}

export interface DepthSnapshot {
  bids: DepthLevel[]
  asks: DepthLevel[]
  ts: number
}

export interface TVOverlayStatus {
  attached_clients: number
  last_paint_at: number | null
  draw_count: number
  retries: number
  error: string | null
  userscript_url: string
}
```

- [ ] **Step 2: Add `depth` to `DashboardState`**

Edit `useDashboardWS.ts` lines 1-18:

```typescript
import { useEffect, useRef, useState, useCallback } from 'react'
import type { Signal, Zone, Fill, ExitEvent, Quote, Position, DQNInferenceEvent, DepthSnapshot } from '@/types/stocks'

export interface DashboardState {
  connected: boolean
  relayConnected: boolean
  streamRunning: boolean
  lastPrice: number | null
  tickCount: number
  signals: Signal[]
  zones: Zone[]
  fills: Fill[]
  exits: ExitEvent[]
  positions: Position[]
  quote: Quote | null
  depth: DepthSnapshot | null
  dqnInference: DQNInferenceEvent | null
  dqnInferenceAt: number | null
}
```

- [ ] **Step 3: Default `depth: null`**

Edit lines 31-45 — add `depth: null,` next to `quote: null,`.

- [ ] **Step 4: Handle `depth` events**

In the `switch (msg.type)` block (around line 109-124), add:

```typescript
        case 'depth':
          setState(s => ({
            ...s,
            depth: { bids: msg.bids, asks: msg.asks, ts: msg.ts },
          }))
          break
```

- [ ] **Step 5: Seed depth from REST snapshot**

Replace the `ws.onopen` block (lines 57-72):

```typescript
    ws.onopen = () => {
      setState(s => ({ ...s, connected: true }))
      fetch('/stocks/api/state')
        .then(r => (r.ok ? r.json() : null))
        .then(snap => {
          if (!snap) return
          setState(s => {
            const next = { ...s }
            if (Array.isArray(snap.zones) && snap.zones.length > 0 && s.zones.length === 0) {
              next.zones = snap.zones
            }
            if (snap.depth && (snap.depth.bids?.length || snap.depth.asks?.length)) {
              next.depth = snap.depth
            }
            return next
          })
        })
        .catch(() => { /* ignore */ })
    }
```

- [ ] **Step 6: Build verification**

Run: `cd arnold/frontend && npm run build`
Expected: PASS, no TS errors.

- [ ] **Step 7: Commit**

```bash
git add arnold/frontend/src/types/stocks.ts arnold/frontend/src/hooks/useDashboardWS.ts
git commit -m "feat(stocks-ui): wire L2 depth + TV overlay status types into useDashboardWS"
```

---

## Phase 3: SignalsPage cards (alongside existing chart)

(Same as prior plan, with `TVBridgeStatus` renamed to `TVOverlayStatus` and pointing at the new endpoint.)

### Task 3.1: Build `TVOverlayStatus` card

**Files:**
- Create: `arnold/frontend/src/components/stocks/TVOverlayStatus.tsx`

- [ ] **Step 1: Implement**

```tsx
import { useEffect, useState } from 'react'
import type { TVOverlayStatus as Status } from '@/types/stocks'

export function TVOverlayStatus() {
  const [status, setStatus] = useState<Status | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    let cancelled = false
    const poll = async () => {
      try {
        const r = await fetch('/stocks/api/tv-overlay/status')
        if (!r.ok) return
        const data = await r.json()
        if (!cancelled) setStatus(data)
      } catch { /* ignore */ }
    }
    poll()
    const iv = setInterval(poll, 3000)
    return () => { cancelled = true; clearInterval(iv) }
  }, [])

  const copyUrl = async () => {
    if (!status) return
    try {
      await navigator.clipboard.writeText(window.location.origin + status.userscript_url)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch { /* ignore */ }
  }

  if (!status) {
    return (
      <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
        <div className="text-zinc-500 uppercase tracking-wider mb-1">TV Overlay</div>
        <div className="text-zinc-400">Loading…</div>
      </div>
    )
  }

  const attached = status.attached_clients > 0
  const dotColor = attached ? 'bg-emerald-500' : 'bg-zinc-600'
  const ageS = status.last_paint_at ? Math.round((Date.now() / 1000) - status.last_paint_at) : null

  return (
    <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
      <div className="flex items-center justify-between mb-2">
        <span className="text-zinc-500 uppercase tracking-wider">TV Overlay</span>
        <button
          onClick={copyUrl}
          className="px-2 py-0.5 text-[10px] uppercase tracking-wider bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded"
        >
          {copied ? 'Copied' : 'Copy userscript URL'}
        </button>
      </div>
      <div className="flex items-center gap-2 mb-1">
        <span className={`inline-block w-2 h-2 rounded-full ${dotColor}`} />
        <span className="text-zinc-300">
          {attached ? `${status.attached_clients} client${status.attached_clients > 1 ? 's' : ''} attached` : 'No overlay clients'}
        </span>
      </div>
      {!attached && (
        <div className="text-zinc-500 mt-1 leading-tight">
          Install the userscript in Tampermonkey, then open NQ on TradingView.
        </div>
      )}
      <div className="flex gap-3 mt-2 text-zinc-500">
        <span>{status.draw_count} drawn</span>
        {ageS !== null && <span>painted {ageS}s ago</span>}
      </div>
      {status.error && <div className="text-red-400 mt-1 truncate" title={status.error}>{status.error}</div>}
    </div>
  )
}
```

- [ ] **Step 2: Build**

Run: `cd arnold/frontend && npm run build`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add arnold/frontend/src/components/stocks/TVOverlayStatus.tsx
git commit -m "feat(stocks-ui): add TVOverlayStatus card"
```

### Task 3.2: Build `ZonesTable`

**Files:**
- Create: `arnold/frontend/src/components/stocks/ZonesTable.tsx`

- [ ] **Step 1: Implement**

```tsx
import { useMemo, useState } from 'react'
import type { Zone } from '@/types/stocks'

interface Props {
  zones: Zone[]
  lastPrice: number | null
}

type SortKey = 'distance' | 'hierarchy' | 'members'

export function ZonesTable({ zones, lastPrice }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>('distance')

  const rows = useMemo(() => {
    const enriched = zones.map(z => ({
      ...z,
      distance: lastPrice !== null ? Math.abs(z.price - lastPrice) : 0,
    }))
    enriched.sort((a, b) => {
      if (sortKey === 'distance') return a.distance - b.distance
      if (sortKey === 'hierarchy') return (b.hierarchy ?? 0) - (a.hierarchy ?? 0)
      return b.members - a.members
    })
    return enriched.slice(0, 20)
  }, [zones, lastPrice, sortKey])

  const ping = async (zoneKey: string) => {
    await fetch(`/stocks/api/tv-overlay/ping-zone/${encodeURIComponent(zoneKey)}`, { method: 'POST' }).catch(() => {})
  }

  return (
    <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
      <div className="flex items-center justify-between mb-2">
        <span className="text-zinc-500 uppercase tracking-wider">Active Zones ({zones.length})</span>
        <div className="flex gap-1">
          {(['distance', 'hierarchy', 'members'] as SortKey[]).map(k => (
            <button
              key={k}
              onClick={() => setSortKey(k)}
              className={`px-2 py-0.5 text-[10px] uppercase tracking-wider rounded ${
                sortKey === k ? 'bg-zinc-700 text-zinc-200' : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              {k}
            </button>
          ))}
        </div>
      </div>
      <table className="w-full text-[11px]">
        <thead>
          <tr className="text-zinc-500 text-left">
            <th className="font-normal">Price</th>
            <th className="font-normal">Range</th>
            <th className="font-normal text-right">Strength</th>
            <th className="font-normal text-right">Members</th>
            <th className="font-normal text-right">Δ</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((z, i) => {
            const id = `${z.price}:${z.members}`
            const strength = z.hierarchy ?? 0
            const bar = Math.round(strength * 100)
            return (
              <tr key={i} className="text-zinc-300 hover:bg-zinc-800/50">
                <td className="py-0.5">{z.price.toFixed(2)}</td>
                <td className="py-0.5 text-zinc-500">
                  {z.lower !== undefined && z.upper !== undefined
                    ? `${z.lower.toFixed(2)}–${z.upper.toFixed(2)}`
                    : '—'}
                </td>
                <td className="py-0.5 text-right">
                  <span className="inline-block w-12 bg-zinc-800 rounded-sm overflow-hidden align-middle">
                    <span className="block h-1.5 bg-orange-400" style={{ width: `${bar}%` }} />
                  </span>
                </td>
                <td className="py-0.5 text-right">{z.members}</td>
                <td className="py-0.5 text-right text-zinc-500">
                  {lastPrice !== null ? (z.price - lastPrice).toFixed(1) : '—'}
                </td>
                <td className="py-0.5 text-right">
                  <button
                    onClick={() => ping(id)}
                    className="px-1.5 py-0.5 text-[10px] uppercase tracking-wider bg-zinc-800 hover:bg-zinc-700 text-zinc-400 rounded"
                  >
                    Ping
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
```

- [ ] **Step 2: Build + commit**

```bash
cd arnold/frontend && npm run build
cd ../..
git add arnold/frontend/src/components/stocks/ZonesTable.tsx
git commit -m "feat(stocks-ui): add ZonesTable with ping-on-chart action"
```

### Task 3.3: Build `PositionCard`, `ModelStateCard`, `EventLog`, `L2Ladder`

**Files:**
- Create: `arnold/frontend/src/components/stocks/PositionCard.tsx`
- Create: `arnold/frontend/src/components/stocks/ModelStateCard.tsx`
- Create: `arnold/frontend/src/components/stocks/EventLog.tsx`
- Create: `arnold/frontend/src/components/stocks/L2Ladder.tsx`

- [ ] **Step 1: Implement `PositionCard.tsx`**

```tsx
import type { Position, ModelStatus } from '@/types/stocks'

interface Props {
  positions: Position[]
  modelStatus: ModelStatus | null
  lastPrice: number | null
}

export function PositionCard({ positions, modelStatus, lastPrice }: Props) {
  const pos = positions[0] ?? null
  const flat = !pos || pos.size === 0
  const ms = modelStatus

  if (flat) {
    return (
      <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
        <div className="text-zinc-500 uppercase tracking-wider mb-1">Position</div>
        <div className="text-zinc-400">Flat</div>
        {ms?.session_pnl !== undefined && (
          <div className="text-zinc-500 mt-1">Session PnL: ${ms.session_pnl.toFixed(2)}</div>
        )}
      </div>
    )
  }

  const sideStr = typeof pos.side === 'string' ? pos.side : (pos.side === 0 ? 'long' : 'short')
  const entry = ms?.entry_price ?? pos.price
  const stop = ms?.stop_price
  const isLong = sideStr === 'long'
  const dir = isLong ? 1 : -1
  const unrealized = lastPrice !== null && entry ? (lastPrice - entry) * dir * pos.size * 20 : 0
  const rMult = stop && entry && lastPrice !== null ? ((lastPrice - entry) * dir) / Math.abs(entry - stop) : 0

  return (
    <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
      <div className="flex items-center justify-between mb-2">
        <span className="text-zinc-500 uppercase tracking-wider">Position</span>
        <span className={`px-2 py-0.5 text-[10px] uppercase rounded ${isLong ? 'bg-emerald-900/50 text-emerald-400' : 'bg-red-900/50 text-red-400'}`}>
          {sideStr} × {pos.size}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-zinc-300">
        <span className="text-zinc-500">Entry</span><span>{entry?.toFixed(2) ?? '—'}</span>
        <span className="text-zinc-500">Stop</span><span>{stop?.toFixed(2) ?? '—'}</span>
        <span className="text-zinc-500">Last</span><span>{lastPrice?.toFixed(2) ?? '—'}</span>
        <span className="text-zinc-500">Unrealized</span>
        <span className={unrealized >= 0 ? 'text-emerald-400' : 'text-red-400'}>${unrealized.toFixed(2)}</span>
        <span className="text-zinc-500">R-multiple</span>
        <span className={rMult >= 0 ? 'text-emerald-400' : 'text-red-400'}>{rMult.toFixed(2)}R</span>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Implement `ModelStateCard.tsx`**

```tsx
import type { DQNInferenceEvent } from '@/types/stocks'

interface Props {
  inference: DQNInferenceEvent | null
  inferenceAt: number | null
  lastPrice: number | null
}

export function ModelStateCard({ inference, inferenceAt, lastPrice }: Props) {
  if (!inference) {
    return (
      <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
        <div className="text-zinc-500 uppercase tracking-wider mb-1">Model</div>
        <div className="text-zinc-400">No inference yet</div>
      </div>
    )
  }

  const ageS = inferenceAt ? Math.round((Date.now() - inferenceAt) / 1000) : null
  const action = inference.action
  const actionColor = action === 'long' ? 'text-emerald-400' : action === 'short' ? 'text-red-400' : 'text-zinc-400'

  return (
    <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
      <div className="flex items-center justify-between mb-2">
        <span className="text-zinc-500 uppercase tracking-wider">Model State</span>
        <span className="text-zinc-500">{ageS !== null ? `${ageS}s ago` : ''}</span>
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-zinc-300">
        <span className="text-zinc-500">Trigger</span><span>{inference.trigger}</span>
        <span className="text-zinc-500">Action</span><span className={actionColor}>{action}</span>
        <span className="text-zinc-500">Confidence</span><span>{inference.confidence?.toFixed(3) ?? '—'}</span>
        <span className="text-zinc-500">Cont P</span><span>{inference.cont_p?.toFixed(3) ?? '—'}</span>
        <span className="text-zinc-500">Rev P</span><span>{inference.rev_p?.toFixed(3) ?? '—'}</span>
        <span className="text-zinc-500">Stop ticks</span><span>{inference.stop_ticks ?? '—'}</span>
        <span className="text-zinc-500">Zone center</span><span>{inference.zone_center?.toFixed(2) ?? '—'}</span>
        <span className="text-zinc-500">Zone members</span><span>{inference.zone_members ?? '—'}</span>
        <span className="text-zinc-500">Last price</span><span>{lastPrice?.toFixed(2) ?? '—'}</span>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Implement `EventLog.tsx`**

```tsx
import type { Signal, Fill, ExitEvent } from '@/types/stocks'

interface Props {
  signals: Signal[]
  fills: Fill[]
  exits: ExitEvent[]
}

interface Row {
  ts: number
  kind: 'signal' | 'fill' | 'exit'
  text: string
  color: string
}

export function EventLog({ signals, fills, exits }: Props) {
  const rows: Row[] = []
  for (const s of signals.slice(-30)) {
    rows.push({
      ts: s.ts ?? 0,
      kind: 'signal',
      text: `${s.action} conf=${s.confidence?.toFixed(2) ?? '—'} zone=${s.zone ?? '—'}`,
      color: 'text-zinc-300',
    })
  }
  for (const f of fills.slice(-30)) {
    rows.push({
      ts: f.ts,
      kind: 'fill',
      text: `${f.side} ${f.size}@${f.price.toFixed(2)}`,
      color: f.side === 'long' ? 'text-emerald-400' : 'text-red-400',
    })
  }
  for (const e of exits.slice(-30)) {
    rows.push({
      ts: e.ts,
      kind: 'exit',
      text: `exit @${e.price.toFixed(2)}${e.was_stop ? ' (STOP)' : ''}`,
      color: e.was_stop ? 'text-red-400' : 'text-zinc-400',
    })
  }
  rows.sort((a, b) => b.ts - a.ts)

  return (
    <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
      <div className="text-zinc-500 uppercase tracking-wider mb-2">Recent Events</div>
      <div className="max-h-64 overflow-y-auto">
        {rows.length === 0 ? (
          <div className="text-zinc-500">none</div>
        ) : (
          rows.slice(0, 50).map((r, i) => {
            const time = r.ts ? new Date(r.ts * 1000).toLocaleTimeString() : ''
            return (
              <div key={i} className="flex gap-2 py-0.5">
                <span className="text-zinc-600 w-20 shrink-0">{time}</span>
                <span className="text-zinc-500 w-12 shrink-0 uppercase">{r.kind}</span>
                <span className={`${r.color} truncate`}>{r.text}</span>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Implement `L2Ladder.tsx`**

```tsx
import type { DepthSnapshot } from '@/types/stocks'

interface Props {
  depth: DepthSnapshot | null
  lastPrice: number | null
}

export function L2Ladder({ depth, lastPrice }: Props) {
  if (!depth || (!depth.bids.length && !depth.asks.length)) {
    return (
      <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
        <div className="text-zinc-500 uppercase tracking-wider mb-1">L2 Depth</div>
        <div className="text-zinc-400">No depth feed</div>
      </div>
    )
  }

  const maxSize = Math.max(
    1,
    ...depth.bids.map(l => l.size),
    ...depth.asks.map(l => l.size),
  )
  const totalBid = depth.bids.reduce((a, l) => a + l.size, 0)
  const totalAsk = depth.asks.reduce((a, l) => a + l.size, 0)
  const imbalance = totalBid + totalAsk > 0 ? (totalBid - totalAsk) / (totalBid + totalAsk) : 0

  const renderRow = (l: { price: number; size: number }, side: 'bid' | 'ask') => {
    const pct = Math.round((l.size / maxSize) * 100)
    const barColor = side === 'bid' ? 'bg-emerald-900/60' : 'bg-red-900/60'
    const textColor = side === 'bid' ? 'text-emerald-400' : 'text-red-400'
    return (
      <div key={`${side}-${l.price}`} className="relative flex justify-between px-1 py-0.5">
        <span className={`absolute inset-y-0 ${side === 'bid' ? 'right-0' : 'left-0'} ${barColor}`} style={{ width: `${pct}%` }} />
        <span className={`relative ${textColor}`}>{l.size}</span>
        <span className="relative text-zinc-300">{l.price.toFixed(2)}</span>
      </div>
    )
  }

  return (
    <div className="rounded border border-zinc-800 bg-zinc-900 p-3 text-xs font-mono">
      <div className="flex items-center justify-between mb-2">
        <span className="text-zinc-500 uppercase tracking-wider">L2 Depth</span>
        <span className={imbalance >= 0 ? 'text-emerald-400' : 'text-red-400'}>
          {(imbalance * 100).toFixed(0)}%
        </span>
      </div>
      <div>
        {[...depth.asks].reverse().map(l => renderRow(l, 'ask'))}
        {lastPrice !== null && (
          <div className="border-y border-zinc-700 px-1 py-0.5 text-center text-zinc-200 bg-zinc-800/50">
            {lastPrice.toFixed(2)}
          </div>
        )}
        {depth.bids.map(l => renderRow(l, 'bid'))}
      </div>
    </div>
  )
}
```

- [ ] **Step 5: Build + commit**

```bash
cd arnold/frontend && npm run build
cd ../..
git add arnold/frontend/src/components/stocks/PositionCard.tsx arnold/frontend/src/components/stocks/ModelStateCard.tsx arnold/frontend/src/components/stocks/EventLog.tsx arnold/frontend/src/components/stocks/L2Ladder.tsx
git commit -m "feat(stocks-ui): add Position, Model, EventLog, L2Ladder cards"
```

### Task 3.4: Build `SignalsPage` shell

**Files:**
- Create: `arnold/frontend/src/pages/stocks/SignalsPage.tsx`

- [ ] **Step 1: Implement layout**

```tsx
import { useEffect, useState } from 'react'
import { TVOverlayStatus } from '@/components/stocks/TVOverlayStatus'
import { ZonesTable } from '@/components/stocks/ZonesTable'
import { PositionCard } from '@/components/stocks/PositionCard'
import { ModelStateCard } from '@/components/stocks/ModelStateCard'
import { EventLog } from '@/components/stocks/EventLog'
import { L2Ladder } from '@/components/stocks/L2Ladder'
import { api } from '@/hooks/useStocksApi'
import type { DashboardState } from '@/hooks/useDashboardWS'
import type { ModelStatus } from '@/types/stocks'

interface Props {
  ws: DashboardState
}

export default function SignalsPage({ ws }: Props) {
  const [modelStatus, setModelStatus] = useState<ModelStatus | null>(null)

  useEffect(() => {
    const poll = () => { api.getModelStatus().then(setModelStatus).catch(() => {}) }
    poll()
    const iv = setInterval(poll, 5000)
    return () => clearInterval(iv)
  }, [])

  return (
    <div className="flex flex-col flex-1 min-h-0 p-3 gap-3 overflow-y-auto bg-zinc-950">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <TVOverlayStatus />
        <PositionCard
          positions={ws.positions}
          modelStatus={modelStatus}
          lastPrice={ws.lastPrice}
        />
        <ModelStateCard
          inference={ws.dqnInference}
          inferenceAt={ws.dqnInferenceAt}
          lastPrice={ws.lastPrice}
        />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <ZonesTable zones={ws.zones} lastPrice={ws.lastPrice} />
        <L2Ladder depth={ws.depth} lastPrice={ws.lastPrice} />
      </div>
      <EventLog signals={ws.signals} fills={ws.fills} exits={ws.exits} />
    </div>
  )
}
```

- [ ] **Step 2: Verify `api.getModelStatus` exists in `useStocksApi.ts`**

Run: `grep -n "getModelStatus" arnold/frontend/src/hooks/useStocksApi.ts`. If missing, add `getModelStatus: () => fetch('/stocks/api/model-status').then(r => r.json())` to the api object.

- [ ] **Step 3: Build + commit**

```bash
cd arnold/frontend && npm run build
cd ../..
git add arnold/frontend/src/pages/stocks/SignalsPage.tsx arnold/frontend/src/hooks/useStocksApi.ts
git commit -m "feat(stocks-ui): add SignalsPage layout combining all stocks cards"
```

---

## Phase 4: TV overlay — server endpoints + userscript

### Task 4.1: Server-side overlay router (status + WS + userscript serving)

**Files:**
- Create: `arnold/tv_overlay/__init__.py`
- Create: `arnold/tv_overlay/status.py`
- Create: `arnold/tv_overlay/router.py`
- Test: `arnold/tests/__init__.py` (empty if missing), `arnold/tests/test_tv_overlay_router.py`

- [ ] **Step 1: Empty package init + status**

`arnold/tv_overlay/__init__.py`:
```python
"""TV overlay — broadcasts zone/position state to a Tampermonkey userscript."""
from arnold.tv_overlay.status import get_status, snapshot
__all__ = ["get_status", "snapshot"]
```

`arnold/tv_overlay/status.py`:
```python
"""Thread-safe overlay status snapshot."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, asdict


@dataclass
class _Status:
    attached_clients: int = 0
    last_paint_at: float | None = None
    draw_count: int = 0
    retries: int = 0
    error: str | None = None


_state = _Status()
_lock = threading.Lock()


def snapshot() -> _Status:
    with _lock:
        return _Status(**asdict(_state))


def get_status() -> dict:
    s = snapshot()
    return {
        "attached_clients": s.attached_clients,
        "last_paint_at": s.last_paint_at,
        "draw_count": s.draw_count,
        "retries": s.retries,
        "error": s.error,
        "userscript_url": "/stocks/api/tv-overlay/userscript",
    }


def client_attached() -> None:
    with _lock:
        _state.attached_clients += 1


def client_detached() -> None:
    with _lock:
        _state.attached_clients = max(0, _state.attached_clients - 1)


def record_paint(count: int = 1) -> None:
    with _lock:
        _state.draw_count += count
        _state.last_paint_at = time.time()


def set_error(err: str | None) -> None:
    with _lock:
        _state.error = err
```

- [ ] **Step 2: Write failing router test**

`arnold/tests/test_tv_overlay_router.py`:

```python
"""Smoke tests for the TV overlay router."""
from __future__ import annotations

from pathlib import Path
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent))

from arnold.tv_overlay.router import create_router  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(create_router(), prefix="/stocks")
    return TestClient(app)


def test_status_endpoint_returns_zero_clients(client: TestClient) -> None:
    r = client.get("/stocks/api/tv-overlay/status")
    assert r.status_code == 200
    body = r.json()
    assert body["attached_clients"] == 0
    assert body["userscript_url"] == "/stocks/api/tv-overlay/userscript"


def test_userscript_endpoint_serves_javascript(client: TestClient) -> None:
    r = client.get("/stocks/api/tv-overlay/userscript")
    assert r.status_code == 200
    assert "javascript" in r.headers.get("content-type", "")
    body = r.text
    assert "==UserScript==" in body
    assert "@match" in body and "tradingview.com" in body


def test_websocket_attaches_and_increments_count(client: TestClient) -> None:
    with client.websocket_connect("/stocks/ws/tv-overlay") as ws:
        ws.send_json({"type": "hello", "version": "test"})
        # Server should accept; status reports 1 client.
        r = client.get("/stocks/api/tv-overlay/status")
        assert r.status_code == 200
        assert r.json()["attached_clients"] == 1
    # After disconnect, client count returns to 0.
    r = client.get("/stocks/api/tv-overlay/status")
    assert r.json()["attached_clients"] == 0
```

- [ ] **Step 3: Run, confirm failure**

Run: `cd arnold && pytest tests/test_tv_overlay_router.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Implement router**

`arnold/tv_overlay/router.py`:

```python
"""FastAPI router exposing overlay WS + status + userscript file."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from arnold.tv_overlay import status as overlay_status

log = logging.getLogger("arnold.tv_overlay")

_USERSCRIPT_PATH = Path(__file__).resolve().parent / "userscript" / "arnold-overlay.user.js"

# Module-level client list — accessed by the broadcaster too.
clients: list[WebSocket] = []
_clients_lock = asyncio.Lock()


async def broadcast(event: dict) -> None:
    if not clients:
        return
    msg = json.dumps(event, default=str)
    dead: list[WebSocket] = []
    async with _clients_lock:
        for ws in list(clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in clients:
                clients.remove(ws)
                overlay_status.client_detached()


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/tv-overlay/status")
    async def status_endpoint() -> dict:
        return overlay_status.get_status()

    @router.get("/api/tv-overlay/userscript")
    async def serve_userscript() -> Response:
        if not _USERSCRIPT_PATH.exists():
            return Response(
                content="// arnold-overlay.user.js missing — install pending",
                media_type="application/javascript",
                status_code=404,
            )
        return Response(
            content=_USERSCRIPT_PATH.read_text(encoding="utf-8"),
            media_type="application/javascript; charset=utf-8",
        )

    @router.post("/api/tv-overlay/ping-zone/{zone_key}")
    async def ping_zone(zone_key: str) -> dict:
        await broadcast({"type": "ping_zone", "zone_key": zone_key})
        return {"ok": True}

    @router.websocket("/ws/tv-overlay")
    async def overlay_ws(ws: WebSocket) -> None:
        await ws.accept()
        async with _clients_lock:
            clients.append(ws)
            overlay_status.client_attached()
        try:
            while True:
                # Userscript may post draw_ack / paint stats / errors.
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                t = msg.get("type")
                if t == "ack":
                    overlay_status.record_paint(int(msg.get("count", 1)))
                elif t == "error":
                    overlay_status.set_error(str(msg.get("message", ""))[:200])
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("overlay ws error")
        finally:
            async with _clients_lock:
                if ws in clients:
                    clients.remove(ws)
                    overlay_status.client_detached()

    return router
```

- [ ] **Step 5: Run, confirm pass**

Run: `cd arnold && pytest tests/test_tv_overlay_router.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add arnold/tv_overlay/__init__.py arnold/tv_overlay/status.py arnold/tv_overlay/router.py arnold/tests/__init__.py arnold/tests/test_tv_overlay_router.py
git commit -m "feat(tv-overlay): add status + WS + userscript router"
```

### Task 4.2: Broadcaster — diff `_state` → overlay events

**Files:**
- Create: `arnold/tv_overlay/broadcaster.py`
- Test: `arnold/tests/test_overlay_broadcaster.py`

- [ ] **Step 1: Write failing test**

`arnold/tests/test_overlay_broadcaster.py`:

```python
"""Broadcaster diff-and-emit logic — fake WS sink."""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent))

from arnold.tv_overlay.broadcaster import OverlayBroadcaster  # noqa: E402


@pytest.mark.asyncio
async def test_zone_diff_only_emits_changed():
    sent: list[dict] = []

    async def fake_emit(event: dict) -> None:
        sent.append(event)

    b = OverlayBroadcaster(emit=fake_emit)

    await b.reconcile_zones([
        {"price": 27400.0, "members": 3, "lower": 27395, "upper": 27405, "hierarchy": 0.6, "name": "x"},
        {"price": 27450.0, "members": 2, "lower": 27445, "upper": 27455, "hierarchy": 0.4, "name": "y"},
    ])
    upserts = [e for e in sent if e["type"] == "zone_upsert"]
    removes = [e for e in sent if e["type"] == "zone_remove"]
    assert len(upserts) == 2
    assert len(removes) == 0

    sent.clear()
    # Same first zone, second zone updated members count, third zone added,
    # nothing removed.
    await b.reconcile_zones([
        {"price": 27400.0, "members": 3, "lower": 27395, "upper": 27405, "hierarchy": 0.6, "name": "x"},
        {"price": 27450.0, "members": 5, "lower": 27445, "upper": 27455, "hierarchy": 0.7, "name": "y"},
        {"price": 27500.0, "members": 1, "lower": 27498, "upper": 27502, "hierarchy": 0.2, "name": "z"},
    ])
    upserts = [e for e in sent if e["type"] == "zone_upsert"]
    removes = [e for e in sent if e["type"] == "zone_remove"]
    assert len(upserts) == 2  # 27450 (changed), 27500 (new)
    assert len(removes) == 0


@pytest.mark.asyncio
async def test_position_close_emits_remove():
    sent: list[dict] = []

    async def fake_emit(event: dict) -> None:
        sent.append(event)

    b = OverlayBroadcaster(emit=fake_emit)

    await b.reconcile_position(
        positions=[{"side": "long", "size": 1, "price": 27400.0}],
        model_status={"entry_price": 27400.0, "stop_price": 27380.0},
    )
    upserts = [e for e in sent if e["type"] == "position_upsert"]
    assert len(upserts) == 1

    sent.clear()
    await b.reconcile_position(positions=[{"side": "long", "size": 0, "price": 0.0}], model_status={})
    removes = [e for e in sent if e["type"] == "position_remove"]
    assert len(removes) == 1
```

- [ ] **Step 2: Run, confirm failure**

Run: `cd arnold && pytest tests/test_overlay_broadcaster.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement broadcaster**

`arnold/tv_overlay/broadcaster.py`:

```python
"""Compares dashboard `_state` snapshots to a "world" set, emits typed deltas.

Designed to be transport-agnostic — caller provides an `emit(dict) -> awaitable`.
In production this is `arnold.tv_overlay.router.broadcast`.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

log = logging.getLogger("arnold.tv_overlay.broadcaster")


def _zone_key(z: dict) -> str:
    """Stable key — zone clusters dedup by centroid price (zone_builder picks a single
    centroid per family on each rebuild)."""
    return f"zone:{float(z['price']):.2f}"


def _zone_payload(z: dict) -> dict:
    return {
        "key": _zone_key(z),
        "price": float(z["price"]),
        "top": float(z.get("upper") or z["price"]),
        "bottom": float(z.get("lower") or z["price"]),
        "members": int(z.get("members", 0)),
        "strength": float(z.get("hierarchy") or 0.0),
        "kind": str(z.get("name") or "zone"),
    }


class OverlayBroadcaster:
    """Holds the last sent state per topic; emits only deltas."""

    def __init__(self, emit: Callable[[dict], Awaitable[None]]) -> None:
        self._emit = emit
        self._zones: dict[str, dict] = {}  # key → last payload
        self._has_position = False
        self._last_position: dict | None = None

    async def reconcile_zones(self, zones: list[dict]) -> None:
        seen: dict[str, dict] = {}
        for z in zones:
            try:
                payload = _zone_payload(z)
                seen[payload["key"]] = payload
            except Exception:
                log.exception("malformed zone %r", z)

        # Upserts
        for key, payload in seen.items():
            prior = self._zones.get(key)
            if prior != payload:
                await self._emit({"type": "zone_upsert", **payload})

        # Removes
        for key in list(self._zones.keys()):
            if key not in seen:
                await self._emit({"type": "zone_remove", "key": key})

        self._zones = seen

    async def reconcile_position(self, positions: list[dict], model_status: dict | None) -> None:
        ms = model_status or {}
        first = positions[0] if positions else None
        flat = first is None or int(first.get("size", 0)) == 0
        if flat:
            if self._has_position:
                await self._emit({"type": "position_remove", "key": "pos:current"})
                self._has_position = False
                self._last_position = None
            return

        side_raw = first.get("side", 0)
        side = "long" if side_raw == 0 or side_raw == "long" else "short"
        entry = float(ms.get("entry_price") or first.get("price") or 0.0)
        stop = ms.get("stop_price")
        tp = ms.get("tp_price")

        payload: dict[str, Any] = {
            "key": "pos:current",
            "side": side,
            "entry": entry,
            "stop": float(stop) if stop is not None else None,
            "tp": float(tp) if tp is not None else None,
            "size": int(first.get("size", 0)),
        }
        if payload != self._last_position:
            await self._emit({"type": "position_upsert", **payload})
            self._last_position = payload
            self._has_position = True

    async def loop(self, *, interval_s: float = 2.0) -> None:
        from src.stocks.dashboard import _state as dash_state
        try:
            while True:
                try:
                    zones: list[dict] = dash_state.get("zones") or []
                    positions: list[dict] = dash_state.get("positions") or []
                    adapter_obj = dash_state.get("adapter")
                    model_status: dict[str, Any] = {}
                    if adapter_obj is not None:
                        tracker = getattr(adapter_obj, "tracker", None)
                        if tracker is not None:
                            model_status = {
                                "entry_price": getattr(tracker, "entry_price", None),
                                "stop_price": getattr(tracker, "stop_price", None),
                                "tp_price": None,
                            }
                    await self.reconcile_zones(zones)
                    await self.reconcile_position(positions, model_status)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("overlay broadcaster iteration failed")
                await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            pass
```

- [ ] **Step 4: Run, confirm pass**

Run: `cd arnold && pytest tests/test_overlay_broadcaster.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add arnold/tv_overlay/broadcaster.py arnold/tests/test_overlay_broadcaster.py
git commit -m "feat(tv-overlay): add diff broadcaster for zones + position"
```

### Task 4.3: Userscript

**Files:**
- Create: `arnold/tv_overlay/userscript/arnold-overlay.user.js`

- [ ] **Step 1: Write the userscript**

```javascript
// ==UserScript==
// @name         Arnold TradingView Overlay
// @namespace    https://github.com/blomen/arnold
// @version      0.1.0
// @description  Draws Arnold zones and open positions on TradingView charts via WebSocket from local Arnold server.
// @match        https://*.tradingview.com/*
// @match        https://tradingview.com/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

(function () {
  'use strict';

  // --- Config ---
  const SERVER_WS = 'ws://127.0.0.1:8000/stocks/ws/tv-overlay';
  const RECONNECT_MS = 2000;
  const ATTACH_POLL_MS = 1000;
  const ATTACH_MAX_TRIES = 60;

  const COLOR_BY_STRENGTH = (s) => {
    if (s < 0.25) return '#475569';
    if (s < 0.5)  return '#6366f1';
    if (s < 0.7)  return '#d946ef';
    if (s < 0.9)  return '#f97316';
    return '#ef4444';
  };

  // --- TV chart attach ---
  function getChart() {
    // Order matters — TV exposes the chart object differently across versions.
    // Phase 0 should pin the right path; we try several.
    try {
      if (window.TradingViewApi && typeof window.TradingViewApi.activeChart === 'function') {
        return window.TradingViewApi.activeChart();
      }
    } catch (_) {}
    try {
      if (window.tvWidget && typeof window.tvWidget.activeChart === 'function') {
        return window.tvWidget.activeChart();
      }
    } catch (_) {}
    try {
      if (window.TradingView && window.TradingView.activeChart) {
        return window.TradingView.activeChart();
      }
    } catch (_) {}
    return null;
  }

  let chart = null;
  let attachAttempts = 0;
  const attachPromise = new Promise((resolve) => {
    const tick = () => {
      attachAttempts += 1;
      const c = getChart();
      if (c) { chart = c; resolve(c); return; }
      if (attachAttempts >= ATTACH_MAX_TRIES) { resolve(null); return; }
      setTimeout(tick, ATTACH_POLL_MS);
    };
    tick();
  });

  // --- Drawing registry ---
  const drawn = new Map(); // key → entityId

  function safeRemove(key) {
    const entityId = drawn.get(key);
    if (entityId == null || !chart) return;
    try { chart.removeEntity(entityId); } catch (e) { /* ignore */ }
    drawn.delete(key);
  }

  function drawZone(p) {
    if (!chart) return;
    safeRemove(p.key);
    const now = Math.floor(Date.now() / 1000);
    const tStart = now - 8 * 60 * 60; // 8h back
    const tEnd = now;
    const color = COLOR_BY_STRENGTH(p.strength);
    try {
      const id = chart.createMultipointShape(
        [
          { time: tStart, price: p.top },
          { time: tEnd,   price: p.bottom },
        ],
        {
          shape: 'rectangle',
          text: `${p.kind} ×${p.members}`,
          overrides: {
            color: color,
            backgroundColor: color,
            transparency: Math.max(20, 80 - Math.round(p.strength * 60)),
            showLabel: true,
          },
        }
      );
      if (id != null) drawn.set(p.key, id);
    } catch (e) {
      sendError(`drawZone failed: ${e && e.message}`);
    }
  }

  function drawPosition(p) {
    if (!chart) return;
    const baseKey = p.key;
    safeRemove(baseKey + ':entry');
    safeRemove(baseKey + ':stop');
    safeRemove(baseKey + ':tp');

    const sideColor = p.side === 'long' ? '#10b981' : '#ef4444';
    const now = Math.floor(Date.now() / 1000);

    try {
      const entryId = chart.createMultipointShape(
        [{ time: now, price: p.entry }],
        { shape: 'horizontal_line', text: `${p.side.toUpperCase()} entry ${p.entry.toFixed(2)}`,
          overrides: { linecolor: sideColor, showLabel: true } }
      );
      if (entryId != null) drawn.set(baseKey + ':entry', entryId);

      if (p.stop != null) {
        const stopId = chart.createMultipointShape(
          [{ time: now, price: p.stop }],
          { shape: 'horizontal_line', text: `stop ${p.stop.toFixed(2)}`,
            overrides: { linecolor: '#dc2626', showLabel: true } }
        );
        if (stopId != null) drawn.set(baseKey + ':stop', stopId);
      }
      if (p.tp != null) {
        const tpId = chart.createMultipointShape(
          [{ time: now, price: p.tp }],
          { shape: 'horizontal_line', text: `tp ${p.tp.toFixed(2)}`,
            overrides: { linecolor: '#22c55e', showLabel: true } }
        );
        if (tpId != null) drawn.set(baseKey + ':tp', tpId);
      }
    } catch (e) {
      sendError(`drawPosition failed: ${e && e.message}`);
    }
  }

  function removePosition(key) {
    safeRemove(key + ':entry');
    safeRemove(key + ':stop');
    safeRemove(key + ':tp');
  }

  // --- WebSocket loop ---
  let ws = null;
  let reconnectTimer = null;

  function sendAck(count) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try { ws.send(JSON.stringify({ type: 'ack', count })); } catch (_) {}
  }

  function sendError(message) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try { ws.send(JSON.stringify({ type: 'error', message })); } catch (_) {}
  }

  function connect() {
    try { ws = new WebSocket(SERVER_WS); } catch (e) { return scheduleReconnect(); }

    ws.onopen = () => {
      console.log('[arnold-overlay] connected');
      try { ws.send(JSON.stringify({ type: 'hello', version: '0.1.0', href: location.href })); } catch (_) {}
    };

    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (_) { return; }
      switch (msg.type) {
        case 'zone_upsert': drawZone(msg); sendAck(1); break;
        case 'zone_remove': safeRemove(msg.key); sendAck(1); break;
        case 'position_upsert': drawPosition(msg); sendAck(1); break;
        case 'position_remove': removePosition(msg.key); sendAck(1); break;
        case 'ping_zone': {
          // Flash-ping a zone — bring camera to it (best effort).
          try {
            const entityId = drawn.get(msg.zone_key);
            if (entityId != null && chart && typeof chart.bringToFront === 'function') {
              chart.bringToFront(entityId);
            }
          } catch (_) {}
          break;
        }
      }
    };

    ws.onclose = () => { ws = null; scheduleReconnect(); };
    ws.onerror = () => { try { ws.close(); } catch (_) {} };
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(() => { reconnectTimer = null; connect(); }, RECONNECT_MS);
  }

  // --- Boot ---
  attachPromise.then((c) => {
    if (!c) {
      console.warn('[arnold-overlay] could not find TradingView chart object — overlay disabled');
      return;
    }
    console.log('[arnold-overlay] attached to chart', c);
    connect();
  });
})();
```

- [ ] **Step 2: Verify the file is served correctly**

Restart `arnold.bat` (Phase 5 Task 5.1 wires this in). For now, lint / format only:

```bash
node -e "require('fs').readFileSync('arnold/tv_overlay/userscript/arnold-overlay.user.js','utf8')"
```

Expected: no error.

- [ ] **Step 3: Commit**

```bash
git add arnold/tv_overlay/userscript/arnold-overlay.user.js
git commit -m "feat(tv-overlay): add Tampermonkey userscript for zone/position drawing"
```

---

## Phase 5: Wire overlay into FastAPI lifespan

### Task 5.1: Server.py wiring

**Files:**
- Modify: `arnold/server.py`

- [ ] **Step 1: Import overlay router + broadcaster**

Edit `arnold/server.py`. Near the existing imports, add:

```python
import asyncio  # noqa: E402  (if not already imported)
from arnold.tv_overlay.broadcaster import OverlayBroadcaster
from arnold.tv_overlay.router import broadcast as overlay_broadcast
from arnold.tv_overlay.router import create_router as create_overlay_router
```

- [ ] **Step 2: Mount router**

After `app.include_router(create_dashboard_router(), prefix="/stocks")` (~line 75), add:

```python
app.include_router(create_overlay_router(), prefix="/stocks")
```

- [ ] **Step 3: Start broadcaster on startup**

Add at module scope alongside `_stocks_runtime`:

```python
_overlay_task = None
```

In the `startup` handler, append after the stocks bootstrap:

```python
    global _overlay_task
    broadcaster = OverlayBroadcaster(emit=overlay_broadcast)
    _overlay_task = asyncio.create_task(broadcaster.loop(), name="tv-overlay-broadcaster")
```

- [ ] **Step 4: Cancel on shutdown**

In the `shutdown` handler, before `_stocks_runtime.shutdown()`:

```python
    if _overlay_task is not None:
        _overlay_task.cancel()
        try:
            await _overlay_task
        except (asyncio.CancelledError, Exception):
            pass
```

- [ ] **Step 5: Smoke test**

```bash
arnold.bat
# In another terminal:
curl http://127.0.0.1:8000/stocks/api/tv-overlay/status
```

Expected: JSON with `attached_clients: 0`. Log lines `tv-overlay-broadcaster` started.

```bash
curl http://127.0.0.1:8000/stocks/api/tv-overlay/userscript | head -10
```

Expected: starts with `// ==UserScript==`.

- [ ] **Step 6: Commit**

```bash
git add arnold/server.py
git commit -m "feat(tv-overlay): wire router + broadcaster into Arnold lifespan"
```

---

## Phase 6: Live verification

### Task 6.1: Install userscript and verify drawing on real TV

**Files:** none (manual)

- [ ] **Step 1: Install Tampermonkey**

If not already installed: Tampermonkey extension for Chrome/Edge/Firefox, or install in TV Desktop's Electron Chromium via the same store flow if supported. (TV Desktop loads userscripts when run in a browser context — easiest path is to install the extension in Chrome and use the userscript on `tradingview.com` web. Also works in Desktop if you can sideload extensions.)

- [ ] **Step 2: Install the userscript**

In Tampermonkey, "Create a new script" → paste the contents of `arnold/tv_overlay/userscript/arnold-overlay.user.js` (or use Tampermonkey's "Install from URL" with `http://127.0.0.1:8000/stocks/api/tv-overlay/userscript`). Save.

- [ ] **Step 3: Load NQ on TradingView**

Open `tradingview.com/chart`, NQ 15m. Wait 1-2 seconds.

- [ ] **Step 4: Verify connection**

```bash
curl http://127.0.0.1:8000/stocks/api/tv-overlay/status
```

Expected: `attached_clients: 1`. Browser DevTools console shows `[arnold-overlay] connected` and `[arnold-overlay] attached to chart`.

- [ ] **Step 5: Verify zone drawing**

Wait for the next zone broadcast tick (≤ 2s). Expect colored rectangles at zone prices on the chart, with member-count labels. Strength colors: slate / indigo / fuchsia / orange / red.

- [ ] **Step 6: If zones do not appear**

Check userscript console for errors. The most common cause is the wrong path to the chart object (Phase 0 should pin this — if not, edit `getChart()` in the userscript with the correct path discovered in Phase 0 and reinstall).

Also verify the local Arnold log shows broadcaster activity. If the broadcaster is silent, restart `arnold.bat`.

- [ ] **Step 7: Verify position drawing**

If actively trading, wait for an entry. Expect three horizontal lines: entry (side color), stop (red), tp (green if set). Close → all three disappear within one broadcaster tick (~2s).

- [ ] **Step 8: Verify reconnect**

Stop arnold.bat. Userscript console logs disconnect. Restart arnold.bat. Within ~2s, reconnect message appears, drawings persist (since we draw idempotently, the first reconcile after reconnect re-applies the world).

- [ ] **Step 9: Verify SignalsPage**

Open http://127.0.0.1:8000/. Stocks tab → SignalsPage shows:
- TVOverlayStatus card with `1 client attached`, `draw_count` increasing.
- Other cards populated from `useDashboardWS` data.

---

## Phase 7: Switch the Stocks tab and delete chart code

(Identical to prior plan — independent of overlay transport.)

### Task 7.1: Wire `SignalsPage` into App.tsx

**Files:**
- Modify: `arnold/frontend/src/App.tsx`

- [ ] **Step 1: Replace ChartPage with SignalsPage**

Replace line 6:
```typescript
import { ChartPage } from './pages/stocks/ChartPage'
```
with:
```typescript
import SignalsPage from './pages/stocks/SignalsPage'
```

Replace lines 142-153:
```tsx
          {/* Stocks — TradingView is the chart, this is the signals/values console */}
          <div className={`flex flex-col flex-1 min-h-0 ${activeTab === 'charts' ? '' : 'hidden'}`}>
            <ErrorBoundary label="Stocks">
              <SignalsPage ws={ws} />
            </ErrorBoundary>
          </div>
```

Drop the `session` state + polling (lines 93-100):

```typescript
  const { state: ws } = useDashboardWS()
```

Remove unused imports: `lastTick`, `session`, `setSession`, `useEffect` polling, `import type { ExpandedSession }`, `import { api as stocksApi }`.

- [ ] **Step 2: Build**

Run: `cd arnold/frontend && npm run build`
Expected: PASS.

- [ ] **Step 3: Verify in browser**

Reload http://127.0.0.1:8000/. Click Stocks tab → SignalsPage renders with all six cards. No console errors.

- [ ] **Step 4: Commit**

```bash
git add arnold/frontend/src/App.tsx
git commit -m "feat(stocks-ui): swap Stocks tab to SignalsPage"
```

### Task 7.2: Delete chart files

**Files:**
- Delete: `arnold/frontend/src/pages/stocks/CandleChart.tsx`
- Delete: `arnold/frontend/src/pages/stocks/ChartPage.tsx`
- Delete: `arnold/frontend/src/pages/stocks/DQNPage.tsx`
- Delete: `arnold/frontend/src/pages/stocks/NeuralNetworkSVG.tsx`
- Delete: `arnold/frontend/src/lib/indicators.ts`

- [ ] **Step 1: Remove files**

```bash
rm arnold/frontend/src/pages/stocks/CandleChart.tsx
rm arnold/frontend/src/pages/stocks/ChartPage.tsx
rm arnold/frontend/src/pages/stocks/DQNPage.tsx
rm arnold/frontend/src/pages/stocks/NeuralNetworkSVG.tsx
rm arnold/frontend/src/lib/indicators.ts
```

- [ ] **Step 2: Audit for stale references**

Run: `grep -rn "CandleChart\|ChartPage\|DQNPage\|NeuralNetworkSVG\|lib/indicators" arnold/frontend/src`. Expected: zero matches. Remove any stragglers.

- [ ] **Step 3: Drop `lightweight-charts` from package.json**

Edit `arnold/frontend/package.json`, remove `"lightweight-charts": "..."`.

```bash
cd arnold/frontend && npm install
```

- [ ] **Step 4: Build + lint**

```bash
cd arnold/frontend && npm run lint && npm run build
```

Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add -u arnold/frontend
git commit -m "chore(stocks-ui): delete legacy lightweight-charts CandleChart + helpers"
```

---

## Phase 8: Final verification

### Task 8.1: End-to-end checklist

- [ ] **Step 1: Cold start**

1. Run `arnold.bat`.
2. Open `tradingview.com/chart` with NQ loaded; userscript installed.
3. `curl http://127.0.0.1:8000/stocks/api/tv-overlay/status` → `attached_clients: 1`.
4. Open http://127.0.0.1:8000/ → Stocks tab → SignalsPage renders, TVOverlayStatus card green.

- [ ] **Step 2: Trigger zone update**

Wait for next 1m candle close (or restart `level_monitor` server-side). New zone rectangles appear on TV with strength-shaded fill + member-count label.

- [ ] **Step 3: Open + close paper trade**

Trigger entry server-side. Three horizontal lines appear on TV. Close → all three disappear within one broadcaster tick (~2s).

- [ ] **Step 4: TV tab reload**

Reload the TV tab in the browser. Userscript reconnects. World re-paints on next reconcile (since broadcaster's "world" is server-side, the userscript will receive zones one-by-one — fine, idempotent).

- [ ] **Step 5: SignalsPage panels**

Confirm all six cards have live data (or "no data" state when applicable):
- TVOverlayStatus → green, 1 client
- PositionCard → "Flat" or live position
- ModelStateCard → recent inference (within last 5m)
- ZonesTable → ≥3 rows, sorted by distance
- L2Ladder → bid/ask ladder + imbalance %
- EventLog → recent signals/fills/exits

- [ ] **Step 6: Multi-client smoke**

Open NQ on a second browser window with userscript installed. `attached_clients: 2`. Both chart windows draw the same zones.

- [ ] **Step 7: arnold.bat restart while position open**

Open a position. Restart `arnold.bat`. Userscript reconnects within 2s, redraws lines.

- [ ] **Step 8: Lint, type, build clean**

```bash
cd backend && pytest tests/test_dashboard_depth.py -v
cd ../arnold && pytest tests/ -v
cd frontend && npm run lint && npm run build
```

Expected: all pass.

- [ ] **Step 9: Final commit**

If any cleanup made during verification:

```bash
git add -A
git commit -m "chore(tv-overlay): final cleanup after E2E verification"
```

---

## Self-Review

**Spec coverage:**

| Spec goal | Implementing tasks |
|---|---|
| Replace CandleChart with SignalsPage | Phase 3, Phase 7 |
| TV overlay (drawing zones + open position) | Phase 4 (router, broadcaster, userscript), Phase 5 (lifespan wiring) |
| Diff & reconcile zones / positions | Task 4.2 (`OverlayBroadcaster.reconcile_zones` / `reconcile_position`) |
| Generic enough for future instruments | Userscript + broadcaster are not NQ-specific; broadcaster reads `_state["zones"]` whatever the symbol; per-instrument adapter knobs (tick size, point value) currently only matter on the SignalsPage card side, not on the TV draw side |
| L2 / orderflow groundwork | Phase 1 (server depth state), Phase 2 (frontend wiring), Task 3.3 (L2Ladder card). TV-side L2 strip painting is a future iteration on top of `drawZone` — out of day-1 scope. |
| Manual TV launch only (Q1) | Userscript activates whenever a TV tab is open. No special launch flag needed. Even simpler than the original requirement. |

**Placeholder check:** Phase 0 outcome is intentionally TBD (the user runs the smoke test); everything else has explicit code or commands. The conditional in Task 1.2 step 3 (if signals_ws.py broadcasts depth) is a fork with both branches handled.

**Type consistency:**
- `OverlayBroadcaster(emit=)` consistent across `router.broadcast` and tests.
- `_zone_payload` shape (`key, price, top, bottom, members, strength, kind`) consistent between server emit and userscript `drawZone`.
- `position_upsert` / `position_remove` shape consistent between broadcaster and userscript.
- `TVOverlayStatus` type matches the dataclass output of `arnold/tv_overlay/status.get_status()`.

**Decomposition gaps:** none. Phases 1, 2, 3 are independent of Phase 4 and could even run in parallel (with the obvious caveat that subagent-driven dev is sequential per-task).

**Risk register:**
- Userscript depends on TV's internal `chart.createMultipointShape` API — Phase 0 pins the exact path.
- WebSocket from `tradingview.com` to `ws://127.0.0.1:8000` works in modern browsers (mixed content is allowed for `127.0.0.1`/`localhost` origins per recent Chromium policy). If a future Chromium version changes this, switch to `wss://` with a self-signed cert.
- TV could in principle add a CSP that blocks userscript injection. Userscripts in Tampermonkey run in their own sandbox and bypass page CSP for their own code, so the risk is specifically about *calling into page-context APIs*; that's mitigated by using Tampermonkey's `unsafeWindow` if needed (not currently needed).
