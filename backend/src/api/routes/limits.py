"""Provider limit API routes."""

from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from ...db.models import Profile, ProfileProviderLimit, ProviderExtractionSetting
from ...services.limit_service import LimitService
from ..deps import get_db
from ..schemas import LimitCreate, LimitUpdate, BanProviderRequest

router = APIRouter(prefix="/api/limits", tags=["limits"])


def _get_active_profile(db: Session) -> Profile:
    """Get active profile or raise 400."""
    profile = db.query(Profile).filter(Profile.is_active == True).first()
    if not profile:
        raise HTTPException(400, "No active profile")
    return profile


@router.get("")
def list_limits(
    provider_id: str | None = None,
    db: Session = Depends(get_db),
):
    """List all limits for the active profile."""
    profile = _get_active_profile(db)
    service = LimitService(db)
    return service.list_limits(profile_id=profile.id, provider_id=provider_id)


@router.post("")
def create_limit(data: LimitCreate, db: Session = Depends(get_db)):
    """Record a new provider limit with auto-generated betting snapshot."""
    profile = _get_active_profile(db)

    # Validate limit_level range (limit_type validated by Pydantic Literal)
    if not (1 <= data.limit_level <= 5):
        raise HTTPException(400, "limit_level must be between 1 and 5")

    detected_at = None
    if data.detected_at:
        try:
            detected_at = datetime.fromisoformat(data.detected_at)
        except ValueError:
            raise HTTPException(400, "Invalid detected_at format. Use ISO 8601.")

    service = LimitService(db)
    result = service.record_limit(
        profile_id=profile.id,
        provider_id=data.provider_id,
        limit_type=data.limit_type,
        limit_level=data.limit_level,
        notes=data.notes,
        detected_at=detected_at,
    )

    if not result["success"]:
        raise HTTPException(400, result["error"])
    return result


@router.put("/{limit_id}")
def update_limit(limit_id: int, data: LimitUpdate, db: Session = Depends(get_db)):
    """Update limit level or notes. Snapshot is immutable."""
    if data.limit_level is not None and not (1 <= data.limit_level <= 5):
        raise HTTPException(400, "limit_level must be between 1 and 5")

    service = LimitService(db)
    result = service.update_limit(limit_id, limit_level=data.limit_level, notes=data.notes)

    if not result["success"]:
        raise HTTPException(404, result["error"])
    return result


@router.delete("/{limit_id}")
def delete_limit(limit_id: int, db: Session = Depends(get_db)):
    """Delete a limit record."""
    service = LimitService(db)
    result = service.delete_limit(limit_id)

    if not result["success"]:
        raise HTTPException(404, result["error"])
    return result


@router.post("/ban")
def ban_provider(data: BanProviderRequest, db: Session = Depends(get_db)):
    """Ban a provider — records fully_banned limit (level 5) and disables extraction."""
    profile = _get_active_profile(db)
    service = LimitService(db)
    result = service.ban_provider(
        profile_id=profile.id,
        provider_id=data.provider_id,
        notes=data.notes,
    )
    if not result["success"]:
        raise HTTPException(400, result["error"])
    return result


@router.delete("/ban/{provider_id}")
def unban_provider(provider_id: str, db: Session = Depends(get_db)):
    """Unban a provider — removes fully_banned limit and re-enables extraction."""
    profile = _get_active_profile(db)

    limit = db.query(ProfileProviderLimit).filter(
        ProfileProviderLimit.profile_id == profile.id,
        ProfileProviderLimit.provider_id == provider_id,
        ProfileProviderLimit.limit_type == "fully_banned",
        ProfileProviderLimit.limit_level == 5,
    ).first()
    if not limit:
        raise HTTPException(404, f"No ban found for {provider_id}")

    db.delete(limit)

    # Re-enable extraction
    setting = db.query(ProviderExtractionSetting).filter(
        ProviderExtractionSetting.profile_id == profile.id,
        ProviderExtractionSetting.provider_id == provider_id,
    ).first()
    if setting:
        setting.enabled = True

    db.commit()
    return {"success": True, "provider_id": provider_id}
