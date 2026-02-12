"""Provider API routes."""

from datetime import datetime
from functools import lru_cache
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
import yaml

from ...db.models import Provider, Profile, ProfileProviderBonus
from ...repositories import ProfileRepo
from ..deps import get_db
from ..schemas import ProviderCreate, ProviderUpdate


@lru_cache(maxsize=1)
def load_provider_bonuses() -> dict[str, dict]:
    """Load bonus info from providers.yaml config (cached — config doesn't change at runtime)."""
    from ...paths import get_config_path
    config_path = get_config_path("providers.yaml")
    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)
        return {
            pid: p['bonus']
            for pid, p in config.get('providers', {}).items()
            if 'bonus' in p
        }
    except Exception:
        return {}


def get_profile_bonus_status(db: Session, provider_id: str) -> str | None:
    """Get bonus status for provider from active profile."""
    active_profile = db.query(Profile).filter(Profile.is_active == True).first()
    if not active_profile:
        return None

    bonus_record = db.query(ProfileProviderBonus).filter(
        ProfileProviderBonus.profile_id == active_profile.id,
        ProfileProviderBonus.provider_id == provider_id
    ).first()

    # If no record exists, bonus is available (not yet used by this profile)
    return bonus_record.bonus_status if bonus_record else None


router = APIRouter(prefix="/api/providers", tags=["providers"])


@router.get("")
async def list_providers(db: Session = Depends(get_db)):
    """Get all providers with status, balance, and bonus info for active profile."""
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()
    providers = db.query(Provider).all()
    bonus_info = load_provider_bonuses()

    provider_list = []
    for p in providers:
        balance = profile_repo.get_balance(profile.id, p.id) if p.is_enabled else 0.0
        provider_list.append({
            "id": p.id,
            "name": p.name,
            "url": p.url,
            "is_enabled": p.is_enabled,
            "balance": balance,
            "bonus": bonus_info.get(p.id),
            "bonus_status": get_profile_bonus_status(db, p.id),
        })

    total_balance = profile_repo.get_total_bankroll(profile.id)

    return {
        "profile_id": profile.id,
        "profile_name": profile.name,
        "providers": provider_list,
        "total_balance": total_balance,
    }


@router.post("")
async def create_provider(provider: ProviderCreate, db: Session = Depends(get_db)):
    """Create a new provider."""
    existing = db.query(Provider).filter(Provider.id == provider.id).first()
    if existing:
        raise HTTPException(400, f"Provider {provider.id} already exists")

    p = Provider(
        id=provider.id,
        name=provider.name,
        url=provider.url,
        balance=provider.balance,
    )
    db.add(p)
    db.commit()
    return {"success": True, "provider_id": p.id}


@router.put("/{provider_id}")
async def update_provider(
    provider_id: str,
    data: ProviderUpdate,
    db: Session = Depends(get_db)
):
    """Update provider (balance, enabled, etc.)."""
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(404, f"Provider {provider_id} not found")

    old_balance = provider.balance

    if data.name is not None:
        provider.name = data.name
    if data.url is not None:
        provider.url = data.url
    if data.is_enabled is not None:
        provider.is_enabled = data.is_enabled
    if data.balance is not None:
        provider.balance = data.balance

    provider.updated_at = datetime.utcnow()
    db.commit()

    return {
        "success": True,
        "provider_id": provider_id,
        "old_balance": old_balance,
        "new_balance": provider.balance,
    }


@router.patch("/{provider_id}/bonus-status")
async def update_bonus_status(
    provider_id: str,
    status: str,
    db: Session = Depends(get_db)
):
    """
    Update bonus extraction status for a provider (per active profile).

    Status transitions:
    - 'available' -> 'in_progress': When user places first bonus bet
    - 'in_progress' -> 'completed': When bonus extraction is done

    Providers with 'completed' status can be used as counterparts
    for other bonus extractions.
    """
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(404, f"Provider {provider_id} not found")

    if status not in ('available', 'in_progress', 'completed', 'claimed'):
        raise HTTPException(400, f"Invalid status: {status}. Must be 'available', 'in_progress', 'completed', or 'claimed'")

    # Get active profile
    active_profile = db.query(Profile).filter(Profile.is_active == True).first()
    if not active_profile:
        raise HTTPException(400, "No active profile. Create and activate a profile first.")

    # Find or create profile-provider bonus record
    bonus_record = db.query(ProfileProviderBonus).filter(
        ProfileProviderBonus.profile_id == active_profile.id,
        ProfileProviderBonus.provider_id == provider_id
    ).first()

    old_status = bonus_record.bonus_status if bonus_record else None

    if bonus_record:
        bonus_record.bonus_status = status
        bonus_record.updated_at = datetime.utcnow()
    else:
        bonus_record = ProfileProviderBonus(
            profile_id=active_profile.id,
            provider_id=provider_id,
            bonus_status=status
        )
        db.add(bonus_record)

    db.commit()

    return {
        "id": provider_id,
        "bonus_status": status,
        "old_status": old_status,
        "profile_id": active_profile.id,
    }
