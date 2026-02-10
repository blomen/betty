"""
OddOpp FastAPI Backend

REST API for the React frontend.
Connects to SQLite database and analysis modules.
"""

import time
from datetime import datetime

from dotenv import load_dotenv

# Load .env from user data directory (AppData in bundled mode, backend/ in dev)
from ..paths import get_env_path
load_dotenv(get_env_path())

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..db.models import init_db
from .state import ws_manager
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
)

app = FastAPI(
    title="OddOpp API",
    description="Polymarket arbitrage & value betting backend",
    version="0.1.0",
)

# Allow CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174", "http://localhost:3000", "tauri://localhost"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Track startup time for uptime calculation
_startup_time: float = 0.0


# Initialize database on startup and auto-start extraction
@app.on_event("startup")
async def startup():
    global _startup_time
    _startup_time = time.time()
    init_db()

    # Auto-start continuous extraction (every 5 min, Pinnacle + Polymarket)
    from ..pipeline.scheduler import get_scheduler
    scheduler = get_scheduler()
    await scheduler.start_continuous(interval_seconds=300)


# Health check endpoints
@app.get("/health")
async def health():
    """Basic health check endpoint."""
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


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
