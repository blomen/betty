# firevstocks UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a 4-tab React dashboard (Chart, DQN, Bankroll, Stats) for the firevstocks NQ trading bot, served from the local dashboard server on port 8001.

**Architecture:** Vite + React 19 + Tailwind + lightweight-charts frontend in `firevstocks/frontend/`, proxied to `dashboard.py` on port 8001. Dashboard.py gets new proxy routes to forward requests to the Hetzner server (via SSH tunnel on port 18000) and to TopstepX REST API. The existing `/ws/dashboard` WebSocket broadcasts live ticks/signals/zones/positions — no WS changes needed except adding fill/exit broadcasts.

**Tech Stack:** React 19, TypeScript, Vite 6, Tailwind 3, lightweight-charts 4, @tanstack/react-query 5, httpx (backend proxy)

**Spec:** `docs/superpowers/specs/2026-04-10-firevstocks-ui-design.md`

---

### Task 1: Backend — Add proxy routes and fill/exit broadcasts to dashboard.py

**Files:**
- Modify: `backend/src/stocks/dashboard.py`
- Modify: `backend/run_firevstocks.py`

- [ ] **Step 1: Add httpx import and server proxy routes to dashboard.py**

Add proxy routes after the existing `/api/state` endpoint. The server is accessible via SSH tunnel on `localhost:18000`. Add these imports at the top:

```python
import httpx
```

Add a module-level async client:

```python
SERVER_URL = "http://127.0.0.1:18000"
_http = httpx.AsyncClient(timeout=10.0)
```

Inside `create_dashboard_app()`, after the existing `get_state` route, add:

```python
    @app.get("/api/candles")
    async def proxy_candles(interval: str = "5m", days: int = 3, date: str | None = None):
        params = {"symbol": "NQ", "interval": interval, "days": str(days)}
        if date:
            params["date"] = date
        r = await _http.get(f"{SERVER_URL}/api/trading/market/candles", params=params)
        return r.json()

    @app.get("/api/session")
    async def proxy_session():
        r = await _http.get(f"{SERVER_URL}/api/trading/market/session")
        return r.json()

    @app.get("/api/session-levels")
    async def proxy_session_levels(days: int = 5):
        r = await _http.get(f"{SERVER_URL}/api/trading/market/session-levels",
                            params={"symbol": "NQ", "days": str(days)})
        return r.json()

    @app.get("/api/vp/{tf}")
    async def proxy_vp(tf: str):
        r = await _http.get(f"{SERVER_URL}/api/trading/market/volume-profile",
                            params={"symbol": "NQ", "timeframe": tf})
        return r.json()

    @app.get("/api/vwap")
    async def proxy_vwap():
        r = await _http.get(f"{SERVER_URL}/api/trading/market/vwap",
                            params={"symbol": "NQ", "interval": "1m"})
        return r.json()

    @app.get("/api/session-tpo")
    async def proxy_session_tpo():
        r = await _http.get(f"{SERVER_URL}/api/trading/market/tpo/sessions",
                            params={"symbol": "NQ"})
        return r.json()

    @app.get("/api/trades")
    async def get_trades():
        client = _state.get("topstepx_client")
        if not client:
            return {"trades": []}
        return await client._post("/api/Trade/search", {
            "accountId": client._account_id,
        })

    @app.get("/api/account-info")
    async def get_account_info():
        client = _state.get("topstepx_client")
        if not client:
            return {}
        accounts = await client._post("/api/Account/search", {
            "onlyActiveAccounts": True,
        })
        return accounts[0] if accounts else {}
```

- [ ] **Step 2: Add fill/exit broadcast functions to dashboard.py**

Add these after the existing `update_status` function:

```python
def record_fill(fill: dict) -> None:
    """Called from pipeline when a trade fill occurs."""
    _state["stats"]["trade_count"] += 1
    asyncio.create_task(broadcast({"type": "fill", **fill}))


def record_exit(exit_info: dict) -> None:
    """Called from pipeline when a trade exit occurs."""
    asyncio.create_task(broadcast({"type": "exit", **exit_info}))
```

- [ ] **Step 3: Serve React dist from dashboard.py**

Replace the existing `index()` route inside `create_dashboard_app()`. Change the import at the top (add `StaticFiles`):

```python
from fastapi.staticfiles import StaticFiles
```

Replace the existing `@app.get("/", response_class=HTMLResponse)` route with:

```python
    dist_path = Path(__file__).parent.parent.parent.parent / "firevstocks" / "frontend" / "dist"
    if dist_path.exists() and (dist_path / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist_path / "assets"), name="assets")

        @app.get("/", response_class=HTMLResponse)
        async def index():
            return HTMLResponse((dist_path / "index.html").read_text(encoding="utf-8"))
    else:
        @app.get("/", response_class=HTMLResponse)
        async def index():
            html_path = Path(__file__).parent / "dashboard.html"
            return HTMLResponse(html_path.read_text(encoding="utf-8"))
```

Note: the path traversal is `dashboard.py` → `stocks/` → `src/` → `backend/` → repo root → `firevstocks/frontend/dist`.

- [ ] **Step 4: Wire TopstepX client and fill/exit callbacks in run_firevstocks.py**

In `run_firevstocks.py`, inside the `_run()` function, after `dash_state["stats"]["session_start"] = time.time()`, add:

```python
    dash_state["topstepx_client"] = topstepx_client
```

Update the imports from dashboard to include the new functions:

```python
    from src.stocks.dashboard import (
        create_dashboard_app,
        record_tick as dash_tick,
        record_quote as dash_quote,
        record_signal as dash_signal,
        record_fill as dash_fill,
        record_exit as dash_exit,
        update_zones,
        update_status,
        _state as dash_state,
    )
```

Wire the fill callback — update the existing `_on_fill` to also call `dash_fill`:

```python
    def _on_fill(fill: dict) -> None:
        side = "long" if fill.get("side", 0) == 0 else "short"
        price = float(fill.get("price", 0))
        size = int(fill.get("size", 1))
        asyncio.create_task(relay.forward_fill(side, price, size, 0.0))
        dash_fill({"side": side, "price": price, "size": size, "ts": time.time()})
```

- [ ] **Step 5: Verify backend starts without errors**

Run: `cd backend && python -c "from src.stocks.dashboard import create_dashboard_app, record_fill, record_exit; print('OK')"`

Expected: `OK` (no import errors)

- [ ] **Step 6: Commit**

```bash
git add backend/src/stocks/dashboard.py backend/run_firevstocks.py
git commit -m "feat(firevstocks): add proxy routes, fill/exit broadcasts, serve React dist"
```

---

### Task 2: Frontend scaffold — Vite + React + Tailwind project

**Files:**
- Create: `firevstocks/frontend/package.json`
- Create: `firevstocks/frontend/vite.config.ts`
- Create: `firevstocks/frontend/tsconfig.json`
- Create: `firevstocks/frontend/postcss.config.js`
- Create: `firevstocks/frontend/tailwind.config.js`
- Create: `firevstocks/frontend/index.html`
- Create: `firevstocks/frontend/src/vite-env.d.ts`
- Create: `firevstocks/frontend/src/main.tsx`
- Create: `firevstocks/frontend/src/index.css`
- Create: `firevstocks/frontend/src/App.tsx`

- [ ] **Step 1: Create package.json**

```json
{
  "name": "firevstocks-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "lightweight-charts": "^4.2.0",
    "@tanstack/react-query": "^5.0.0"
  },
  "devDependencies": {
    "@vitejs/plugin-react-swc": "^4.0.0",
    "vite": "^6.0.0",
    "typescript": "^5.7.0",
    "tailwindcss": "^3.4.0",
    "postcss": "^8.4.0",
    "autoprefixer": "^10.4.0",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0"
  }
}
```

