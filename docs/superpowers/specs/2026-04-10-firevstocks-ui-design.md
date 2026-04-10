# firevstocks UI — Local NQ Trading Dashboard

**Date:** 2026-04-10
**Status:** Draft

## Purpose

React frontend for the firevstocks local trading client. Provides visual monitoring of the NQ futures trading bot during paper trading: candlestick chart with full overlays, specialist signal display, account/drawdown tracking, and trade statistics.

Replaces the barebones `dashboard.html` (canvas line chart) with a proper lightweight-charts candlestick chart ported from the main `frontend/` CandleChart, plus 3 additional tabs.

## Decisions

- **All 4 tabs** built in one pass: Chart, DQN, Bankroll, Stats
- **Full overlay port** from existing CandleChart.tsx: session boxes, VWAP/SD bands, POC/VAH/VAL, VP histograms, TPO grids, swing levels, IB levels, PDH/PDL
- **Historical candles from server** via SSH tunnel proxy (not TopstepX history API) — consistent with ML inference data
- **Vite dev server** for development (hot reload), built dist served from dashboard.py for daily use
- **Fork firevsports scaffold** — same Vite + React + Tailwind + tsconfig + index.css

## Architecture

```
Local PC (firevstocks.bat)
┌──────────────────────────────────────────────────────┐
│  dashboard.py (FastAPI, port 8001)                   │
│  ├── GET /              → serves React dist          │
│  ├── GET /api/state     → current ticks/signals/etc  │
│  ├── WS  /ws/dashboard  → live ticks/signals/zones   │
│  │                                                    │
│  │  NEW proxy endpoints:                              │
│  ├── GET /api/candles        → tunnel → server        │
│  ├── GET /api/session        → tunnel → server        │
│  ├── GET /api/session-levels → tunnel → server        │
│  ├── GET /api/vp/{tf}        → tunnel → server        │
│  ├── GET /api/session-tpo    → tunnel → server        │
│  ├── GET /api/trades         → TopstepX REST          │
│  └── GET /api/account        → TopstepX REST          │
│                                                       │
│  firevstocks/frontend/ (Vite dev: port 5174)          │
│  └── React app → 4 tabs                              │
└──────────────────────────────────────────────────────┘
         │                              │
    SSH tunnel                    TopstepX REST
    localhost:18000               api.topstepx.com
         │
    Hetzner Server
    ├── /api/trading/market/candles
    ├── /api/trading/market/session
    ├── /api/trading/market/session-levels
    ├── /api/trading/market/vp/{tf}
    └── /api/trading/market/session-tpo
```

## Data Flow

### Live data (WebSocket)

The existing `/ws/dashboard` already broadcasts these event types — no changes needed:

| Event type | Payload | Used by |
|------------|---------|---------|
| `tick` | `{price, ts, tick_count}` | Chart (live candle update) |
| `signal` | `{action, confidence, cont_p, rev_p, stop_ticks, zone, specialist, features?}` | Chart (markers), DQN (signal panel) |
| `zones` | `{zones: [{price, members, ...}]}` | Chart (zone lines) |
| `quote` | `{bid, ask, bid_size, ask_size}` | Chart (status bar) |
| `account` | `{balance, buying_power, ...}` | Bankroll |
| `positions` | `{positions: [{side, size, price, ...}]}` | Bankroll |
| `status` | `{relay_connected, stream_running}` | All (connection indicator) |

**New broadcast needed**: `fill` and `exit` events for chart markers:

```python
def record_fill(fill: dict) -> None:
    _state["stats"]["trade_count"] += 1
    asyncio.create_task(broadcast({"type": "fill", **fill}))

def record_exit(exit_info: dict) -> None:
    asyncio.create_task(broadcast({"type": "exit", **exit_info}))
```

### Historical data (REST proxies)

Dashboard.py proxies these to the server via SSH tunnel (`localhost:18000`):

