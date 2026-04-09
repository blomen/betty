"""FirevSports local server — thin proxy + mirror browser control + static frontend."""
import logging
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from proxy import create_proxy_router
from mirror.browser import MirrorBrowser
from mirror.router import create_mirror_router
from mirror.sse import mirror_broadcaster

logger = logging.getLogger(__name__)

TUNNEL_URL = os.environ.get("FIREVSPORTS_TUNNEL_URL", "http://localhost:18000")
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend", "dist")

app = FastAPI(title="FirevSports", docs_url=None, redoc_url=None)

# Mirror browser (singleton)
browser = MirrorBrowser()

# Mount mirror control endpoints (must be before proxy to avoid /mirror/* being caught by /api/*)
app.include_router(create_mirror_router(browser, mirror_broadcaster, TUNNEL_URL))

# Mount API proxy
app.include_router(create_proxy_router(TUNNEL_URL))

# Serve frontend static files (must be last)
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")


@app.on_event("startup")
async def startup():
    logger.info(f"FirevSports starting — tunnel: {TUNNEL_URL}")
    try:
        await browser.start()
    except Exception:
        logger.warning("Mirror browser auto-start failed — start manually via /mirror/start")


@app.on_event("shutdown")
async def shutdown():
    await browser.stop()
    logger.info("FirevSports stopped")
