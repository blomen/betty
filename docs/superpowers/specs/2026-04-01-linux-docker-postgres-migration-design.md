# Linux + Docker + PostgreSQL Migration Design

**Date:** 2026-04-01
**Status:** Approved
**Scope:** Migrate Firev from Windows-only local desktop app to headless Linux Docker deployment with PostgreSQL, targeting a single Hetzner VPS for 24/7 uptime, performance, and scalability.

---

## Problem

Firev runs exclusively as a Windows desktop application (PyInstaller `.exe` + pywebview). This causes:

1. **Downtime** — extraction cycles missed when PC sleeps, restarts, or Windows updates
2. **Write contention** — SQLite single-writer lock blocks extraction + trading + API concurrent writes
3. **Suboptimal async** — Windows `ProactorEventLoop` is slower than Linux `uvloop` (2-4x for async I/O)
4. **No remote access** — can only use from the desk, not phone/laptop
5. **IP risk** — home ISP IP can get flagged by bookmakers
6. **Process overhead** — Windows `CreateProcess` ~20x slower than Linux `fork` for Playwright browser spawning

## Solution

Single Hetzner CX32 VPS (4 vCPU, 8 GB RAM, 80 GB NVMe, ~7 EUR/month) running Docker Compose with 3 containers: FastAPI backend, PostgreSQL, Nginx reverse proxy.

### Architecture

```
Hetzner CX32 (Ubuntu 24.04)
├── nginx (:443)           — SSL termination, reverse proxy, SSE/WS passthrough
├── backend (:8000)        — FastAPI + uvloop + headless Playwright + Databento stream + DQN inference
└── postgres (:5432)       — Two databases: firev (main) + market (ticks)

Docker Volumes:
├── pg_data/               — PostgreSQL data (persistent)
├── chrome/                — Chrome profile for browser providers
├── logs/                  — Application logs
└── models/                — DQN .pt checkpoints
```

### What runs where

| VPS (24/7, headless) | Local PC (as needed) |
|----------------------|---------------------|
| Extraction pipeline (all 3 tiers) | Bet placement (BankID required) |
| Pinnacle devig + value scanner | Mirror interceptor (browser interception) |
| Databento NQ tick stream | DQN model training (when needed) |
| DQN inference (CPU, ~1-3/min) | |
| Opportunity API + SSE streams | |
| PostgreSQL databases | |
| Frontend (accessible from any device) | |

No login/authentication needed for extraction — all providers expose odds publicly. BankID is only required for bet placement, which stays on the user's PC.

No VNC/Xvfb needed — entire server runs headless.

### Key design decisions

1. **One backend container** — extraction, trading, analysis, and API are tightly coupled (shared scheduler locks, circuit breakers, in-memory caches). Microservices would add complexity for zero benefit at this scale.

2. **Two PostgreSQL databases in one instance** — `firev` (betting/extraction) and `market` (high-frequency ticks) use separate connection pools to prevent tick writes from blocking extraction queries. Zero extra cost.

3. **asyncpg driver** — true async PostgreSQL driver, combined with uvloop for non-blocking DB queries. Current sync SQLite blocks the event loop on every query.

4. **Headless Playwright** — browser providers (Spectate, ComeOn, Coolbet, Tipwin, 10bet) run headless on Linux with ~30% less RAM per instance vs Windows headed mode.

5. **No noVNC/Xvfb** — extraction doesn't require authenticated sessions. Removes entire remote-desktop complexity.

---

## Migration Phases

### Phase 1: Dockerize (keep SQLite)

Get the app running in Docker on Linux first, before touching the database. Isolates infrastructure change from database change.

**Changes:**
- Create `Dockerfile` (Python 3.10 + Node 20 + Playwright + headless Chromium)
- Create `docker-compose.yml` (backend + nginx)
- Refactor `paths.py` — simplify to `/app/data/`, `/app/logs/`, `/app/config/`
- Delete Windows-only files: `launcher.py`, `build.ps1`, `first_run.py`, `firev.spec`
- Simplify `run_dev.py` — keep as thin uvicorn launcher for local dev, remove Windows process hacks
- Remove all `sys.platform == "win32"` branches (encoding, event loop policy)
- Remove `is_bundled()` / `get_bundle_dir()` / `%LOCALAPPDATA%` logic — single code path for both Docker and local dev
- Add `uvloop` to dependencies
- Mount Chrome profile as Docker volume
- Test all 16 providers extract successfully in container

**Result:** App runs on Linux in Docker with SQLite. Same behavior, new platform.

### Phase 2: PostgreSQL migration

Swap SQLite for PostgreSQL while Docker environment is stable.

