"""Betty local server — sports mirror + static frontend.

Serves one FastAPI process on port 8000:
- /mirror/*  → Playwright browser control (sports)
- /api/*     → reverse proxy to Hetzner server API
- /          → React frontend static assets
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

# Make `local` package importable (parent of this file's directory)
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Pick up shared credentials from backend/.env (gitignored)
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

for _name in ("httpx", "httpcore", "playwright", "urllib3", "asyncio"):
    logging.getLogger(_name).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

TUNNEL_URL = (
    os.environ.get("BETTY_TUNNEL_URL")
    or os.environ.get("ARNOLD_TUNNEL_URL")
    or os.environ.get("ARNOLDSPORTS_TUNNEL_URL", "http://localhost:18000")
)
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")

app = FastAPI(title="Betty", docs_url=None, redoc_url=None)

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

# Mount order matters: mirror routes first, then /api/* proxy catchall
app.include_router(create_mirror_router(browser, mirror_broadcaster, TUNNEL_URL))
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
    logger.info("Betty starting — tunnel: %s", TUNNEL_URL)

    # Server-side play-loop autostart: polls every 30s and kicks off the
    # polymarket runner whenever it's not running AND polymarket is logged in
    # AND there are +EV opportunities.
    asyncio.create_task(_auto_start_play_loop(), name="play-loop-autostart")

    # Reset stale runner_state rows on launch — a previous betty.bat that
    # died abnormally leaves "ready_to_run"/"running" in `mirror_runner_state`.
    asyncio.create_task(_reset_stale_runner_state(), name="reset-runner-state")

    # Polymarket Gamma API live-odds poller — extraction runs every 10 min so
    # cached odds drift; this polls the top-N polymarket value candidates
    # every 30s and broadcasts live_price SSE so PlayPage rows stay fresh.
    asyncio.create_task(run_poly_live_poller(), name="poly-live-poll")

    # Periodic auto-poller for the local API-based recorders (kalshi +
    # cookie-based pinnacle/cloudbet): every 5 min, hits /mirror/sync-positions.
    from mirror.recorders.auto_poller import run_auto_poller as _run_auto_poller

    asyncio.create_task(_run_auto_poller(), name="recorder-auto-poll")


async def _reset_stale_runner_state() -> None:
    """Clear stale mirror_runner_state rows on every betty.bat launch."""
    try:
        from mirror.state_writer import write_runner_state

        from local.http_client import tunnel_client

        await asyncio.sleep(2)
        client = tunnel_client()
        try:
            r = await client.get("/api/mirror/state", timeout=10.0)
            if r.status_code != 200:
                logger.debug(
                    f"[reset-runner-state] GET /api/mirror/state status={r.status_code}"
                )
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


async def _auto_start_play_loop() -> None:
    """Background task: ensures the polymarket play loop is always running
    when polymarket is logged in and has +EV opportunities."""
    from local.http_client import local_client, tunnel_client

    POLL_INTERVAL = 30.0
    while True:
        try:
            await asyncio.sleep(POLL_INTERVAL)
            if not browser.running or not browser.context:
                continue
            poly_data = browser.provider_data.get("polymarket", {})
            if not poly_data.get("logged_in"):
                continue
            balance = poly_data.get("balance") or 0.0
            if balance <= 0:
                continue
            local = local_client()
            tunnel = tunnel_client()
            pstatus = await local.get("/mirror/play/status", timeout=10.0)
            if pstatus.status_code == 200:
                pdata = pstatus.json()
                poly_runner = (pdata.get("providers") or {}).get("polymarket") or {}
                if poly_runner.get("state") and poly_runner.get("state") not in (
                    "idle",
                    "none",
                    None,
                ):
                    continue  # already running
            bresp = await tunnel.post(
                "/api/opportunities/play/batch", json={}, timeout=10.0
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
            sresp = await local.post(
                "/mirror/play/start", json=start_payload, timeout=10.0
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
    try:
        await browser.stop()
    except Exception:
        logger.exception("Mirror browser stop raised")

    try:
        from proxy import close_proxy_clients

        from local.http_client import close_all as _close_clients

        await _close_clients()
        await close_proxy_clients()
    except Exception:
        logger.exception("HTTP client shutdown raised")
    logger.info("Betty stopped")
