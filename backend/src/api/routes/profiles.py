"""Profiles API routes."""

import json
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from ...db.models import (
    Profile, Provider,
    get_active_profile as get_active_profile_helper,
    get_total_profile_bankroll, copy_profile_balances
)
from ...bankroll import kelly_stake
from ..deps import get_db
from ..schemas import ProfileCreate, ProfileUpdate

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


def profile_to_dict(profile: Profile, db: Session) -> dict:
    """Convert profile to dict response."""
    # Parse preferred_counterparts JSON if exists
    preferred_counterparts = []
    if profile.preferred_counterparts:
        try:
            preferred_counterparts = json.loads(profile.preferred_counterparts)
        except:
            pass

    # Calculate real bankroll from profile's provider balances
    real_bankroll = get_total_profile_bankroll(db, profile.id)

    return {
        "id": profile.id,
        "name": profile.name,
        "bankroll": real_bankroll,
        "currency": profile.currency,
        "kelly_fraction": profile.kelly_fraction,
        "min_edge_pct": profile.min_edge_pct,
        "min_arb_pct": profile.min_arb_pct,
        "max_stake_pct": profile.max_stake_pct,
        "min_retention_pct": profile.min_retention_pct,
        "preferred_counterparts": preferred_counterparts,
        "bonus_enabled": profile.bonus_enabled,
        "double_deposit": profile.double_deposit or 0.0,
        "is_active": profile.is_active,
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
    }


@router.get("")
async def list_profiles(db: Session = Depends(get_db)):
    """List all profiles."""
    profiles = db.query(Profile).order_by(Profile.created_at).all()

    # Ensure at least one default profile exists
    if not profiles:
        default = Profile(name="default", is_active=True)
        db.add(default)
        db.commit()
        profiles = [default]

    return {
        "profiles": [profile_to_dict(p, db) for p in profiles],
        "active": next((profile_to_dict(p, db) for p in profiles if p.is_active), None),
    }


@router.get("/active")
async def get_active_profile(db: Session = Depends(get_db)):
    """Get currently active profile."""
    profile = db.query(Profile).filter(Profile.is_active == True).first()

    if not profile:
        # Create and activate default profile
        profile = Profile(name="default", is_active=True)
        db.add(profile)
        db.commit()

    return profile_to_dict(profile, db)


@router.post("")
async def create_profile(data: ProfileCreate, db: Session = Depends(get_db)):
    """Create a new profile, copying balances from active profile."""
    # Check name uniqueness
    existing = db.query(Profile).filter(Profile.name == data.name).first()
    if existing:
        raise HTTPException(400, f"Profile '{data.name}' already exists")

    # Get active profile to copy balances from
    active_profile = db.query(Profile).filter(Profile.is_active == True).first()

    profile = Profile(
        name=data.name,
        bankroll=data.bankroll,
        currency=data.currency,
        kelly_fraction=data.kelly_fraction,
        min_edge_pct=data.min_edge_pct,
        min_arb_pct=data.min_arb_pct,
        max_stake_pct=data.max_stake_pct,
        is_active=False,
    )
    db.add(profile)
    db.flush()  # Get the profile ID

    # Copy balances from active profile to new profile
    balances_copied = 0
    if active_profile:
        balances_copied = copy_profile_balances(db, active_profile.id, profile.id)

    db.commit()

    return {
        "success": True,
        "profile": profile_to_dict(profile, db),
        "balances_copied": balances_copied,
        "copied_from_profile": active_profile.name if active_profile else None,
    }


@router.get("/{profile_id}")
async def get_profile(profile_id: int, db: Session = Depends(get_db)):
    """Get profile by ID."""
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, f"Profile {profile_id} not found")

    return profile_to_dict(profile, db)


@router.put("/{profile_id}")
async def update_profile(profile_id: int, data: ProfileUpdate, db: Session = Depends(get_db)):
    """Update profile settings."""
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, f"Profile {profile_id} not found")

    if data.name is not None:
        # Check name uniqueness
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
    if data.double_deposit is not None:
        profile.double_deposit = data.double_deposit

    db.commit()
    return {"success": True, "profile": profile_to_dict(profile, db)}


@router.post("/{profile_id}/activate")
async def activate_profile(profile_id: int, db: Session = Depends(get_db)):
    """Set profile as active (deactivates others)."""
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, f"Profile {profile_id} not found")

    # Deactivate all profiles
    db.query(Profile).update({Profile.is_active: False})

    # Activate selected
    profile.is_active = True
    db.commit()

    return {"success": True, "profile": profile_to_dict(profile, db)}


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
async def calculate_stake(
    odds: float,
    fair_odds: float,
    db: Session = Depends(get_db)
):
    """Calculate recommended stake using Kelly criterion for active profile."""
    # Get active profile and its bankroll
    profile = get_active_profile_helper(db)
    bankroll = get_total_profile_bankroll(db, profile.id)

    kelly_frac = profile.kelly_fraction if profile else 0.25
    max_stake_pct = profile.max_stake_pct if profile else 5.0

    win_prob = 1 / fair_odds
    rec = kelly_stake(
        odds=odds,
        win_probability=win_prob,
        bankroll=bankroll,
        kelly_fraction=kelly_frac,
        max_stake_pct=max_stake_pct,
    )

    return {
        "profile_id": profile.id,
        "recommended_stake": rec.stake,
        "kelly_stake": rec.kelly_stake,
        "max_stake": rec.max_stake,
        "bankroll": bankroll,
        "reason": rec.reason,
    }
