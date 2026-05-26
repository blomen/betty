"""
Betty FastAPI Backend

REST API for the React frontend.
Connects to SQLite database and analysis modules.
"""

import asyncio
import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timezone

# Dedicated executor for /health probes so they don't queue behind extraction
# threads on the default asyncio loop executor.
_health_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="health")

from dotenv import load_dotenv

# Load .env from user data directory (AppData in bundled mode, backend/ in dev)
from ..paths import get_env_path

load_dotenv(get_env_path(), override=True)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.gzip import GZipMiddleware

from ..db.models import init_db
from .routes import (
    bankroll_router,
    bets_router,
    chat_router,
    events_router,
    extraction_router,
    fire_window_router,
    limits_router,
    metrics_router,
    mirror_router,
    mirror_state_router,
    mirror_stream_router,
    monitoring_router,
    opportunities_router,
    polymarket_router,
    profiles_router,
    providers_router,
    risk_router,
    settings_router,
    slip_odds_router,
    specials_router,
)

logger = logging.getLogger(__name__)

# Track startup time and boot ID for restart detection
_startup_time: float = 0.0
_boot_id: str = uuid.uuid4().hex[:8]
_background_tasks: set = set()  # prevent GC of fire-and-forget tasks


def _install_asyncio_exception_handler() -> None:
    """Surface "Future exception was never retrieved" errors with their task name.

    The default handler logs only the bare exception, which is useless for
    finding the offender among hundreds of background tasks. We add the
    task name + traceback so silent failures stop being silent.
    """

    def _handler(loop, context):
        msg = context.get("message", "")
        exc = context.get("exception")
        task = context.get("future") or context.get("task")
        task_name = getattr(task, "get_name", lambda: "<unnamed>")() if task else "<no-task>"
        if exc is not None:
            logger.error("[asyncio] uncaught exception in task=%s: %s — %s", task_name, msg, exc, exc_info=exc)
        else:
            logger.error("[asyncio] uncaught error in task=%s: %s — %r", task_name, msg, context)

    asyncio.get_event_loop().set_exception_handler(_handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown logic."""
    global _startup_time
    _startup_time = time.time()
    _install_asyncio_exception_handler()
    await asyncio.to_thread(init_db)

    # Add new columns to existing Postgres tables (create_all only makes new tables)
    def _pg_migrations():
        from sqlalchemy import inspect, text

        from ..db.models import get_engine

        engine = get_engine()
        insp = inspect(engine)
        cols = {c["name"] for c in insp.get_columns("profiles")}
        if "liquid_balance" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE profiles ADD COLUMN liquid_balance FLOAT DEFAULT 0.0"))

    try:
        await asyncio.to_thread(_pg_migrations)
    except Exception:
        pass  # Column already exists or SQLite (handled by _run_migrations)

    # Clear any stale fire window from previous session
    from ..services.fire_window import close_window

    close_window()

    # Kill orphaned browser processes from previous mirror session
    import subprocess

    with suppress(Exception):
        subprocess.run(
            ["taskkill", "/F", "/IM", "firefox.exe", "/T"],
            capture_output=True,
            timeout=5,
        )

    # Add extraction-specific log file (INFO level) alongside root handlers
    import logging.handlers

    from ..paths import get_logs_dir

    extraction_handler = logging.handlers.RotatingFileHandler(
        get_logs_dir() / "extraction.log",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    extraction_handler.setLevel(logging.INFO)
    extraction_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] [%(name)s:%(lineno)d] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root_logger = logging.getLogger()
    root_logger.addHandler(extraction_handler)
    if root_logger.level > logging.INFO:
        root_logger.setLevel(logging.INFO)
    # Silence noisy third-party loggers
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Warm up singletons / heavy imports in background — don't block API startup
    import threading

    def _warmup_imports():
        from ..config.loader import load_config

        load_config()
        try:
            import numpy  # noqa: F401
        except ImportError:
            pass

    threading.Thread(target=_warmup_imports, daemon=True, name="startup-imports").start()

    # Warm up opportunity cache in background thread so first page load is fast
    def _warmup_opportunities():
        try:
            from ..db.models import get_session as _warmup_session
            from ..services import OpportunityService
            from .routes.opportunities import _OPP_CACHE_TTL, _opp_cache

            _wdb = _warmup_session()
            try:
                _wsvc = OpportunityService(_wdb)
                result = _wsvc.list_opportunities(type="value", limit=500)
                import json

                from fastapi.encoders import jsonable_encoder

                cache_key = ("value", None, None, None, None, None, None, 500)
                serialized = json.dumps(jsonable_encoder(result), ensure_ascii=False, separators=(",", ":"))
                _opp_cache[cache_key] = (serialized, time.time() + _OPP_CACHE_TTL)
                logger.info("[Startup] Opportunity cache warmed (%d opps)", result.get("count", 0))
            finally:
                _wdb.close()
        except Exception as e:
            logger.warning("[Startup] Opportunity warmup failed: %s", e)

    threading.Thread(target=_warmup_opportunities, daemon=True, name="startup-warmup").start()

    # Mirror-only mode: skip scheduler
    _mirror_only = bool(os.environ.get("ARNOLD_MIRROR_ONLY"))
    if _mirror_only:
        logger.info("[Startup] Mirror-only mode — skipping scheduler")

    # Auto-start continuous extraction (server only — skip for local mirror)
    if not _mirror_only:
        from ..pipeline.scheduler import get_scheduler

        scheduler = get_scheduler()

        async def _start_scheduler():
            try:
                await scheduler.start_continuous(interval_seconds=300)
                logger.info("[Startup] Scheduler started successfully")
            except Exception:
                logger.exception("[Startup] Scheduler start_continuous failed")

        _scheduler_task = asyncio.create_task(_start_scheduler())
        _scheduler_task.set_name("scheduler-start")
        _background_tasks.add(_scheduler_task)
        _scheduler_task.add_done_callback(_background_tasks.discard)

        # Auto-start the server-side Polymarket position recorder. Pure HTTP
        # (public data-api, wallet-keyed) — runs 24/7 independent of the local
        # betty.bat client so manually-placed Polymarket bets are recorded
        # within ~1.5 min instead of waiting on the local 5-min auto-poller.
        async def _start_position_recorder():
            try:
                from ..recorders.server_poller import run_position_recorder

                await run_position_recorder()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[Startup] position recorder crashed")

        _position_recorder_task = asyncio.create_task(_start_position_recorder())
        _position_recorder_task.set_name("position-recorder")
        _background_tasks.add(_position_recorder_task)
        _position_recorder_task.add_done_callback(_background_tasks.discard)
    else:
        logger.info("[Startup] Scheduler disabled (ARNOLD_NO_SCHEDULER set)")

    # Phase 4 (2026-05-08): mirror health smoke-test loop. Runs every
    # MIRROR_SMOKE_INTERVAL_S (default 24h), HTTP-probes each provider's
    # home_url + recomputes event-derived health from `mirror_event_log`.
    try:
        from ..jobs.mirror_smoke import smoke_loop

        asyncio.create_task(smoke_loop(), name="mirror_smoke_loop")
        logger.info("[lifespan] mirror_smoke_loop scheduled")
    except Exception as e:
        logger.error("mirror_smoke startup failed: %s", e, exc_info=True)

    yield  # App is running

    logger.info("Shutting down...")

    # Stop all mirrors
    from .routes.mirror import _mirrors

    for pid in list(_mirrors.keys()):
        try:
            mirror = _mirrors.pop(pid)
            await mirror.stop()
        except Exception as e:
            logger.warning(f"Mirror stop failed for {pid}: {e}")

    if not _mirror_only:
        try:
            from ..pipeline.scheduler import get_scheduler

            get_scheduler().stop_all()
        except Exception:
            pass

    logger.info("Shutdown complete.")


app = FastAPI(
    title="Betty API",
    description="Betting analytics & value betting backend",
    version="0.1.0",
    lifespan=lifespan,
)


# GZip compression for responses > 1KB
app.add_middleware(GZipMiddleware, minimum_size=1000)


# App-level API key auth — defense-in-depth behind nginx basic auth
_api_key = os.environ.get("ARNOLD_API_KEY")
_auth_exempt = {"/health", "/health/live", "/health/ready", "/health/extraction"}


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    if _api_key and request.url.path not in _auth_exempt:
        # Skip if request already passed nginx basic auth
        passed_nginx = request.headers.get("X-Nginx-Authenticated")
        if not passed_nginx:
            provided = request.headers.get("X-API-Key")
            if provided != _api_key:
                return JSONResponse(status_code=401, content={"error": "Invalid or missing API key"})
    return await call_next(request)


# Cache-Control for GET API responses — lets the browser skip redundant fetches
@app.middleware("http")
async def cache_control_middleware(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if request.method == "GET" and path.startswith("/api/") and "/stream" not in path:
        # Short private cache — browser can reuse within window, must revalidate after
        response.headers.setdefault("Cache-Control", "private, max-age=5")
    return response


# Allow CORS for frontend
_default_origins = "http://localhost:5173,http://localhost:5174,http://localhost:3000,tauri://localhost"
_cors_origins = os.environ.get("CORS_ORIGINS", _default_origins).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
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
    """Basic health check endpoint with boot ID for restart detection."""
    return {
        "status": "ok",
        "time": datetime.now(UTC).isoformat(),
        "boot_id": _boot_id,
        "uptime": round(time.time() - _startup_time) if _startup_time else 0,
    }


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
    from ..db.models import Provider
    from .deps import get_db

    status = "ready"
    database_ok = False
    db_latency_ms = 0.0
    providers_available = 0
    providers_total = 0

    # Check database connectivity (run in thread to avoid blocking event loop)
    def _check_db():
        db = None
        try:
            db = next(get_db())
            providers = db.query(Provider).all()
            total = len(providers)
            available = sum(1 for p in providers if p.is_enabled)
            return True, total, available
        except Exception:
            return False, 0, 0
        finally:
            if db:
                db.close()

    try:
        db_start = time.time()
        database_ok, providers_total, providers_available = await asyncio.wait_for(
            asyncio.to_thread(_check_db), timeout=5.0
        )
        db_latency_ms = (time.time() - db_start) * 1000
    except (TimeoutError, Exception):
        status = "not_ready"
        database_ok = False

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


def _compute_unscannable_markets(odds_rows: list[dict]) -> int:
    """Count (event_id, market, point) triples where Pinnacle has a
    non-canonical-scope row AND no other provider has the canonical-scope row.

    These are markets where we have a sharp baseline at the wrong scope for
    the sport — they silently drop out of opportunity scanning. Surface them
    here so a creeping data-quality regression is visible.
    """
    from collections import defaultdict

    from ..constants import canonical_scope_for

    # Group by (event_id, market, point)
    by_key: dict[tuple, list[dict]] = defaultdict(list)
    for r in odds_rows:
        by_key[(r["event_id"], r["market"], r.get("point"))].append(r)

    count = 0
    for (_event_id, _market, _point), rows in by_key.items():
        # Find the sport from any row in the group (all share the event)
        sport = rows[0].get("sport")
        canonical = canonical_scope_for(sport)

        # Does Pinnacle have a non-canonical scope row?
        has_pinnacle_noncanonical = any(
            r["provider_id"] == "pinnacle" and r.get("scope", "ft") != canonical for r in rows
        )
        # Does anyone have a canonical-scope row?
        has_canonical = any(r.get("scope", "ft") == canonical for r in rows)

        if has_pinnacle_noncanonical and not has_canonical:
            count += 1

    return count


@app.get("/health/extraction")
async def health_extraction():
    """Public extraction health endpoint — no auth required.

    Deep health assessment: checks sharp source freshness, consecutive
    provider failures, staleness vs expected intervals, DB integrity
    errors, and opportunity volume drops.
    """
    from ..db.models import ExtractionRun, ProviderRunMetrics
    from ..pipeline.health import assess_extraction_health, get_provider_intervals
    from .deps import get_db

    def _query():
        db = None
        try:
            db = next(get_db())

            # ── Deep health assessment ──
            intervals = get_provider_intervals()
            health_status, issues, providers_health = assess_extraction_health(db, intervals)

            # ── Last 3 runs for the response body ──
            runs = db.query(ExtractionRun).order_by(ExtractionRun.start_time.desc()).limit(3).all()
            run_data = []
            for run in runs:
                providers = db.query(ProviderRunMetrics).filter(ProviderRunMetrics.run_id == run.id).all()
                failed = [
                    {"provider": p.provider_id, "error": (p.error_message or "")[:200], "status": p.status}
                    for p in providers
                    if p.status in ("failed", "timeout")
                ]
                low_match = [
                    {
                        "provider": p.provider_id,
                        "matched": p.events_matched or 0,
                        "unmatched": p.events_unmatched or 0,
                        "match_rate": round(
                            (p.events_matched or 0) / max((p.events_matched or 0) + (p.events_unmatched or 0), 1) * 100
                        ),
                    }
                    for p in providers
                    if (p.events_matched or 0) + (p.events_unmatched or 0) > 0
                    and (p.events_matched or 0) / max((p.events_matched or 0) + (p.events_unmatched or 0), 1) < 0.3
                ]
                run_data.append(
                    {
                        "id": run.id,
                        "start_time": run.start_time.isoformat() if run.start_time else None,
                        "duration_seconds": run.duration_seconds,
                        "trigger": run.trigger,
                        "providers_attempted": run.providers_attempted,
                        "providers_succeeded": run.providers_succeeded,
                        "providers_failed": run.providers_failed,
                        "total_events": run.total_events,
                        "total_odds": run.total_odds,
                        "failed_providers": failed,
                        "low_match_rate": low_match,
                    }
                )

            # ── Trust-gate counters ──
            from sqlalchemy import text

            n_phantom_value = (
                db.execute(
                    text("SELECT COUNT(*) FROM opportunities WHERE is_active=true AND type='value' AND edge_pct > 10")
                ).scalar()
                or 0
            )
            n_phantom_arb = (
                db.execute(
                    text("SELECT COUNT(*) FROM opportunities WHERE is_active=true AND type='arb' AND profit_pct > 5")
                ).scalar()
                or 0
            )
            n_unvalidated_events = (
                db.execute(
                    text(
                        "SELECT COUNT(*) FROM events "
                        "WHERE home_away_validated = false "
                        "AND start_time > NOW() AND start_time < NOW() + INTERVAL '24 hours'"
                    )
                ).scalar()
                or 0
            )
            n_active_total = db.execute(text("SELECT COUNT(*) FROM opportunities WHERE is_active=true")).scalar() or 0

            # ── Unscannable markets (scope mismatch) ──
            odds_rows = (
                db.execute(
                    text(
                        "SELECT o.event_id, o.provider_id, e.sport, o.market, o.point, o.scope "
                        "FROM odds o JOIN events e ON e.id = o.event_id "
                        "WHERE e.start_time > NOW() AND e.start_time < NOW() + INTERVAL '24 hours'"
                    )
                )
                .mappings()
                .all()
            )
            unscannable = _compute_unscannable_markets(list(odds_rows))

            return {
                "status": health_status,
                "issues": issues,
                "providers": providers_health,
                "runs": run_data,
                "phantom_value_count": int(n_phantom_value),
                "phantom_arb_count": int(n_phantom_arb),
                "unvalidated_events_24h": int(n_unvalidated_events),
                "active_opportunities_total": int(n_active_total),
                "trust_gates_status": "WARNING" if (n_phantom_value + n_phantom_arb) > 5 else "OK",
                "unscannable_markets": unscannable,
                "unscannable_markets_status": "WARNING" if unscannable > 10 else "OK",
            }
        except Exception as e:
            return {"error": str(e)}
        finally:
            if db:
                db.close()

    # Run on a dedicated executor so health probes don't compete with the
    # default asyncio executor, which is heavily used by per-sport storage
    # threads during extraction and can saturate the 8-thread default pool.
    try:
        data = await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(_health_executor, _query),
            timeout=15.0,
        )
    except TimeoutError:
        return {"status": "error", "message": "Database query timed out"}

    if isinstance(data, dict) and "error" in data:
        return {"status": "error", "message": data["error"]}

    data["checked_at"] = datetime.now(UTC).isoformat()
    return data


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
# app.include_router(specials_router)  # DISABLED — boosts/specials turned off
app.include_router(settings_router)
app.include_router(limits_router)
app.include_router(mirror_router)
app.include_router(mirror_state_router)
app.include_router(mirror_stream_router)
app.include_router(fire_window_router)
app.include_router(slip_odds_router)


# Version endpoint
@app.get("/api/version")
async def get_version():
    """Return app version and runtime info."""
    from ..paths import get_data_dir

    return {
        "version": app.version,
        "data_dir": str(get_data_dir()),
    }


@app.get("/")
async def root():
    """Backend is API-only. The local betty client renders the UI."""
    return {"status": "betty-api", "version": app.version}


# Dev entry point (no --reload). On Windows, --reload forces SelectorEventLoop
# which breaks patchright subprocess spawning. Without --reload, uvicorn uses
# ProactorEventLoop correctly. Use run_dev.py if you need hot-reload.
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.api:app", host="127.0.0.1", port=8000)
