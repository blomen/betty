"""
BankrollBBQ FastAPI Backend

REST API for the React frontend.
Connects to SQLite database and analysis modules.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv

# Load .env from user data directory (AppData in bundled mode, backend/ in dev)
from ..paths import get_env_path
load_dotenv(get_env_path(), override=True)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..db.models import init_db
from .routes import (
    providers_router,
    bankroll_router,
    events_router,
    opportunities_router,
    bets_router,
    profiles_router,
    extraction_router,
    metrics_router,
    monitoring_router,
    chat_router,
    polymarket_router,
    risk_router,
    specials_router,
    trading_router,
    settings_router,
    market_router,
    limits_router,
    postmortem_router,
)

logger = logging.getLogger(__name__)

# Track startup time for uptime calculation
_startup_time: float = 0.0


def _startup_purge():
    """Clear all extracted data on startup. Preserves user data (bets, profiles, balances).

    Events linked to bets are kept (needed for history). All other events, odds,
    opportunities, specials, and live status flags are wiped so re-extraction
    starts fresh.

    Uses a dedicated sqlite3 connection with a short busy_timeout. Each DELETE
    runs as its own transaction so it grabs and releases the write lock quickly.
    If any step hits a lock (e.g. from MCP sqlite tool), it skips gracefully —
    stale data gets overwritten on next extraction anyway.
    """
    import sqlite3
    from ..db.models import DB_PATH
    from ..constants import SHARP_PROVIDERS

    sharp_list = ",".join(f"'{p}'" for p in SHARP_PROVIDERS)

    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")  # 5s — enough for engine pool to settle

        steps = [
            ("opportunities", "DELETE FROM opportunities"),
            # NOTE: specials are NOT purged — LLM research results are expensive
            # and must persist across restarts. The scraper fully replaces them on each run.
            ("odds (no bets)", """
                DELETE FROM odds WHERE event_id IN (
                    SELECT e.id FROM events e
                    WHERE e.id NOT IN (SELECT DISTINCT event_id FROM bets WHERE event_id IS NOT NULL)
                )
            """),
            ("events (no bets)", """
                DELETE FROM events WHERE id NOT IN (
                    SELECT DISTINCT event_id FROM bets WHERE event_id IS NOT NULL
                )
            """),
            ("live tracking reset", """
                UPDATE events SET match_minute = NULL, match_period = NULL
                WHERE id IN (SELECT DISTINCT event_id FROM bets WHERE event_id IS NOT NULL)
            """),
            ("non-sharp odds", f"""
                DELETE FROM odds WHERE event_id IN (
                    SELECT DISTINCT event_id FROM bets WHERE event_id IS NOT NULL
                ) AND provider_id NOT IN ({sharp_list})
            """),
        ]

        completed = 0
        for label, sql in steps:
            try:
                conn.execute(sql)
                conn.commit()
                completed += 1
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e):
                    logger.warning(f"[Startup] Purge skipped '{label}' (DB locked)")
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                else:
                    raise

        logger.info(f"[Startup] Purge completed ({completed}/{len(steps)} steps)")
    except Exception as e:
        logger.error(f"[Startup] Purge failed: {e}")
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown logic."""
    global _startup_time
    _startup_time = time.time()
    init_db()

    # Guard: if another backend is already serving, skip the purge.
    # A duplicate lifespan would wipe all extracted data via _startup_purge().
    import socket as _sock
    _dup = False
    _s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
    try:
        _s.connect(("127.0.0.1", 8000))
        _dup = True
        logger.warning(
            "Port 8000 already serving — skipping startup purge to protect existing data. "
            "This instance will likely fail to bind."
        )
    except (ConnectionRefusedError, OSError):
        pass  # Port not in use — safe to purge
    finally:
        _s.close()

    # No startup purge — data persists across restarts.
    # Stale data is visible via per-row "Upd" timestamps in the frontend.
    # Cleanup happens on re-extraction and via the 6-hour cleanup tier.

    # Add extraction-specific log file (DEBUG level) alongside launcher's root handlers
    import logging.handlers
    from ..paths import get_logs_dir
    extraction_handler = logging.handlers.RotatingFileHandler(
        get_logs_dir() / "extraction.log",
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    extraction_handler.setLevel(logging.DEBUG)
    extraction_handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] [%(name)s:%(lineno)d] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    ))
    root_logger = logging.getLogger()
    root_logger.addHandler(extraction_handler)
    # Ensure root logger passes DEBUG+ to handlers (uvicorn sets it to WARNING)
    if root_logger.level > logging.DEBUG:
        root_logger.setLevel(logging.DEBUG)

    # Eagerly warm up singletons / heavy imports so the first API request is fast
    from ..config.loader import load_config
    load_config()
    try:
        import numpy  # noqa: F401 — imported by ml.serving.predictor on first use
    except ImportError:
        pass

    # Auto-start continuous extraction (every 5 min, Pinnacle + Polymarket)
    from ..pipeline.scheduler import get_scheduler
    scheduler = get_scheduler()
    await scheduler.start_continuous(interval_seconds=300)

    # Auto-start Databento live stream + prune old ticks
    import os
    databento_key = os.environ.get("DATABENTO_API_KEY")
    _databento_stream = None
    if databento_key:
        from ..db.models import get_session as _get_db_session
        from ..market_data.stream import DatabentoLiveStream, TickWriter

        # Prune ticks from prior sessions
        await TickWriter.prune_old_trades(_get_db_session, symbol="NQ")

        _databento_stream = DatabentoLiveStream(
            api_key=databento_key,
            db_session_factory=_get_db_session,
        )

        # Seed CandleFlow from last DB candle so live updates continue
        # rather than starting fresh (which causes fake wicks on chart).
        from ..repositories.market_repo import MarketRepo as _MR
        _seed_db = _get_db_session()
        try:
            for _flow, _interval in [
                (_databento_stream._candle_flow, "5m"),
                (_databento_stream._candle_flow_1m, "1m"),
            ]:
                _last = _MR(_seed_db).get_latest_candle("NQ", _interval)
                if _last:
                    _ts = _last.ts.replace(tzinfo=timezone.utc) if not _last.ts.tzinfo else _last.ts
                    _flow.seed(int(_ts.timestamp()), _last.o, _last.h, _last.l, _last.c, _last.v)
                    logger.info("Seeded %s CandleFlow from DB: bucket=%s", _interval, _ts)
        finally:
            _seed_db.close()

        await _databento_stream.start()
        app.state.databento_stream = _databento_stream

        # Backfill market_candles once from Databento historical.
        # On every startup: fetch only the missing gap per interval.
        # History never changes, so existing rows are skipped.
        async def _backfill_candles():
            from ..market_data.databento_provider import DabentoProvider
            from ..repositories.market_repo import MarketRepo
            from ..config.trading_loader import get_market_data_config
            from datetime import timedelta

            config = get_market_data_config()
            symbol = "NQ"
            db_symbol = config.get("symbol", "NQ.c.0")
            now = datetime.now(timezone.utc)
            fetch_end = now - timedelta(minutes=30)  # Databento ~15-30 min delay

            # 5m: full history from 2010 (already backfilled, only forward gap)
            # 1m: 30 days max (5x more data, don't need years of 1m bars)
            interval_targets = {
                "5m": datetime(2010, 6, 6, tzinfo=timezone.utc),
                "1m": now - timedelta(days=36),
            }

            inner = DabentoProvider(config)

            for interval, target_start in interval_targets.items():
                db = _get_db_session()
                try:
                    repo = MarketRepo(db)
                    oldest = repo.get_oldest_candle(symbol, interval)
                    latest = repo.get_latest_candle(symbol, interval)
                finally:
                    db.close()

                fetch_start = target_start
                if oldest:
                    oldest_ts = oldest.ts if oldest.ts.tzinfo else oldest.ts.replace(tzinfo=timezone.utc)
                    if oldest_ts <= target_start + timedelta(days=1):
                        fetch_start = latest.ts if latest.ts.tzinfo else latest.ts.replace(tzinfo=timezone.utc)

                logger.info("Candle backfill %s: %s → %s", interval, fetch_start.date(), fetch_end.date())
                try:
                    bars = await asyncio.wait_for(
                        inner.get_bars(db_symbol, interval, fetch_start, fetch_end),
                        timeout=300.0,
                    )
                    if bars:
                        db = _get_db_session()
                        try:
                            inserted = MarketRepo(db).bulk_insert_candles(symbol, interval, bars)
                            logger.info("Candle backfill %s: %d new (%d fetched)", interval, inserted, len(bars))
                        finally:
                            db.close()
                    else:
                        logger.warning("Candle backfill %s: no bars returned", interval)
                except Exception as e:
                    logger.warning("Candle backfill %s failed: %s", interval, e)

        asyncio.create_task(_backfill_candles())

        # Initialize Level Monitor for proximity-based level alerts
        from ..market_data.level_monitor import LevelMonitor
        from ..services.market_service import MarketService

        level_monitor = LevelMonitor(publish_fn=_databento_stream._publish)
        _databento_stream.set_level_monitor(level_monitor)
        app.state.level_monitor = level_monitor
        level_monitor.set_async_context(asyncio.get_event_loop(), _get_db_session)

        # Load initial levels if session exists
        try:
            svc = MarketService(_get_db_session())
            try:
                expanded = await svc.build_expanded_session()
                if expanded:
                    level_monitor.load_levels(expanded)
            finally:
                svc.db.close()
        except Exception as e:
            logger.warning("Failed to load initial levels for monitor: %s", e)

        # Refresh COT data on startup
        try:
            from ..market_data.cot import fetch_cot, store_cot_data
            reports = await fetch_cot()
            if reports:
                db = _get_db_session()
                try:
                    store_cot_data(db, reports)
                    db.commit()
                finally:
                    db.close()
                logger.info("COT data refreshed: %d reports", len(reports))
        except Exception as e:
            logger.warning("COT refresh failed: %s", e)

        logger.info("Trading features started: Databento stream + level monitor + COT")
    else:
        logger.warning("DATABENTO_API_KEY not set — trading features disabled")

    yield  # App is running

    # Graceful shutdown
    logger.info("Shutting down...")
    if _databento_stream:
        await _databento_stream.stop()
    scheduler.stop_all()
    logger.info("Shutdown complete.")


