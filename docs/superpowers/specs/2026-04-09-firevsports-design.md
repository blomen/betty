# FirevSports Design

**Date:** 2026-04-09
**Status:** Approved

## Problem

The current "mirror" system runs a full clone of the server backend locally (`mirror.bat`), duplicating 3000+ lines of FastAPI code just to get Playwright browser control. It connects directly to the production database via SSH tunnel, mixes server and local concerns, and shares the same UI as the server with a hacky tab-hiding mechanism.

## Solution

Replace "mirror" with **FirevSports** — a dedicated local client application that:
- Runs a thin local FastAPI server (~200 lines): proxy + mirror browser control
- Connects to the server's **API** (not the DB) via SSH tunnel
- Has its own frontend with 5 focused tabs: Play, Pending, Dutch, Bankroll, Stats
- Keeps all bet placement (Playwright browser) local, all compute (extraction, analysis) on server

## Architecture

```
Hetzner Server (24/7, headless)              Your PC (firevsports.bat)
├── Extraction pipeline (sharp + soft)       ├── SSH tunnel to server API (port 8000)
├── Opportunity scanner                      ├── Playwright mirror browser
├── Analysis (devig, EV, Kelly)              │   ├── Navigate to events
├── API endpoints                            │   ├── Autofill stakes
│   ├── /api/play/batch                      │   ├── Place bets
│   ├── /api/bankroll/*                      │   └── Intercept prices/history/balance
│   ├── /api/stats/*                         ├── Thin local FastAPI (server.py)
│   ├── /api/dutch/*                         │   ├── Serves frontend
│   ├── /api/pending/*                       │   ├── Mirror browser endpoints
│   └── /api/extraction/stream (SSE)         │   └── Proxies /api/* to server tunnel
└── PostgreSQL                               ├── Local UI: Play, Pending, Dutch,
                                             │   Bankroll, Stats
                                             └── firevsports.bat launcher
```

### Data Flow

1. `firevsports.bat` starts → SSH tunnel to `server:8000` on local port `18000` → launches thin local backend on `127.0.0.1:8000` → opens browser
2. Frontend loads, each tab fetches from local backend
3. Local backend proxies `/api/*` requests to `localhost:18000` (tunnel → server)
4. Mirror browser endpoints (`/mirror/*`) are handled locally (Playwright)
5. Bet placement: mirror browser places on provider site → interceptor captures confirmation → local backend reports to server API for DB storage

### Boundaries

**Local-only:** Playwright browser, HTTP/WS interception, DOM automation (navigate, autofill, place), bet confirmation detection, provider login detection

**Server-only:** Extraction, analysis, opportunity scanning, DB queries, Kelly sizing, devig, EV calculation, all data storage

**Shared via API:** Batch data, bankroll stats, bet history, provider balances, settlement data, SSE event streams

## Local Tabs

| Tab | Purpose | Server Endpoint | Local Action |
|-----|---------|-----------------|--------------|
| **Play** | Bet queue by edge, auto-navigate, autofill, Place/Skip | `/api/play/batch` (poll/SSE) | Mirror: navigate, autofill, place |
| **Pending** | Open bets, settlements, balance per provider | `/api/pending/*`, `/api/mirror/state/*` | Mirror: verify settlements |
| **Dutch** | Dutch betting opportunities | `/api/dutch/*` | Read-only |
| **Bankroll** | Balance per provider, Kelly sizing, deposit/withdraw | `/api/bankroll/*` | Read-only |
| **Stats** | Win rate, ROI, CLV, provider P&L, trade history | `/api/stats/*`, `/api/bets/*` | Read-only |

Play and Pending are action tabs (need mirror browser). Dutch, Bankroll, Stats are read-only (same data as server, available locally for convenience while betting).

## Thin Local Backend

The local FastAPI server has 3 responsibilities:

### 1. Mirror Browser Control

Local-only endpoints for Playwright automation:

- `POST /mirror/start` — launch headed browser
- `POST /mirror/navigate` — navigate to event URL, autofill stake
- `POST /mirror/place` — confirm bet placement
- `POST /mirror/check-price` — read live price from DOM/intercepted data
- `GET /mirror/status` — browser state, detected providers
- `GET /mirror/stream` — SSE: provider_opened, balance_synced, bet_mirrored, etc.

### 2. API Proxy

All `/api/*` requests forwarded to server via SSH tunnel:

```python
TUNNEL_BASE = "http://localhost:18000"

@app.api_route("/api/{path:path}", methods=["GET","POST","PUT","DELETE"])
async def proxy(path: str, request: Request):
    async with httpx.AsyncClient() as client:
        resp = await client.request(
            method=request.method,
            url=f"{TUNNEL_BASE}/api/{path}",
            content=await request.body(),
            headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
            params=request.query_params,
        )
        return Response(content=resp.content, status_code=resp.status_code,
                       headers=dict(resp.headers))
```

