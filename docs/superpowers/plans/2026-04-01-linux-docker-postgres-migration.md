# Linux + Docker + PostgreSQL Migration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate Firev from Windows-only desktop app to headless Linux Docker deployment with PostgreSQL on a single Hetzner VPS.

**Architecture:** 3 Docker containers (FastAPI backend + PostgreSQL 16 + Nginx reverse proxy) on Hetzner CX32. Frontend served as static files from backend. Extraction, analysis, trading inference all run in one backend container. PostgreSQL replaces both SQLite databases (firev + market).

**Tech Stack:** Docker Compose, PostgreSQL 16, asyncpg, uvloop, Nginx, Let's Encrypt, Playwright headless on Linux.

**Spec:** `docs/superpowers/specs/2026-04-01-linux-docker-postgres-migration-design.md`

---

## File Structure

### New files
- `Dockerfile` — multi-stage backend build (Python 3.10 + Node 20 + Playwright)
- `docker-compose.yml` — 3 containers: backend, postgres, nginx
- `docker-compose.dev.yml` — override for local dev (no nginx, port mapping)
- `nginx/nginx.conf` — reverse proxy, SSL, SSE/WS passthrough
- `.env.docker.example` — secrets template
- `backend/entrypoint.sh` — uvicorn startup with uvloop
- `backend/scripts/migrate_sqlite_to_postgres.py` — one-time data migration
- `backend/alembic/versions/100_postgres_baseline.py` — fresh Postgres schema migration

### Modified files
- `backend/src/paths.py` — simplify to environment-based paths, remove Windows logic
- `backend/src/db/models.py` — PostgreSQL engine, async sessions, remove PRAGMAs
- `backend/src/api/__init__.py` — remove WAL checkpoint, async DB init
- `backend/src/api/deps.py` — async session dependency
- `backend/alembic.ini` — DATABASE_URL from environment
- `backend/alembic/env.py` — remove render_as_batch, Postgres target
- `backend/src/app.py` — remove Windows encoding
- `backend/src/logging_config.py` — remove Windows encoding
- `backend/run_dev.py` — simplify to thin uvicorn launcher
- `backend/pyproject.toml` — add asyncpg, uvloop; remove pywebview, pyinstaller deps
- `backend/tests/conftest.py` — Postgres test fixtures
- `.github/workflows/ci.yml` — add Postgres service container for tests

### Deleted files
- `backend/launcher.py` — pywebview desktop GUI
- `backend/build.ps1` — PyInstaller bundling
- `backend/src/first_run.py` — first-run AppData wizard
- `backend/firev.spec` — PyInstaller spec (if exists)

---

## Phase 1: Dockerize (Keep SQLite)

### Task 1: Simplify paths.py — remove Windows/bundled logic

**Files:**
- Modify: `backend/src/paths.py` (all lines — full rewrite)

- [ ] **Step 1: Read the current file to confirm exact contents**

```bash
cat -n backend/src/paths.py
```

- [ ] **Step 2: Rewrite paths.py to use environment variables with sensible defaults**

Replace the entire file with:

```python
"""
Centralized path resolution for Firev.

Uses environment variables with defaults:
  FIREV_DATA_DIR  → /app/data   (Docker) or backend/data (dev)
  FIREV_LOGS_DIR  → /app/logs   (Docker) or backend/logs (dev)
  FIREV_CONFIG_DIR → src/config  (always relative to source)
"""

import os
from pathlib import Path

# Base: the backend/ directory (parent of src/)
_BACKEND_DIR = Path(__file__).parent.parent


def get_data_dir() -> Path:
    """Persistent data directory (DB files, exports)."""
    d = Path(os.environ.get("FIREV_DATA_DIR", str(_BACKEND_DIR / "data")))
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_db_path() -> Path:
    """SQLite database path (used until Postgres migration)."""
    return get_data_dir() / "firev.db"


def get_market_db_path() -> Path:
    """Separate SQLite database for market tick/candle data."""
    return get_data_dir() / "market.db"


def get_logs_dir() -> Path:
    """Logs directory."""
    d = Path(os.environ.get("FIREV_LOGS_DIR", str(_BACKEND_DIR / "logs")))
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_config_path(filename: str) -> Path:
    """Config file path (providers.yaml, sports.yaml)."""
    config_dir = Path(os.environ.get(
        "FIREV_CONFIG_DIR",
        str(Path(__file__).parent / "config"),
    ))
    return config_dir / filename


def get_config_dir() -> Path:
    """Config directory."""
    return Path(os.environ.get(
        "FIREV_CONFIG_DIR",
        str(Path(__file__).parent / "config"),
    ))


def get_aliases_path() -> Path:
    """Team name aliases YAML."""
    return Path(__file__).parent / "matching" / "aliases.yaml"


def get_frontend_dir() -> Path:
    """Frontend dist directory."""
    return Path(os.environ.get(
        "FIREV_FRONTEND_DIR",
        str(_BACKEND_DIR.parent / "frontend" / "dist"),
    ))


def get_env_path() -> Path:
    """.env file path."""
    return _BACKEND_DIR / ".env"
```