- [ ] **Step 2: Create vite.config.ts**

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react-swc'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  server: {
    host: '127.0.0.1',
    port: 5175,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8001', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8001', ws: true },
    },
  },
  build: { outDir: 'dist' },
})
```

- [ ] **Step 3: Create tsconfig.json**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "paths": { "@/*": ["./src/*"] }
  },
  "include": ["src"]
}
```

- [ ] **Step 4: Create postcss.config.js and tailwind.config.js**

`postcss.config.js`:
```javascript
export default {
  plugins: { tailwindcss: {}, autoprefixer: {} },
}
```

`tailwind.config.js`:
```javascript
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: { extend: {} },
  plugins: [],
}
```

- [ ] **Step 5: Create index.html**

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>firevstocks</title>
  </head>
  <body class="bg-zinc-950 text-zinc-200">
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 6: Create src/vite-env.d.ts**

```typescript
/// <reference types="vite/client" />
```

- [ ] **Step 7: Create src/main.tsx**

```typescript
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 5000 } },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
```

- [ ] **Step 8: Create src/index.css**

Copy from `firevsports/frontend/src/index.css` (the full retro terminal theme). It's 246 lines — copy the entire file. No modifications needed except removing the betting-specific flash animations (`flash-up`, `flash-down`, `row-enter`, `row-exit`). Keep: tailwind directives, root CSS vars, body font, scrollbar styles, `table.sq` styles, selection styling.

- [ ] **Step 9: Create src/App.tsx**

```typescript
import { useState } from 'react'
import { useDashboardWS } from './hooks/useDashboardWS'

type Tab = 'chart' | 'dqn' | 'bankroll' | 'stats'

const TABS: { name: Tab; label: string; color: string }[] = [
  { name: 'chart',    label: 'Chart',    color: '#f59e0b' },
  { name: 'dqn',      label: 'DQN',      color: '#8b5cf6' },
  { name: 'bankroll', label: 'Bankroll', color: '#ec4899' },
  { name: 'stats',    label: 'Stats',    color: '#3b82f6' },
]

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('chart')
  const ws = useDashboardWS()

  return (
    <div className="flex flex-col h-screen bg-zinc-950">
      <div className="flex items-center gap-1 px-3 py-1 border-b border-zinc-800 bg-zinc-900">
        <span className="text-sm font-bold text-amber-500 mr-4">firevstocks</span>
        {TABS.map(tab => (
          <button
            key={tab.name}
            onClick={() => setActiveTab(tab.name)}
            className={`px-3 py-1.5 text-xs font-mono uppercase tracking-wider ${
              activeTab === tab.name ? 'text-zinc-950 font-bold' : 'text-zinc-500 hover:text-zinc-300'
            }`}
            style={activeTab === tab.name ? { backgroundColor: tab.color } : undefined}
          >
            <span style={{ color: activeTab === tab.name ? undefined : tab.color }}>● </span>
            {tab.label}
          </button>
        ))}
        <div className="flex-1" />
        <span className={`text-xs font-mono ${ws.state.connected ? 'text-emerald-400' : 'text-red-400'}`}>
          {ws.state.connected ? '● Connected' : '● Disconnected'}
        </span>
        {ws.state.lastPrice && (
          <span className="text-xs font-mono text-zinc-400 ml-2">
            NQ {ws.state.lastPrice.toFixed(2)}
          </span>
        )}
      </div>
      <div className="flex flex-col flex-1 min-h-0 min-w-0 overflow-hidden p-2">
        {activeTab === 'chart' && <div className="flex-1 min-h-0 text-zinc-500 flex items-center justify-center">Chart tab — coming in Task 5</div>}
        {activeTab === 'dqn' && <div className="flex-1 min-h-0 text-zinc-500 flex items-center justify-center">DQN tab — coming in Task 6</div>}
        {activeTab === 'bankroll' && <div className="flex-1 min-h-0 text-zinc-500 flex items-center justify-center">Bankroll tab — coming in Task 7</div>}
        {activeTab === 'stats' && <div className="flex-1 min-h-0 text-zinc-500 flex items-center justify-center">Stats tab — coming in Task 8</div>}
      </div>
    </div>
  )
}
```

- [ ] **Step 10: Install dependencies and verify dev server starts**

Run:
```bash
cd firevstocks/frontend && npm install && npm run dev -- --host 127.0.0.1
```

Expected: Vite dev server starts on http://127.0.0.1:5175. Kill with Ctrl+C after verifying.

- [ ] **Step 11: Commit**

```bash
git add firevstocks/frontend/
git commit -m "feat(firevstocks): scaffold React frontend — Vite + Tailwind + tab shell"
```

---

### Task 3: Types and API hooks

**Files:**
- Create: `firevstocks/frontend/src/types/index.ts`
- Create: `firevstocks/frontend/src/hooks/useApi.ts`
- Create: `firevstocks/frontend/src/hooks/useDashboardWS.ts`

- [ ] **Step 1: Create types/index.ts**

```typescript
/** OHLCV candle for chart rendering */
export interface CandleData {
  t: number  // Unix epoch seconds
  o: number
  h: number
  l: number
  c: number
  v: number
}

export interface CandlesResponse {
  candles: CandleData[]
  symbol: string
  interval: string
}

/** Per-day session levels with CET epoch boundaries */
export interface SessionLevelDay {
  date: string
  pdh: number | null
  pdl: number | null
  ib_high: number | null
  ib_low: number | null
  tokyo_high: number | null
  tokyo_low: number | null
  london_high: number | null
  london_low: number | null
  ny_high: number | null
  ny_low: number | null
  tokyo_start: number
  tokyo_end: number
  london_start: number
  london_end: number
  ib_start: number
  ib_end: number
  ny_start: number
  ny_end: number
  day_start: number
  day_end: number
  daily_swing_high: number | null
  daily_swing_low: number | null
  weekly_swing_high: number | null
  weekly_swing_low: number | null
  monthly_swing_high: number | null
  monthly_swing_low: number | null
}

export interface SessionLevelsResponse {
  days: SessionLevelDay[]
  symbol: string
}

export interface VPData {
  levels: Array<{ price: number; volume: number }>
  poc: number
  vah: number
  val: number
  timeframe: string
}

export interface VWAPPoint {
  t: number
  vwap: number
  sd1_u: number
  sd1_l: number
  sd2_u: number
  sd2_l: number
  sd3_u: number
  sd3_l: number
}

export interface VWAPResponse {
  vwap: VWAPPoint[]
  symbol: string
  count: number
}

export interface SessionTPOData {
  letters: Record<string, string[]>
  tpo_counts: Record<string, number>
  poc: number
  vah: number
  val: number
  ib_high: number
  ib_low: number
  ib_valid: boolean
  shape: string
  opening_type: string
  opening_direction: string
  poor_high: boolean
  poor_low: boolean
  upper_excess: number
  lower_excess: number
  session_high: number
  session_low: number
  rotation_factor: number
}

export interface SessionTPOResponse {
  date: string
  sessions: {
    tokyo: SessionTPOData | null
    london: SessionTPOData | null
    ny: SessionTPOData | null
  }
  poc_migration_tokyo_london: number
  poc_migration_london_ny: number
}

/** Expanded session from server */
export interface ExpandedSession {
  session: {
    vwap?: number
    poc?: number
    vah?: number
    val?: number
    ib_high?: number
    ib_low?: number
    last_price?: number
  }
  macro: {
    cot_net_position?: number | null
    cot_change_1w?: number | null
  }
  profiles: {
    session: { poc: number; vah: number; val: number }
    weekly?: { poc: number; vah: number; val: number }
    monthly?: { poc: number; vah: number; val: number }
  }
  price_position: { last_price: number | null }
}

/** WS event types from /ws/dashboard */
export interface Signal {
  action: string
  confidence: number
  cont_p?: number
  rev_p?: number
  stop_ticks?: number
  zone?: string
  specialist?: string
  price?: number
  features?: number[]
  ts?: number
}

export interface Zone {
  price: number
  members: number
  name?: string
}

export interface Fill {
  side: string
  price: number
  size: number
  ts: number
}

export interface ExitEvent {
  price: number
  was_stop?: boolean
  ts: number
}

export interface Quote {
  bid: number
  ask: number
  bid_size: number
  ask_size: number
}

export interface Position {
  side: string | number
  size: number
  price: number
  contractId?: string
}

export interface Account {
  id?: number
  balance?: number
  buyingPower?: number
  canTrade?: boolean
  [key: string]: unknown
}

export interface Trade {
  id: number
  accountId: number
  contractId: string
  side: number  // 0=Buy, 1=Sell
  size: number
  price: number
  timestamp: string
  [key: string]: unknown
}
```

