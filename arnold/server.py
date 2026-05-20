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
from mirror.poly_live_poller import run_poly_live_poller  # noqa: E402
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

    # Reset stale runner_state rows on launch — a previous arnold.bat that
    # died abnormally (Ctrl+C, kill, OOM) leaves "ready_to_run"/"running" in
    # `mirror_runner_state`. The frontend's recovery effect would re-show
    # those as active providers in this fresh session, even though no runner
    # exists in-memory. Clear the slate; runners will write fresh state as
    # they actually start this session.
    asyncio.create_task(_reset_stale_runner_state(), name="reset-runner-state")

    # Polymarket Gamma API live-odds poller — extraction runs every 10 min so
    # cached odds drift; this polls the top-N polymarket value candidates
    # every 30s and broadcasts live_price SSE so PlayPage rows stay fresh.
    asyncio.create_task(run_poly_live_poller(), name="poly-live-poll")

    # Periodic auto-poller for ALL API-based recorders (polymarket + kalshi):
    # every 5 min, hits /mirror/sync-positions for each, which runs the full
    # insert + settle cycle. Replaces the need for user to click "sync" — open
    # positions enter the DB and settled ones close themselves continuously,
    # no Playwright tab required.
    from mirror.recorders.auto_poller import run_auto_poller as _run_auto_poller

    asyncio.create_task(_run_auto_poller(), name="recorder-auto-poll")


async def _reset_stale_runner_state() -> None:
    """Clear stale mirror_runner_state rows on every arnold.bat launch.

    The previous session may have died without writing state='idle' (Ctrl+C,
    kill, OOM, exception inside the runner), leaving "ready_to_run" or
    "running" rows in the DB. The frontend recovery effect uses those rows
    to refine card state for already-active providers; if they're stale a
    page reload would reactivate phantom runners. Clearing on startup gives
    a clean slate — runners overwrite as they actually start this session.

    Uses the same fire-and-forget tunnel POSTs as state_writer. Failures are
    swallowed (server may be momentarily down; the recovery effect already
    tolerates stale data because it gates on activeProviders membership).
    """
    try:
        from mirror.state_writer import write_runner_state

        from arnold.http_client import tunnel_client

        # Brief grace so the tunnel + server are ready to accept POSTs.
        await asyncio.sleep(2)
        client = tunnel_client()
        try:
            r = await client.get("/api/mirror/state", timeout=10.0)
            if r.status_code != 200:
                logger.debug(f"[reset-runner-state] GET /api/mirror/state status={r.status_code}")
                return
            payload = r.json()
        except Exception as e:
            logger.debug(f"[reset-runner-state] GET failed: {e!r}")
            return
        runners = payload.get("runners", []) or []
        cleared = 0
        for row in runners:
            pid = row.get("provider_id")
            state = row.get("state")
            if not pid or not state or state in ("idle", "none"):
                continue
            write_runner_state(pid, state="idle")
            cleared += 1
        if cleared:
            logger.info(f"[reset-runner-state] cleared {cleared} stale runner rows")
    except Exception:
        logger.exception("[reset-runner-state] unexpected error")


async def _auto_open_tradingview() -> None:
    """Ensure a TradingView NQ chart tab is open in the mirror. Idempotent:
    if a TV tab already exists (live, http(s), and not closed), no new tab
    is created.

    Print to stdout with flush=True so arnold.bat's cmd window surfaces
    failures (uvicorn's log_level=warning swallows logger.info). Persistent
    retry: a finite backoff schedule was giving up before the Chromium
    profile lock cleared, leaving the chart silently absent until the user
    noticed. Now we keep retrying with a capped backoff until the tab is
    open or the lifespan teardown cancels us.
    """
    url = "https://www.tradingview.com/chart/?symbol=CME_MINI%3ANQ1!"
    backoff = [3, 6, 12, 30, 60]
    attempt = 0
    while True:
        attempt += 1
        delay = backoff[min(attempt - 1, len(backoff) - 1)]
        try:
            await asyncio.sleep(delay)
            print(f"[tv-overlay] auto-open attempt {attempt}", flush=True)
            if not browser.running:
                await browser.start()
                print("[tv-overlay] mirror browser started", flush=True)
            # Idempotency check — reuse existing TV tab only if it's a real
            # live page. A closed or about:blank page lingering in
            # context.pages must NOT short-circuit re-open (same class of
            # bug as the cloudbet "already" check in /mirror/start).
            already_url: str | None = None
            if browser.context:
                for p in browser.context.pages:
                    try:
                        if p.is_closed():
                            continue
                        purl = (p.url or "").lower()
                        if not purl.startswith(("http://", "https://")):
                            continue
                        if "tradingview.com" in purl:
                            already_url = p.url
                            break
                    except Exception:
                        continue
            if already_url:
                print(f"[tv-overlay] TV tab already open: {already_url}", flush=True)
                return
            page = await browser.open_tab(url)
            print(f"[tv-overlay] opened TV tab: {page.url}", flush=True)
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(
                f"[tv-overlay] auto-open attempt {attempt} failed: {type(exc).__name__}: {exc}",
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
    from arnold.http_client import local_client, tunnel_client

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
            local = local_client()
            tunnel = tunnel_client()
            # Skip if a polymarket runner is already active. /play/status
            # returns providers.polymarket.state == 'navigating'/'ready'/
            # 'placing'/etc. when a runner is alive.
            pstatus = await local.get("/mirror/play/status", timeout=10.0)
            if pstatus.status_code == 200:
                pdata = pstatus.json()
                poly_runner = (pdata.get("providers") or {}).get("polymarket") or {}
                if poly_runner.get("state") and poly_runner.get("state") not in ("idle", "none", None):
                    continue  # already running
            # Fetch fresh batch directly through the tunnel — skip the
            # local /api proxy hop.
            bresp = await tunnel.post("/api/opportunities/play/batch", json={}, timeout=10.0)
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
            sresp = await local.post("/mirror/play/start", json=start_payload, timeout=10.0)
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

    # Close pooled httpx clients (tunnel + local self-call + proxy pool).
    try:
        from proxy import close_proxy_clients

        from arnold.http_client import close_all as _close_clients

        await _close_clients()
        await close_proxy_clients()
    except Exception:
        logger.exception("HTTP client shutdown raised")
    logger.info("Arnold stopped")
