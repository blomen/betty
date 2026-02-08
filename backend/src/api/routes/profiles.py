"""Profiles API routes."""

import json
from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...db.models import Profile, Provider, ProfileProviderBalance
from ...repositories import ProfileRepo
from ...bankroll import calculate_stake as calc_stake
from ..deps import get_db
from ..schemas import ProfileCreate, ProfileUpdate


class AccountDateUpdate(BaseModel):
    """Request body for setting account opened date."""
    opened_at: str  # ISO date string e.g. "2025-06-15"

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


def profile_to_dict(profile: Profile, profile_repo: ProfileRepo) -> dict:
    """Convert profile to dict response."""
    preferred_counterparts = []
    if profile.preferred_counterparts:
        try:
            preferred_counterparts = json.loads(profile.preferred_counterparts)
        except:
            pass

    real_bankroll = profile_repo.get_total_bankroll(profile.id)

    return {
        "id": profile.id,
        "name": profile.name,
        "bankroll": real_bankroll,
        "currency": "SEK",
        "kelly_fraction": profile.kelly_fraction,
        "min_edge_pct": profile.min_edge_pct,
        "min_arb_pct": profile.min_arb_pct,
        "max_stake_pct": profile.max_stake_pct,
        "min_retention_pct": profile.min_retention_pct,
        "preferred_counterparts": preferred_counterparts,
        "bonus_enabled": profile.bonus_enabled,
        "bonus_deposit": profile.bonus_deposit or 0.0,
        "is_active": profile.is_active,
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
    }


@router.get("")
async def list_profiles(db: Session = Depends(get_db)):
    """List all profiles."""
    profile_repo = ProfileRepo(db)
    profiles = db.query(Profile).order_by(Profile.created_at).all()

    if not profiles:
        default = Profile(name="default", is_active=True)
        db.add(default)
        db.commit()
        profiles = [default]

    return {
        "profiles": [profile_to_dict(p, profile_repo) for p in profiles],
        "active": next((profile_to_dict(p, profile_repo) for p in profiles if p.is_active), None),
    }


@router.get("/active")
async def get_active_profile(db: Session = Depends(get_db)):
    """Get currently active profile."""
    profile_repo = ProfileRepo(db)
    profile = db.query(Profile).filter(Profile.is_active == True).first()

    if not profile:
        profile = Profile(name="default", is_active=True)
        db.add(profile)
        db.commit()

    return profile_to_dict(profile, profile_repo)


@router.post("")
async def create_profile(data: ProfileCreate, db: Session = Depends(get_db)):
    """Create a new profile with fresh state (0 balance, no data copied)."""
    profile_repo = ProfileRepo(db)
    existing = db.query(Profile).filter(Profile.name == data.name).first()
    if existing:
        raise HTTPException(400, f"Profile '{data.name}' already exists")

    profile = Profile(
        name=data.name,
        bankroll=0.0,
        currency="SEK",
        kelly_fraction=data.kelly_fraction or 0.25,
        max_stake_pct=data.max_stake_pct or 5.0,
        is_active=False,
    )
    db.add(profile)
    db.commit()

    return {
        "success": True,
        "profile": profile_to_dict(profile, profile_repo),
    }


@router.get("/{profile_id}")
async def get_profile(profile_id: int, db: Session = Depends(get_db)):
    """Get profile by ID."""
    profile_repo = ProfileRepo(db)
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, f"Profile {profile_id} not found")

    return profile_to_dict(profile, profile_repo)


@router.put("/{profile_id}")
async def update_profile(profile_id: int, data: ProfileUpdate, db: Session = Depends(get_db)):
    """Update profile settings."""
    profile_repo = ProfileRepo(db)
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, f"Profile {profile_id} not found")

    if data.name is not None:
        existing = db.query(Profile).filter(Profile.name == data.name, Profile.id != profile_id).first()
        if existing:
            raise HTTPException(400, f"Profile name '{data.name}' already exists")
        profile.name = data.name
    if data.bankroll is not None:
        profile.bankroll = data.bankroll
    if data.currency is not None:
        profile.currency = data.currency
    if data.kelly_fraction is not None:
        profile.kelly_fraction = data.kelly_fraction
    if data.min_edge_pct is not None:
        profile.min_edge_pct = data.min_edge_pct
    if data.min_arb_pct is not None:
        profile.min_arb_pct = data.min_arb_pct
    if data.max_stake_pct is not None:
        profile.max_stake_pct = data.max_stake_pct
    if data.min_retention_pct is not None:
        profile.min_retention_pct = data.min_retention_pct
    if data.preferred_counterparts is not None:
        profile.preferred_counterparts = json.dumps(data.preferred_counterparts)
    if data.bonus_enabled is not None:
        profile.bonus_enabled = data.bonus_enabled
    if data.bonus_deposit is not None:
        profile.bonus_deposit = data.bonus_deposit

    db.commit()
    return {"success": True, "profile": profile_to_dict(profile, profile_repo)}