- [ ] **Step 3: Verify no imports broke**

```bash
cd backend && python -c "from src.paths import get_db_path, get_market_db_path, get_logs_dir, get_config_path, get_config_dir, get_aliases_path, get_frontend_dir, get_env_path; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add backend/src/paths.py
git commit -m "refactor(paths): replace Windows/bundled logic with env-var-based paths"
```

---

### Task 2: Remove Windows-specific code from app.py, logging_config.py, run_dev.py

**Files:**
- Modify: `backend/src/app.py` (lines 26-28)
- Modify: `backend/src/logging_config.py` (lines 39-43)
- Modify: `backend/run_dev.py` (full rewrite)

- [ ] **Step 1: Remove Windows encoding from app.py**

In `backend/src/app.py`, delete lines 26-28:
```python
# DELETE these lines:
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
```

- [ ] **Step 2: Remove Windows encoding from logging_config.py**

In `backend/src/logging_config.py`, delete the Windows block (lines ~39-43):
```python
# DELETE these lines:
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
```

- [ ] **Step 3: Simplify run_dev.py to thin uvicorn launcher**

Replace entire file with:

```python
"""
Dev server launcher.

Usage:
    python run_dev.py
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "src.api:app",
        host="127.0.0.1",
        port=8000,
        timeout_keep_alive=120,
        reload=True,
    )
```

- [ ] **Step 4: Verify backend starts**

```bash
cd backend && python -c "from src.api import app; print('FastAPI app loads OK')"
```

- [ ] **Step 5: Commit**

```bash
git add backend/src/app.py backend/src/logging_config.py backend/run_dev.py
git commit -m "refactor: remove Windows-specific code from app.py, logging, run_dev"
```

---

### Task 3: Delete Windows-only files

**Files:**
- Delete: `backend/launcher.py`
- Delete: `backend/build.ps1`
- Delete: `backend/src/first_run.py`
- Delete: `backend/firev.spec` (if exists)

- [ ] **Step 1: Check for references to deleted files**

```bash
cd backend && grep -r "launcher" src/ --include="*.py" -l
cd backend && grep -r "first_run" src/ --include="*.py" -l
cd backend && grep -r "build.ps1" . -l
```

- [ ] **Step 2: Remove any imports of first_run**

If `first_run` is imported anywhere (likely in `api/__init__.py` or `launcher.py`), remove those imports and calls.

- [ ] **Step 3: Delete the files**

```bash
rm backend/launcher.py backend/build.ps1 backend/src/first_run.py
rm -f backend/firev.spec
```

- [ ] **Step 4: Verify no import errors**

```bash
cd backend && python -c "from src.api import app; print('OK')"
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: delete Windows-only files (launcher, build.ps1, first_run, firev.spec)"
```

---

### Task 4: Create Dockerfile

**Files:**
- Create: `Dockerfile`

- [ ] **Step 1: Create the Dockerfile**

```dockerfile
FROM python:3.10-slim AS base

# System deps for Playwright headless Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2 fonts-liberation \
    curl && rm -rf /var/lib/apt/lists/*

# Node.js for frontend build
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps (cached layer — only rebuilds when pyproject.toml changes)
COPY backend/pyproject.toml backend/
RUN cd backend && pip install --no-cache-dir -e ".[scrape]" && \
    pip install --no-cache-dir uvloop

# Playwright browser
RUN playwright install chromium && playwright install-deps

# Frontend build
COPY frontend/package.json frontend/package-lock.json frontend/
RUN cd frontend && npm ci --ignore-scripts

COPY frontend/ frontend/
RUN cd frontend && npm run build

# Backend source
COPY backend/ backend/

# Data directories
RUN mkdir -p /app/data /app/logs /app/models

ENV FIREV_DATA_DIR=/app/data
ENV FIREV_LOGS_DIR=/app/logs
ENV FIREV_FRONTEND_DIR=/app/frontend/dist

EXPOSE 8000

WORKDIR /app/backend
CMD ["python", "-m", "uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "120", "--loop", "uvloop"]
```