app = FastAPI(
    title="BankrollBBQ API",
    description="Polymarket arbitrage & value betting backend",
    version="0.1.0",
    lifespan=lifespan,
)

# GZip compression for responses > 1KB
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Allow CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174", "http://localhost:3000", "tauri://localhost"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch unhandled exceptions and return a safe JSON response."""
    logger.error(f"Unhandled exception on {request.method} {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"},
    )


# Health check endpoints
@app.get("/health")
async def health():
    """Basic health check endpoint."""
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/health/live")
async def health_live():
    """
    Liveness check - is the service running?

    Returns 200 if the service is alive and can handle requests.
    Used by Kubernetes/Docker for liveness probes.
    """
    return {
        "status": "alive",
        "uptime_seconds": time.time() - _startup_time if _startup_time else 0,
    }


@app.get("/health/ready")
async def health_ready():
    """
    Readiness check - is the service ready to accept traffic?

    Checks database connectivity and provider availability.
    Used by Kubernetes/Docker for readiness probes.
    """
    from .deps import get_db
    from ..db.models import Provider

    status = "ready"
    database_ok = False
    db_latency_ms = 0.0
    providers_available = 0
    providers_total = 0

    # Check database connectivity
    db = None
    try:
        db_start = time.time()
        db = next(get_db())
        # Simple query to verify DB is working
        providers = db.query(Provider).all()
        db_latency_ms = (time.time() - db_start) * 1000
        database_ok = True

        # Count enabled providers
        providers_total = len(providers)
        providers_available = sum(1 for p in providers if p.is_enabled)
    except Exception as e:
        status = "not_ready"
        database_ok = False
    finally:
        if db:
            db.close()

    # Determine overall status
    if not database_ok:
        status = "not_ready"
    elif providers_available == 0 and providers_total > 0:
        status = "degraded"

    return {
        "status": status,
        "database": database_ok,
        "database_latency_ms": round(db_latency_ms, 2),
        "providers_available": providers_available,
        "providers_total": providers_total,
    }


