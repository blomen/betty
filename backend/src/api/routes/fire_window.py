"""Fire Window API routes — provider-by-provider batch execution."""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...services import fire_window as fw
from .mirror import _get_active_mirror, _mirrors, _start_lock, _any_running

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fire-window", tags=["fire-window"])


class OpenRequest(BaseModel):
    batch: list[dict]
    provider_order: list[str] | None = None


@router.post("/open")
def open_fire_window(request: OpenRequest):
    """Build fire window from an allocated batch."""
    if not request.batch:
        raise HTTPException(400, "Empty batch")
    return fw.open_window(request.batch, request.provider_order)


@router.post("/activate/{provider_id}")
def activate_provider(provider_id: str):
    """Set the current provider (no polling, no tabs)."""
    window = fw.get_window()
    if not window:
        raise HTTPException(400, "No fire window open")

    if provider_id not in window.provider_bets:
        raise HTTPException(400, f"Provider '{provider_id}' not in queue")

    return fw.set_current_provider(provider_id)


@router.get("/state")
def get_state():
    """Get current provider's bet states + balance."""
    window = fw.get_window()
    if not window:
        raise HTTPException(400, "No fire window open")
    return fw.get_live_state()


@router.post("/fire")
async def fire_current_provider():
    """Fire +EV bets for current provider (legacy batch fire)."""
    window = fw.get_window()
    if not window or not window.current_provider:
        raise HTTPException(400, "No active provider to fire")
    mirror = _get_active_mirror()
    return await fw.fire_provider(mirror)


@router.get("/next-bet")
def get_next_bet():
    """Get the next unfired bet for the current provider."""
    window = fw.get_window()
    if not window or not window.current_provider:
        raise HTTPException(400, "No active provider")
    return fw.get_next_bet()


@router.post("/check-bet/{bet_id}")
async def check_bet(bet_id: int):
    """Check live price for a specific bet. Opens only this bet's tab."""
    window = fw.get_window()
    if not window:
        raise HTTPException(400, "No fire window open")

    mirror = _get_active_mirror()

    # Open only this bet's tab (not all bets)
    pid = window.current_provider
    if pid == "polymarket" and mirror:
        bets = window.provider_bets.get(pid, [])
        bet = next((b for b in bets if b.bet_id == bet_id), None)
        if bet and bet.market_slug:
            try:
                await mirror._ensure_poly_tabs([
                    {"market_slug": bet.market_slug, "poly_outcome": bet.poly_outcome, "bet_id": bet.bet_id}
                ])
            except Exception:
                pass

    return await fw.check_bet(bet_id, mirror)


@router.post("/place-bet/{bet_id}")
async def place_bet(bet_id: int):
    """Place a single confirmed bet."""
    window = fw.get_window()
    if not window:
        raise HTTPException(400, "No fire window open")
    mirror = _get_active_mirror()
    return await fw.place_bet(bet_id, mirror)


@router.post("/skip-bet/{bet_id}")
def skip_single_bet(bet_id: int):
    """Skip a single bet without placing."""
    return fw.skip_bet(bet_id)


@router.post("/skip")
def skip_current_provider():
    """Skip current provider, advance to next."""
    window = fw.get_window()
    if not window or not window.current_provider:
        raise HTTPException(400, "No active provider to skip")
    return fw.skip_provider()


@router.get("/queue")
def get_queue():
    """Get the provider queue with status."""
    window = fw.get_window()
    if not window:
        raise HTTPException(400, "No fire window open")
    return fw._build_queue_response()


@router.post("/close")
async def close_fire_window():
    """Close fire window, cleanup tabs."""
    mirror = _get_active_mirror()
    if mirror:
        try:
            await mirror.close_poly_tabs()
        except Exception:
            pass
    fw.close_window()
    return {"status": "closed"}


@router.get("/summary")
def get_summary():
    """Get summary of all fired providers."""
    return fw.get_fired_summary()