- [ ] **Step 2: Create .dockerignore**

```
.git
__pycache__
*.pyc
*.pyo
backend/data/
backend/logs/
backend/.env
frontend/node_modules/
frontend/dist/
*.egg-info
.venv
chrome-profile/
```

- [ ] **Step 3: Verify Dockerfile builds**

```bash
docker build -t firev:test .
```

Expected: Build completes, image created.

- [ ] **Step 4: Commit**

```bash
git add Dockerfile .dockerignore
git commit -m "feat: add Dockerfile for Linux deployment"
```

---

### Task 5: Create docker-compose.yml (SQLite phase)

**Files:**
- Create: `docker-compose.yml`
- Create: `docker-compose.dev.yml`

- [ ] **Step 1: Create docker-compose.yml**

```yaml
services:
  backend:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - firev_data:/app/data
      - firev_logs:/app/logs
      - firev_models:/app/models
      - chrome_profile:/app/chrome-profile
    env_file:
      - .env.docker
    environment:
      - FIREV_DATA_DIR=/app/data
      - FIREV_LOGS_DIR=/app/logs
      - FIREV_FRONTEND_DIR=/app/frontend/dist
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s

volumes:
  firev_data:
  firev_logs:
  firev_models:
  chrome_profile:
```

- [ ] **Step 2: Create docker-compose.dev.yml**

Override for local development (no nginx, mounts source for live reload):

```yaml
services:
  backend:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./backend:/app/backend
      - ./frontend/dist:/app/frontend/dist
      - firev_data:/app/data
      - firev_logs:/app/logs
    env_file:
      - .env.docker
    environment:
      - FIREV_DATA_DIR=/app/data
      - FIREV_LOGS_DIR=/app/logs
    command: ["python", "-m", "uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--loop", "uvloop"]

volumes:
  firev_data:
  firev_logs:
```

- [ ] **Step 3: Create .env.docker.example**

```env
# Copy to .env.docker and fill in values
DATABENTO_API_KEY=db-xxx
ANTHROPIC_API_KEY=sk-ant-xxx
BRAVE_API_KEY=BSAxx

# Phase 2: PostgreSQL (uncomment when migrating)
# DATABASE_URL=postgresql+asyncpg://firev:changeme@postgres:5432/firev
# MARKET_DATABASE_URL=postgresql+asyncpg://firev:changeme@postgres:5432/market
# DB_PASSWORD=changeme
```

- [ ] **Step 4: Test docker-compose up**

```bash
cp .env.docker.example .env.docker
# Fill in real API keys
docker compose up --build
```

Expected: Backend starts on port 8000, frontend loads, extraction endpoints respond.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml docker-compose.dev.yml .env.docker.example
git commit -m "feat: add docker-compose for SQLite phase deployment"
```

---

### Task 6: Test extraction pipeline in Docker

**Files:** None (testing only)

- [ ] **Step 1: Verify health endpoint**

```bash
curl http://localhost:8000/health
```

Expected: `{"status": "ok"}`

- [ ] **Step 2: Trigger sharp extraction**

```bash
curl -X POST "http://localhost:8000/api/extraction/run?providers=pinnacle"
```

Expected: Extraction starts, returns 200.

- [ ] **Step 3: Verify frontend loads**

Open `http://localhost:8000` in browser. Expected: React app loads, tabs render.

- [ ] **Step 4: Test SSE stream**

```bash
curl -N http://localhost:8000/api/extraction/stream
```

Expected: SSE events arrive when extraction runs.

- [ ] **Step 5: Test one browser provider**

```bash
curl -X POST "http://localhost:8000/api/extraction/run?providers=mrgreen"
```

Expected: Playwright launches headless, Spectate extraction completes.

---

## Phase 2: PostgreSQL Migration

### Task 7: Add PostgreSQL to docker-compose

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add postgres service to docker-compose.yml**

Add to services section:

```yaml
  postgres:
    image: postgres:16-alpine
    volumes:
      - pg_data:/var/lib/postgresql/data
    environment:
      - POSTGRES_USER=firev
      - POSTGRES_PASSWORD=${DB_PASSWORD}
      - POSTGRES_DB=firev
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U firev"]
      interval: 5s
      retries: 5
    restart: unless-stopped
    ports:
      - "5432:5432"
```

Add to backend service:

```yaml
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      - DATABASE_URL=postgresql+asyncpg://firev:${DB_PASSWORD}@postgres:5432/firev
      - MARKET_DATABASE_URL=postgresql+asyncpg://firev:${DB_PASSWORD}@postgres:5432/market
```

