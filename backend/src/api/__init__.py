"""
BankrollBBQ FastAPI Backend

REST API for the React frontend.
Connects to SQLite database and analysis modules.
"""

import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv

# Load .env from user data directory (AppData in bundled mode, backend/ in dev)
from ..paths import get_env_path
load_dotenv(get_env_path())

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..db.models import init_db
from .state import ws_manager, recorder_ws_manager
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
    placement_router,
    trading_router,
    recorder_router,
)

logger = logging.getLogger(__name__)

# Track startup time for uptime calculation
_startup_time: float = 0.0


def _startup_purge():
    """Clear all extracted data on startup. Preserves user data (bets, profiles, balances).

    Events linked to bets are kept (needed for history). All other events, odds,
    opportunities, specials, and live status flags are wiped so re-extraction
    starts fresh.
    """
    from ..db.models import Event, Odds, Opportunity, Bet, get_session

    session = get_session()
    try:
        # Find events that have bets — these must be preserved
        event_ids_with_bets = set(
            row[0] for row in session.query(Bet.event_id).filter(
                Bet.event_id.isnot(None)
            ).distinct().all()
            if row[0]
        )

        # Get ALL event IDs
        all_event_ids = [
            row[0] for row in session.query(Event.id).all()
        ]
        deletable_ids = [eid for eid in all_event_ids if eid not in event_ids_with_bets]

        # 1. Delete all opportunities
        opps_deleted = session.query(Opportunity).delete()

        # 2. Delete all specials
        from ..db.models import SpecialOdds
        specials_deleted = session.query(SpecialOdds).delete()

        # 3. Delete odds + events that have no bets (batched)
        odds_deleted = 0
        events_deleted = 0
        for i in range(0, len(deletable_ids), 500):
            batch = deletable_ids[i:i + 500]
            odds_deleted += session.query(Odds).filter(
                Odds.event_id.in_(batch)
            ).delete(synchronize_session=False)
            events_deleted += session.query(Event).filter(
                Event.id.in_(batch)
            ).delete(synchronize_session=False)

        # 4. For bet-linked events: clear live status (stale from last session)
        #    but keep the event row + odds for history/CLV
        if event_ids_with_bets:
            session.query(Event).filter(
                Event.id.in_(list(event_ids_with_bets))
            ).update({
                Event.match_status: None,
                Event.match_minute: None,
                Event.match_period: None,
                Event.stats_json: None,
            }, synchronize_session=False)

        # 5. Delete odds for bet-linked events from non-sharp providers
        #    (keep Pinnacle odds for CLV reference)
        if event_ids_with_bets:
            from ..constants import SHARP_PROVIDERS
            session.query(Odds).filter(
                Odds.event_id.in_(list(event_ids_with_bets)),
                ~Odds.provider_id.in_(SHARP_PROVIDERS),
            ).delete(synchronize_session=False)

        session.commit()
        logger.info(
            f"[Startup] Purged extracted data: "
            f"{events_deleted} events, {odds_deleted} odds, "
            f"{opps_deleted} opportunities, {specials_deleted} specials "
            f"({len(event_ids_with_bets)} bet-linked events preserved)"
        )
    except Exception as e:
        session.rollback()
        logger.error(f"[Startup] Purge failed: {e}", exc_info=True)
    finally:
        session.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown logic."""
    global _startup_time
    _startup_time = time.time()
    init_db()

    # Purge stale extracted data — fresh re-extraction on every startup
    _startup_purge()

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
    logging.getLogger().addHandler(extraction_handler)

    # Auto-start continuous extraction (every 5 min, Pinnacle + Polymarket)
    from ..pipeline.scheduler import get_scheduler
    scheduler = get_scheduler()
    await scheduler.start_continuous(interval_seconds=300)

    yield  # App is running

    # Graceful shutdown: stop all scheduler tiers
    logger.info("Shutting down: stopping scheduler tiers...")
    scheduler.stop_all()
    logger.info("Scheduler stopped.")


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
app.include_router(placement_router)
app.include_router(trading_router)
app.include_router(recorder_router)


# WebSocket endpoint for extraction progress (legacy path)
@app.websocket("/ws/extraction")
async def websocket_extraction_progress(websocket: WebSocket):
    """WebSocket endpoint for real-time extraction progress."""
    await ws_manager.connect(websocket)

    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


# WebSocket endpoint for recorder live feed
@app.websocket("/ws/recorder")
async def websocket_recorder(websocket: WebSocket):
    """WebSocket endpoint for real-time recording action feed."""
    await recorder_ws_manager.connect(websocket)

    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        recorder_ws_manager.disconnect(websocket)


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


# Entry point for development
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.api:app", host="0.0.0.0", port=8000, reload=True)
