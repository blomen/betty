"""Mirror API routes — start/stop bet interception browser."""

import logging
from fastapi import APIRouter, HTTPException, BackgroundTasks

from ...mirror.service import MirrorService
from ...pipeline.broadcast import odds_broadcaster

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mirror", tags=["mirror"])

# Singleton mirror service (one provider at a time for v1)
_mirror: MirrorService | None = None


@router.post("/start")
async def start_mirror(
    background_tasks: BackgroundTasks,
    provider: str = "spelklubben",
    url: str | None = None,
    discovery: bool = False,
):
    """Start bet interception for a provider."""
    global _mirror

    if _mirror and _mirror.get_status()["running"]:
        raise HTTPException(400, f"Mirror already running for {_mirror.provider_id}")

    _mirror = MirrorService(provider_id=provider, broadcaster=odds_broadcaster, discovery=discovery)
    await _mirror.start(site_url=url)

    return _mirror.get_status()


@router.post("/stop")
async def stop_mirror():
    """Stop bet interception."""
    global _mirror

    if not _mirror:
        raise HTTPException(400, "No mirror running")

    await _mirror.stop()
    status = _mirror.get_status()
    _mirror = None
    return status


@router.get("/status")
async def mirror_status():
    """Get current mirror status."""
    if not _mirror:
        return {"running": False, "provider": None, "status": "stopped", "since": None}
    return _mirror.get_status()
