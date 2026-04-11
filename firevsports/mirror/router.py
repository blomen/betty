"""Mirror router — browser control and bet placement endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.requests import Request

from .browser import MirrorBrowser
from .play_loop import PlayLoop
from .sse import MirrorBroadcaster
from .workflows import get_workflow

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class NavigateRequest(BaseModel):
    provider_id: str
    event_id: str
    market: str
    outcome: str
    point: float | None = None
    odds: float
    fair_odds: float
    stake: float
    display_home: str
    display_away: str


class PlaceRequest(BaseModel):
    provider_id: str
    bet_id: int


class OpenTabRequest(BaseModel):
    url: str


class PlayStartRequest(BaseModel):
    batch: list[dict[str, Any]]
    balances: dict[str, Any]


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_mirror_router(browser: MirrorBrowser, broadcaster: MirrorBroadcaster, proxy_url: str) -> APIRouter:
    """Return an APIRouter with mirror browser control and placement endpoints."""

    router = APIRouter(prefix="/mirror", tags=["mirror"])

    play_loop = PlayLoop(browser, broadcaster, proxy_url)

    @router.post("/open-provider-tab")
    async def open_provider_tab(request: Request):
        """Open a provider's site in a new tab (starts browser if needed)."""
        body = await request.json()
        pid = body.get("provider_id", "")
        if not pid:
            raise HTTPException(400, "provider_id required")
        # Start browser if not running
        if not browser.running:
            await browser.start()
        # Check if tab already open
        workflow = get_workflow(pid)
        if browser.context:
            for page in browser.context.pages:
                if workflow.domain and workflow.domain in page.url:
                    return {"status": "already_open", "url": page.url, "provider_id": pid}
        # Open new tab
        domain = workflow.domain
        if not domain:
            raise HTTPException(400, f"No domain for provider {pid}")
        page = await browser.open_tab(f"https://{domain}")
        return {"status": "opened", "url": page.url, "provider_id": pid}

    @router.get("/browser/tabs")
    async def browser_tabs():
        """Live browser state — which tabs are open, URLs, provider detection."""
        if not browser.running or not browser.context:
            return {"tabs": []}
        tabs = []
        for page in browser.context.pages:
            url = page.url
            title = ""
            try:
                title = await page.title()
            except Exception:
                pass
            tabs.append({"url": url, "title": title})
        return {"tabs": tabs}

    @router.get("/browser/provider/{provider_id}")
    async def browser_provider_state(provider_id: str):
        """Live state of a provider — from intercepted/cached data only. Never opens tabs."""
        if not browser.running or not browser.context:
            return {"found": False, "logged_in": False, "balance": None, "reason": "browser_not_started"}
        # Check if we have a tab for this provider
        workflow = get_workflow(provider_id)
        tab_url = None
        for page in browser.context.pages:
            if workflow.domain and workflow.domain in page.url:
                tab_url = page.url
                break
        if not tab_url:
            return {"found": False, "logged_in": False, "balance": None, "domain": workflow.domain}
        # Use intercepted data, fallback to DOM scrape (no new tabs opened)
        intercepted = browser.provider_data.get(provider_id, {})
        logged_in = intercepted.get("logged_in", False)
        balance = intercepted.get("balance")
        if not logged_in:
            dom = await browser.check_login_dom(provider_id)
            logged_in = dom.get("logged_in", False)
            balance = dom.get("balance") or balance
        return {
            "found": True,
            "provider_id": provider_id,
            "url": tab_url,
            "logged_in": logged_in,
            "balance": balance,
            "domain": workflow.domain,
        }

    @router.get("/browser/screenshot/{provider_id}")
    async def browser_screenshot(provider_id: str):
        """Take screenshot of provider tab and check for balance text."""
        if not browser.running or not browser.context:
            return {"error": "browser not running"}
        workflow = get_workflow(provider_id)
        page = await workflow.find_tab(browser.context)
        if not page:
            return {"error": "no tab found"}
        # Check page content for balance
        balance_text = await page.evaluate("""() => {
            const text = document.body.innerText;
            const m = text.match(/(\\d+[,.]\\d+)\\s*KR/i);
            return m ? m[0] : null;
        }""")
        await page.screenshot(path="debug_screenshot.png")
        return {"url": page.url, "balance_text": balance_text, "screenshot": "debug_screenshot.png"}

    @router.get("/status")
    async def get_status():
        """Return current browser status: running flag, tab count, open pages."""
        return browser.get_status()

    @router.post("/start")
    async def start_browser():
        """Launch the mirror browser. Idempotent — safe to call when already running."""
        await browser.start()
        return browser.get_status()

    @router.post("/stop")
    async def stop_browser():
        """Stop the mirror browser and close all tabs."""
        await browser.stop()
        return browser.get_status()

    @router.post("/navigate")
    async def navigate(req: NavigateRequest):
        """Navigate the provider's tab to the event for a pending bet."""
        if not browser.running:
            raise HTTPException(status_code=400, detail="Mirror browser is not running")

        workflow = get_workflow(req.provider_id)
        context = browser.context
        page = await workflow.find_tab(context)
        if page is None:
            raise HTTPException(
                status_code=404,
                detail=f"No open tab found for provider '{req.provider_id}' (domain: {workflow.domain})",
            )

        # Build a lightweight bet-like object the workflow can consume.
        class _Bet:
            pass

        bet = _Bet()
        for field, value in req.model_dump().items():
            setattr(bet, field, value)

        success = await workflow.navigate_to_event(page, bet)
        return {"success": success, "url": page.url}

    @router.post("/place")
    async def place_bet(req: PlaceRequest):
        """Place a pending bet by bet_id via the provider's workflow."""
        if not browser.running:
            raise HTTPException(status_code=400, detail="Mirror browser is not running")

        workflow = get_workflow(req.provider_id)
        context = browser.context
        page = await workflow.find_tab(context)
        if page is None:
            raise HTTPException(
                status_code=404,
                detail=f"No open tab found for provider '{req.provider_id}' (domain: {workflow.domain})",
            )

        # Fetch the bet from DB / service layer if available; fall back to a
        # minimal shim so the workflow still receives a typed object.
        try:
            from ...services.bet_service import BetService  # type: ignore

            bet = await BetService.get_by_id(req.bet_id)
        except Exception:

            class _Bet:
                pass

            bet = _Bet()
            bet.id = req.bet_id

        stake = getattr(bet, "stake", None)
        result = await workflow.place_bet(page, bet, stake)
        return {
            "status": result.status,
            "bet_id": result.bet_id,
            "actual_odds": result.actual_odds,
            "actual_stake": result.actual_stake,
            "reason": result.reason,
        }

    @router.post("/open-tab")
    async def open_tab(req: OpenTabRequest):
        """Open a new browser tab navigated to the given URL."""
        if not browser.running:
            raise HTTPException(status_code=400, detail="Mirror browser is not running")
        page = await browser.open_tab(req.url)
        return {"url": page.url}

    # -----------------------------------------------------------------------
    # Play loop
    # -----------------------------------------------------------------------

    @router.post("/play/start")
    async def play_start(req: PlayStartRequest):
        """Load a batch of bets and start the play loop."""
        play_loop.load_batch(req.batch, req.balances)
        play_loop.start()
        return play_loop.get_status()

    @router.post("/play/confirm-settlements")
    async def play_confirm_settlements():
        """Confirm the settlement breakdown and proceed to bets."""
        play_loop.confirm_settlements()
        return play_loop.get_status()

    @router.post("/play/place")
    async def play_place():
        """Confirm placement of the current bet in the play loop."""
        play_loop.place()
        return play_loop.get_status()

    @router.post("/play/skip")
    async def play_skip():
        """Skip the current bet in the play loop."""
        play_loop.skip()
        return play_loop.get_status()

    @router.post("/play/stop")
    async def play_stop():
        """Stop the play loop."""
        play_loop.stop()
        return play_loop.get_status()

    @router.get("/play/status")
    async def play_status():
        """Return current play loop status."""
        return play_loop.get_status()

    # -----------------------------------------------------------------------
    # SSE stream
    # -----------------------------------------------------------------------

    @router.get("/stream")
    async def mirror_stream(request: Request):
        """Server-sent events stream for real-time mirror updates."""
        from sse_starlette.sse import EventSourceResponse

        client_id, queue = broadcaster.subscribe()

        async def generator():
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=10.0)
                        yield {"event": msg["event"], "data": json.dumps(msg["data"])}
                    except asyncio.TimeoutError:
                        yield {"event": "heartbeat", "data": ""}
            except asyncio.CancelledError:
                pass
            finally:
                broadcaster.unsubscribe(client_id)

        return EventSourceResponse(generator(), ping=15)

    return router