Add to volumes:

```yaml
  pg_data:
```

- [ ] **Step 2: Create market database on Postgres startup**

Create `docker/init-market-db.sql`:

```sql
-- Create the market database for tick/candle data
SELECT 'CREATE DATABASE market OWNER firev'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'market')\gexec
```

Add to postgres service:

```yaml
    volumes:
      - pg_data:/var/lib/postgresql/data
      - ./docker/init-market-db.sql:/docker-entrypoint-initdb.d/01-market-db.sql
```

- [ ] **Step 3: Update .env.docker.example**

```env
DATABENTO_API_KEY=db-xxx
ANTHROPIC_API_KEY=sk-ant-xxx
BRAVE_API_KEY=BSAxx
DB_PASSWORD=changeme
DATABASE_URL=postgresql+asyncpg://firev:changeme@postgres:5432/firev
MARKET_DATABASE_URL=postgresql+asyncpg://firev:changeme@postgres:5432/market
```

- [ ] **Step 4: Verify Postgres starts**

```bash
docker compose up postgres -d
docker compose exec postgres psql -U firev -c "SELECT 1"
docker compose exec postgres psql -U firev -d market -c "SELECT 1"
```

Expected: Both databases respond.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml docker/init-market-db.sql .env.docker.example
git commit -m "feat: add PostgreSQL container to docker-compose"
```

---

### Task 8: Add asyncpg and psycopg2 to dependencies

**Files:**
- Modify: `backend/pyproject.toml`

- [ ] **Step 1: Add PostgreSQL dependencies to pyproject.toml**

Add to the `[project.dependencies]` list:

```
asyncpg>=0.29.0
psycopg2-binary>=2.9.9
```

Add `uvloop` too if not already present:

```
uvloop>=0.19.0; sys_platform != "win32"
```

- [ ] **Step 2: Remove pywebview and pyinstaller from optional deps**

If present in `[project.optional-dependencies]`, remove:
```
pywebview
pyinstaller
```

- [ ] **Step 3: Install and verify**

```bash
cd backend && pip install -e ".[dev,scrape]"
python -c "import asyncpg; import psycopg2; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add backend/pyproject.toml
git commit -m "deps: add asyncpg, psycopg2-binary, uvloop; remove pywebview/pyinstaller"
```

---

### Task 9: Rewrite database engine layer for PostgreSQL

**Files:**
- Modify: `backend/src/db/models.py` (lines 1409-1451 and 2033-2095)

This is the highest-risk change. We replace the SQLite engine with PostgreSQL while keeping the same ORM models.

- [ ] **Step 1: Rewrite get_engine() to support both SQLite and Postgres**

Replace the engine section (lines ~1409-1451) with:

```python
import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

_engine = None
_async_engine = None
_AsyncSessionFactory = None


def _is_postgres() -> bool:
    """Check if we're configured for PostgreSQL."""
    return bool(os.environ.get("DATABASE_URL", "").startswith("postgresql"))


def get_engine():
    """Get or create the sync database engine (for Alembic and legacy code)."""
    global _engine
    if _engine is None:
        db_url = os.environ.get("DATABASE_URL")
        if db_url:
            # PostgreSQL — convert async URL to sync for Alembic
            sync_url = db_url.replace("+asyncpg", "+psycopg2")
            _engine = create_engine(sync_url)
        else:
            # SQLite fallback (local dev without Docker)
            from ..paths import get_db_path
            db_path = get_db_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            _engine = create_engine(
                f"sqlite:///{db_path}",
                poolclass=NullPool,
                connect_args={"check_same_thread": False, "timeout": 30},
            )
            with _engine.connect() as conn:
                conn.execute(text("PRAGMA journal_mode=WAL"))
                conn.commit()

            @event.listens_for(_engine, "connect")
            def _set_sqlite_pragmas(dbapi_conn, connection_record):
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.close()

        Base.metadata.create_all(_engine)
        _run_migrations(_engine)
    return _engine


def get_async_engine():
    """Get or create the async database engine (for FastAPI routes)."""
    global _async_engine
    if _async_engine is None:
        db_url = os.environ.get("DATABASE_URL")
        if db_url:
            _async_engine = create_async_engine(db_url, pool_size=20, max_overflow=10)
        else:
            # SQLite async fallback
            from ..paths import get_db_path
            db_path = get_db_path()
            _async_engine = create_async_engine(
                f"sqlite+aiosqlite:///{db_path}",
                connect_args={"check_same_thread": False},
            )
    return _async_engine