**Changes:**
- Add PostgreSQL 16 container to `docker-compose.yml`
- Replace `sqlite:///` connection strings with `postgresql+asyncpg://`
- Remove all SQLite PRAGMAs (WAL, busy_timeout, synchronous)
- Remove WAL checkpoint code from `api/__init__.py`
- Remove `render_as_batch=True` from Alembic env.py
- Remove `NullPool` — use asyncpg's connection pooling
- Convert sync `sessionmaker` → async `async_sessionmaker`
- Convert `Depends(get_db)` → async session dependency across all routes
- Rewrite ~10 raw SQL queries for PostgreSQL compatibility
- Create fresh Alembic migration targeting PostgreSQL schema
- Write one-time data migration script (SQLite → PostgreSQL via pandas/psycopg2)
- Separate connection pools for `firev` and `market` databases

**Raw SQL queries requiring conversion:**
- `orchestrator.py`: UPDATE Event SET match_status (standard SQL, likely compatible)
- `orchestrator.py`: SELECT Event JOIN Bet for pending bet detection (standard SQL)
- `ml/analytics/engine.py`: SELECT from Odds/Bet tables (standard SQL)
- `ml/optimizer/coverage.py`: GROUP BY aggregations (standard SQL)
- `db/models.py`: ALTER TABLE migrations (replace with proper Alembic migrations)

**Result:** Full async database. Extraction + trading + API write concurrently without blocking.

### Phase 3: Server deployment

Provision VPS and go live.

**Steps:**
1. Create Hetzner CX32 (Ubuntu 24.04, Falkenstein datacenter)
2. Install Docker + Docker Compose
3. Configure Nginx with Let's Encrypt SSL (certbot)
4. Configure firewall (ufw): only ports 443, 22
5. Copy `.env` with API keys (DATABENTO_API_KEY, ANTHROPIC_API_KEY, DB_PASSWORD)
6. `docker compose up -d`
7. Verify: extraction runs, Databento stream connects, frontend loads, SSE/WS work
8. Run both local and server in parallel for validation period (few days)

**Result:** Production server running 24/7.

### Phase 4: Hardening

**Steps:**
- Docker restart policies (`restart: unless-stopped`)
- Log rotation (Docker logging driver: `max-size: 50m`, `max-file: 5`)
- PostgreSQL daily backups (`pg_dump` cron job to volume)
- Hetzner weekly snapshots (~1 EUR/month)
- Health check endpoint wired to Docker healthcheck directive
- UptimeRobot free tier (pings domain, alerts on downtime)

**Result:** Self-healing, backed-up, monitored system.

---

## Server Specification

### Hetzner CX32

| Resource | Spec |
|----------|------|
| CPU | 4 vCPU (AMD EPYC, shared) |
| RAM | 8 GB |
| Storage | 80 GB NVMe SSD |
| Network | 20 TB traffic/month |
| OS | Ubuntu 24.04 LTS |
| Location | Falkenstein, Germany |
| Price | 7.11 EUR/month |

### RAM Budget

| Component | Estimated |
|-----------|-----------|
| PostgreSQL | ~500 MB |
| FastAPI + uvicorn | ~400 MB |
| Playwright (peak ~3 concurrent) | ~1.5 GB |
| Databento stream + RL inference | ~300 MB |
| Nginx + OS | ~300 MB |
| **Used** | **~3.0 GB** |
| **Free** | **~5.0 GB** |

Browser instances are tier-scheduled (sharp → api_soft → browser_soft), so peak concurrent browsers is ~3, not the max 6.

### Monthly Cost

| Item | Cost |
|------|------|
| Hetzner CX32 | 7.11 EUR |
| Domain (optional, for SSL) | ~1 EUR/month |
| Hetzner snapshots (weekly) | ~1 EUR/month |
| **Total** | **~9 EUR/month** |

---

## Docker Configuration

### docker-compose.yml

```yaml
services:
  backend:
    build: ./backend
    ports:
      - "8000:8000"
    volumes:
      - chrome:/home/firev/chrome-profile
      - logs:/app/logs
      - models:/app/models
    environment:
      - DATABASE_URL=postgresql+asyncpg://firev:${DB_PASSWORD}@postgres:5432/firev
      - MARKET_DATABASE_URL=postgresql+asyncpg://firev:${DB_PASSWORD}@postgres:5432/market
      - DATABENTO_API_KEY=${DATABENTO_API_KEY}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
    depends_on:
      postgres:
        condition: service_healthy
    restart: unless-stopped

  postgres:
    image: postgres:16-alpine
    volumes:
      - pg_data:/var/lib/postgresql/data
    environment:
      - POSTGRES_USER=firev
      - POSTGRES_PASSWORD=${DB_PASSWORD}
    healthcheck:
      test: pg_isready -U firev
      interval: 5s
      retries: 5
    restart: unless-stopped

  nginx:
    image: nginx:alpine
    ports:
      - "443:443"
      - "80:80"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf
      - certs:/etc/letsencrypt
    depends_on:
      - backend
    restart: unless-stopped

volumes:
  pg_data:
  chrome:
  logs:
  models:
  certs:
```

### Dockerfile (backend)