# Include routers
app.include_router(providers_router)
app.include_router(bankroll_router)
app.include_router(events_router)
app.include_router(opportunities_router)
app.include_router(bets_router)
app.include_router(profiles_router)
app.include_router(extraction_router)
app.include_router(metrics_router)
app.include_router(monitoring_router)
app.include_router(chat_router)
app.include_router(polymarket_router)
app.include_router(risk_router)
app.include_router(specials_router)
app.include_router(trading_router)
app.include_router(market_router)
app.include_router(settings_router)
app.include_router(limits_router)
app.include_router(postmortem_router)


# Version endpoint
@app.get("/api/version")
async def get_version():
    """Return app version and runtime info."""
    from ..paths import get_app_data_dir, is_bundled
    return {
        "version": app.version,
        "data_dir": str(get_app_data_dir()),
        "is_bundled": is_bundled(),
    }


# Serve frontend static files (when dist/ exists — bundled mode or pre-built dev)
from ..paths import get_frontend_dir

_frontend_dir = get_frontend_dir()
if _frontend_dir.exists():
    # Mount JS/CSS/image assets
    _assets_dir = _frontend_dir / "assets"
    if _assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="static-assets")

    # Serve favicon and other root static files
    @app.get("/terminal.svg")
    async def serve_favicon():
        svg = _frontend_dir / "terminal.svg"
        if svg.exists():
            return FileResponse(str(svg), media_type="image/svg+xml")

    # SPA catch-all: serve index.html for all non-API routes
    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        """Serve React app for client-side routing."""
        index = _frontend_dir / "index.html"
        if index.exists():
            return FileResponse(str(index), media_type="text/html")


# Dev entry point (no --reload). On Windows, --reload forces SelectorEventLoop
# which breaks patchright subprocess spawning. Without --reload, uvicorn uses
# ProactorEventLoop correctly. Use run_dev.py if you need hot-reload.
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.api:app", host="127.0.0.1", port=8000)
