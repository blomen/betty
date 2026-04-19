"""Bankroll API routes."""

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from ...db.models import ProfileProviderBonus, Provider
from ...repositories import ProfileRepo
from ...services import BankrollService
from ..deps import get_db
from ..schemas import (
    AllocateRequest,
    BalanceSet,
    BonusTransitionRequest,
    BulkBalanceUpdate,
    DepositRequest,
    RecordBetRequest,
    StakePreviewRequest,
)
from .providers import load_provider_bonuses

router = APIRouter(prefix="/api/bankroll", tags=["bankroll"])


def _get_service(db: Session = Depends(get_db)) -> BankrollService:
    return BankrollService(db)


@router.get("")
def get_bankroll(service: BankrollService = Depends(_get_service)):
    """Get provider balances and total bankroll for active profile."""
    return service.get_bankroll()


@router.get("/bonuses")
def get_provider_bonuses():
    """Get bonus configurations for all providers from providers.yaml."""
    return load_provider_bonuses()


@router.get("/stats")
def get_bankroll_stats(service: BankrollService = Depends(_get_service)):
    """Get bankroll statistics for active profile."""
    return service.get_stats()


@router.post("/set-all")
def set_all_balances(data: BulkBalanceUpdate, db: Session = Depends(get_db)):
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


@router.post("/set/{provider_id}")
def set_balance(
    provider_id: str,
    data: BalanceSet,
    db: Session = Depends(get_db),
):
    """Set exact balance for a provider (for manual sync)."""
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()

    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(404, f"Provider {provider_id} not found")

    old_balance = profile_repo.get_balance(profile.id, provider_id)
    profile_repo.set_balance(profile.id, provider_id, data.balance)
    db.commit()

    return {
        "success": True,
        "profile_id": profile.id,
        "provider_id": provider_id,
        "old_balance": old_balance,
        "new_balance": data.balance,
    }


@router.post("/allocate")
def allocate_funds(
    data: AllocateRequest,
    service: BankrollService = Depends(_get_service),
):
    """Given liquid amount (or null for unbounded), return allocation envelope."""
    if data.liquid_amount is not None and data.liquid_amount < 0:
        raise HTTPException(400, "liquid_amount must be non-negative")
    envelope = service.allocate(data.liquid_amount)
    # effective_budget is float('inf') for unbounded mode — coerce to None for JSON
    if envelope.get("effective_budget") == float("inf"):
        envelope["effective_budget"] = None
    return envelope


@router.get("/liquid")
def get_liquid_balance(service: BankrollService = Depends(_get_service)):
    """Get last-stored liquid balance for the active profile."""
    return {"liquid_balance": service.get_liquid_balance()}


@router.post("/deposit/{provider_id}")
def deposit_with_bonus(
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
def reset_all_balances(db: Session = Depends(get_db)):
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
def get_bankroll_exposure(service: BankrollService = Depends(_get_service)):
    """Get bankroll with exposure breakdown per provider for active profile."""
    return service.get_exposure()


@router.get("/status")
def get_bankroll_status(service: BankrollService = Depends(_get_service)):
    """Get comprehensive bankroll status including exposures and bonus progress."""
    return service.get_status()


@router.post("/stake-preview")
def preview_stake(data: StakePreviewRequest, service: BankrollService = Depends(_get_service)):
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
        "skip_reason": result.skip_reason,
        "counts_toward_wagering": result.counts_toward_wagering,
        "bonus_cleared": bonus_cleared,
        "min_odds_applied": 0.0 if bonus_cleared else calc.get_min_odds_for_provider(data.provider_id or ""),
    }


@router.post("/record-bet")
def record_bet_exposure(data: RecordBetRequest, service: BankrollService = Depends(_get_service)):
    """Record a placed bet for exposure tracking."""
    profile = service.profile_repo.get_active()
    calc = service.get_stake_calculator(profile.id)

    calc.record_bet(
        event_id=data.event_id,
        provider_id=data.provider_id,
        stake=data.stake,
        odds=data.odds,
    )

    wagering_status = service.profile_repo.record_wagering(profile.id, data.provider_id, data.stake, data.odds)

    return {
        "success": True,
        "bonus_wagering": wagering_status if wagering_status.get("status") == "in_progress" else None,
    }