def get_async_session_factory():
    """Get or create the async session factory."""
    global _AsyncSessionFactory
    if _AsyncSessionFactory is None:
        _AsyncSessionFactory = async_sessionmaker(
            bind=get_async_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _AsyncSessionFactory
```

- [ ] **Step 2: Keep sync get_session() working for non-route code**

Keep existing `get_session_factory()` and `get_session()` functions — they're used by the extraction pipeline, scheduler, and mirror (33 call sites). These run in background threads and don't need async.

Update `get_session_factory()` to use the new engine:

```python
_SessionFactory = None

def get_session_factory():
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine())
    return _SessionFactory

def get_session():
    factory = get_session_factory()
    return factory()
```

- [ ] **Step 3: Rewrite market DB engine for Postgres**

Replace the market engine section (lines ~2033-2095):

```python
_market_engine = None
_market_async_engine = None
_MarketSessionFactory = None
_MarketAsyncSessionFactory = None

def get_market_engine():
    global _market_engine
    if _market_engine is None:
        db_url = os.environ.get("MARKET_DATABASE_URL")
        if db_url:
            sync_url = db_url.replace("+asyncpg", "+psycopg2")
            _market_engine = create_engine(sync_url)
        else:
            from ..paths import get_market_db_path
            market_path = get_market_db_path()
            market_path.parent.mkdir(parents=True, exist_ok=True)
            _market_engine = create_engine(
                f"sqlite:///{market_path}",
                poolclass=NullPool,
                connect_args={"check_same_thread": False, "timeout": 10},
            )
            with _market_engine.connect() as conn:
                conn.execute(text("PRAGMA journal_mode=WAL"))
                conn.commit()

            @event.listens_for(_market_engine, "connect")
            def _set_market_pragmas(dbapi_conn, connection_record):
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA busy_timeout=10000")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.close()

        for table in MARKET_DB_TABLES:
            table.create(_market_engine, checkfirst=True)
    return _market_engine

def get_market_session_factory():
    global _MarketSessionFactory
    if _MarketSessionFactory is None:
        _MarketSessionFactory = sessionmaker(bind=get_market_engine())
    return _MarketSessionFactory

def get_market_session():
    factory = get_market_session_factory()
    return factory()
```

- [ ] **Step 4: Remove the direct sqlite3 WAL checkpoint from api/__init__.py**

In `backend/src/api/__init__.py`, find and delete the WAL checkpoint block (lines ~64-74):

```python
# DELETE this block:
import sqlite3
_wal_conn = sqlite3.connect(str(DB_PATH), timeout=5)
_wal_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
_wal_conn.close()
```

- [ ] **Step 5: Verify engine creation works**

```bash
cd backend
DATABASE_URL=postgresql+asyncpg://firev:changeme@localhost:5432/firev python -c "
from src.db.models import get_engine, get_async_engine, _is_postgres
print('Is Postgres:', _is_postgres())
print('Sync engine:', get_engine())
print('Async engine:', get_async_engine())
print('OK')
"
```

- [ ] **Step 6: Commit**

```bash
git add backend/src/db/models.py backend/src/api/__init__.py
git commit -m "feat(db): add PostgreSQL engine support alongside SQLite fallback"
```

---

### Task 10: Convert FastAPI deps to async sessions

**Files:**
- Modify: `backend/src/api/deps.py`

- [ ] **Step 1: Add async session dependency**

Replace `backend/src/api/deps.py` with:

```python
"""FastAPI dependencies."""

import logging
from typing import AsyncGenerator

from sqlalchemy.orm import Session
from ..db.models import get_session, get_async_session_factory, _is_postgres

logger = logging.getLogger(__name__)

_pipeline_instance = None


# Async dependency (for Postgres)
async def get_db_async() -> AsyncGenerator:
    """Async database session dependency for PostgreSQL."""
    factory = get_async_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Sync dependency (for SQLite fallback)
def get_db_sync():
    """Sync database session dependency for SQLite."""
    db = get_session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db_writer_sync():
    """Sync session for write-heavy routes (no auto-commit)."""
    db = get_session()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# Route-facing dependencies — pick async or sync based on config
def get_db():
    """Database session dependency. Routes use this."""
    if _is_postgres():
        return get_db_async()
    return get_db_sync()


def get_db_writer():
    """Write-heavy session dependency. Routes use this."""
    if _is_postgres():
        return get_db_async()  # Postgres doesn't need special write handling
    return get_db_writer_sync()


def get_pipeline():
    """Get or create pipeline singleton."""
    global _pipeline_instance
    if _pipeline_instance is None:
        from ..pipeline import ExtractionPipeline
        _pipeline_instance = ExtractionPipeline()
    return _pipeline_instance
