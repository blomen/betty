"""Mirror API routes — start/stop bet interception browsers."""

import logging
import yaml
from fastapi import APIRouter, HTTPException

from ...mirror.service import MirrorService
from ...pipeline.broadcast import odds_broadcaster
from ...paths import get_config_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mirror", tags=["mirror"])

# Multiple mirrors can run simultaneously — one per provider
_mirrors: dict[str, MirrorService] = {}

# Cache of gecko provider configs
_gecko_providers: dict[str, dict] | None = None


def _load_gecko_providers() -> dict[str, dict]:
    """Load all Gecko V2 providers from providers.yaml."""
    global _gecko_providers
    if _gecko_providers is not None:
        return _gecko_providers

    config_path = get_config_dir() / "providers.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    _gecko_providers = {}
    for pid, pconf in config.get("providers", {}).items():
        if pconf.get("retriever_type") == "gecko_v2":
            site = pconf.get("site_url", f"https://www.{pconf.get('domain', '')}")
            init_path = pconf.get("init_path", "/sv/odds")
            _gecko_providers[pid] = {
                "id": pid,
                "name": pconf.get("name", pid),
                "url": f"{site.rstrip('/')}{init_path}",
            }
    return _gecko_providers


@router.get("/providers")
async def list_mirror_providers():
    """List providers available for mirroring with their current status."""
    providers = _load_gecko_providers()
    result = []
    for pid, pconf in providers.items():
        mirror = _mirrors.get(pid)
        result.append({
            "id": pid,
            "name": pconf["name"],
            "running": mirror.get_status()["running"] if mirror else False,
        })
    return {"providers": result}


@router.post("/start")
async def start_mirror(
    provider: str = "spelklubben",
    discovery: bool = False,
):
    """Start bet interception for a provider."""
    if provider in _mirrors and _mirrors[provider].get_status()["running"]:
        raise HTTPException(400, f"Mirror already running for {provider}")

    providers = _load_gecko_providers()
    pconf = providers.get(provider)
    site_url = pconf["url"] if pconf else f"https://www.{provider}.com/sv/odds"

    mirror = MirrorService(provider_id=provider, broadcaster=odds_broadcaster, discovery=discovery)
    await mirror.start(site_url=site_url)
    _mirrors[provider] = mirror

    return mirror.get_status()


@router.post("/stop")
async def stop_mirror(provider: str | None = None):
    """Stop bet interception. If no provider specified, stops all."""
    if provider:
        mirror = _mirrors.pop(provider, None)
        if not mirror:
            raise HTTPException(400, f"No mirror running for {provider}")
        await mirror.stop()
        return mirror.get_status()
    else:
        results = []
        for pid in list(_mirrors.keys()):
            mirror = _mirrors.pop(pid)
            await mirror.stop()
            results.append(mirror.get_status())
        return {"stopped": results}


@router.get("/status")
async def mirror_status():
    """Get status of all mirrors."""
    statuses = {}
    for pid, mirror in _mirrors.items():
        statuses[pid] = mirror.get_status()
    return {
        "running": any(s["running"] for s in statuses.values()),
        "mirrors": statuses,
    }
