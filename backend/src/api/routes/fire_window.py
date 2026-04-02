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
async def activate_provider(provider_id: str):
    """Open tabs for provider and start live price polling."""
    window = fw.get_window()
    if not window:
        raise HTTPException(400, "No fire window open")

    if provider_id not in window.provider_bets:
        raise HTTPException(400, f"Provider '{provider_id}' not in queue")

    # Auto-ensure mirror is started for Polymarket
    mirror = _get_active_mirror()
    if not mirror and provider_id == "polymarket":
        from ...mirror.service import MirrorService
        from ...pipeline.broadcast import odds_broadcaster

        async with _start_lock:
            if not _any_running():
                mirror = MirrorService(broadcaster=odds_broadcaster, provider_id="spelklubben")
                await mirror.start()
                _mirrors["spelklubben"] = mirror
            else:
                mirror = _get_active_mirror()

    if not mirror:
        raise HTTPException(400, "Could not start mirror browser")

    return await fw.activate_provider(provider_id, mirror)


@router.get("/state")
def get_state():
    """Get current provider's live bet states + delta."""
    window = fw.get_window()
    if not window:
        raise HTTPException(400, "No fire window open")
    return fw.get_live_state()


@router.post("/fire")
async def fire_current_provider():
    """Fire +EV bets for current provider, advance to next."""
    window = fw.get_window()
    if not window or not window.current_provider:
        raise HTTPException(400, "No active provider to fire")

    mirror = _get_active_mirror()
    if not mirror:
        raise HTTPException(400, "No mirror running")

    return await fw.fire_provider(mirror)


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