```

- [ ] **Step 2: Verify deps import without error**

```bash
cd backend && python -c "from src.api.deps import get_db, get_db_writer, get_pipeline; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/api/deps.py
git commit -m "feat(deps): add async/sync database session switching for Postgres/SQLite"
```

---

### Task 11: Update Alembic for PostgreSQL

**Files:**
- Modify: `backend/alembic.ini` (line 60)
- Modify: `backend/alembic/env.py`

- [ ] **Step 1: Update alembic.ini to use environment variable**

Replace line 60 in `backend/alembic.ini`:

```ini
# Database URL — overridden by env.py from DATABASE_URL environment variable
sqlalchemy.url = sqlite:///data/firev.db
```

- [ ] **Step 2: Rewrite alembic/env.py**

Replace the entire file:

```python
"""Alembic Environment Configuration for Firev."""
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool
from alembic import context

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.models import Base

config = context.config

# Use DATABASE_URL env var if set, otherwise fall back to alembic.ini value
db_url = os.environ.get("DATABASE_URL")
if db_url:
    # Convert async URL to sync for Alembic
    sync_url = db_url.replace("+asyncpg", "+psycopg2")
    config.set_main_option("sqlalchemy.url", sync_url)
else:
    from src.db.models import DB_PATH
    config.set_main_option("sqlalchemy.url", f"sqlite:///{DB_PATH}")

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 3: Generate fresh Postgres baseline migration**

```bash
cd backend
DATABASE_URL=postgresql+psycopg2://firev:changeme@localhost:5432/firev \
  alembic revision --autogenerate -m "postgres baseline"
```

- [ ] **Step 4: Run migration against Postgres**

```bash
DATABASE_URL=postgresql+psycopg2://firev:changeme@localhost:5432/firev \
  alembic upgrade head
```

- [ ] **Step 5: Verify tables exist**

```bash
docker compose exec postgres psql -U firev -c "\dt"
```

Expected: All tables listed (event, odds, bet, provider, profile, etc.)

- [ ] **Step 6: Commit**

```bash
git add backend/alembic.ini backend/alembic/env.py backend/alembic/versions/
git commit -m "feat(alembic): support PostgreSQL via DATABASE_URL env var"
```

---

### Task 12: Write SQLite → PostgreSQL data migration script

**Files:**
- Create: `backend/scripts/migrate_sqlite_to_postgres.py`

- [ ] **Step 1: Create the migration script**

```python
"""
One-time migration: SQLite → PostgreSQL.

Reads all data from SQLite firev.db and inserts into PostgreSQL.
Run with both databases accessible:

    DATABASE_URL=postgresql+psycopg2://firev:pw@localhost:5432/firev \
    python scripts/migrate_sqlite_to_postgres.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3
import psycopg2
from psycopg2.extras import execute_values

SQLITE_PATH = os.environ.get("SQLITE_PATH", "data/firev.db")
PG_URL = os.environ["DATABASE_URL"].replace("+asyncpg", "").replace("+psycopg2", "")
# Parse PG_URL: postgresql://user:pw@host:port/db
# psycopg2 accepts this format directly

# Tables to migrate in dependency order (FKs)
TABLES = [
    "provider",
    "profile",
    "event",
    "odds",
    "bet",
    "profile_provider_bonuses",
    "profile_provider_balances",
    "profile_provider_limits",
    "opportunity",
    "bet_postmortem",
    "extraction_runs",
    "provider_run_metrics",
    "sport_run_metrics",
    "deferred_events",
    "special_odds",
    "provider_risk_profiles",
]


def migrate():
    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    pg_conn = psycopg2.connect(PG_URL)
    pg_cur = pg_conn.cursor()

    for table in TABLES:
        try:
            rows = sqlite_conn.execute(f"SELECT * FROM {table}").fetchall()
        except sqlite3.OperationalError:
            print(f"  SKIP {table} (not in SQLite)")
            continue

        if not rows:
            print(f"  SKIP {table} (0 rows)")
            continue

        cols = rows[0].keys()
        col_str = ", ".join(cols)
        template = "(" + ", ".join(["%s"] * len(cols)) + ")"

        # Truncate target first
        pg_cur.execute(f"TRUNCATE {table} CASCADE")

        # Batch insert
        values = [tuple(row) for row in rows]
        execute_values(
            pg_cur,
            f"INSERT INTO {table} ({col_str}) VALUES %s",
            values,
            template=template,
            page_size=1000,
        )
        pg_conn.commit()
        print(f"  OK {table}: {len(rows)} rows")

    sqlite_conn.close()
    pg_conn.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    migrate()
```

