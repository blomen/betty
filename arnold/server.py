"""Arnold local server — unified sports mirror + stocks runtime + static frontend.

Serves one FastAPI process on port 8000:
- /mirror/*            → Playwright browser control (sports)
- /api/*               → reverse proxy to Hetzner server API (sports)
- /stocks/api/*        → TopstepX + zone/signal dashboard (stocks)
- /stocks/ws/dashboard → live dashboard WebSocket (stocks)
- /                    → unified React frontend static assets
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_env_local = Path(__file__).parent / ".env.local"
if _env_local.exists():
    load_dotenv(_env_local)

_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# Make `arnold` package importable (parent of this file's directory)
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Pick up TopstepX + other shared credentials from backend/.env (gitignored)
# Loaded after .env.local so .env.local can still override.
_backend_env = _BACKEND_DIR / ".env"
if _backend_env.exists():
    load_dotenv(_backend_env, override=False)

from fastapi import FastAPI  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from mirror import stream_registry  # noqa: E402
from mirror.browser import MirrorBrowser  # noqa: E402
from mirror.router import create_mirror_router  # noqa: E402
from mirror.sse import mirror_broadcaster  # noqa: E402
from proxy import create_proxy_router  # noqa: E402
from stocks_runtime import bootstrap_stocks  # noqa: E402

from arnold.tv_overlay.broadcaster import OverlayBroadcaster  # noqa: E402
from arnold.tv_overlay.router import broadcast as overlay_broadcast  # noqa: E402
from arnold.tv_overlay.router import create_router as create_overlay_router  # noqa: E402
from src.stocks.dashboard import create_dashboard_router  # noqa: E402

for _name in ("httpx", "httpcore", "playwright", "urllib3", "asyncio"):
    logging.getLogger(_name).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

TUNNEL_URL = os.environ.get("ARNOLD_TUNNEL_URL") or os.environ.get("ARNOLDSPORTS_TUNNEL_URL", "http://localhost:18000")
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend", "dist")

app = FastAPI(title="Arnold", docs_url=None, redoc_url=None)

browser = MirrorBrowser()
browser.set_event_callback(mirror_broadcaster.publish)


def _dispatch_to_stream(provider_id: str, event_type: str, data: dict):
    stream = stream_registry.get(provider_id)
    if not stream:
        return
    if event_type == "balance_intercepted":
        stream.on_balance_intercepted(data["balance"])
    elif event_type == "bet_intercepted":
        stream.on_placement_intercepted(data["body"])
    elif event_type == "history_intercepted":
        stream.on_history_intercepted(data.get("url", ""), data.get("body", ""))


browser.set_stream_callback(_dispatch_to_stream)

_stocks_runtime = None
_overlay_task: asyncio.Task | None = None

# Mount order matters: more specific prefixes before the /api/* proxy catchall
app.include_router(create_mirror_router(browser, mirror_broadcaster, TUNNEL_URL))
app.include_router(create_dashboard_router(), prefix="/stocks")
app.include_router(create_overlay_router(), prefix="/stocks")
app.include_router(create_proxy_router(TUNNEL_URL))

if os.path.isdir(FRONTEND_DIR):
    _static = StaticFiles(directory=FRONTEND_DIR, html=True)

    @app.middleware("http")
    async def no_cache_html(request, call_next):
        response = await call_next(request)
        ct = response.headers.get("content-type", "")
        if "text/html" in ct:
            response.headers["cache-control"] = "no-cache, no-store, must-revalidate"
        return response

    app.mount("/", _static, name="static")


@app.on_event("startup")
async def startup():
    global _stocks_runtime, _overlay_task
    logger.info("Arnold starting — tunnel: %s", TUNNEL_URL)
    try:
        _stocks_runtime = await bootstrap_stocks()
        if _stocks_runtime:
            logger.info("Arnold stocks runtime active")
        else:
            logger.info("Arnold stocks runtime disabled (TopstepX not configured or auth failed)")
    except Exception:
        logger.exception("Stocks bootstrap raised — continuing sports-only")
        _stocks_runtime = None

    broadcaster = OverlayBroadcaster(emit=overlay_broadcast)
    _overlay_task = asyncio.create_task(broadcaster.loop(), name="tv-overlay-broadcaster")
    logger.info("TV overlay broadcaster started")


@app.on_event("shutdown")
async def shutdown():
    global _overlay_task
    if _overlay_task is not None:
        _overlay_task.cancel()
        try:
            await _overlay_task
        except (asyncio.CancelledError, Exception):
            pass
        _overlay_task = None

    if _stocks_runtime is not None:
        try:
            await _stocks_runtime.shutdown()
        except Exception:
            logger.exception("Stocks shutdown raised")
    try:
        await browser.stop()
    except Exception:
        logger.exception("Mirror browser stop raised")
    logger.info("Arnold stopped")
