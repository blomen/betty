"""Bankroll API routes."""

from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
import yaml

from ...db.models import Provider, Bet, Profile, ProfileProviderBonus
from ..deps import get_db
from ..schemas import BulkBalanceUpdate, BalanceAdjustment, DepositRequest


def load_provider_bonuses() -> dict[str, dict]:
    """Load bonus info from providers.yaml config."""
    config_path = Path(__file__).parent.parent.parent / "config" / "providers.yaml"
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

router = APIRouter(prefix="/api/bankroll", tags=["bankroll"])


@router.get("")
async def get_bankroll(db: Session = Depends(get_db)):
    """Get provider balances and total bankroll."""
    providers = db.query(Provider).filter(Provider.is_enabled == True).all()

    return {
        "total": sum(p.balance for p in providers),
        "providers": [
            {"id": p.id, "name": p.name, "balance": p.balance}
            for p in providers
        ],
    }


@router.get("/stats")
async def get_bankroll_stats(db: Session = Depends(get_db)):
    """Get bankroll statistics including bet history."""
    # Get all settled bets
    bets = db.query(Bet).filter(Bet.result != "pending").all()

    total_staked = sum(b.stake for b in bets)
    total_profit = sum(b.profit for b in bets)
    win_count = len([b for b in bets if b.result == "won"])
    loss_count = len([b for b in bets if b.result == "lost"])
    void_count = len([b for b in bets if b.result == "void"])

    return {
        "total_bets": len(bets),
        "wins": win_count,
        "losses": loss_count,
        "voids": void_count,
        "total_staked": round(total_staked, 2),
        "total_profit": round(total_profit, 2),
        "roi_pct": round(total_profit / total_staked * 100, 2) if total_staked > 0 else 0,
        "win_rate": round(win_count / len(bets) * 100, 2) if len(bets) > 0 else 0,
    }


@router.post("/set-all")
async def set_all_balances(data: BulkBalanceUpdate, db: Session = Depends(get_db)):
    """Set balance for multiple providers at once."""
    if data.provider_ids:
        providers = db.query(Provider).filter(Provider.id.in_(data.provider_ids)).all()
    else:
        providers = db.query(Provider).filter(Provider.is_enabled == True).all()

    if not providers:
        raise HTTPException(404, "No providers found")

    updated_count = 0
    for provider in providers:
        provider.balance = data.balance
        provider.updated_at = datetime.utcnow()
        updated_count += 1

    db.commit()

    total_balance = sum(p.balance for p in providers)

    return {
        "success": True,
        "updated_count": updated_count,
        "balance_per_provider": data.balance,
        "total_balance": total_balance,
    }


@router.post("/adjust/{provider_id}")
async def adjust_balance(
    provider_id: str,
    data: BalanceAdjustment,
    db: Session = Depends(get_db)
):
    """Add or subtract from provider balance."""
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(404, f"Provider {provider_id} not found")

    old_balance = provider.balance
    provider.balance += data.amount
    provider.updated_at = datetime.utcnow()
    db.commit()

    return {
        "success": True,
        "provider_id": provider_id,
        "old_balance": old_balance,
        "adjustment": data.amount,
        "new_balance": provider.balance,
    }


@router.post("/deposit/{provider_id}")
async def deposit_with_bonus(
    provider_id: str,
    data: DepositRequest,
    db: Session = Depends(get_db)
):
    """
    Deposit with automatic bonus claim.

    For providers with a double deposit bonus:
    1. Adds deposit amount to balance
    2. Adds bonus amount (up to configured limit) to balance
    3. Sets bonus_status to 'in_progress'

    Returns breakdown of deposit and bonus amounts.
    """
    # 1. Get provider
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(404, f"Provider {provider_id} not found")

    # 2. Get bonus config from providers.yaml
    bonus_config = load_provider_bonuses().get(provider_id, {})

    # 3. Get active profile and check bonus eligibility
    active_profile = db.query(Profile).filter(Profile.is_active == True).first()
    bonus_record = None
    if active_profile:
        bonus_record = db.query(ProfileProviderBonus).filter(
            ProfileProviderBonus.profile_id == active_profile.id,
            ProfileProviderBonus.provider_id == provider_id
        ).first()

    # Check if bonus is available
    is_double_deposit = bonus_config.get('type') == 'doubledeposit'
    is_available = not bonus_record or bonus_record.bonus_status == 'available'

    # 4. Calculate amounts
    deposit_amount = data.amount
    bonus_amount = 0.0
    bonus_limit = bonus_config.get('amount', 0)

    if is_double_deposit and is_available and bonus_limit > 0:
        # Bonus matches deposit up to the configured limit
        bonus_amount = min(deposit_amount, bonus_limit)

    # 5. Update balance
    old_balance = provider.balance
    provider.balance += deposit_amount + bonus_amount
    provider.updated_at = datetime.utcnow()

    # 6. Update bonus status if bonus was claimed
    new_bonus_status = None
    if bonus_amount > 0 and active_profile:
        if bonus_record:
            bonus_record.bonus_status = 'in_progress'
            bonus_record.updated_at = datetime.utcnow()
        else:
            bonus_record = ProfileProviderBonus(
                profile_id=active_profile.id,
                provider_id=provider_id,
                bonus_status='in_progress'
            )
            db.add(bonus_record)
        new_bonus_status = 'in_progress'

    db.commit()

    return {
        "success": True,
        "provider_id": provider_id,
        "deposit": deposit_amount,
        "bonus_claimed": bonus_amount,
        "total_added": deposit_amount + bonus_amount,
        "old_balance": old_balance,
        "new_balance": provider.balance,
        "bonus_status": new_bonus_status,
        "bonus_limit": bonus_limit if is_double_deposit else None,
    }


@router.post("/reset-all")
async def reset_all_balances(db: Session = Depends(get_db)):
    """Reset all provider balances to 0."""
    providers = db.query(Provider).all()

    for provider in providers:
        provider.balance = 0.0
        provider.updated_at = datetime.utcnow()

    db.commit()

    return {
        "success": True,
        "reset_count": len(providers),
        "message": "All balances reset to 0",
    }


@router.get("/exposure")
async def get_bankroll_exposure(db: Session = Depends(get_db)):
    """Get bankroll with exposure breakdown per provider."""
    providers = db.query(Provider).filter(Provider.is_enabled == True).all()

    exposure_data = []
    for provider in providers:
        # Calculate pending bets for this provider
        pending_bets = db.query(Bet).filter(
            Bet.provider_id == provider.id,
            Bet.result == "pending"
        ).all()

        pending_exposure = sum(b.stake for b in pending_bets if not b.is_bonus)
        pending_count = len(pending_bets)

        exposure_data.append({
            "provider_id": provider.id,
            "provider_name": provider.name,
            "total_balance": provider.balance,
            "pending_exposure": pending_exposure,
            "pending_bets_count": pending_count,
            "available": provider.balance,  # Already deducted when bet placed
        })

    total_balance = sum(p.balance for p in providers)
    total_pending = sum(e["pending_exposure"] for e in exposure_data)

    return {
        "total_balance": total_balance,
        "total_pending": total_pending,
        "total_available": total_balance,
        "providers": exposure_data,
    }