- [ ] **Step 2: Test the migration**

```bash
cd backend
SQLITE_PATH=data/firev.db \
DATABASE_URL=postgresql://firev:changeme@localhost:5432/firev \
python scripts/migrate_sqlite_to_postgres.py
```

Expected: Each table migrated with row count printed.

- [ ] **Step 3: Verify data in Postgres**

```bash
docker compose exec postgres psql -U firev -c "SELECT COUNT(*) FROM event"
docker compose exec postgres psql -U firev -c "SELECT COUNT(*) FROM odds"
docker compose exec postgres psql -U firev -c "SELECT COUNT(*) FROM bet"
```

Compare counts against SQLite:
```bash
cd backend && sqlite3 data/firev.db "SELECT COUNT(*) FROM event"
```

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/migrate_sqlite_to_postgres.py
git commit -m "feat: add SQLite-to-PostgreSQL data migration script"
```

---

### Task 13: Update CI for PostgreSQL

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Add Postgres service to backend tests**

Update the `backend-tests` job:

```yaml
  backend-tests:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_USER: firev
          POSTGRES_PASSWORD: test
          POSTGRES_DB: firev
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U firev"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 5
    env:
      DATABASE_URL: postgresql+asyncpg://firev:test@localhost:5432/firev
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.10"
          cache: pip
      - run: |
          cd backend
          pip install -e ".[dev,scrape]"
      - run: |
          cd backend
          pytest tests/ --ignore=tests/test_rl_evaluate.py -x
```

- [ ] **Step 2: Update conftest.py for Postgres tests**

Replace `backend/tests/conftest.py`:

```python
"""Shared test fixtures for Firev tests."""
import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.db.models import Base


@pytest.fixture
def db_session():
    """Database session — uses Postgres if DATABASE_URL set, else in-memory SQLite."""
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        sync_url = db_url.replace("+asyncpg", "+psycopg2")
        engine = create_engine(sync_url)
    else:
        engine = create_engine("sqlite:///:memory:")

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()

    if db_url:
        # Clean up tables after test
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)

    engine.dispose()
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml backend/tests/conftest.py
git commit -m "ci: add PostgreSQL service container for tests"
```

---

## Phase 3: Server Deployment

### Task 14: Create Nginx configuration

**Files:**
- Create: `nginx/nginx.conf`

- [ ] **Step 1: Create nginx directory and config**

```bash
mkdir -p nginx
```

Write `nginx/nginx.conf`:

```nginx
events {
    worker_connections 1024;
}

http {
    upstream backend {
        server backend:8000;
    }

    # Rate limiting
    limit_req_zone $binary_remote_addr zone=api:10m rate=30r/s;

    # Redirect HTTP → HTTPS
    server {
        listen 80;
        server_name _;
        return 301 https://$host$request_uri;
    }

    server {
        listen 443 ssl;
        server_name _;

        ssl_certificate     /etc/letsencrypt/live/firev/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/firev/privkey.pem;
        ssl_protocols TLSv1.2 TLSv1.3;

        # Security headers
        add_header X-Frame-Options DENY;
        add_header X-Content-Type-Options nosniff;

        # Frontend + API (default)
        location / {
            proxy_pass http://backend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            limit_req zone=api burst=50 nodelay;
        }

        # WebSocket
        location /ws {
            proxy_pass http://backend;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
            proxy_read_timeout 86400s;
        }

        # SSE — extraction stream
        location /api/extraction/stream {
            proxy_pass http://backend;
            proxy_buffering off;
            proxy_cache off;
            proxy_set_header Connection "";
            proxy_http_version 1.1;
            proxy_read_timeout 3600s;
        }

        # SSE — market data stream
        location /api/trading/market/stream {
            proxy_pass http://backend;
            proxy_buffering off;
            proxy_cache off;
            proxy_set_header Connection "";
            proxy_http_version 1.1;
            proxy_read_timeout 86400s;
        }

        # API with generous timeout for extraction runs
        location /api/ {
            proxy_pass http://backend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_read_timeout 300s;
            limit_req zone=api burst=50 nodelay;
        }

        # Health check (no rate limit)
        location /health {
            proxy_pass http://backend;
        }

        gzip on;
        gzip_min_length 1000;
        gzip_types text/plain application/json application/javascript text/css;
    }
}
```

- [ ] **Step 2: Add nginx to docker-compose.yml**

Add to services:

```yaml
  nginx:
    image: nginx:alpine
    ports:
      - "443:443"
      - "80:80"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - certs:/etc/letsencrypt:ro
    depends_on:
      - backend
    restart: unless-stopped
