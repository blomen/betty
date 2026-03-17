"""Settings API routes."""

import logging

import yaml
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...db.models import Profile, ProviderExtractionSetting
from ...paths import get_config_path
from ..deps import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])

PLATFORM_NAMES = {
    "pinnacle": "Pinnacle",
    "polymarket": "Polymarket",
    "kambi": "Kambi",
    "altenar": "Altenar",
    "gecko_v2": "Gecko V2",
    "betconstruct": "BetConstruct",
    "spectate": "Spectate",
    "custom": "ComeOn Group",
    "snabbare": "Snabbare",
    "tenbet": "10Bet",
    "interwetten": "Interwetten",
    "coolbet": "Coolbet",
    "tipwin": "Tipwin",
}

PLATFORM_ORDER = [
    "pinnacle", "polymarket",
    "kambi", "altenar", "gecko_v2", "betconstruct",
    "spectate", "custom", "snabbare", "tenbet", "interwetten", "coolbet",
]


class ProviderExtractionToggle(BaseModel):
    provider_id: str
    enabled: bool


class BatchProviderToggle(BaseModel):
    provider_ids: list[str]
    enabled: bool


def _get_active_profile_id(db: Session) -> int:
    """Resolve the active profile ID."""
    profile = db.query(Profile).filter(Profile.is_active == True).first()  # noqa: E712
    if not profile:
        profile = db.query(Profile).first()
        if not profile:
            profile = Profile(name="default", is_active=True)
            db.add(profile)
            db.flush()
    return profile.id


@router.get("/extraction")
def get_extraction_settings(db: Session = Depends(get_db)):
    """Get providers grouped by platform with enabled/disabled status."""
    profile_id = _get_active_profile_id(db)

    config_path = get_config_path("providers.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    extraction_tiers = config.get("extraction_scheduling", config.get("extraction_tiers", {}))
    providers_config = config.get("providers", {})

    # Load DB overrides for active profile
    overrides = {
        s.provider_id: s.enabled
        for s in db.query(ProviderExtractionSetting).filter(
            ProviderExtractionSetting.profile_id == profile_id
        ).all()
    }

    # Build map of all sites per retriever_type (from full providers config)
    all_sites_by_platform: dict[str, list[str]] = {}
    for pid, pconfig in providers_config.items():
        if not isinstance(pconfig, dict):
            continue
        rtype = pconfig.get("retriever_type", "unknown")
        name = pconfig.get("name", pid)
        all_sites_by_platform.setdefault(rtype, []).append(name)

    # Collect all providers from all tiers, group by platform (retriever_type)
    platforms: dict = {}
    for tier_name, tier_config in extraction_tiers.items():
        for pid in tier_config.get("providers", []):
            pconfig = providers_config.get(pid, {})
            rtype = pconfig.get("retriever_type", "unknown")
            if rtype not in platforms:
                platforms[rtype] = {"providers": [], "tier": tier_name}
            platforms[rtype]["providers"].append({
                "provider_id": pid,
                "name": pconfig.get("name", pid),
                "enabled": overrides.get(pid, True),
            })

    # Build ordered result
    result = []
    seen = set()
    for platform_id in PLATFORM_ORDER:
        if platform_id in platforms:
            seen.add(platform_id)
            p = platforms[platform_id]
            result.append({
                "platform_id": platform_id,
                "platform_name": PLATFORM_NAMES.get(platform_id, platform_id),
                "tier": p["tier"],
                "providers": p["providers"],
                "sites": all_sites_by_platform.get(platform_id, []),
            })
    for platform_id, p in platforms.items():
        if platform_id not in seen:
            result.append({
                "platform_id": platform_id,
                "platform_name": PLATFORM_NAMES.get(platform_id, platform_id),
                "tier": p["tier"],
                "providers": p["providers"],
                "sites": all_sites_by_platform.get(platform_id, []),
            })

    return {"platforms": result}


@router.put("/extraction/provider")
def toggle_extraction_provider(
    body: ProviderExtractionToggle,
    db: Session = Depends(get_db),
):
    """Enable or disable a provider for extraction (for active profile)."""
    profile_id = _get_active_profile_id(db)

    existing = db.query(ProviderExtractionSetting).get(
        (profile_id, body.provider_id)
    )

    if existing:
        existing.enabled = body.enabled
    else:
        db.add(ProviderExtractionSetting(
            profile_id=profile_id,
            provider_id=body.provider_id,
            enabled=body.enabled,
        ))

    db.flush()
    return {"success": True, "provider_id": body.provider_id, "enabled": body.enabled}


@router.put("/extraction/batch")
def toggle_extraction_batch(
    body: BatchProviderToggle,
    db: Session = Depends(get_db),
):
    """Enable or disable multiple providers at once (for active profile)."""
    profile_id = _get_active_profile_id(db)

    for pid in body.provider_ids:
        existing = db.query(ProviderExtractionSetting).get((profile_id, pid))
        if existing:
            existing.enabled = body.enabled
        else:
            db.add(ProviderExtractionSetting(
                profile_id=profile_id,
                provider_id=pid,
                enabled=body.enabled,
            ))

    db.flush()
    return {"success": True, "provider_ids": body.provider_ids, "enabled": body.enabled}