@router.post("/bonus-transition/{provider_id}")
def bonus_transition(
    provider_id: str,
    data: BonusTransitionRequest,
    db: Session = Depends(get_db),
):
    """Advance bonus status for a provider (freebet or bonusdeposit phases)."""
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(404, f"Provider {provider_id} not found")

    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()

    if data.action == "start_freebet":
        bonus_config = load_provider_bonuses().get(provider_id, {})
        result = profile_repo.start_freebet_tracking(
            profile.id,
            provider_id,
            bonus_amount=bonus_config.get("amount", 0),
            min_odds=bonus_config.get("min_odds", 1.80),
        )
    elif data.action == "trigger_settled":
        # Check bonus type to decide next state
        bonus_record = (
            db.query(ProfileProviderBonus)
            .filter(
                ProfileProviderBonus.profile_id == profile.id,
                ProfileProviderBonus.provider_id == provider_id,
                ProfileProviderBonus.bonus_status == "trigger_needed",
            )
            .first()
        )
        if not bonus_record:
            raise HTTPException(400, f"No trigger_needed bonus for {provider_id}")

        if bonus_record.bonus_type == "bonusdeposit":
            # Bonus unlocked — add bonus money to balance, start real wagering
            bonus_amount = bonus_record.bonus_amount or 0.0
            if bonus_amount > 0:
                profile_repo.adjust_balance(profile.id, provider_id, bonus_amount)
            bonus_config = load_provider_bonuses().get(provider_id, {})
            bonus_record.bonus_status = "in_progress"
            bonus_record.wagered_amount = 0.0
            bonus_record.wagering_requirement = bonus_amount * bonus_config.get("wagering_multiplier", 12.0)
            bonus_record.min_odds = bonus_config.get("min_odds", 1.80)
            bonus_record.updated_at = datetime.now(timezone.utc)
            result = profile_repo.get_bonus_status(profile.id, provider_id)
            result["bonus_credited"] = bonus_amount
        else:
            # Freebet: trigger settled → freebet available
            result = profile_repo.advance_freebet_status(profile.id, provider_id, "freebet_available")
    elif data.action == "freebet_used":
        result = profile_repo.advance_freebet_status(profile.id, provider_id, "completed")
    else:
        raise HTTPException(400, f"Unknown action: {data.action}")

    db.commit()
    return {"success": True, "provider_id": provider_id, **result}


@router.post("/claim-bonus/{provider_id}")
def claim_bonus(provider_id: str, db: Session = Depends(get_db)):
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
def unclaim_bonus(provider_id: str, db: Session = Depends(get_db)):
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
def reset_calculator(service: BankrollService = Depends(_get_service)):
    """Reset the stake calculator's exposure tracking."""
    profile = service.profile_repo.get_active()
    BankrollService.reset_calculators(profile.id)

    return {
        "success": True,
        "message": "Exposure tracking reset",
    }


@router.post("/backfill-wagering")
def backfill_wagering(db: Session = Depends(get_db)):
    """Recalculate wagered_amount for all active bonuses from settled bets.

    Fixes bonuses where wagering wasn't tracked (e.g., bets settled via edit_bet).
    Replays all settled bets in chronological order through record_wagering().
    """
    import logging

    from ...db.models import Bet

    logger = logging.getLogger(__name__)
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()

    # Get all active bonuses
    active_bonuses = (
        db.query(ProfileProviderBonus)
        .filter(
            ProfileProviderBonus.profile_id == profile.id,
            ProfileProviderBonus.bonus_status.in_(("in_progress", "trigger_needed")),
        )
        .all()
    )

    results = []
    for bonus in active_bonuses:
        old_wagered = bonus.wagered_amount or 0.0
        old_status = bonus.bonus_status

        # Reset wagered amount to replay from scratch
        bonus.wagered_amount = 0.0

        # Get all settled bets for this provider, ordered by settlement time
        settled_bets = (
            db.query(Bet)
            .filter(
                Bet.profile_id == profile.id,
                Bet.provider_id == bonus.provider_id,
                Bet.result.in_(("won", "lost", "void")),
                Bet.settled_at.isnot(None),
            )
            .order_by(Bet.settled_at)
            .all()
        )

        # Only count bets settled after the bonus was claimed
        qualifying_bets = [b for b in settled_bets if not bonus.claimed_at or b.settled_at >= bonus.claimed_at]

        # Replay each bet through record_wagering
        for bet in qualifying_bets:
            profile_repo.record_wagering(profile.id, bonus.provider_id, bet.stake, bet.odds)
            # If bonus transitioned (trigger_needed → in_progress), stop replaying
            # since remaining bets belong to the new phase
            db.refresh(bonus)
            if bonus.bonus_status != old_status:
                # Continue replaying remaining bets into the new phase
                old_status = bonus.bonus_status

        db.refresh(bonus)
        results.append(
            {
                "provider_id": bonus.provider_id,
                "old_wagered": old_wagered,
                "new_wagered": bonus.wagered_amount,
                "status": bonus.bonus_status,
                "total_bets_replayed": len(qualifying_bets),
                "wagering_requirement": bonus.wagering_requirement,
            }
        )

        logger.info(
            f"[Backfill] {bonus.provider_id}: wagered {old_wagered} → {bonus.wagered_amount} "
            f"({len(qualifying_bets)} bets replayed, status={bonus.bonus_status})"
        )

    return {"success": True, "results": results}


# ── Bankroll Planner ──


@router.get("/plan")
def get_bankroll_plan(db: Session = Depends(get_db)):
    """Get current planner recommendation (returns cached if fresh)."""
    from ...services.planner_service import BankrollPlannerService

    profile = ProfileRepo(db).get_active()
    if not profile:
        raise HTTPException(status_code=404, detail="No active profile")

    service = BankrollPlannerService(db)
    recommendation = service.get_latest_recommendation(profile.id)
    if not recommendation:
        return {"status": "no_plan", "message": "No plan available. POST /plan/replan to generate."}
    return recommendation.to_dict()


@router.post("/plan/replan")
def trigger_replan(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Trigger re-planning in background. Returns immediately."""
    from ...services.planner_service import BankrollPlannerService

    profile = ProfileRepo(db).get_active()
    if not profile:
        raise HTTPException(status_code=404, detail="No active profile")

    service = BankrollPlannerService(db)

    async def _run_planner():
        await service.run_planner(profile.id)

    background_tasks.add_task(_run_planner)
    return {"status": "replanning", "message": "Re-plan triggered in background."}
