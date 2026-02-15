"""Bankroll service - balance management, bonus tracking, stake calculation."""

import logging
from sqlalchemy.orm import Session

from ..repositories import ProfileRepo, BetRepo
from ..db.models import Profile, Provider, ProfileProviderBonus
from ..bankroll.stake_calculator import StakeCalculator, BONUS_MIN_ODDS

logger = logging.getLogger(__name__)


# In-memory stake calculators (reset on server restart)
_stake_calculators: dict[int, StakeCalculator] = {}


class BankrollService:
    """Business logic for bankroll management, deposits, and stake calculation."""

    def __init__(self, db: Session):
        self.db = db
        self.profile_repo = ProfileRepo(db)
        self.bet_repo = BetRepo(db)

    def get_bankroll(self) -> dict:
        """Get provider balances and total bankroll for active profile."""
        profile = self.profile_repo.get_active()
        providers = self.db.query(Provider).filter(Provider.is_enabled == True).all()

        provider_data = []
        total = 0.0
        for p in providers:
            balance = self.profile_repo.get_balance(profile.id, p.id)
            total += balance
            provider_data.append({"id": p.id, "name": p.name, "balance": balance})

        return {
            "total": total,
            "profile_id": profile.id,
            "profile_name": profile.name,
            "providers": provider_data,
        }

    def get_stats(self) -> dict:
        """Get bankroll statistics for active profile."""
        profile = self.profile_repo.get_active()
        bets = self.bet_repo.get_settled(profile.id)

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

    def get_exposure(self) -> dict:
        """Get bankroll with exposure breakdown per provider."""
        profile = self.profile_repo.get_active()
        providers = self.db.query(Provider).filter(Provider.is_enabled == True).all()

        exposure_data = []
        total_balance = 0.0
        for provider in providers:
            balance = self.profile_repo.get_balance(profile.id, provider.id)
            total_balance += balance

            pending_bets = self.bet_repo.get_pending_for_provider(provider.id, profile.id)
            pending_exposure = sum(b.stake for b in pending_bets if not b.is_bonus)

            exposure_data.append({
                "provider_id": provider.id,
                "provider_name": provider.name,
                "total_balance": balance,
                "pending_exposure": pending_exposure,
                "pending_bets_count": len(pending_bets),
                "available": balance,
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

    def get_stake_calculator(self, profile_id: int) -> StakeCalculator:
        """Get or create a StakeCalculator for a profile, using profile risk settings."""
        # Load profile settings
        profile = self.db.query(Profile).filter(Profile.id == profile_id).first()
        bankroll = self.profile_repo.get_total_bankroll(profile_id)

        # Profile settings -> calculator params
        # kelly_fraction (0.25 = Quarter Kelly) caps the dynamic Kelly scaling
        # max_stake_pct (5.0 = 5%) -> single_bet_cap_pct (0.05)
        # min_edge_pct (2.0 = 2%) -> min_edge (0.02)
        max_kelly = profile.kelly_fraction if profile else 0.25
        single_bet_cap_pct = (profile.max_stake_pct / 100.0) if profile else 0.03
        min_edge = (profile.min_edge_pct / 100.0) if profile else 0.01

        if profile_id not in _stake_calculators:
            _stake_calculators[profile_id] = StakeCalculator(
                bankroll=bankroll,
                max_kelly=max_kelly,
                single_bet_cap_pct=single_bet_cap_pct,
                min_edge=min_edge,
            )

        calc = _stake_calculators[profile_id]

        # Always update to current values (profile settings may have changed)
        calc.update_bankroll(bankroll)
        calc.max_kelly = max_kelly
        calc.single_bet_cap_pct = single_bet_cap_pct
        calc.min_edge = min_edge

        # Always reload bonus statuses from DB
        calc.bonus_tracker.bonuses.clear()
        bonuses = self.db.query(ProfileProviderBonus).filter(
            ProfileProviderBonus.profile_id == profile_id,
            ProfileProviderBonus.bonus_status == "in_progress"
        ).all()

        for bonus in bonuses:
            if bonus.wagering_requirement and bonus.wagering_requirement > 0:
                calc.bonus_tracker.bonuses[bonus.provider_id] = {
                    "wagered": bonus.wagered_amount or 0.0,
                    "requirement": bonus.wagering_requirement,
                    "bonus_amount": bonus.bonus_amount or 0.0,
                    "min_odds": bonus.min_odds if bonus.min_odds else BONUS_MIN_ODDS,
                }

        return calc

    def get_status(self) -> dict:
        """Get comprehensive bankroll status including exposures and bonus progress."""
        profile = self.profile_repo.get_active()
        calc = self.get_stake_calculator(profile.id)

        bonuses = self.db.query(ProfileProviderBonus).filter(
            ProfileProviderBonus.profile_id == profile.id
        ).all()

        from datetime import datetime

        bonus_progress = {}
        for bonus in bonuses:
            provider_min_odds = bonus.min_odds if bonus.min_odds else BONUS_MIN_ODDS

            # Auto-expire in-progress bonuses past deadline
            if (bonus.bonus_status == "in_progress" and bonus.expires_at
                    and datetime.utcnow() > bonus.expires_at):
                bonus.bonus_status = "completed"
                bonus.updated_at = datetime.utcnow()

            days_remaining = None
            if bonus.expires_at and bonus.bonus_status == "in_progress":
                delta = bonus.expires_at - datetime.utcnow()
                days_remaining = max(0, delta.days)

            bonus_progress[bonus.provider_id] = {
                "status": bonus.bonus_status,
                "bonus_amount": bonus.bonus_amount or 0.0,
                "wagering_requirement": bonus.wagering_requirement or 0.0,
                "wagered_amount": bonus.wagered_amount or 0.0,
                "min_odds": provider_min_odds,
                "progress_pct": (
                    min(100.0, (bonus.wagered_amount or 0.0) / bonus.wagering_requirement * 100)
                    if bonus.wagering_requirement and bonus.wagering_requirement > 0
                    else 100.0
                ),
                "is_cleared": (
                    bonus.bonus_status in ("completed", "available", "claimed") or
                    (bonus.wagering_requirement and (bonus.wagered_amount or 0.0) >= bonus.wagering_requirement)
                ),
                "claimed_at": bonus.claimed_at.isoformat() if bonus.claimed_at else None,
                "expires_at": bonus.expires_at.isoformat() if bonus.expires_at else None,
                "days_remaining": days_remaining,
            }

        status = calc.get_status()

        return {
            "profile_id": profile.id,
            "profile_name": profile.name,
            "bankroll": status["bankroll"],
            "event_exposures": status["event_exposures"],
            "event_cap_pct": calc.event_tracker.max_event_exposure_pct * 100,
            "bonus_progress": bonus_progress,
            "min_odds_bonus_default": BONUS_MIN_ODDS,
        }

    def deposit_with_bonus(self, provider_id: str, amount: float) -> dict:
        """Deposit with automatic bonus claim for active profile."""
        provider = self.db.query(Provider).filter(Provider.id == provider_id).first()
        if not provider:
            return None

        from ..api.routes.providers import load_provider_bonuses
        bonus_config = load_provider_bonuses().get(provider_id, {})
        active_profile = self.profile_repo.get_active()

        bonus_record = self.db.query(ProfileProviderBonus).filter(
            ProfileProviderBonus.profile_id == active_profile.id,
            ProfileProviderBonus.provider_id == provider_id
        ).first()

        is_bonus_deposit = bonus_config.get('type') == 'bonusdeposit'
        is_available = not bonus_record or bonus_record.bonus_status == 'available'

        deposit_amount = amount
        bonus_amount = 0.0
        bonus_limit = bonus_config.get('amount', 0)

        if is_bonus_deposit and is_available and bonus_limit > 0:
            bonus_amount = min(deposit_amount, bonus_limit)

        old_balance = self.profile_repo.get_balance(active_profile.id, provider_id)
        total_added = deposit_amount + bonus_amount
        new_balance = self.profile_repo.adjust_balance(active_profile.id, provider_id, total_added)

        bonus_info = None
        if bonus_amount > 0:
            wagering_multiplier = bonus_config.get('wagering_multiplier', 10.0)
            bonus_min_odds = bonus_config.get('min_odds', 1.80)
            bonus_info = self.profile_repo.start_bonus_wagering(
                active_profile.id, provider_id, bonus_amount,
                wagering_multiplier, min_odds=bonus_min_odds,
            )

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
            "bonus_limit": bonus_limit if is_bonus_deposit else None,
            "wagering_requirement": bonus_info.get("wagering_requirement") if bonus_info else None,
            "min_odds": bonus_info.get("min_odds") if bonus_info else None,
        }

    @staticmethod
    def reset_calculators(profile_id: int | None = None):
        """Reset stake calculator exposure tracking."""
        if profile_id and profile_id in _stake_calculators:
            _stake_calculators[profile_id].reset_event_exposures()
