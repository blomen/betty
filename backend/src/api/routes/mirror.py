"""Mirror API routes — start/stop bet interception browser."""

import logging
import yaml
from pathlib import Path
from fastapi import APIRouter, HTTPException

from ...mirror.service import MirrorService
from ...pipeline.broadcast import odds_broadcaster

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mirror", tags=["mirror"])

# Multi-provider mirror state (used by lifespan auto-start AND manual start/stop)
_mirrors: dict[str, MirrorService] = {}

# Default provider when none specified
_DEFAULT_PROVIDER = "spelklubben"


def _load_all_providers() -> dict[str, dict]:
    """Load provider configs from providers.yaml for mirror auto-start."""
    config_path = Path(__file__).resolve().parents[2] / "config" / "providers.yaml"
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        cfg = yaml.safe_load(f) or {}
    return cfg.get("providers", {})


def _any_running() -> bool:
    """Check if any mirror instance is running."""
    return any(m.get_status()["running"] for m in _mirrors.values())


@router.post("/start")
async def start_mirror():
    """Launch the mirror browser."""
    if _any_running():
        raise HTTPException(400, "Mirror already running")

    mirror = MirrorService(broadcaster=odds_broadcaster, provider_id=_DEFAULT_PROVIDER)
    await mirror.start()
    _mirrors[_DEFAULT_PROVIDER] = mirror
    return mirror.get_status()


@router.post("/stop")
async def stop_mirror():
    """Stop all mirror browsers."""
    if not _mirrors:
        raise HTTPException(400, "No mirror running")

    for pid in list(_mirrors.keys()):
        try:
            await _mirrors.pop(pid).stop()
        except Exception as e:
            logger.warning(f"Error stopping mirror {pid}: {e}")

    return {"running": False, "status": "stopped"}


@router.get("/status")
async def mirror_status():
    """Get mirror status — returns running if any mirror instance is active."""
    for m in _mirrors.values():
        status = m.get_status()
        if status["running"]:
            return status
    return {"running": False, "status": "stopped"}