```

Add to volumes:

```yaml
  certs:
```

- [ ] **Step 3: Commit**

```bash
git add nginx/nginx.conf docker-compose.yml
git commit -m "feat: add Nginx reverse proxy with SSL, SSE, and WebSocket support"
```

---

### Task 15: Server provisioning checklist

**Files:** None (manual server setup)

This task is a checklist — not code. Execute on the actual Hetzner server.

- [ ] **Step 1: Create Hetzner CX32**

Go to https://console.hetzner.cloud, create CX32 (Ubuntu 24.04, Falkenstein). Note the IP address.

- [ ] **Step 2: SSH in and install Docker**

```bash
ssh root@YOUR_SERVER_IP
apt update && apt upgrade -y
curl -fsSL https://get.docker.com | sh
apt install -y docker-compose-plugin
```

- [ ] **Step 3: Configure firewall**

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

- [ ] **Step 4: Set up SSL with certbot**

```bash
apt install -y certbot
certbot certonly --standalone -d your-domain.com
# Certs go to /etc/letsencrypt/live/your-domain.com/
```

- [ ] **Step 5: Clone repo and configure**

```bash
git clone https://github.com/your/BankrollBBQ.git /opt/firev
cd /opt/firev
cp .env.docker.example .env.docker
# Edit .env.docker with real API keys and DB password
nano .env.docker
```

- [ ] **Step 6: Start everything**

```bash
cd /opt/firev
docker compose up -d --build
docker compose logs -f backend
```

- [ ] **Step 7: Run data migration**

```bash
# Copy SQLite DB from local machine
scp backend/data/firev.db root@YOUR_SERVER_IP:/opt/firev/backend/data/

# Run migration inside container
docker compose exec backend python scripts/migrate_sqlite_to_postgres.py
```

- [ ] **Step 8: Verify everything works**

```bash
curl https://your-domain.com/health
curl https://your-domain.com/api/providers
# Open https://your-domain.com in browser — frontend should load
```

---

## Phase 4: Hardening

### Task 16: Add monitoring and backups

**Files:**
- Create: `docker/pg-backup.sh`
- Modify: `docker-compose.yml` (healthchecks already added)

- [ ] **Step 1: Create PostgreSQL backup script**

```bash
#!/bin/bash
# Daily PostgreSQL backup
BACKUP_DIR=/opt/firev/backups
mkdir -p $BACKUP_DIR
DATE=$(date +%Y%m%d_%H%M%S)

docker compose -f /opt/firev/docker-compose.yml exec -T postgres \
    pg_dump -U firev firev | gzip > $BACKUP_DIR/firev_$DATE.sql.gz

docker compose -f /opt/firev/docker-compose.yml exec -T postgres \
    pg_dump -U firev market | gzip > $BACKUP_DIR/market_$DATE.sql.gz

# Keep last 7 days
find $BACKUP_DIR -name "*.sql.gz" -mtime +7 -delete

echo "Backup complete: $DATE"
```

- [ ] **Step 2: Add cron job for daily backups**

```bash
chmod +x /opt/firev/docker/pg-backup.sh
crontab -e
# Add: 0 3 * * * /opt/firev/docker/pg-backup.sh >> /opt/firev/backups/backup.log 2>&1
```

- [ ] **Step 3: Configure Docker log rotation**

Add to each service in `docker-compose.yml`:

```yaml
    logging:
      driver: json-file
      options:
        max-size: "50m"
        max-file: "5"
```

- [ ] **Step 4: Set up UptimeRobot**

Go to https://uptimerobot.com (free tier), add HTTPS monitor for `https://your-domain.com/health`. Alert on downtime.

- [ ] **Step 5: Enable Hetzner snapshots**

In Hetzner console → Server → Snapshots → Enable weekly automatic snapshots.

- [ ] **Step 6: Commit**

```bash
git add docker/pg-backup.sh docker-compose.yml
git commit -m "feat: add PostgreSQL backup script and Docker log rotation"
```

---

## Summary

| Phase | Tasks | Key deliverable |
|-------|-------|----------------|
| 1. Dockerize | Tasks 1-6 | App runs in Docker with SQLite on Linux |
| 2. PostgreSQL | Tasks 7-13 | Async Postgres, concurrent writes, data migrated |
| 3. Deploy | Tasks 14-15 | Live on Hetzner with SSL, accessible from anywhere |
| 4. Harden | Task 16 | Backups, monitoring, log rotation |