| Dashboard endpoint | Server endpoint | Purpose |
|-------------------|-----------------|---------|
| `GET /api/candles?interval=1m&days=3` | `/api/trading/market/candles` | Historical OHLCV |
| `GET /api/session` | `/api/trading/market/session` | Expanded session (VWAP, POC, VAH/VAL, delta) |
| `GET /api/session-levels` | `/api/trading/market/session-levels` | Per-day session H/L, IB, swing, PDH/PDL |
| `GET /api/vp/{tf}` | `/api/trading/market/vp/{tf}` | Volume profile (session/weekly/monthly) |
| `GET /api/session-tpo` | `/api/trading/market/session-tpo` | TPO letter grid per session |

TopstepX proxies (dashboard.py calls TopstepX REST directly using stored token):

| Dashboard endpoint | TopstepX endpoint | Purpose |
|-------------------|-------------------|---------|
| `GET /api/trades` | `POST /api/Trade/search` | Trade history for Stats tab |
| `GET /api/account` | `POST /api/Account/search` | Account details for Bankroll tab |

### Tick → candle aggregation (frontend)

The WS broadcasts ticks (every 10th tick). The frontend aggregates into the current open candle bar:

```
WS tick {price, ts} →
  if ts within current candle's minute → update H/L/C
  if ts starts new minute → push closed candle, start new
  → candlestickSeries.update(currentCandle)
```

This matches the pattern in the existing CandleChart.tsx.

## Tab Designs

### Chart Tab

Full port of `frontend/src/components/Terminal/pages/CandleChart.tsx` (~600 lines of chart logic) with adaptations:

**Components:**
- `ChartPage.tsx` — orchestrator: fetches session/VP/TPO data, renders status bar + CandleChart
- `CandleChart.tsx` — lightweight-charts instance with all overlays

**Chart features (ported):**
- Candlestick series with volume histogram (green/red by direction)
- Interval selector: 1m / 5m / 15m (default 5m)
- Infinite scroll: 3-day initial load, 1-day scroll-back chunks
- localStorage cache for candles (`firevstocks_candles_{interval}`)
- Local timezone axis (toLocalEpoch shift)
- Dedup + sort (prevents "Cannot update oldest data" crash)

**Overlays (ported from canvas drawing):**
- Session boxes (Tokyo/London/NY) with H/L computed from candles, colored backgrounds + borders + labels
- VP histograms on right edge (session/weekly/monthly, stacked, color-coded)
- VWAP developing line series + SD bands (1sd, 2sd as dashed lines)
- POC/VAH/VAL price lines (purple=daily, pink=weekly, yellow=monthly)
- Session H/L extension lines (dashed, from box end to day end)
- PDH/PDL dashed lines (orange)
- NY IB H/L levels (amber, anchored NY open → close)
- Swing levels (daily/weekly/monthly, dashed)
- TPO letter grids inside session boxes (with shape/IB/opening metadata footer)
- TPO POC/VAH/VAL/IBH/IBL extension lines

**New overlays:**
- Zone lines from server WS `zones` event — dashed horizontal lines, color intensity by member count
- Signal markers from WS `signal` event — green up-triangle for CONT, red down-triangle for REV, with label text
- Fill markers from WS `fill` event — solid green/red triangle at fill price
- Exit markers from WS `exit` event — X marker at exit price

**Status bar (top of chart):**
- Last price + change
- Connection status (relay + stream)
- Tick count
- Current session name (Tokyo/London/NY based on CET time)

**Level toggle panel:**
Same as existing CandleChart — checkboxes to show/hide overlay groups (VP session/weekly/monthly, session levels, TPO, IB, swings, PDH/PDL, zones).

### DQN Tab

**Signal Panel (top):**
- Cards showing latest specialist output:
  - Action: `CONT` / `REV` / `HOLD` (color-coded)
  - Confidence: 0-100% bar
  - `cont_p` / `rev_p` probabilities
  - `stop_ticks` (suggested stop distance)
  - Zone that triggered the signal
  - Specialist name (continuation / reversal / stop)