- [ ] **Step 2: Create hooks/useApi.ts**

```typescript
const API_BASE = '/api'

async function fetchJson<T>(endpoint: string): Promise<T> {
  const res = await fetch(`${API_BASE}${endpoint}`)
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`)
  return res.json()
}

export const api = {
  getCandles(interval = '5m', days = 3, date?: string) {
    const params = new URLSearchParams({ interval, days: String(days) })
    if (date) params.set('date', date)
    return fetchJson<import('@/types').CandlesResponse>(`/candles?${params}`)
  },

  getSession() {
    return fetchJson<import('@/types').ExpandedSession>('/session')
  },

  getSessionLevels(days = 5) {
    return fetchJson<import('@/types').SessionLevelsResponse>(`/session-levels?days=${days}`)
  },

  getVP(tf: string) {
    return fetchJson<import('@/types').VPData>(`/vp/${tf}`)
  },

  getVWAP() {
    return fetchJson<import('@/types').VWAPResponse>('/vwap')
  },

  getSessionTPO() {
    return fetchJson<import('@/types').SessionTPOResponse>('/session-tpo')
  },

  getState() {
    return fetchJson<{
      ticks: Array<{ p: number; s: number; t: number; d: string }>
      signals: import('@/types').Signal[]
      quote: import('@/types').Quote | null
      zones: import('@/types').Zone[]
      account: import('@/types').Account
      positions: import('@/types').Position[]
      stats: { tick_count: number; signal_count: number; trade_count: number; session_start: number | null; relay_connected: boolean; stream_running: boolean }
    }>('/state')
  },

  getTrades() {
    return fetchJson<{ trades?: import('@/types').Trade[] }>('/trades')
  },

  getAccountInfo() {
    return fetchJson<import('@/types').Account>('/account-info')
  },
}
```

- [ ] **Step 3: Create hooks/useDashboardWS.ts**

```typescript
import { useEffect, useRef, useState, useCallback } from 'react'
import type { Signal, Zone, Fill, ExitEvent, Quote, Position } from '@/types'

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
}

export interface TickEvent {
  price: number
  ts: number
  tick_count: number
}

const MAX_SIGNALS = 100
const MAX_FILLS = 200
const RECONNECT_MS = 2000

