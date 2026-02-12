"""Bankroll API routes."""

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from ...services import BankrollService
from ...repositories import ProfileRepo
from ...db.models import Provider
from ..deps import get_db
from ..schemas import BulkBalanceUpdate, BalanceAdjustment, DepositRequest, StakePreviewRequest, RecordBetRequest
from .providers import load_provider_bonuses

router = APIRouter(prefix="/api/bankroll", tags=["bankroll"])


def _get_service(db: Session = Depends(get_db)) -> BankrollService:
    return BankrollService(db)


@router.get("")
async def get_bankroll(service: BankrollService = Depends(_get_service)):
    """Get provider balances and total bankroll for active profile."""
    return service.get_bankroll()


@router.get("/bonuses")
async def get_provider_bonuses():
    """Get bonus configurations for all providers from providers.yaml."""
    return load_provider_bonuses()


@router.get("/stats")
async def get_bankroll_stats(service: BankrollService = Depends(_get_service)):
    """Get bankroll statistics for active profile."""
    return service.get_stats()


@router.post("/set-all")
async def set_all_balances(data: BulkBalanceUpdate, db: Session = Depends(get_db)):
    """Set balance for multiple providers at once for active profile."""
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()

    if data.provider_ids:
        providers = db.query(Provider).filter(Provider.id.in_(data.provider_ids)).all()
    else:
        providers = db.query(Provider).filter(Provider.is_enabled == True).all()

    if not providers:
        raise HTTPException(404, "No providers found")

    updated_count = 0
    for provider in providers:
        profile_repo.set_balance(profile.id, provider.id, data.balance)
        updated_count += 1

    db.commit()
    total_balance = profile_repo.get_total_bankroll(profile.id)

    return {
        "success": True,
        "profile_id": profile.id,
        "updated_count": updated_count,
        "balance_per_provider": data.balance,
        "total_balance": total_balance,
    }


@router.post("/adjust/{provider_id}")
async def adjust_balance(
    provider_id: str,
    data: BalanceAdjustment,
    db: Session = Depends(get_db),
):
    """Add or subtract from provider balance for active profile."""
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()

    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(404, f"Provider {provider_id} not found")

    old_balance = profile_repo.get_balance(profile.id, provider_id)
    new_balance = profile_repo.adjust_balance(profile.id, provider_id, data.amount)
    db.commit()

    return {
        "success": True,
        "profile_id": profile.id,
        "provider_id": provider_id,
        "old_balance": old_balance,
        "adjustment": data.amount,
        "new_balance": new_balance,
    }


@router.post("/deposit/{provider_id}")
async def deposit_with_bonus(
    provider_id: str,
    data: DepositRequest,
    service: BankrollService = Depends(_get_service),
):
    """Deposit with automatic bonus claim for active profile."""
    result = service.deposit_with_bonus(provider_id, data.amount)
    if result is None:
        raise HTTPException(404, f"Provider {provider_id} not found")
    return result


@router.post("/reset-all")
async def reset_all_balances(db: Session = Depends(get_db)):
    """Reset all provider balances to 0 for active profile."""
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()
    providers = db.query(Provider).all()

    for provider in providers:
        profile_repo.set_balance(profile.id, provider.id, 0.0)

    db.commit()

    return {
        "success": True,
        "profile_id": profile.id,
        "reset_count": len(providers),
        "message": "All balances reset to 0",
    }


@router.get("/exposure")
async def get_bankroll_exposure(service: BankrollService = Depends(_get_service)):
    """Get bankroll with exposure breakdown per provider for active profile."""
    return service.get_exposure()


@router.get("/status")
async def get_bankroll_status(service: BankrollService = Depends(_get_service)):
    """Get comprehensive bankroll status including exposures and bonus progress."""
    return service.get_status()


@router.post("/stake-preview")
async def preview_stake(data: StakePreviewRequest, service: BankrollService = Depends(_get_service)):
    """Preview recommended stake for an opportunity."""
    profile = service.profile_repo.get_active()
    calc = service.get_stake_calculator(profile.id)

    edge_decimal = data.edge_pct / 100.0

    result = calc.calculate(
        edge_raw=edge_decimal,
        odds=data.odds,
        event_id=data.event_id,
        provider_id=data.provider_id,
        high_confidence=True,
    )

    bonus_cleared = True
    if data.provider_id:
        bonus_cleared = calc.bonus_tracker.is_cleared(data.provider_id)

    return {
        "recommended_stake": result.stake,
        "kelly_fraction": result.kelly_fraction,
        "edge_raw": result.edge_raw,
        "edge_used": result.edge_used,
        "bankroll": result.bankroll,
        "raw_kelly_stake": result.raw_kelly_stake,
        "single_bet_cap": result.single_bet_cap,
        "was_capped_single": result.was_capped_single,
        "was_capped_event": result.was_capped_event,
        "was_capped_daily": result.was_capped_daily,
        "skip_reason": result.skip_reason,
        "bonus_cleared": bonus_cleared,
        "min_odds_applied": 0.0 if bonus_cleared else calc.get_min_odds_for_provider(data.provider_id or ""),
    }


@router.post("/record-bet")
async def record_bet_exposure(data: RecordBetRequest, service: BankrollService = Depends(_get_service)):
    """Record a placed bet for exposure tracking."""
    profile = service.profile_repo.get_active()
    calc = service.get_stake_calculator(profile.id)

    calc.record_bet(
        event_id=data.event_id,
        provider_id=data.provider_id,
        stake=data.stake,
        odds=data.odds,
    )

    wagering_status = service.profile_repo.record_wagering(
        profile.id, data.provider_id, data.stake, data.odds
    )

    return {
        "success": True,
        "event_exposure": calc.event_tracker.get_exposure(data.event_id),
        "daily_exposure": calc.daily_tracker.get_daily_exposure(),
        "bonus_wagering": wagering_status if wagering_status.get("status") == "in_progress" else None,
    }


@router.post("/claim-bonus/{provider_id}")
async def claim_bonus(provider_id: str, db: Session = Depends(get_db)):
    """Mark a provider's bonus as already claimed for active profile."""
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(404, f"Provider {provider_id} not found")

    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()
    result = profile_repo.claim_bonus(profile.id, provider_id)
    db.commit()

    return {"success": True, "provider_id": provider_id, **result}


@router.post("/unclaim-bonus/{provider_id}")
async def unclaim_bonus(provider_id: str, db: Session = Depends(get_db)):
    """Reset a claimed bonus back to available for active profile."""
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(404, f"Provider {provider_id} not found")

    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()
    result = profile_repo.unclaim_bonus(profile.id, provider_id)
    db.commit()

    return {"success": True, "provider_id": provider_id, **result}


@router.post("/reset-calculator")
async def reset_calculator(service: BankrollService = Depends(_get_service)):
    """Reset the stake calculator's exposure tracking."""
    profile = service.profile_repo.get_active()
    BankrollService.reset_calculators(profile.id)

    return {
        "success": True,
        "message": "Exposure tracking reset",
    }