```dockerfile
FROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2 \
    curl && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs

WORKDIR /app

COPY backend/pyproject.toml .
RUN pip install -e ".[scrape]" && pip install uvloop asyncpg
RUN playwright install chromium && playwright install-deps

COPY frontend/ /app/frontend/
RUN cd /app/frontend && npm ci && npm run build

COPY backend/ /app/

EXPOSE 8000

CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000", "--loop", "uvloop"]
```

### Nginx config (key sections)

```nginx
server {
    listen 443 ssl;
    server_name firev.example.com;

    ssl_certificate     /etc/letsencrypt/live/firev.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/firev.example.com/privkey.pem;

    # Static frontend + API
    location / {
        proxy_pass http://backend:8000;
    }

    # WebSocket upgrade
    location /ws {
        proxy_pass http://backend:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    # SSE streams — disable buffering
    location /api/extraction/stream {
        proxy_pass http://backend:8000;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
    }
    location /api/trading/market/stream {
        proxy_pass http://backend:8000;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 86400s;
    }

    # Standard API with generous timeout for extraction
    location /api/ {
        proxy_pass http://backend:8000;
        proxy_read_timeout 120s;
    }

    gzip on;
    gzip_types text/plain application/json application/javascript text/css;
}
```

---

## Performance Gains

| Area | Windows (current) | Linux Docker (after) |
|------|-------------------|---------------------|
| Async event loop | ProactorEventLoop | uvloop (2-4x faster) |
| DB writes | SQLite single-writer | PostgreSQL concurrent |
| Browser spawn | CreateProcess ~200ms | fork ~10ms |
| Browser RAM | ~700 MB headed | ~500 MB headless |
| Network to bookmakers | Home ISP | Hetzner datacenter |
| Market data stream | Gaps on sleep/restart | 24/7 continuous |
| Uptime | Manual, PC-dependent | 24/7, auto-restart |

---

## Files Changed

### Deleted files
- `backend/launcher.py` (366 lines) — pywebview, Win32 API, desktop GUI
- `backend/build.ps1` (143 lines) — PyInstaller bundling
- `backend/first_run.py` (~80 lines) — first-run wizard
- `backend/firev.spec` — PyInstaller spec

### Simplified files
- `backend/run_dev.py` — stripped to thin uvicorn launcher (remove Windows process hacks, keep for local dev)

### New files
- `Dockerfile` — backend container build
- `docker-compose.yml` — 3-container orchestration
- `nginx/nginx.conf` — reverse proxy + SSL + SSE/WS
- `.env.docker.example` — secrets template
- `entrypoint.sh` — uvicorn startup

### Modified files (Phase 1 — Dockerize)
- `backend/src/paths.py` — simplify to `/app/data/`, remove Windows logic
- `backend/src/app.py` — remove Windows encoding fixes
- `backend/pyproject.toml` — add uvloop, asyncpg; remove pywebview, pyinstaller

### Modified files (Phase 2 — PostgreSQL)
- `backend/src/db/models.py` — PostgreSQL engine, async sessions, remove PRAGMAs
- `backend/src/api/__init__.py` — remove WAL checkpoint, async DB init
- `backend/src/api/deps.py` — async session dependency
- `backend/alembic.ini` — DATABASE_URL from environment
- `backend/alembic/env.py` — remove render_as_batch, PostgreSQL target
- `backend/src/pipeline/orchestrator.py` — verify raw SQL compatibility
- `backend/src/ml/analytics/engine.py` — verify raw SQL compatibility
- `backend/src/ml/optimizer/coverage.py` — verify raw SQL compatibility
- `backend/scripts/*.py` — use DATABASE_URL instead of hardcoded paths

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Postgres migration breaks queries | Medium | High | Phase 1 validates Docker+SQLite first |
| Async session conversion misses routes | Medium | Medium | Grep all `Depends(get_db)`, convert systematically |
| Playwright headless differs on Linux | Low | Medium | Test each browser provider individually |
| Datacenter IP blocked by Cloudflare | Medium | Low | Only ComeOn affected; Camoufox handles it; proxy fallback |
| Databento stream drops | Low | Low | Existing reconnection logic handles this |
| Data loss during migration | Low | High | Migration script verifies row counts; keep SQLite backup |
| 8 GB RAM insufficient | Low | Medium | Tier scheduling limits concurrent browsers; monitor via docker stats |

### Rollback plan
- Phase 1: Windows app unchanged, Docker is additive
- Phase 2: Revert to SQLite connection string (one line)
- Phase 3: Keep running locally, server is a copy
- Phase 4: Nothing destructive, just monitoring/backups

---

## Notes

- Frontend requires zero changes — same `/api` prefix, same SSE/WS paths
- All 16 provider extractors unchanged — extraction is provider logic, not infrastructure
- All analysis/matching/bankroll/risk code unchanged — pure business logic
- Mirror interceptor stays on local PC (requires BankID for bet placement)
- DQN training stays on local PC (optional GPU); server runs inference only (CPU, 170K params, <1ms)
- Domain name optional — can use raw IP with self-signed cert initially
- Hetzner CX32 upgradeable to CX42 (8 vCPU, 16 GB, ~13 EUR/month) without downtime