SSE streams (`/api/extraction/stream`, `/api/mirror/stream/*`) are proxied with streaming support.

### 3. Static Frontend

```python
app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="static")
```

## firevsports.bat Launcher

Same pattern as the current `mirror.bat`:

```
1. Kill any previous firevsports instance (port 8000 + port 18000)
2. Open SSH tunnel: ssh -N -L 18000:localhost:8000 root@148.251.40.251
3. Wait for tunnel ready (poll localhost:18000/health)
4. Start thin local backend: python server.py (port 8000)
5. Wait for backend ready (poll localhost:8000/health)
6. Open browser to http://127.0.0.1:8000
```

No DB connection, no DB password, no psycopg2 — just an API tunnel.

## File Structure

```
firevsports/                        # New top-level directory
├── firevsports.bat                 # Windows launcher
├── server.py                       # Thin FastAPI: proxy + mirror + static
├── mirror/
│   ├── __init__.py
│   ├── browser.py                  # Playwright browser management
│   ├── interceptor.py              # HTTP/WS interception
│   ├── router.py                   # FastAPI router for /mirror/* endpoints
│   └── workflows/                  # Provider-specific DOM automation
│       ├── __init__.py             # Workflow registry
│       ├── base.py                 # ProviderWorkflow ABC
│       ├── generic.py              # GenericWorkflow (data-driven)
│       ├── altenar.py
│       ├── kambi.py
│       ├── gecko.py
│       ├── pinnacle.py
│       ├── polymarket.py
│       └── strategies/             # Per-provider edge case overrides
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── src/
│   │   ├── App.tsx                 # TabBar + 5 pages
│   │   ├── pages/
│   │   │   ├── PlayPage.tsx        # Bet queue + mirror control
│   │   │   ├── PendingPage.tsx     # Open bets + settlements
│   │   │   ├── DutchPage.tsx       # Dutch opportunities
│   │   │   ├── BankrollPage.tsx    # Balances + Kelly
│   │   │   └── StatsPage.tsx       # Performance metrics
│   │   ├── hooks/
│   │   │   ├── useServerStream.ts  # SSE connection to server via proxy
│   │   │   ├── useMirrorStream.ts  # SSE connection to local mirror
│   │   │   └── useApi.ts           # Fetch wrapper (all goes through proxy)
│   │   └── components/             # Shared UI components
│   └── dist/                       # Built static files
└── requirements.txt                # Minimal: fastapi, uvicorn, httpx, playwright
```

## Migration Path

### What gets extracted from `backend/src/mirror/`
- `interceptor.py` → `firevsports/mirror/interceptor.py` (extracted, simplified)
- `workflows/` → `firevsports/mirror/workflows/` (copied, no changes needed)
- `service.py` → split: browser management → `browser.py`, REST endpoints → `router.py`
- `recorder.py` → `firevsports/mirror/recorder.py` (JSONL recording, unchanged)

### What stays on server
- `backend/src/mirror/event_router.py` — classifies + broadcasts intercepted data
- `backend/src/mirror/channels.py` — SSE channels
- `backend/src/api/routes/mirror_stream.py` — SSE endpoints + bootstrap
- `backend/src/api/routes/fire_window.py` — batch/bet management (called via proxy)

### What gets deleted
- `mirror.bat` → replaced by `firevsports/firevsports.bat`
- `backend/run_mirror.py` → replaced by `firevsports/server.py`
- Server-side Play tab hiding logic in `TabBar.tsx` / `TerminalWindow.tsx`
- `frontend/src/components/Terminal/pages/play/` — replaced by `firevsports/frontend/`
- `frontend/src/hooks/useSyncStream.ts`, `usePriceStream.ts`, `useBettingLane.ts`, `useProviderQueue.ts` — moved to firevsports frontend

### Server UI after migration
Tabs: Poly, Soft, Pinnacle, Dutch, Bankroll, Stats (no Play, no mirror)

## Server API Requirements

The server already has most endpoints needed. Gaps to fill:

- `GET /api/pending/bets` — list open bets with provider, event, odds, stake (exists as part of bankroll)
- `GET /api/pending/settlements` — pending settlements per provider (new, from settlement_queue table)
- `POST /api/bets/record` — record a placed bet (called by local mirror after placement)
- `POST /api/bets/settle` — confirm settlement (called by local mirror after verification)

SSE streams already exist: `/api/extraction/stream`, `/api/mirror/stream/sync`, `/api/mirror/stream/prices`, `/api/mirror/stream/actions`.
