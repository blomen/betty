"""FirevSports local server — thin proxy + mirror browser control + static frontend."""

import logging
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from mirror import stream_registry
from mirror.browser import MirrorBrowser
from mirror.router import create_mirror_router
from mirror.sse import mirror_broadcaster
from proxy import create_proxy_router

# Quiet noisy loggers — only warnings+
for _name in ("httpx", "httpcore", "playwright", "urllib3", "asyncio"):
    logging.getLogger(_name).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

TUNNEL_URL = os.environ.get("FIREVSPORTS_TUNNEL_URL", "http://localhost:18000")
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend", "dist")

app = FastAPI(title="FirevSports", docs_url=None, redoc_url=None)

# Mirror browser (singleton) — with event interception wired to SSE
browser = MirrorBrowser()
browser.set_event_callback(mirror_broadcaster.publish)


def _dispatch_to_stream(provider_id: str, event_type: str, data: dict):
    """Route intercepted events to the active ProviderDataStream (if any)."""
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

# Mount mirror control endpoints (must be before proxy to avoid /mirror/* being caught by /api/*)
app.include_router(create_mirror_router(browser, mirror_broadcaster, TUNNEL_URL))

# Mount API proxy
app.include_router(create_proxy_router(TUNNEL_URL))

# Serve frontend static files with no-cache on HTML (must be last)
if os.path.isdir(FRONTEND_DIR):
    _static = StaticFiles(directory=FRONTEND_DIR, html=True)

    @app.middleware("http")
    async def no_cache_html(request, call_next):
        response = await call_next(request)
        # No-cache on HTML — forces browser to always fetch fresh index.html
        # (JS/CSS have content hashes in filenames so they self-bust)
        ct = response.headers.get("content-type", "")
        if "text/html" in ct:
            response.headers["cache-control"] = "no-cache, no-store, must-revalidate"
        return response

    app.mount("/", _static, name="static")


@app.on_event("startup")
async def startup():
    logger.info(f"FirevSports starting — tunnel: {TUNNEL_URL}")
    # Browser starts empty — tabs open only when user selects a provider and clicks Start


@app.on_event("shutdown")
async def shutdown():
    await browser.stop()
    logger.info("FirevSports stopped")