@router.post("/{profile_id}/activate")
async def activate_profile(profile_id: int, db: Session = Depends(get_db)):
    """Set profile as active (deactivates others)."""
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, f"Profile {profile_id} not found")

    db.query(Profile).update({Profile.is_active: False})
    profile.is_active = True
    db.commit()

    profile_repo = ProfileRepo(db)
    return {"success": True, "profile": profile_to_dict(profile, profile_repo)}


@router.delete("/{profile_id}")
async def delete_profile(profile_id: int, db: Session = Depends(get_db)):
    """Delete a profile."""
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, f"Profile {profile_id} not found")

    if profile.is_active:
        raise HTTPException(400, "Cannot delete active profile. Activate another profile first.")

    db.delete(profile)
    db.commit()

    return {"success": True}


@router.post("/calculate/stake")
async def calculate_stake_endpoint(
    odds: float,
    fair_odds: float,
    db: Session = Depends(get_db),
):
    """Calculate recommended stake using Kelly criterion for active profile."""
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()
    bankroll = profile_repo.get_total_bankroll(profile.id)

    edge_raw = odds / fair_odds - 1 if fair_odds > 1 else 0
    rec = calc_stake(
        bankroll_total=bankroll,
        edge_raw=edge_raw,
        odds=odds,
        min_odds=0.0,
    )

    return {
        "profile_id": profile.id,
        "recommended_stake": rec.stake,
        "kelly_stake": rec.raw_kelly_stake,
        "max_stake": rec.single_bet_cap,
        "bankroll": bankroll,
        "reason": rec.skip_reason or "Kelly",
    }


@router.put("/providers/{provider_id}/account-date")
async def set_account_opened_date(
    provider_id: str,
    data: AccountDateUpdate,
    db: Session = Depends(get_db),
):
    """Set the account opened date for a provider."""
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(404, f"Provider {provider_id} not found")

    try:
        opened_at = datetime.fromisoformat(data.opened_at)
    except ValueError:
        raise HTTPException(400, f"Invalid date format: {data.opened_at}. Use ISO format (YYYY-MM-DD)")

    if opened_at > datetime.utcnow():
        raise HTTPException(400, "Account opened date cannot be in the future")

    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()

    balance = db.query(ProfileProviderBalance).filter(
        ProfileProviderBalance.profile_id == profile.id,
        ProfileProviderBalance.provider_id == provider_id
    ).first()

    if balance:
        balance.account_opened_at = opened_at
        balance.updated_at = datetime.utcnow()
    else:
        balance = ProfileProviderBalance(
            profile_id=profile.id,
            provider_id=provider_id,
            balance=0.0,
            account_opened_at=opened_at
        )
        db.add(balance)

    db.commit()

    age_days = (datetime.utcnow() - opened_at).days

    return {
        "success": True,
        "provider_id": provider_id,
        "account_opened_at": opened_at.isoformat(),
        "account_age_days": age_days,
        "message": f"Account opened date set to {data.opened_at} ({age_days} days ago)"
    }


@router.get("/providers/{provider_id}/account-date")
async def get_account_opened_date(
    provider_id: str,
    db: Session = Depends(get_db),
):
    """Get the account opened date for a provider."""
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()

    balance = db.query(ProfileProviderBalance).filter(
        ProfileProviderBalance.profile_id == profile.id,
        ProfileProviderBalance.provider_id == provider_id
    ).first()

    if not balance or not balance.account_opened_at:
        return {
            "provider_id": provider_id,
            "account_opened_at": None,
            "account_age_days": None,
            "source": "none"
        }

    age_days = (datetime.utcnow() - balance.account_opened_at).days

    return {
        "provider_id": provider_id,
        "account_opened_at": balance.account_opened_at.isoformat(),
        "account_age_days": age_days,
        "source": "manual"
    }


@router.delete("/providers/{provider_id}/account-date")
async def clear_account_opened_date(
    provider_id: str,
    db: Session = Depends(get_db),
):
    """Clear the manual account opened date for a provider."""
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()

    balance = db.query(ProfileProviderBalance).filter(
        ProfileProviderBalance.profile_id == profile.id,
        ProfileProviderBalance.provider_id == provider_id
    ).first()

    if not balance:
        raise HTTPException(404, f"No balance record for {provider_id}")

    balance.account_opened_at = None
    balance.updated_at = datetime.utcnow()
    db.commit()

    return {
        "success": True,
        "provider_id": provider_id,
        "message": "Account opened date cleared. Will use first bet date for age calculation."
    }
