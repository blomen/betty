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
    # Stash on app.state so the overlay router can expose its snapshot.
    app.state.overlay_broadcaster = broadcaster
    _overlay_task = asyncio.create_task(broadcaster.loop(), name="tv-overlay-broadcaster")
    logger.info("TV overlay broadcaster started")

    # Eagerly start the mirror Chromium and auto-open TradingView so the
    # overlay extension attaches without the user having to click anything.
    # Sportsbook flows continue to use the same mirror — they share tabs.
    asyncio.create_task(_auto_open_tradingview(), name="tv-mirror-autoopen")

    # Server-side play-loop autostart: polls every 30s and kicks off the
    # polymarket runner whenever it's not running AND polymarket is logged in
    # AND there are +EV opportunities. This makes "always on" robust to React
    # UI being closed, the runner exiting after queue drain, or anything else
    # that would otherwise leave the loop idle.
    asyncio.create_task(_auto_start_play_loop(), name="play-loop-autostart")

    # SSH tunnel watchdog lives in launch.py (background thread, 6-fail
    # tolerance over ~2 min, /health/live probe). Don't double up here —
    # racing two watchdogs causes thrash + duplicate respawn attempts.


async def _auto_open_tradingview() -> None:
    """Ensure a TradingView NQ chart tab is open in the mirror. Idempotent:
    if a TV tab already exists (from a previous launch's persistent profile
    or from a prior auto-open), no new tab is created.

    Print to stdout with flush=True so arnold.bat's cmd window surfaces
    failures (uvicorn's log_level=warning swallows logger.info). Long
    backoff schedule because Chrome profile-lock takes 30-60s to clear
    when a previous arnold session crashed.
    """
    url = "https://www.tradingview.com/chart/?symbol=CME_MINI%3ANQ1!"
    delays = [3, 6, 12, 30, 60]  # extended backoff
    for attempt, delay in enumerate(delays, 1):
        try:
            await asyncio.sleep(delay)
            print(f"[tv-overlay] auto-open attempt {attempt}/{len(delays)}", flush=True)
            if not browser.running:
                await browser.start()
                print("[tv-overlay] mirror browser started", flush=True)
            # Idempotency check — reuse existing TV tab if any.
            if browser.context:
                for p in browser.context.pages:
                    try:
                        if "tradingview.com" in (p.url or ""):
                            print(f"[tv-overlay] TV tab already open: {p.url}", flush=True)
                            return
                    except Exception:
                        continue
            page = await browser.open_tab(url)
            print(f"[tv-overlay] opened TV tab: {page.url}", flush=True)
            return
        except Exception as exc:
            print(
                f"[tv-overlay] auto-open attempt {attempt} failed: {type(exc).__name__}: {exc}",
                flush=True,
            )
            if attempt == len(delays):
                logger.exception("Auto-open TradingView failed after %d attempts", len(delays))
                print(
                    "[tv-overlay] auto-open giving up. POST /mirror/tv-open or click 'Open TV in mirror' to retry.",
                    flush=True,
                )


async def _auto_start_play_loop() -> None:
    """Background task: ensures the polymarket play loop is always running
    when polymarket is logged in and has +EV opportunities.

    Polls every 30s. Kicks off the loop via the same /mirror/play/start API
    the React UI uses, so the runner gets the latest batch + balance and
    auto-restarts after queue drains, runner exits, or React UI is closed.

    Idempotent: skips if play loop is already running for polymarket.
    Survives transient errors (tunnel hiccups, batch fetch failures) and
    retries on the next 30s tick.
    """
    import httpx

    POLL_INTERVAL = 30.0
    while True:
        try:
            await asyncio.sleep(POLL_INTERVAL)
            if not browser.running or not browser.context:
                continue
            # Check polymarket login state via the existing internal cache.
            poly_data = browser.provider_data.get("polymarket", {})
            if not poly_data.get("logged_in"):
                continue
            balance = poly_data.get("balance") or 0.0
            if balance <= 0:
                continue
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Skip if a polymarket runner is already active. /play/status
                # returns providers.polymarket.state == 'navigating'/'ready'/
                # 'placing'/etc. when a runner is alive.
                pstatus = await client.get("http://127.0.0.1:8000/mirror/play/status")
                if pstatus.status_code == 200:
                    pdata = pstatus.json()
                    poly_runner = (pdata.get("providers") or {}).get("polymarket") or {}
                    if poly_runner.get("state") and poly_runner.get("state") not in ("idle", "none", None):
                        continue  # already running
                # Fetch fresh batch (same path React uses).
                bresp = await client.post(
                    "http://127.0.0.1:8000/api/opportunities/play/batch",
                    json={},
                )
                if bresp.status_code != 200:
                    continue
                bdata = bresp.json()
                full_batch = bdata.get("batch") or []
                poly_bets = [b for b in full_batch if b.get("provider_id") == "polymarket"]
                if not poly_bets:
                    continue
                start_payload = {
                    "provider_ids": ["polymarket"],
                    "batch": poly_bets,
                    "balances": {"polymarket": balance},
                }
                sresp = await client.post(
                    "http://127.0.0.1:8000/mirror/play/start",
                    json=start_payload,
                )
                if sresp.status_code == 200:
                    print(
                        f"[play-autostart] kicked off polymarket runner — "
                        f"{len(poly_bets)} bets queued, balance ${balance:.2f}",
                        flush=True,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("play-loop autostart tick raised — will retry")


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
