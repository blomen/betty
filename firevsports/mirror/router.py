"""Mirror router — browser control and bet placement endpoints."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .browser import MirrorBrowser
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


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_mirror_router(browser: MirrorBrowser) -> APIRouter:
    """Return an APIRouter with mirror browser control and placement endpoints."""

    router = APIRouter(prefix="/mirror", tags=["mirror"])

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

    return router
