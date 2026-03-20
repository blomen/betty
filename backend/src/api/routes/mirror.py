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

# Cache of all provider configs for mirroring
_all_providers: dict[str, dict] | None = None


def _load_all_providers() -> dict[str, dict]:
    """Load all providers from providers.yaml with their site URLs."""
    global _all_providers
    if _all_providers is not None:
        return _all_providers

    config_path = get_config_dir() / "providers.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    _all_providers = {}
    for pid, pconf in config.get("providers", {}).items():
        site = pconf.get("site_url", "")
        domain = pconf.get("domain", "")
        if not site and domain:
            site = f"https://www.{domain}"
        if not site:
            continue

        init_path = pconf.get("init_path", "")
        # Default betting paths per provider type
        if not init_path:
            rtype = pconf.get("retriever_type", "")
            if rtype == "gecko_v2":
                init_path = "/sv/odds"
            elif rtype == "kambi":
                init_path = "/betting"
            else:
                init_path = ""

        url = f"{site.rstrip('/')}{init_path}" if init_path else site.rstrip("/")

        _all_providers[pid] = {
            "id": pid,
            "name": pconf.get("name", pid),
            "type": pconf.get("retriever_type", "unknown"),
            "url": url,
        }
    return _all_providers


@router.get("/providers")
async def list_mirror_providers():
    """List all providers available for mirroring with their current status."""
    providers = _load_all_providers()
    result = []
    for pid, pconf in providers.items():
        mirror = _mirrors.get(pid)
        result.append({
            "id": pid,
            "name": pconf["name"],
            "type": pconf["type"],
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

    providers = _load_all_providers()
    pconf = providers.get(provider)
    if not pconf:
        raise HTTPException(404, f"Provider {provider} not found")

    # Non-gecko providers always start in discovery mode until we have parsers
    has_parser = pconf["type"] == "gecko_v2"
    effective_discovery = discovery or not has_parser

    site_url = pconf["url"]
    mirror = MirrorService(provider_id=provider, broadcaster=odds_broadcaster, discovery=effective_discovery)
    await mirror.start(site_url=site_url)
    _mirrors[provider] = mirror

    status = mirror.get_status()
    status["discovery"] = effective_discovery
    return status


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
