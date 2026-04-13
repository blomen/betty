# TopstepX REST Candle Poller — Design Spec

**Date**: 2026-04-13
**Goal**: Continuous 24/5 NQ candle data in `market_candles` via TopstepX REST API polling from the server.

## Problem

The chart has gaps because candle data only records when firevstocks runs locally. We need the server (Hetzner, 24/7 Docker) to collect candles independently so the chart always has complete data.

## Solution

A lightweight poller inside the existing backend container that calls the TopstepX REST API (`POST /api/History/retrieveBars`) every 60 seconds to fetch the latest 1m candle and upsert it into `market_candles`. No WebSocket, no trading, no persistent session — just stateless HTTP requests.

## Why REST Polling (Not WebSocket)

- TopstepX enforces one WebSocket session per username. The local firevstocks needs that session for trading.
- REST `retrieveBars` is stateless (JWT bearer token, no persistent connection). It does not create an "active session" that would conflict with local trading.
- The endpoint returns near-real-time data (latest bar matches current minute).
- Rate limit: 50 requests / 30 seconds. We use 1 request / 60 seconds (~3% of capacity).

## Architecture

```
Server (Hetzner Docker, 24/7)
├── Existing backend container
│   ├── Existing services (extraction, analysis, API, RL)
│   └── NEW: TopstepXPoller (async background task)
│       ├── Auth: POST /api/Auth/loginKey (paper account)
│       ├── Poll: POST /api/History/retrieveBars every 60s
│       ├── Write: UPSERT into market_candles (1m interval)
│       └── Backfill: on startup, fill gaps from last known candle
└── PostgreSQL (market DB, market_candles table)

Local (firevstocks on your PC)
├── SSH tunnel → server PostgreSQL (port 15432, already exists)
├── Chart reads market_candles via tunnel (already works)
└── TopstepX WebSocket (prop account) for trading (unchanged)
```

## Components

### 1. TopstepXPoller

**Location**: `backend/src/market_data/topstepx_poller.py`

Single class with three responsibilities:

**Auth** — Token management
- Call `POST /api/Auth/loginKey` with paper account credentials
- Cache token in memory (valid 24h)
- Refresh via `POST /api/Auth/validate` when token age > 23h
- Credentials from env vars: `TOPSTEPX_PAPER_USERNAME`, `TOPSTEPX_PAPER_API_KEY`

**Poll** — Periodic candle fetch
- Every 60 seconds, call `POST /api/History/retrieveBars`:
  - `contractId`: from env `TOPSTEPX_CONTRACT` (default `CON.F.US.ENQ.M26`)
  - `live`: false
  - `startTime`: last known candle timestamp (or now - 5min on first poll)
  - `endTime`: now
  - `unit`: 2 (Minute), `unitNumber`: 1
  - `limit`: 10, `includePartialBar`: false
- Parse response bars (t, o, h, l, c, v)
- Upsert each closed bar into `market_candles` table (ON CONFLICT DO UPDATE)
- Skip if market is closed (Sat 17:00 ET → Sun 18:00 ET, daily halt 17:00-18:00 ET)

**Backfill** — Gap detection and fill on startup
- On startup, query `MAX(ts)` from `market_candles` where symbol='NQ' and interval='1m'
- If gap > 2 minutes, fetch missing range via `retrieveBars` (paginate in 20,000-bar chunks)
- Rate-limit backfill requests to stay under 50 req/30s (add 1s delay between chunk fetches)

### 2. Database

Uses existing `market_candles` table — no schema changes:

```sql
-- Already exists in market DB
market_candles (
  id SERIAL PRIMARY KEY,
  symbol VARCHAR,        -- 'NQ'
  interval VARCHAR,      -- '1m'
  ts TIMESTAMP WITH TIME ZONE,
  o FLOAT, h FLOAT, l FLOAT, c FLOAT,
  v INTEGER,
  UNIQUE (symbol, interval, ts)
)
```

Upsert query:
```sql
INSERT INTO market_candles (symbol, interval, ts, o, h, l, c, v)
VALUES (:symbol, :interval, :ts, :o, :h, :l, :c, :v)
ON CONFLICT (symbol, interval, ts) DO UPDATE
SET o=EXCLUDED.o, h=EXCLUDED.h, l=EXCLUDED.l, c=EXCLUDED.c, v=EXCLUDED.v
```

### 3. Integration with Backend

- Start poller as an async background task in the FastAPI app startup (same pattern as extraction scheduler)
- Gated by env var: `TOPSTEPX_POLLER_ENABLED=true` (default false — doesn't run unless configured)
- Logs to standard backend logger, no new log files
- Health check: expose last poll timestamp and status via `/health/extraction` or a new `/health/market-data` endpoint

### 4. Environment Variables

Add to `.env.docker` on server:
```
TOPSTEPX_POLLER_ENABLED=true
TOPSTEPX_PAPER_USERNAME=<paper account username>
TOPSTEPX_PAPER_API_KEY=<paper account API key>
TOPSTEPX_CONTRACT=CON.F.US.ENQ.M26
```

## Market Hours Awareness

Globex schedule (all times ET):
- **Open**: Sunday 18:00 → Friday 17:00
- **Daily halt**: 17:00 → 18:00 (every day)
- **Weekend close**: Friday 17:00 → Sunday 18:00

The poller skips polling during known closed periods to avoid empty responses and unnecessary API calls. It resumes polling 1 minute before expected open to catch the first bar.

## Error Handling

- **Auth failure**: Log error, retry in 5 minutes. Don't crash the backend.
- **Rate limit (HTTP 429)**: Back off for 60 seconds, then resume.
- **Network error**: Log, retry next cycle (60s).
- **Bad data**: Validate bar timestamps are within expected range. Skip bars with zero volume.
- **Token expiry**: Auto-refresh via validate endpoint. If validate fails, full re-auth.

## What Changes

| Component | Change |
|-----------|--------|
| `backend/src/market_data/topstepx_poller.py` | NEW — poller class |
| `backend/src/api/__init__.py` | Add poller startup task |
| `.env.docker` | Add 4 env vars |
| Dockerfile | No change (uses existing `requests`/`httpx`) |

## What Does NOT Change

- firevstocks (local) — no changes
- Docker container setup — no new containers
- Database schema — uses existing table
- Existing Databento code — left as-is (dead code, separate cleanup)
- Chart frontend — already reads from `market_candles`

## Verification

1. Deploy with env vars set
2. Check logs for "TopstepXPoller started" message
3. Wait 2 minutes, query: `SELECT COUNT(*) FROM market_candles WHERE symbol='NQ' AND interval='1m' AND ts > NOW() - INTERVAL '5 minutes'`
4. Should see 2-3 new rows
5. Check chart in firevstocks — gaps should stop appearing for current session