export function useDashboardWS() {
  const [state, setState] = useState<DashboardState>({
    connected: false,
    relayConnected: false,
    streamRunning: false,
    lastPrice: null,
    tickCount: 0,
    signals: [],
    zones: [],
    fills: [],
    exits: [],
    positions: [],
    quote: null,
  })

  const [lastTick, setLastTick] = useState<TickEvent | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>()

  const connect = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/dashboard`)
    wsRef.current = ws

    ws.onopen = () => {
      setState(s => ({ ...s, connected: true }))
    }

    ws.onclose = () => {
      setState(s => ({ ...s, connected: false }))
      wsRef.current = null
      reconnectTimer.current = setTimeout(connect, RECONNECT_MS)
    }

    ws.onerror = () => {
      ws.close()
    }

    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data)
      switch (msg.type) {
        case 'tick':
          setLastTick({ price: msg.price, ts: msg.ts, tick_count: msg.tick_count })
          setState(s => ({ ...s, lastPrice: msg.price, tickCount: msg.tick_count }))
          break
        case 'signal':
          setState(s => ({
            ...s,
            signals: [...s.signals.slice(-(MAX_SIGNALS - 1)), msg as Signal],
          }))
          break
        case 'zones':
          setState(s => ({ ...s, zones: msg.zones }))
          break
        case 'quote':
          setState(s => ({ ...s, quote: msg as Quote }))
          break
        case 'account':
          break  // handled by polling
        case 'positions':
          setState(s => ({ ...s, positions: msg.positions }))
          break
        case 'status':
          setState(s => ({
            ...s,
            relayConnected: msg.relay_connected,
            streamRunning: msg.stream_running,
          }))
          break
        case 'fill':
          setState(s => ({
            ...s,
            fills: [...s.fills.slice(-(MAX_FILLS - 1)), msg as Fill],
          }))
          break
        case 'exit':
          setState(s => ({
            ...s,
            exits: [...s.exits, msg as ExitEvent],
          }))
          break
      }
    }
  }, [])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  return { state, lastTick }
}
```

- [ ] **Step 4: Verify TypeScript compiles**

Run:
```bash
cd firevstocks/frontend && npx tsc --noEmit
```

Expected: No errors.

- [ ] **Step 5: Commit**

```bash
git add firevstocks/frontend/src/types/ firevstocks/frontend/src/hooks/
git commit -m "feat(firevstocks): types, API hooks, and WebSocket state management"
```

---

### Task 4: Update App.tsx to wire tabs with real data

**Files:**
- Modify: `firevstocks/frontend/src/App.tsx`

- [ ] **Step 1: Update App.tsx to pass WS state to tab placeholders**

Replace the placeholder tab content with actual pages as they're built. For now, update the import and pass ws data to show it's working:

```typescript
import { useState, useEffect } from 'react'
import { useDashboardWS } from './hooks/useDashboardWS'
import { api } from './hooks/useApi'
import type { ExpandedSession } from './types'

type Tab = 'chart' | 'dqn' | 'bankroll' | 'stats'

const TABS: { name: Tab; label: string; color: string }[] = [
  { name: 'chart',    label: 'Chart',    color: '#f59e0b' },
  { name: 'dqn',      label: 'DQN',      color: '#8b5cf6' },
  { name: 'bankroll', label: 'Bankroll', color: '#ec4899' },
  { name: 'stats',    label: 'Stats',    color: '#3b82f6' },
]

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('chart')
  const { state: ws, lastTick } = useDashboardWS()
  const [session, setSession] = useState<ExpandedSession | null>(null)

  // Fetch session data periodically
  useEffect(() => {
    const fetch = () => { api.getSession().then(setSession).catch(() => {}) }
    fetch()
    const iv = setInterval(fetch, 60_000)
    return () => clearInterval(iv)
  }, [])

  return (
    <div className="flex flex-col h-screen bg-zinc-950">
      <div className="flex items-center gap-1 px-3 py-1 border-b border-zinc-800 bg-zinc-900">
        <span className="text-sm font-bold text-amber-500 mr-4">firevstocks</span>
        {TABS.map(tab => (
          <button
            key={tab.name}
            onClick={() => setActiveTab(tab.name)}
            className={`px-3 py-1.5 text-xs font-mono uppercase tracking-wider ${
              activeTab === tab.name ? 'text-zinc-950 font-bold' : 'text-zinc-500 hover:text-zinc-300'
            }`}
            style={activeTab === tab.name ? { backgroundColor: tab.color } : undefined}
          >
            <span style={{ color: activeTab === tab.name ? undefined : tab.color }}>● </span>
            {tab.label}
          </button>
        ))}
        <div className="flex-1" />
        <span className={`text-xs font-mono ${ws.relayConnected ? 'text-emerald-400' : ws.connected ? 'text-yellow-400' : 'text-red-400'}`}>
          ● {ws.relayConnected ? 'Relay' : ws.connected ? 'WS only' : 'Disconnected'}
        </span>
        {ws.streamRunning && <span className="text-xs font-mono text-emerald-400 ml-1">● Stream</span>}
        {ws.lastPrice && (
          <span className="text-xs font-mono text-zinc-400 ml-2">
            NQ {ws.lastPrice.toFixed(2)}
          </span>
        )}
        {ws.tickCount > 0 && (
          <span className="text-xs font-mono text-zinc-600 ml-2">
            {ws.tickCount.toLocaleString()} ticks
          </span>
        )}
      </div>
      <div className="flex flex-col flex-1 min-h-0 min-w-0 overflow-hidden p-2">
        {activeTab === 'chart' && <ChartPlaceholder ws={ws} lastTick={lastTick} session={session} />}
        {activeTab === 'dqn' && <DQNPlaceholder ws={ws} />}
        {activeTab === 'bankroll' && <BankrollPlaceholder ws={ws} />}
        {activeTab === 'stats' && <StatsPlaceholder />}
      </div>
    </div>
  )
}

// Temporary placeholders — replaced by real pages in Tasks 5-8
function ChartPlaceholder({ ws, lastTick, session }: any) {
  return <div className="flex-1 flex items-center justify-center text-zinc-600 text-sm font-mono">
    Chart tab — {lastTick ? `Last: ${lastTick.price}` : 'waiting for ticks...'}
    {session && ` | VWAP: ${session.session?.vwap?.toFixed(2) ?? '—'}`}
  </div>
}
function DQNPlaceholder({ ws }: any) {
  return <div className="flex-1 flex items-center justify-center text-zinc-600 text-sm font-mono">
    DQN — {ws.signals.length} signals
  </div>
}
function BankrollPlaceholder({ ws }: any) {
  return <div className="flex-1 flex items-center justify-center text-zinc-600 text-sm font-mono">
    Bankroll — {ws.positions.length} positions
  </div>
}
function StatsPlaceholder() {
  return <div className="flex-1 flex items-center justify-center text-zinc-600 text-sm font-mono">
    Stats tab — coming soon
  </div>
}
```

- [ ] **Step 2: Verify dev server renders tabs**

Run: `cd firevstocks/frontend && npm run dev`

Open http://127.0.0.1:5175 — verify tab bar renders, clicking tabs switches content. WS will show "Disconnected" unless dashboard.py is running — that's expected.

- [ ] **Step 3: Commit**

```bash
git add firevstocks/frontend/src/App.tsx
git commit -m "feat(firevstocks): wire App shell with WS state and session polling"
```

---

### Task 5: Chart tab — CandleChart with full overlays

**Files:**
- Create: `firevstocks/frontend/src/pages/ChartPage.tsx`
- Create: `firevstocks/frontend/src/pages/CandleChart.tsx`
- Modify: `firevstocks/frontend/src/App.tsx`

This is the largest task — it ports the existing `frontend/src/components/Terminal/pages/CandleChart.tsx` (1078 lines) with adaptations for the firevstocks data flow.

- [ ] **Step 1: Create pages/CandleChart.tsx**

Copy `frontend/src/components/Terminal/pages/CandleChart.tsx` into `firevstocks/frontend/src/pages/CandleChart.tsx`. Then make these modifications:

1. **Replace `api` import**: Change `import { api } from '@/services/api'` to `import { api } from '@/hooks/useApi'`

2. **Replace type imports**: Change `import type { CandleData, ExpandedSession, SessionTPOResponse, SessionTPOData } from '@/types/market'` to `import type { CandleData, ExpandedSession, SessionTPOResponse, SessionTPOData } from '@/types'`

3. **Update API calls** — the dashboard.py proxy uses shorter paths:
   - `api.getCandles('NQ', INTERVAL, undefined, INITIAL_DAYS)` → `api.getCandles(INTERVAL, INITIAL_DAYS)`
   - `api.getCandles('NQ', INTERVAL, endDate, SCROLL_DAYS)` → `api.getCandles(INTERVAL, SCROLL_DAYS, endDate)`
   - `api.getVolumeProfile('NQ', overlay.tf)` → `api.getVP(overlay.tf)`
   - `api.getSessionLevels('NQ', INITIAL_DAYS + 2)` → `api.getSessionLevels(INITIAL_DAYS + 2)`
   - `api.getSessionTPO('NQ')` → `api.getSessionTPO()`
   - `api.getDevelopingVwap('NQ', '1m')` → `api.getVWAP()`

4. **Update VWAP response shape**: The proxy returns the same shape, so no changes needed to the VWAP effect.

5. **Update cache key**: Change `CANDLE_CACHE_KEY` from `'firev_candles_1m'` to `'firevstocks_candles_1m'`

6. **Add zone overlay support**: Add a new prop `zones` to the Props interface and a new `useEffect` to draw zone lines on the canvas:

Add to Props:
```typescript
zones?: Array<{ price: number; members: number }>
```

Add to `drawOverlays` callback (after the VP histograms section, before the COT annotation):

```typescript
    // --- Zone lines from server ---
    const zonesData = zonesRef.current;
    if (zonesData.length > 0) {
      const maxMembers = Math.max(...zonesData.map(z => z.members));
      for (const zone of zonesData) {
        const y = pSeries.priceToCoordinate(zone.price);
        if (y === null || y < 0 || y > rect.height) continue;
        const alpha = 0.2 + 0.5 * (zone.members / maxMembers);
        ctx.save();
        ctx.strokeStyle = `rgba(167, 139, 250, ${alpha})`;
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(rect.width, y);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.font = '8px monospace';
        ctx.fillStyle = `rgba(167, 139, 250, ${alpha + 0.2})`;
        ctx.textAlign = 'right';
        ctx.fillText(`Z:${zone.members}`, rect.width - 70, y - 3);
        ctx.restore();
      }
    }
```

Add a ref for zones:
```typescript
const zonesRef = useRef<Array<{ price: number; members: number }>>([]);
```

Add an effect to update it:
```typescript
useEffect(() => {
  zonesRef.current = zones ?? [];
  drawOverlays();
}, [zones, drawOverlays]);
```

7. **Remove COT annotation** — not available from dashboard proxy. Delete the COT block and `macroRef`.

8. **Keep everything else** — session boxes, VP, TPO, VWAP, swing levels, IB, PDH/PDL all remain.

- [ ] **Step 2: Create pages/ChartPage.tsx**

```typescript
import { useState, useEffect, useCallback } from 'react'
import { CandleChart } from './CandleChart'
import { api } from '@/hooks/useApi'
import type { CandleData, ExpandedSession, Zone, Signal, Fill, ExitEvent } from '@/types'
import type { TickEvent } from '@/hooks/useDashboardWS'

interface Props {
  lastTick: TickEvent | null
  session: ExpandedSession | null
  zones: Zone[]
  signals: Signal[]
  fills: Fill[]
  exits: ExitEvent[]
}

export function ChartPage({ lastTick, session, zones, signals, fills, exits }: Props) {
  const [interval, setInterval_] = useState<'1m' | '5m' | '15m'>('5m')
  const [hiddenLevels, setHiddenLevels] = useState<Set<string>>(() => {
    try {
      const saved = localStorage.getItem('firevstocks-hidden-levels')
      return saved ? new Set(JSON.parse(saved)) : new Set()
    } catch { return new Set() }
  })

  // Persist hidden levels
  useEffect(() => {
    localStorage.setItem('firevstocks-hidden-levels', JSON.stringify([...hiddenLevels]))
  }, [hiddenLevels])

  const toggleLevel = useCallback((key: string) => {
    setHiddenLevels(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])

  // Build a CandleData from the last tick for live updates
  const [lastCandle, setLastCandle] = useState<CandleData | null>(null)
  const candleIntervalSec = interval === '1m' ? 60 : interval === '5m' ? 300 : 900

  useEffect(() => {
    if (!lastTick) return
    const { price, ts } = lastTick
    const bucketStart = Math.floor(ts / candleIntervalSec) * candleIntervalSec

    setLastCandle(prev => {
      if (prev && prev.t === bucketStart) {
        return {
          ...prev,
          h: Math.max(prev.h, price),
          l: Math.min(prev.l, price),
          c: price,
          v: prev.v + 1,
        }
      }
      return { t: bucketStart, o: price, h: price, l: price, c: price, v: 1 }
    })
  }, [lastTick, candleIntervalSec])

  const price = lastTick?.price ?? session?.price_position?.last_price ?? null

  return (
    <div className="flex flex-col flex-1 min-h-0 gap-1">
      {/* Status + controls */}
      <div className="flex items-center gap-3 px-1">
        {price && (
          <span className="text-sm font-mono font-bold text-zinc-200">
            NQ {price.toFixed(2)}
          </span>
        )}
        <div className="flex gap-1">
          {(['1m', '5m', '15m'] as const).map(iv => (
            <button
              key={iv}
              onClick={() => setInterval_(iv)}
              className={`px-2 py-0.5 text-[10px] font-mono border ${
                interval === iv
                  ? 'border-amber-500 text-amber-500'
                  : 'border-zinc-700 text-zinc-500 hover:text-zinc-300'
              }`}
            >
              {iv}
            </button>
          ))}
        </div>
        {lastTick && (
          <span className="text-[10px] font-mono text-zinc-600">
            {lastTick.tick_count.toLocaleString()} ticks
          </span>
        )}
      </div>

      {/* Chart */}
      <div className="flex-1 border border-zinc-800 bg-zinc-950 min-h-0 overflow-hidden">
        <CandleChart
          lastCandle={lastCandle}
          session={session}
          hiddenLevels={hiddenLevels}
          zones={zones}
        />
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Update App.tsx to render ChartPage**

Add import at top:
```typescript
import { ChartPage } from './pages/ChartPage'
```

Replace the chart placeholder:
```typescript
{activeTab === 'chart' && (
  <ChartPage
    lastTick={lastTick}
    session={session}
    zones={ws.zones}
    signals={ws.signals}
    fills={ws.fills}
    exits={ws.exits}
  />
)}
```

- [ ] **Step 4: Build and verify chart renders**

Run:
```bash
cd firevstocks/frontend && npx tsc --noEmit
```

Expected: No type errors. If there are type mismatches from the ported CandleChart, fix the imports.

- [ ] **Step 5: Commit**

```bash
git add firevstocks/frontend/src/pages/ChartPage.tsx firevstocks/frontend/src/pages/CandleChart.tsx firevstocks/frontend/src/App.tsx
git commit -m "feat(firevstocks): Chart tab — full CandleChart port with zones overlay"
```

---

### Task 6: DQN tab — Signal panel + feature heatmap + history

**Files:**
- Create: `firevstocks/frontend/src/pages/DQNPage.tsx`
- Modify: `firevstocks/frontend/src/App.tsx`

- [ ] **Step 1: Create pages/DQNPage.tsx**

```typescript
import { useRef, useEffect } from 'react'
import type { Signal, Zone } from '@/types'

interface Props {
  signals: Signal[]
  zones: Zone[]
  lastPrice: number | null
}

export function DQNPage({ signals, zones, lastPrice }: Props) {
  const latest = signals.length > 0 ? signals[signals.length - 1] : null
  const historyRef = useRef<HTMLDivElement>(null)

  // Auto-scroll signal history
  useEffect(() => {
    historyRef.current?.scrollTo({ top: historyRef.current.scrollHeight, behavior: 'smooth' })
  }, [signals.length])

  return (
    <div className="flex flex-col flex-1 min-h-0 gap-3 overflow-y-auto">
      {/* Signal Panel */}
      <div className="grid grid-cols-3 gap-2">
        <SignalCard
          label="Action"
          value={latest?.action ?? '—'}
          color={latest?.action?.includes('long') || latest?.action === 'CONT' ? '#4ade80'
            : latest?.action?.includes('short') || latest?.action === 'REV' ? '#ef4444'
            : '#a1a1aa'}
        />
        <SignalCard
          label="Confidence"
          value={latest ? `${(latest.confidence * 100).toFixed(1)}%` : '—'}
          color="#f59e0b"
          bar={latest?.confidence}
        />
        <SignalCard
          label="Specialist"
          value={latest?.specialist ?? '—'}
          color="#8b5cf6"
        />
        <SignalCard
          label="cont_p"
          value={latest?.cont_p != null ? latest.cont_p.toFixed(3) : '—'}
          color="#4ade80"
        />
        <SignalCard
          label="rev_p"
          value={latest?.rev_p != null ? latest.rev_p.toFixed(3) : '—'}
          color="#ef4444"
        />
        <SignalCard
          label="stop_ticks"
          value={latest?.stop_ticks != null ? String(latest.stop_ticks) : '—'}
          color="#f59e0b"
        />
      </div>

      {/* Feature Heatmap */}
      <div className="border border-zinc-800 bg-zinc-900 p-3">
        <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider mb-2">Feature Activation (276-dim)</h3>
        {latest?.features ? (
          <FeatureHeatmap features={latest.features} />
        ) : (
          <div className="text-xs font-mono text-zinc-600 py-4 text-center">
            No feature data — waiting for signal with features array
          </div>
        )}
      </div>

      {/* Zone Status */}
      <div className="border border-zinc-800 bg-zinc-900 p-3">
        <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider mb-2">
          Active Zones ({zones.length})
        </h3>
        {zones.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {zones.map((z, i) => {
              const dist = lastPrice ? Math.abs(lastPrice - z.price) : null
              return (
                <span key={i} className="text-xs font-mono px-2 py-1 border border-zinc-700 bg-zinc-950">
                  <span className="text-purple-400">{z.price.toFixed(2)}</span>
                  <span className="text-zinc-600 ml-1">×{z.members}</span>
                  {dist != null && <span className="text-zinc-600 ml-1">({dist.toFixed(2)})</span>}
                </span>
              )
            })}
          </div>
        ) : (
          <div className="text-xs font-mono text-zinc-600">No zones loaded</div>
        )}
      </div>

      {/* Signal History */}
      <div className="border border-zinc-800 bg-zinc-900 flex-1 min-h-[200px] flex flex-col">
        <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider p-3 pb-1">
          Signal History ({signals.length})
        </h3>
        <div ref={historyRef} className="flex-1 overflow-y-auto">
          <table className="sq w-full">
            <thead>
              <tr>
                <th>Time</th>
                <th>Action</th>
                <th>Confidence</th>
                <th>Zone</th>
                <th>Specialist</th>
                <th>Price</th>
              </tr>
            </thead>
            <tbody>
              {signals.length === 0 ? (
                <tr><td colSpan={6} className="text-center text-zinc-600">No signals yet</td></tr>
              ) : (
                signals.map((sig, i) => (
                  <tr key={i}>
                    <td className="text-zinc-500">
                      {sig.ts ? new Date(sig.ts * 1000).toLocaleTimeString() : '—'}
                    </td>
                    <td className={sig.action?.includes('long') || sig.action === 'CONT' ? 'text-emerald-400' : sig.action?.includes('short') || sig.action === 'REV' ? 'text-red-400' : ''}>
                      {sig.action}
                    </td>
                    <td>{(sig.confidence * 100).toFixed(1)}%</td>
                    <td className="text-purple-400">{sig.zone ?? '—'}</td>
                    <td className="text-zinc-400">{sig.specialist ?? '—'}</td>
                    <td>{sig.price?.toFixed(2) ?? '—'}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function SignalCard({ label, value, color, bar }: { label: string; value: string; color: string; bar?: number }) {
  return (
    <div className="border border-zinc-800 bg-zinc-900 p-3">
      <div className="text-[10px] font-mono text-zinc-500 uppercase tracking-wider">{label}</div>
      <div className="text-lg font-mono font-bold mt-1" style={{ color }}>{value}</div>
      {bar != null && (
        <div className="mt-1 h-1 bg-zinc-800">
          <div className="h-full" style={{ width: `${bar * 100}%`, backgroundColor: color }} />
        </div>
      )}
    </div>
  )
}

function FeatureHeatmap({ features }: { features: number[] }) {
  const cols = 12
  const rows = Math.ceil(features.length / cols)
  const maxAbs = Math.max(...features.map(Math.abs), 0.001)

  return (
    <div className="grid gap-px" style={{ gridTemplateColumns: `repeat(${cols}, 1fr)` }}>
      {features.map((val, i) => {
        const norm = val / maxAbs
        const bg = val === 0
          ? '#2a2a2a'
          : val > 0
            ? `rgba(74, 222, 128, ${Math.abs(norm) * 0.8})`
            : `rgba(248, 113, 113, ${Math.abs(norm) * 0.8})`
        return (
          <div
            key={i}
            className="aspect-square"
            style={{ backgroundColor: bg }}
            title={`dim ${i}: ${val.toFixed(4)}`}
          />
        )
      })}
    </div>
  )
}
```

- [ ] **Step 2: Update App.tsx to import and render DQNPage**

Add import:
```typescript
import { DQNPage } from './pages/DQNPage'
```

Replace DQN placeholder:
```typescript
{activeTab === 'dqn' && (
  <DQNPage
    signals={ws.signals}
    zones={ws.zones}
    lastPrice={ws.lastPrice}
  />
)}
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd firevstocks/frontend && npx tsc --noEmit`

Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add firevstocks/frontend/src/pages/DQNPage.tsx firevstocks/frontend/src/App.tsx
git commit -m "feat(firevstocks): DQN tab — signal panel, feature heatmap, signal history"
```

---

### Task 7: Bankroll tab — Account, drawdown, positions

**Files:**
- Create: `firevstocks/frontend/src/pages/BankrollPage.tsx`
- Modify: `firevstocks/frontend/src/App.tsx`

- [ ] **Step 1: Create pages/BankrollPage.tsx**

```typescript
import { useState, useEffect } from 'react'
import { api } from '@/hooks/useApi'
import type { Position, Account, Fill, ExitEvent } from '@/types'

interface Props {
  positions: Position[]
  fills: Fill[]
  exits: ExitEvent[]
  lastPrice: number | null
}

const MAX_LOSS = 4500
const NQ_TICK_VALUE = 5  // $5 per tick (0.25 points per tick, $20 per point)
const NQ_POINT_VALUE = 20

export function BankrollPage({ positions, fills, exits, lastPrice }: Props) {
  const [account, setAccount] = useState<Account | null>(null)

  // Poll account info every 30s
  useEffect(() => {
    const fetch = () => { api.getAccountInfo().then(setAccount).catch(() => {}) }
    fetch()
    const iv = setInterval(fetch, 30_000)
    return () => clearInterval(iv)
  }, [])

  // Compute session P&L from fills and exits
  const closedPnL = computeClosedPnL(fills, exits)

  // Compute unrealized P&L from open positions
  const unrealizedPnL = positions.reduce((sum, pos) => {
    if (!lastPrice) return sum
    const entryPrice = pos.price
    const side = typeof pos.side === 'number' ? (pos.side === 0 ? 'long' : 'short') : pos.side
    const pnlPoints = side === 'long' ? lastPrice - entryPrice : entryPrice - lastPrice
    return sum + pnlPoints * NQ_POINT_VALUE * pos.size
  }, 0)

  const totalPnL = closedPnL + unrealizedPnL
  const drawdownUsed = Math.max(0, -totalPnL)
  const drawdownPct = Math.min(drawdownUsed / MAX_LOSS * 100, 100)
  const drawdownColor = drawdownPct > 75 ? '#ef4444' : drawdownPct > 50 ? '#f59e0b' : '#4ade80'

  return (
    <div className="flex flex-col gap-3 overflow-y-auto flex-1">
      {/* Account Card */}
      <div className="grid grid-cols-3 gap-2">
        <StatCard label="Balance" value={account?.balance != null ? `$${Number(account.balance).toLocaleString()}` : '—'} color="#ec4899" />
        <StatCard label="Buying Power" value={account?.buyingPower != null ? `$${Number(account.buyingPower).toLocaleString()}` : '—'} color="#8b5cf6" />
        <StatCard label="Account" value={account?.id ? `#${account.id}` : '—'} color="#3b82f6" sub="Practice" />
      </div>

      {/* Drawdown Gauge */}
      <div className="border border-zinc-800 bg-zinc-900 p-3">
        <div className="flex justify-between items-center mb-2">
          <span className="text-[10px] font-mono text-zinc-500 uppercase tracking-wider">
            Drawdown vs Max Loss ($4,500)
          </span>
          <span className="text-sm font-mono font-bold" style={{ color: drawdownColor }}>
            ${drawdownUsed.toFixed(0)} / ${MAX_LOSS}
          </span>
        </div>
        <div className="h-3 bg-zinc-800">
          <div
            className="h-full transition-all duration-300"
            style={{ width: `${drawdownPct}%`, backgroundColor: drawdownColor }}
          />
        </div>
        <div className="flex justify-between mt-1">
          <span className="text-[10px] font-mono text-zinc-600">{drawdownPct.toFixed(1)}% used</span>
          <span className="text-[10px] font-mono text-zinc-600">${(MAX_LOSS - drawdownUsed).toFixed(0)} remaining</span>
        </div>
      </div>

      {/* Session P&L */}
      <div className="grid grid-cols-3 gap-2">
        <StatCard label="Closed P&L" value={`$${closedPnL.toFixed(2)}`} color={closedPnL >= 0 ? '#4ade80' : '#ef4444'} />
        <StatCard label="Unrealized P&L" value={`$${unrealizedPnL.toFixed(2)}`} color={unrealizedPnL >= 0 ? '#4ade80' : '#ef4444'} />
        <StatCard label="Total P&L" value={`$${totalPnL.toFixed(2)}`} color={totalPnL >= 0 ? '#4ade80' : '#ef4444'} />
      </div>

      {/* Open Positions */}
      <div className="border border-zinc-800 bg-zinc-900 flex-1 min-h-[200px]">
        <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider p-3 pb-1">
          Open Positions ({positions.length})
        </h3>
        <table className="sq w-full">
          <thead>
            <tr>
              <th>Side</th>
              <th>Contracts</th>
              <th>Entry</th>
              <th>Current</th>
              <th>Unreal. P&L</th>
            </tr>
          </thead>
          <tbody>
            {positions.length === 0 ? (
              <tr><td colSpan={5} className="text-center text-zinc-600">Flat — no open positions</td></tr>
            ) : (
              positions.map((pos, i) => {
                const side = typeof pos.side === 'number' ? (pos.side === 0 ? 'Long' : 'Short') : pos.side
                const pnlPoints = lastPrice
                  ? (side === 'Long' ? lastPrice - pos.price : pos.price - lastPrice)
                  : 0
                const pnl = pnlPoints * NQ_POINT_VALUE * pos.size
                return (
                  <tr key={i}>
                    <td className={side === 'Long' ? 'text-emerald-400' : 'text-red-400'}>{side}</td>
                    <td>{pos.size}</td>
                    <td>{pos.price.toFixed(2)}</td>
                    <td>{lastPrice?.toFixed(2) ?? '—'}</td>
                    <td className={pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                      ${pnl.toFixed(2)}
                    </td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function StatCard({ label, value, color, sub }: { label: string; value: string; color: string; sub?: string }) {
  return (
    <div className="border border-zinc-800 bg-zinc-900 p-3">
      <div className="text-[10px] font-mono text-zinc-500 uppercase tracking-wider">{label}</div>
      <div className="text-lg font-mono font-bold mt-1" style={{ color }}>{value}</div>
      {sub && <div className="text-[10px] font-mono text-zinc-600 mt-0.5">{sub}</div>}
    </div>
  )
}

function computeClosedPnL(fills: Fill[], exits: ExitEvent[]): number {
  let pnl = 0
  // Pair fills with exits in order
  const exitsCopy = [...exits]
  for (const fill of fills) {
    const exit = exitsCopy.shift()
    if (!exit) break
    const side = fill.side
    const pnlPoints = side === 'long' ? exit.price - fill.price : fill.price - exit.price
    pnl += pnlPoints * 20 * fill.size  // NQ $20/point
  }
  return pnl
}
```

- [ ] **Step 2: Update App.tsx to import and render BankrollPage**

Add import:
```typescript
import { BankrollPage } from './pages/BankrollPage'
```

Replace bankroll placeholder:
```typescript
{activeTab === 'bankroll' && (
  <BankrollPage
    positions={ws.positions}
    fills={ws.fills}
    exits={ws.exits}
    lastPrice={ws.lastPrice}
  />
)}
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd firevstocks/frontend && npx tsc --noEmit`

Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add firevstocks/frontend/src/pages/BankrollPage.tsx firevstocks/frontend/src/App.tsx
git commit -m "feat(firevstocks): Bankroll tab — account, drawdown gauge, positions"
```

---

### Task 8: Stats tab — Trade history, summary cards, equity curve

**Files:**
- Create: `firevstocks/frontend/src/pages/StatsPage.tsx`
- Modify: `firevstocks/frontend/src/App.tsx`

- [ ] **Step 1: Create pages/StatsPage.tsx**

```typescript
import { useState, useEffect, useRef } from 'react'
import {
  createChart,
  LineSeries,
  ColorType,
  type IChartApi,
  type Time,
} from 'lightweight-charts'
import { api } from '@/hooks/useApi'
import type { Trade } from '@/types'

export function StatsPage() {
  const [trades, setTrades] = useState<Trade[]>([])
  const [loading, setLoading] = useState(true)
  const [sortKey, setSortKey] = useState<'timestamp' | 'price'>('timestamp')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  useEffect(() => {
    setLoading(true)
    api.getTrades()
      .then(data => {
        const list = data.trades ?? (Array.isArray(data) ? data : [])
        setTrades(list)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  // Compute stats
  const { winRate, profitFactor, totalPnL, tradeCount } = computeStats(trades)

  // Sort trades
  const sorted = [...trades].sort((a, b) => {
    const va = sortKey === 'timestamp' ? a.timestamp : a.price
    const vb = sortKey === 'timestamp' ? b.timestamp : b.price
    const cmp = va < vb ? -1 : va > vb ? 1 : 0
    return sortDir === 'asc' ? cmp : -cmp
  })

  const toggleSort = (key: typeof sortKey) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('desc') }
  }

  return (
    <div className="flex flex-col flex-1 min-h-0 gap-3 overflow-y-auto">
      {/* Summary Cards */}
      <div className="grid grid-cols-4 gap-2">
        <SummaryCard label="Trades" value={String(tradeCount)} color="#3b82f6" />
        <SummaryCard label="Win Rate" value={tradeCount > 0 ? `${winRate.toFixed(1)}%` : '—'} color={winRate >= 50 ? '#4ade80' : '#ef4444'} />
        <SummaryCard label="Profit Factor" value={tradeCount > 0 ? profitFactor.toFixed(2) : '—'} color={profitFactor >= 1 ? '#4ade80' : '#ef4444'} />
        <SummaryCard label="Net P&L" value={`$${totalPnL.toFixed(2)}`} color={totalPnL >= 0 ? '#4ade80' : '#ef4444'} />
      </div>

      {/* Equity Curve */}
      <div className="border border-zinc-800 bg-zinc-950 h-[200px]">
        <EquityCurve trades={trades} />
      </div>

      {/* Trade History Table */}
      <div className="border border-zinc-800 bg-zinc-900 flex-1 min-h-[200px]">
        <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider p-3 pb-1">
          Trade History
        </h3>
        {loading ? (
          <div className="text-xs font-mono text-zinc-600 text-center py-8">Loading trades...</div>
        ) : (
          <div className="overflow-y-auto max-h-[400px]">
            <table className="sq w-full">
              <thead>
                <tr>
                  <th className="cursor-pointer" onClick={() => toggleSort('timestamp')}>
                    Time {sortKey === 'timestamp' && (sortDir === 'asc' ? '↑' : '↓')}
                  </th>
                  <th>Side</th>
                  <th className="cursor-pointer" onClick={() => toggleSort('price')}>
                    Price {sortKey === 'price' && (sortDir === 'asc' ? '↑' : '↓')}
                  </th>
                  <th>Size</th>
                  <th>Contract</th>
                </tr>
              </thead>
              <tbody>
                {sorted.length === 0 ? (
                  <tr><td colSpan={5} className="text-center text-zinc-600">No trades yet</td></tr>
                ) : (
                  sorted.map((trade, i) => (
                    <tr key={trade.id ?? i}>
                      <td className="text-zinc-500">
                        {new Date(trade.timestamp).toLocaleString()}
                      </td>
                      <td className={trade.side === 0 ? 'text-emerald-400' : 'text-red-400'}>
                        {trade.side === 0 ? 'Buy' : 'Sell'}
                      </td>
                      <td>{trade.price.toFixed(2)}</td>
                      <td>{trade.size}</td>
                      <td className="text-zinc-500">{trade.contractId}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

function SummaryCard({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="border border-zinc-800 bg-zinc-900 p-3">
      <div className="text-[10px] font-mono text-zinc-500 uppercase tracking-wider">{label}</div>
      <div className="text-lg font-mono font-bold mt-1" style={{ color }}>{value}</div>
    </div>
  )
}

function EquityCurve({ trades }: { trades: Trade[] }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    if (!containerRef.current || trades.length === 0) return

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#9AA0A6',
        fontSize: 10,
        fontFamily: 'monospace',
      },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.03)' },
        horzLines: { color: 'rgba(255,255,255,0.03)' },
      },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.08)' },
      timeScale: { borderColor: 'rgba(255,255,255,0.08)', timeVisible: true },
      handleScroll: { vertTouchDrag: false },
    })

    const series = chart.addSeries(LineSeries, {
      color: '#3b82f6',
      lineWidth: 2,
      lastValueVisible: true,
      priceLineVisible: false,
    })

    // Build cumulative P&L — pair buys with sells
    const sorted = [...trades].sort((a, b) => a.timestamp.localeCompare(b.timestamp))
    const data: Array<{ time: Time; value: number }> = []
    let cumPnL = 0

    // Simple approach: track cumulative realized P&L from paired trades
    let pendingBuy: Trade | null = null
    for (const trade of sorted) {
      if (trade.side === 0) {
        pendingBuy = trade
      } else if (pendingBuy) {
        const pnl = (trade.price - pendingBuy.price) * 20 * trade.size
        cumPnL += pnl
        const ts = Math.floor(new Date(trade.timestamp).getTime() / 1000) as Time
        data.push({ time: ts, value: cumPnL })
        pendingBuy = null
      }
    }

    if (data.length > 0) {
      series.setData(data)
      chart.timeScale().fitContent()
    }

    chartRef.current = chart

    const observer = new ResizeObserver(entries => {
      for (const entry of entries) {
        chart.applyOptions({ width: entry.contentRect.width, height: entry.contentRect.height })
      }
    })
    observer.observe(containerRef.current)

    return () => {
      observer.disconnect()
      chart.remove()
      chartRef.current = null
    }
  }, [trades])

  if (trades.length === 0) {
    return (
      <div className="w-full h-full flex items-center justify-center text-xs font-mono text-zinc-600">
        No trades for equity curve
      </div>
    )
  }

  return <div ref={containerRef} className="w-full h-full" />
}

function computeStats(trades: Trade[]) {
  if (trades.length === 0) return { winRate: 0, profitFactor: 0, totalPnL: 0, tradeCount: 0 }

  const sorted = [...trades].sort((a, b) => a.timestamp.localeCompare(b.timestamp))
  let wins = 0
  let losses = 0
  let grossProfit = 0
  let grossLoss = 0
  let totalPnL = 0
  let pendingBuy: Trade | null = null

  for (const trade of sorted) {
    if (trade.side === 0) {
      pendingBuy = trade
    } else if (pendingBuy) {
      const pnl = (trade.price - pendingBuy.price) * 20 * trade.size
      totalPnL += pnl
      if (pnl >= 0) { wins++; grossProfit += pnl }
      else { losses++; grossLoss += Math.abs(pnl) }
      pendingBuy = null
    }
  }

  const tradeCount = wins + losses
  const winRate = tradeCount > 0 ? (wins / tradeCount) * 100 : 0
  const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0

  return { winRate, profitFactor, totalPnL, tradeCount }
}
```

- [ ] **Step 2: Update App.tsx to import and render StatsPage**

Add import:
```typescript
import { StatsPage } from './pages/StatsPage'
```

Replace stats placeholder:
```typescript
{activeTab === 'stats' && <StatsPage />}
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd firevstocks/frontend && npx tsc --noEmit`

Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add firevstocks/frontend/src/pages/StatsPage.tsx firevstocks/frontend/src/App.tsx
git commit -m "feat(firevstocks): Stats tab — trade history, summary cards, equity curve"
```

---

### Task 9: Clean up App.tsx — remove placeholders, final wiring

**Files:**
- Modify: `firevstocks/frontend/src/App.tsx`

- [ ] **Step 1: Remove all placeholder functions from App.tsx**

Remove the `ChartPlaceholder`, `DQNPlaceholder`, `BankrollPlaceholder`, `StatsPlaceholder` functions entirely. The final App.tsx should import all 4 page components:

```typescript
import { useState, useEffect } from 'react'
import { useDashboardWS } from './hooks/useDashboardWS'
import { api } from './hooks/useApi'
import { ChartPage } from './pages/ChartPage'
import { DQNPage } from './pages/DQNPage'
import { BankrollPage } from './pages/BankrollPage'
import { StatsPage } from './pages/StatsPage'
import type { ExpandedSession } from './types'

type Tab = 'chart' | 'dqn' | 'bankroll' | 'stats'

const TABS: { name: Tab; label: string; color: string }[] = [
  { name: 'chart',    label: 'Chart',    color: '#f59e0b' },
  { name: 'dqn',      label: 'DQN',      color: '#8b5cf6' },
  { name: 'bankroll', label: 'Bankroll', color: '#ec4899' },
  { name: 'stats',    label: 'Stats',    color: '#3b82f6' },
]

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('chart')
  const { state: ws, lastTick } = useDashboardWS()
  const [session, setSession] = useState<ExpandedSession | null>(null)

  useEffect(() => {
    const fetch = () => { api.getSession().then(setSession).catch(() => {}) }
    fetch()
    const iv = setInterval(fetch, 60_000)
    return () => clearInterval(iv)
  }, [])

  return (
    <div className="flex flex-col h-screen bg-zinc-950">
      <div className="flex items-center gap-1 px-3 py-1 border-b border-zinc-800 bg-zinc-900">
        <span className="text-sm font-bold text-amber-500 mr-4">firevstocks</span>
        {TABS.map(tab => (
          <button
            key={tab.name}
            onClick={() => setActiveTab(tab.name)}
            className={`px-3 py-1.5 text-xs font-mono uppercase tracking-wider ${
              activeTab === tab.name ? 'text-zinc-950 font-bold' : 'text-zinc-500 hover:text-zinc-300'
            }`}
            style={activeTab === tab.name ? { backgroundColor: tab.color } : undefined}
          >
            <span style={{ color: activeTab === tab.name ? undefined : tab.color }}>● </span>
            {tab.label}
          </button>
        ))}
        <div className="flex-1" />
        <span className={`text-xs font-mono ${ws.relayConnected ? 'text-emerald-400' : ws.connected ? 'text-yellow-400' : 'text-red-400'}`}>
          ● {ws.relayConnected ? 'Relay' : ws.connected ? 'WS only' : 'Disconnected'}
        </span>
        {ws.streamRunning && <span className="text-xs font-mono text-emerald-400 ml-1">● Stream</span>}
        {ws.lastPrice && (
          <span className="text-xs font-mono text-zinc-400 ml-2">
            NQ {ws.lastPrice.toFixed(2)}
          </span>
        )}
        {ws.tickCount > 0 && (
          <span className="text-xs font-mono text-zinc-600 ml-2">
            {ws.tickCount.toLocaleString()} ticks
          </span>
        )}
      </div>
      <div className="flex flex-col flex-1 min-h-0 min-w-0 overflow-hidden p-2">
        {activeTab === 'chart' && (
          <ChartPage lastTick={lastTick} session={session} zones={ws.zones} signals={ws.signals} fills={ws.fills} exits={ws.exits} />
        )}
        {activeTab === 'dqn' && (
          <DQNPage signals={ws.signals} zones={ws.zones} lastPrice={ws.lastPrice} />
        )}
        {activeTab === 'bankroll' && (
          <BankrollPage positions={ws.positions} fills={ws.fills} exits={ws.exits} lastPrice={ws.lastPrice} />
        )}
        {activeTab === 'stats' && <StatsPage />}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Verify full build succeeds**

Run:
```bash
cd firevstocks/frontend && npm run build
```

Expected: Build completes, `dist/` directory created with `index.html` and `assets/`.

- [ ] **Step 3: Commit**

```bash
git add firevstocks/frontend/src/App.tsx
git commit -m "feat(firevstocks): final App wiring — all 4 tabs connected"
```

---

### Task 10: Build dist and add .gitignore

**Files:**
- Create: `firevstocks/frontend/.gitignore`

- [ ] **Step 1: Create .gitignore**

```
node_modules/
dist/
```

- [ ] **Step 2: Build production dist**

Run:
```bash
cd firevstocks/frontend && npm run build
```

Expected: `dist/` created with `index.html` and `assets/` folder.

- [ ] **Step 3: Verify dashboard.py serves the built dist**

With the SSH tunnels and firevstocks running (`python run_firevstocks.py`), open http://127.0.0.1:8001 — should show the React UI instead of the old `dashboard.html`.

If firevstocks isn't running, at minimum verify the dist path resolution:

```bash
cd backend && python -c "
from pathlib import Path
dist = Path('src/stocks/dashboard.py').parent.parent.parent.parent / 'firevstocks' / 'frontend' / 'dist'
print(f'Dist path: {dist}')
print(f'Exists: {dist.exists()}')
if dist.exists():
    print(f'Files: {list(dist.iterdir())}')
"
```

Expected: Shows the dist path and confirms `index.html` exists.

- [ ] **Step 4: Commit**

```bash
git add firevstocks/frontend/.gitignore
git commit -m "chore(firevstocks): add .gitignore for frontend build artifacts"
```
