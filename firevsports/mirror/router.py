"""Mirror router — browser control and bet placement endpoints."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.requests import Request

from .browser import MirrorBrowser
from .play_loop import PlayLoop
from .pending_loop import PendingLoop
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
    point: Optional[float] = None
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
    batch: List[Dict[str, Any]]
    balances: Dict[str, Any]


class PendingConfirmRequest(BaseModel):
    provider_id: str


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_mirror_router(browser: MirrorBrowser, broadcaster: MirrorBroadcaster, proxy_url: str) -> APIRouter:
    """Return an APIRouter with mirror browser control and placement endpoints."""

    router = APIRouter(prefix="/mirror", tags=["mirror"])

    play_loop = PlayLoop(browser, broadcaster, proxy_url)
    pending_loop = PendingLoop(browser, broadcaster, proxy_url)

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
        """Check live state of a provider tab — URL, logged in, balance."""
        if not browser.running or not browser.context:
            return {"found": False, "reason": "browser_not_running"}
        workflow = get_workflow(provider_id)
        page = await workflow.find_tab(browser.context)
        if not page:
            return {"found": False, "reason": "no_tab", "domain": workflow.domain}
        logged_in = False
        balance = None
        try:
            logged_in = await workflow.check_login(page)
        except Exception:
            pass
        if logged_in:
            try:
                balance = await workflow.sync_balance(page)
            except Exception:
                pass
        return {
            "found": True,
            "provider_id": provider_id,
            "url": page.url,
            "logged_in": logged_in,
            "balance": balance,
            "domain": workflow.domain,
        }

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

    @router.post("/play/place")
    async def play_place():
        """Confirm placement of the current bet in the play loop."""
        await play_loop.place()
        return play_loop.get_status()

    @router.post("/play/skip")
    async def play_skip():
        """Skip the current bet in the play loop."""
        await play_loop.skip()
        return play_loop.get_status()

    @router.post("/play/stop")
    async def play_stop():
        """Stop the play loop."""
        await play_loop.stop()
        return play_loop.get_status()

    @router.get("/play/status")
    async def play_status():
        """Return current play loop status."""
        return play_loop.get_status()

    # -----------------------------------------------------------------------
    # Pending loop
    # -----------------------------------------------------------------------

    @router.post("/pending/start")
    async def pending_start():
        """Start the pending loop to monitor open bets."""
        await pending_loop.start()
        return pending_loop.get_status()

    @router.post("/pending/confirm")
    async def pending_confirm(req: PendingConfirmRequest):
        """Confirm a pending bet by provider ID."""
        await pending_loop.confirm(req.provider_id)
        return pending_loop.get_status()

    @router.post("/pending/stop")
    async def pending_stop():
        """Stop the pending loop."""
        await pending_loop.stop()
        return pending_loop.get_status()

    @router.get("/pending/status")
    async def pending_status():
        """Return current pending loop status."""
        return pending_loop.get_status()

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