**Feature Heatmap (middle):**
- 276-dimension grid (e.g., 23 rows x 12 cols)
- Each cell colored by activation magnitude: gray (#2a2a2a) = 0, green scale = positive, red scale = negative
- Tooltip shows dimension name + value on hover
- Only rendered when signal includes `features` array in WS payload
- Requires server `/ws/signals` to include feature vector in signal messages — add `features: list[float]` to the signal dict sent from SpecialistEnsemble. If not available yet, heatmap shows "No feature data" placeholder until wired.

**Signal History (bottom half):**
- Table of recent signals (from WS, accumulated in state):
  - Timestamp, Action, Confidence, Zone, Specialist, Price, Outcome (if fill followed)
- Auto-scrolls to latest, max 100 entries

**Zone Touch Log:**
- Separate section below or tabbed within DQN:
  - Time, Price, Zone name/price, Member count, Specialist response
- From WS `zones` + `signal` events correlated by timestamp

### Bankroll Tab

**Account Card:**
- Balance, buying power, account ID from `/api/account` (polled every 30s)
- Practice vs live indicator

**Drawdown Gauge:**
- Horizontal bar: current drawdown / $4,500 max loss
- Color: green (<50%), yellow (50-75%), red (>75%)
- Shows dollar amount consumed + remaining

**Session P&L:**
- Real-time P&L from WS `positions` events
- Entry price vs current tick price × contracts × $5 (NQ tick value)
- Running total of closed trades' P&L (from fills/exits accumulated)

**Position Display:**
- Table of open positions:
  - Side (Long/Short), Contracts, Entry Price, Current Price, Unrealized P&L, Stop Price
- Empty state when flat

**Risk Metrics:**
- Max position: current / allowed (e.g., 1/2 contracts)
- Daily loss: consumed / limit
- Time to flatten: countdown to 15:55 ET

### Stats Tab

**Summary Cards (top row):**
- Win rate: wins / total (from `/api/trades`)
- Profit factor: gross profit / gross loss
- Avg R-multiple: mean R across closed trades
- Total trades count
- Net P&L (dollar amount)

**Trade History Table:**
- Fetched from `/api/trades`, sortable columns:
  - Date/time, Side, Entry, Exit, P&L ($), R-multiple, Duration
- Paginated or virtual scroll for long lists

**Equity Curve:**
- lightweight-charts line series
- X = trade sequence number (or timestamp), Y = cumulative P&L
- Colored green when rising, red when falling (via histogram or crosshair)

## File Structure

```
firevstocks/frontend/
├── package.json
├── vite.config.ts            # proxy /api + /ws → localhost:8001
├── tsconfig.json
├── postcss.config.js
├── tailwind.config.js
├── index.html
├── src/
│   ├── main.tsx              # React root + QueryClient
│   ├── App.tsx               # Tab shell: Chart / DQN / Bankroll / Stats
│   ├── index.css             # Fork of firevsports/frontend/src/index.css
│   ├── vite-env.d.ts
│   ├── types/
│   │   └── index.ts          # CandleData, Signal, Zone, Account, Trade, Position
│   ├── hooks/
│   │   ├── useDashboardWS.ts # WS /ws/dashboard — shared state for all tabs
│   │   └── useApi.ts         # fetch wrappers for REST endpoints
│   ├── pages/
│   │   ├── ChartPage.tsx     # Status bar + overlay toggles + CandleChart
│   │   ├── CandleChart.tsx   # Full port of lightweight-charts with all overlays
│   │   ├── DQNPage.tsx       # Signal panel + heatmap + history
│   │   ├── BankrollPage.tsx  # Account + drawdown + positions
│   │   └── StatsPage.tsx     # Summary + trade table + equity curve
│   └── components/
│       └── StatusBar.tsx     # Connection status + price (shared across tabs)
```

## Backend Changes

### dashboard.py additions

**Proxy routes** — use `httpx.AsyncClient` to forward requests:

```python
import httpx

SERVER_URL = "http://127.0.0.1:18000"  # SSH tunnel to Hetzner

_http = httpx.AsyncClient(timeout=10.0)

@app.get("/api/candles")
async def proxy_candles(interval: str = "5m", days: int = 3):
    r = await _http.get(f"{SERVER_URL}/api/trading/market/candles",
                        params={"interval": interval, "days": days})
    return r.json()

@app.get("/api/session")
async def proxy_session():
    r = await _http.get(f"{SERVER_URL}/api/trading/market/session")
    return r.json()

@app.get("/api/session-levels")
async def proxy_session_levels():
    r = await _http.get(f"{SERVER_URL}/api/trading/market/session-levels")
    return r.json()

@app.get("/api/vp/{tf}")
async def proxy_vp(tf: str):
    r = await _http.get(f"{SERVER_URL}/api/trading/market/vp/{tf}")
    return r.json()

@app.get("/api/session-tpo")
async def proxy_session_tpo():
    r = await _http.get(f"{SERVER_URL}/api/trading/market/session-tpo")
    return r.json()
```

**TopstepX proxies** — use the existing TopstepXClient instance:

```python
@app.get("/api/trades")
async def get_trades():
    client = _state.get("topstepx_client")
    if not client:
        return {"trades": []}
    # POST /api/Trade/search with account ID
    return await client._post("/api/Trade/search", {
        "accountId": client._account_id,
    })

@app.get("/api/account")
async def get_account():
    client = _state.get("topstepx_client")
    if not client:
        return {}
    accounts = await client._post("/api/Account/search", {
        "onlyActiveAccounts": True,
    })
    return accounts[0] if accounts else {}
```

**Serve React dist:**

```python
from fastapi.staticfiles import StaticFiles

dist_path = Path(__file__).parent.parent.parent / "firevstocks" / "frontend" / "dist"
if dist_path.exists():
    app.mount("/assets", StaticFiles(directory=dist_path / "assets"), name="assets")

    @app.get("/")
    async def index():
        return HTMLResponse((dist_path / "index.html").read_text(encoding="utf-8"))
else:
    # Fallback to old dashboard.html during development
    @app.get("/")
    async def index():
        html_path = Path(__file__).parent / "dashboard.html"
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
```

**New broadcast functions** (add to dashboard.py):

```python
def record_fill(fill: dict) -> None:
    _state["stats"]["trade_count"] += 1
    asyncio.create_task(broadcast({"type": "fill", **fill}))

def record_exit(exit_info: dict) -> None:
    asyncio.create_task(broadcast({"type": "exit", **exit_info}))
```

### run_firevstocks.py changes

- Pass TopstepXClient reference to dashboard state: `dashboard._state["topstepx_client"] = client`
- Wire fill/exit callbacks to `dashboard.record_fill()` / `dashboard.record_exit()`

## Shared WS Hook Design

`useDashboardWS.ts` manages a single WebSocket connection shared across all tabs:

```typescript
interface DashboardState {
  connected: boolean;
  relayConnected: boolean;
  streamRunning: boolean;
  lastPrice: number | null;
  tickCount: number;
  signals: Signal[];        // accumulated, max 100
  zones: Zone[];            // latest zone set
  fills: Fill[];            // accumulated fills for chart markers
  exits: Exit[];            // accumulated exits for chart markers
  account: Account | null;
  positions: Position[];
  quote: Quote | null;
}
```

Returns `{ state, lastTick }` where `lastTick` is the most recent tick event (used by CandleChart to update the current candle).

Connection logic: connect on mount, reconnect with 2s backoff on close, ping every 30s.

## Dependencies

```json
{
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "lightweight-charts": "^4.2.0",
    "@tanstack/react-query": "^5.0.0"
  },
  "devDependencies": {
    "@vitejs/plugin-react-swc": "^4.0.0",
    "vite": "^6.0.0",
    "typescript": "^5.5.0",
    "tailwindcss": "^3.4.0",
    "postcss": "^8.4.0",
    "autoprefixer": "^10.4.0",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0"
  }
}
```

Backend addition: `httpx` (already in requirements for TopstepX client).

## What's NOT in Scope

- No trading controls (place/cancel orders from UI) — bot trades automatically
- No settings/config UI — env vars handle configuration
- No authentication — local-only (127.0.0.1)
- No mobile responsive — desktop-only monitoring tool
- No dark/light theme toggle — dark theme only (retro terminal aesthetic)
