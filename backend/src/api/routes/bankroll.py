"""Bankroll API routes."""

from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
import yaml

from ...db.models import (
    Provider, Bet, Profile, ProfileProviderBonus, ProfileProviderBalance,
    get_active_profile, get_profile_balance, set_profile_balance,
    adjust_profile_balance, get_total_profile_bankroll,
    get_bonus_status, record_wagering, start_bonus_wagering,
)
from ..deps import get_db
from ..schemas import BulkBalanceUpdate, BalanceAdjustment, DepositRequest, StakePreviewRequest, RecordBetRequest
from ...bankroll.stake_calculator import StakeCalculator, BONUS_MIN_ODDS


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
    """Get provider balances and total bankroll for active profile."""
    profile = get_active_profile(db)
    providers = db.query(Provider).filter(Provider.is_enabled == True).all()

    provider_data = []
    total = 0.0
    for p in providers:
        balance = get_profile_balance(db, profile.id, p.id)
        total += balance
        provider_data.append({"id": p.id, "name": p.name, "balance": balance})

    return {
        "total": total,
        "profile_id": profile.id,
        "profile_name": profile.name,
        "providers": provider_data,
    }


@router.get("/stats")
async def get_bankroll_stats(db: Session = Depends(get_db)):
    """Get bankroll statistics for active profile."""
    profile = get_active_profile(db)

    # Get settled bets for this profile only
    bets = db.query(Bet).filter(
        Bet.result != "pending",
        Bet.profile_id == profile.id
    ).all()

    total_staked = sum(b.stake for b in bets)
    total_profit = sum(b.profit for b in bets)
    win_count = len([b for b in bets if b.result == "won"])
    loss_count = len([b for b in bets if b.result == "lost"])
    void_count = len([b for b in bets if b.result == "void"])

    return {
        "profile_id": profile.id,
        "profile_name": profile.name,
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
    """Set balance for multiple providers at once for active profile."""
    profile = get_active_profile(db)

    if data.provider_ids:
        providers = db.query(Provider).filter(Provider.id.in_(data.provider_ids)).all()
    else:
        providers = db.query(Provider).filter(Provider.is_enabled == True).all()

    if not providers:
        raise HTTPException(404, "No providers found")

    updated_count = 0
    for provider in providers:
        set_profile_balance(db, profile.id, provider.id, data.balance)
        updated_count += 1

    db.commit()

    total_balance = get_total_profile_bankroll(db, profile.id)

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
    db: Session = Depends(get_db)
):
    """Add or subtract from provider balance for active profile."""
    profile = get_active_profile(db)

    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(404, f"Provider {provider_id} not found")

    old_balance = get_profile_balance(db, profile.id, provider_id)
    new_balance = adjust_profile_balance(db, profile.id, provider_id, data.amount)
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
    db: Session = Depends(get_db)
):
    """
    Deposit with automatic bonus claim for active profile.

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
    active_profile = get_active_profile(db)
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

    # 5. Update balance using per-profile balance
    old_balance = get_profile_balance(db, active_profile.id, provider_id)
    total_added = deposit_amount + bonus_amount
    new_balance = adjust_profile_balance(db, active_profile.id, provider_id, total_added)

    # 6. Update bonus status if bonus was claimed
    bonus_info = None
    if bonus_amount > 0:
        # Get wagering multiplier from config (default 10x)
        wagering_multiplier = bonus_config.get('wagering_multiplier', 10.0)

        # Start bonus wagering tracking
        bonus_info = start_bonus_wagering(
            db,
            active_profile.id,
            provider_id,
            bonus_amount,
            wagering_multiplier,
        )

    db.commit()

    return {
        "success": True,
        "profile_id": active_profile.id,
        "provider_id": provider_id,
        "deposit": deposit_amount,
        "bonus_claimed": bonus_amount,
        "total_added": total_added,
        "old_balance": old_balance,
        "new_balance": new_balance,
        "bonus_status": bonus_info.get("status") if bonus_info else None,
        "bonus_limit": bonus_limit if is_double_deposit else None,
        "wagering_requirement": bonus_info.get("wagering_requirement") if bonus_info else None,
    }


@router.post("/reset-all")
async def reset_all_balances(db: Session = Depends(get_db)):
    """Reset all provider balances to 0 for active profile."""
    profile = get_active_profile(db)
    providers = db.query(Provider).all()

    for provider in providers:
        set_profile_balance(db, profile.id, provider.id, 0.0)

    db.commit()

    return {
        "success": True,
        "profile_id": profile.id,
        "reset_count": len(providers),
        "message": "All balances reset to 0",
    }


@router.get("/exposure")
async def get_bankroll_exposure(db: Session = Depends(get_db)):
    """Get bankroll with exposure breakdown per provider for active profile."""
    profile = get_active_profile(db)
    providers = db.query(Provider).filter(Provider.is_enabled == True).all()

    exposure_data = []
    total_balance = 0.0
    for provider in providers:
        balance = get_profile_balance(db, profile.id, provider.id)
        total_balance += balance

        # Calculate pending bets for this provider and profile
        pending_bets = db.query(Bet).filter(
            Bet.provider_id == provider.id,
            Bet.profile_id == profile.id,
            Bet.result == "pending"
        ).all()

        pending_exposure = sum(b.stake for b in pending_bets if not b.is_bonus)
        pending_count = len(pending_bets)

        exposure_data.append({
            "provider_id": provider.id,
            "provider_name": provider.name,
            "total_balance": balance,
            "pending_exposure": pending_exposure,
            "pending_bets_count": pending_count,
            "available": balance,  # Already deducted when bet placed
        })

    total_pending = sum(e["pending_exposure"] for e in exposure_data)

    return {
        "profile_id": profile.id,
        "profile_name": profile.name,
        "total_balance": total_balance,
        "total_pending": total_pending,
        "total_available": total_balance,
        "providers": exposure_data,
    }


# In-memory stake calculator (reset on server restart)
# This tracks daily/event exposure across requests
_stake_calculators: dict[int, StakeCalculator] = {}


def get_stake_calculator(db: Session, profile_id: int) -> StakeCalculator:
    """Get or create a StakeCalculator for a profile."""
    if profile_id not in _stake_calculators:
        bankroll = get_total_profile_bankroll(db, profile_id)
        _stake_calculators[profile_id] = StakeCalculator(bankroll=bankroll)

    calc = _stake_calculators[profile_id]

    # Always update bankroll to current value
    bankroll = get_total_profile_bankroll(db, profile_id)
    calc.update_bankroll(bankroll)

    # Always reload bonus statuses from DB (they may have changed)
    calc.bonus_tracker.bonuses.clear()
    bonuses = db.query(ProfileProviderBonus).filter(
        ProfileProviderBonus.profile_id == profile_id,
        ProfileProviderBonus.bonus_status == "in_progress"
    ).all()

    for bonus in bonuses:
        if bonus.wagering_requirement and bonus.wagering_requirement > 0:
            calc.bonus_tracker.bonuses[bonus.provider_id] = {
                "wagered": bonus.wagered_amount or 0.0,
                "requirement": bonus.wagering_requirement,
                "bonus_amount": bonus.bonus_amount or 0.0,
            }

    return calc


@router.get("/status")
async def get_bankroll_status(db: Session = Depends(get_db)):
    """
    Get comprehensive bankroll status including exposures and bonus progress.

    Returns:
        - Total bankroll
        - Daily exposure tracking
        - Event exposures
        - Bonus wagering progress per provider
    """
    profile = get_active_profile(db)
    calc = get_stake_calculator(db, profile.id)

    # Get bonus progress from DB
    bonuses = db.query(ProfileProviderBonus).filter(
        ProfileProviderBonus.profile_id == profile.id
    ).all()

    bonus_progress = {}
    for bonus in bonuses:
        bonus_progress[bonus.provider_id] = {
            "status": bonus.bonus_status,
            "bonus_amount": bonus.bonus_amount or 0.0,
            "wagering_requirement": bonus.wagering_requirement or 0.0,
            "wagered_amount": bonus.wagered_amount or 0.0,
            "progress_pct": (
                min(100.0, (bonus.wagered_amount or 0.0) / bonus.wagering_requirement * 100)
                if bonus.wagering_requirement and bonus.wagering_requirement > 0
                else 100.0
            ),
            "is_cleared": (
                bonus.bonus_status == "completed" or
                bonus.bonus_status == "available" or
                (bonus.wagering_requirement and (bonus.wagered_amount or 0.0) >= bonus.wagering_requirement)
            ),
        }

    status = calc.get_status()

    return {
        "profile_id": profile.id,
        "profile_name": profile.name,
        "bankroll": status["bankroll"],
        "daily_exposure": status["daily_exposure"],
        "daily_remaining": status["daily_remaining"],
        "daily_cap_pct": calc.daily_tracker.max_daily_exposure_pct * 100,
        "event_exposures": status["event_exposures"],
        "event_cap_pct": calc.event_tracker.max_event_exposure_pct * 100,
        "bonus_progress": bonus_progress,
        "min_odds_bonus": BONUS_MIN_ODDS,
    }


@router.post("/stake-preview")
async def preview_stake(data: StakePreviewRequest, db: Session = Depends(get_db)):
    """
    Preview recommended stake for an opportunity.

    Uses the stake calculator with current bankroll, exposure caps,
    and bonus status to recommend a stake.
    """
    profile = get_active_profile(db)
    calc = get_stake_calculator(db, profile.id)

    # Convert edge percentage to decimal
    edge_decimal = data.edge_pct / 100.0

    # Calculate stake
    result = calc.calculate(
        edge_raw=edge_decimal,
        odds=data.odds,
        event_id=data.event_id,
        provider_id=data.provider_id,
        high_confidence=True,  # Assume high confidence for preview
    )

    # Get bonus status if provider specified
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
        "min_odds_applied": 0.0 if bonus_cleared else BONUS_MIN_ODDS,
    }


@router.post("/record-bet")
async def record_bet_exposure(data: RecordBetRequest, db: Session = Depends(get_db)):
    """
    Record a placed bet for exposure tracking.

    Updates the stake calculator's event and daily exposure trackers,
    as well as bonus wagering progress.

    Note: This does NOT create a Bet record - use /api/bets for that.
    This endpoint is for exposure tracking when you want to track
    without creating a full bet record.
    """
    profile = get_active_profile(db)
    calc = get_stake_calculator(db, profile.id)

    # Record in calculator
    calc.record_bet(
        event_id=data.event_id,
        provider_id=data.provider_id,
        stake=data.stake,
        odds=data.odds,
    )

    # Also update DB bonus wagering
    wagering_status = record_wagering(
        db, profile.id, data.provider_id, data.stake, data.odds
    )
    db.commit()

    return {
        "success": True,
        "event_exposure": calc.event_tracker.get_exposure(data.event_id),
        "daily_exposure": calc.daily_tracker.get_daily_exposure(),
        "bonus_wagering": wagering_status if wagering_status.get("status") == "in_progress" else None,
    }


@router.post("/reset-calculator")
async def reset_calculator(db: Session = Depends(get_db)):
    """
    Reset the stake calculator's exposure tracking.

    Useful after events settle or at start of new day.
    """
    profile = get_active_profile(db)

    if profile.id in _stake_calculators:
        _stake_calculators[profile.id].reset_event_exposures()
        _stake_calculators[profile.id].reset_daily_exposure()

    return {
        "success": True,
        "message": "Exposure tracking reset",
    }
