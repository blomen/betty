"""Bankroll service - balance management, bonus tracking, stake calculation."""

import logging
from datetime import datetime
from sqlalchemy.orm import Session

from ..repositories import ProfileRepo, BetRepo
from ..db.models import Profile, Provider, ProfileProviderBonus
from ..bankroll.stake_calculator import StakeCalculator, BONUS_MIN_ODDS
from ..config import get_exchange_rate, get_provider_currency
from ..constants import PLATFORM_MAP

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
        total_sek = 0.0
        for p in providers:
            balance = self.profile_repo.get_balance(profile.id, p.id)
            currency = get_provider_currency(p.id)
            rate = get_exchange_rate(p.id)
            total_sek += balance * rate
            provider_data.append({
                "id": p.id,
                "name": p.name,
                "balance": balance,
                "currency": currency,
                "exchange_rate_sek": rate,
                "balance_sek": round(balance * rate, 2),
            })

        return {
            "total": total_sek,
            "profile_id": profile.id,
            "profile_name": profile.name,
            "providers": provider_data,
        }

    def get_stats(self) -> dict:
        """Get bankroll statistics for active profile."""
        from ..config import get_exchange_rate

        profile = self.profile_repo.get_active()
        bets = self.bet_repo.get_settled(profile.id)

        def to_sek(amount: float, bet) -> float:
            """Convert bet amount to SEK using provider exchange rate."""
            currency = getattr(bet, "currency", None) or "SEK"
            if currency == "SEK":
                return amount
            return amount * get_exchange_rate(bet.provider_id)

        total_deposited = profile.total_deposited or 0.0
        total_withdrawn = profile.total_withdrawn or 0.0
        net_deposited = total_deposited - total_withdrawn
        bet_profit = sum(to_sek(b.profit, b) for b in bets if not b.is_bonus)
        freebet_profit = sum(to_sek(b.profit, b) for b in bets if b.is_bonus)
        total_staked = sum(to_sek(b.stake, b) for b in bets)
        win_count = len([b for b in bets if b.result == "won"])
        loss_count = len([b for b in bets if b.result == "lost"])
        void_count = len([b for b in bets if b.result == "void"])

        # Bonus deposits = pure profit, but only after wagering is completed.
        # Exclude freebets — their profit is already tracked via the settled
        # bet's b.profit (is_bonus=True), counted in freebet_profit above.
        bonus_records = self.db.query(ProfileProviderBonus).filter(
            ProfileProviderBonus.profile_id == profile.id,
            ProfileProviderBonus.bonus_amount > 0,
            ProfileProviderBonus.bonus_status.in_(["completed", "claimed"]),
            ProfileProviderBonus.bonus_type != "freebet",
        ).all()
        bonus_profit = sum(b.bonus_amount for b in bonus_records)

        # Combined: betting profit + freebet profit + deposit bonus profit
        combined_profit = bet_profit + freebet_profit + bonus_profit

        # CLV metrics
        clv_values = [b.clv_pct for b in bets if b.clv_pct is not None]
        clv_count = len(clv_values)
        avg_clv = round(sum(clv_values) / clv_count, 2) if clv_count > 0 else 0
        clv_positive_pct = round(len([v for v in clv_values if v > 0]) / clv_count * 100, 1) if clv_count > 0 else 0

        return {
            "profile_id": profile.id,
            "profile_name": profile.name,
            "total_bets": len(bets),
            "wins": win_count,
            "losses": loss_count,
            "voids": void_count,
            "total_deposited": round(total_deposited, 2),
            "total_withdrawn": round(total_withdrawn, 2),
            "net_deposited": round(net_deposited, 2),
            "total_staked": round(total_staked, 2),
            "total_profit": round(combined_profit, 2),
            "bet_profit": round(bet_profit, 2),
            "freebet_profit": round(freebet_profit, 2),
            "bonus_profit": round(bonus_profit, 2),
            "roi_pct": round(combined_profit / total_staked * 100, 2) if total_staked > 0 else 0,
            "win_rate": round(win_count / len(bets) * 100, 2) if len(bets) > 0 else 0,
            "avg_clv": avg_clv,
            "clv_positive_pct": clv_positive_pct,
            "clv_count": clv_count,
        }

    def get_exposure(self) -> dict:
        """Get bankroll with exposure breakdown per provider."""
        profile = self.profile_repo.get_active()
        providers = self.db.query(Provider).filter(Provider.is_enabled == True).all()

        # Load active bonus statuses for all providers
        active_bonuses = self.db.query(ProfileProviderBonus).filter(
            ProfileProviderBonus.profile_id == profile.id,
            ProfileProviderBonus.bonus_status.in_(["in_progress", "trigger_needed"]),
        ).all()
        bonus_map = {b.provider_id: b for b in active_bonuses}

        exposure_data = []
        total_balance_sek = 0.0
        total_locked_sek = 0.0
        total_free_sek = 0.0
        for provider in providers:
            balance = self.profile_repo.get_balance(profile.id, provider.id)
            currency = get_provider_currency(provider.id)
            rate = get_exchange_rate(provider.id)
            balance_sek = balance * rate
            total_balance_sek += balance_sek

            pending_bets = self.bet_repo.get_pending_for_provider(provider.id, profile.id)
            # Convert non-SEK stakes (e.g. Polymarket USDC) to SEK
            pending_exposure = sum(
                (b.stake * rate if getattr(b, 'currency', 'SEK') != 'SEK' else b.stake)
                for b in pending_bets if not b.is_bonus
            )

            # Wagering progress for this provider
            bonus = bonus_map.get(provider.id)
            wagering_info = None
            is_locked = False
            if bonus and bonus.wagering_requirement and bonus.wagering_requirement > 0:
                is_locked = True
                wagered = bonus.wagered_amount or 0.0
                requirement = bonus.wagering_requirement
                remaining = max(0, requirement - wagered)

                # Deadline info
                days_remaining = None
                if bonus.expires_at and bonus.bonus_status in ("in_progress", "trigger_needed"):
                    delta = bonus.expires_at - datetime.utcnow()
                    days_remaining = max(0, round(delta.total_seconds() / 86400, 1))

                wagering_info = {
                    "status": bonus.bonus_status,
                    "wagered": round(wagered, 0),
                    "requirement": round(requirement, 0),
                    "progress_pct": round(min(100.0, wagered / requirement * 100), 1),
                    "remaining": round(remaining, 0),
                    "min_odds": bonus.min_odds or BONUS_MIN_ODDS,
                    "days_remaining": days_remaining,
                    "expires_at": bonus.expires_at.isoformat() if bonus.expires_at else None,
                }

            if is_locked:
                total_locked_sek += balance_sek
            else:
                total_free_sek += balance_sek

            pending_native = pending_exposure / rate  # Convert SEK pending to native currency
            exposure_data.append({
                "provider_id": provider.id,
                "provider_name": provider.name,
                "total_balance": balance + pending_native,
                "balance_sek": round(balance_sek + pending_exposure, 2),
                "currency": currency,
                "exchange_rate_sek": rate,
                "pending_exposure": pending_exposure,
                "pending_bets_count": len(pending_bets),
                "available": balance,
                "platform": PLATFORM_MAP.get(provider.id, provider.id),
                "is_locked": is_locked,
                "wagering": wagering_info,
            })

        total_pending = sum(e["pending_exposure"] for e in exposure_data)

        return {
            "profile_id": profile.id,
            "profile_name": profile.name,
            "total_balance": total_balance_sek + total_pending,
            "total_pending": total_pending,
            "total_available": total_balance_sek,
            "total_free": round(total_free_sek, 0),
            "total_locked": round(total_locked_sek, 0),
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
            ProfileProviderBonus.bonus_status.in_(["in_progress", "trigger_needed"])
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
        from ..api.routes.providers import load_provider_bonuses
        all_bonus_configs = load_provider_bonuses()

        active_statuses = ("in_progress", "trigger_needed")

        bonus_progress = {}
        for bonus in bonuses:
            provider_min_odds = bonus.min_odds if bonus.min_odds else BONUS_MIN_ODDS

            # Auto-expire active bonuses past deadline
            if (bonus.bonus_status in active_statuses and bonus.expires_at
                    and datetime.utcnow() > bonus.expires_at):
                bonus.bonus_status = "completed"
                bonus.updated_at = datetime.utcnow()

            days_remaining = None
            if bonus.expires_at and bonus.bonus_status in active_statuses:
                delta = bonus.expires_at - datetime.utcnow()
                days_remaining = max(0, delta.days)

            # Resolve bonus_type from DB or fallback to config
            bonus_type = bonus.bonus_type
            if not bonus_type:
                cfg = all_bonus_configs.get(bonus.provider_id, {})
                bonus_type = cfg.get("type")

            # Compute action_needed
            action_needed = self._compute_action_needed(
                bonus.bonus_status, bonus_type,
                bonus.bonus_amount or 0.0, provider_min_odds,
            )

            # Compute prognosis for active wagering
            prognosis = None
            if bonus.bonus_status in active_statuses:
                prognosis = self.profile_repo.get_wagering_prognosis(profile.id, bonus.provider_id)

            bonus_progress[bonus.provider_id] = {
                "status": bonus.bonus_status,
                "bonus_type": bonus_type,
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
                "action_needed": action_needed,
                "prognosis": prognosis,
            }

        status = calc.get_status()

        return {
            "profile_id": profile.id,
            "profile_name": profile.name,
            "bankroll": status["bankroll"],
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

        bonus_type = bonus_config.get('type')
        is_available = not bonus_record or bonus_record.bonus_status == 'available'

        deposit_amount = amount
        bonus_amount = 0.0
        bonus_limit = bonus_config.get('amount', 0)
        trigger_odds = bonus_config.get('trigger_odds')

        # Bonusdeposit: match deposit with bonus money
        if bonus_type == 'bonusdeposit' and is_available and bonus_limit > 0:
            bonus_amount = min(deposit_amount, bonus_limit)

        # Track cumulative deposits for ROI calculation
        active_profile.total_deposited = (active_profile.total_deposited or 0.0) + deposit_amount
        active_profile.updated_at = datetime.utcnow()

        old_balance = self.profile_repo.get_balance(active_profile.id, provider_id)

        # Two-phase bonus (trigger_odds set): only add deposit now, bonus
        # gets added when trigger wagering is completed.  Without trigger_odds
        # the bonus is available immediately.
        if trigger_odds and bonus_amount > 0:
            total_added = deposit_amount  # bonus locked until trigger met
        else:
            total_added = deposit_amount + bonus_amount
        new_balance = self.profile_repo.adjust_balance(active_profile.id, provider_id, total_added)

        bonus_info = None
        if bonus_type == 'bonusdeposit' and bonus_amount > 0:
            wagering_multiplier = bonus_config.get('wagering_multiplier', 10.0)
            bonus_min_odds = bonus_config.get('min_odds', 1.80)
            deadline_days = bonus_config.get('deadline_days')
            if trigger_odds:
                # Two-phase: start in trigger_needed, wager deposit×trigger_multiplier at trigger_odds
                trigger_multiplier = bonus_config.get('trigger_multiplier', 1)
                bonus_info = self.profile_repo.start_bonus_trigger(
                    active_profile.id, provider_id, bonus_amount,
                    trigger_wagering=deposit_amount * trigger_multiplier,
                    trigger_min_odds=trigger_odds,
                    main_wagering_multiplier=wagering_multiplier,
                    main_min_odds=bonus_min_odds,
                    deadline_days=deadline_days,
                    deposit_amount=deposit_amount,
                )
            else:
                bonus_info = self.profile_repo.start_bonus_wagering(
                    active_profile.id, provider_id, bonus_amount,
                    wagering_multiplier, min_odds=bonus_min_odds,
                    deadline_days=deadline_days,
                )
        elif bonus_type == 'freebet' and is_available and bonus_limit > 0:
            # Freebet: start trigger tracking (no bonus money added to balance)
            bonus_min_odds = bonus_config.get('min_odds', 1.80)
            trigger_multiplier = bonus_config.get('trigger_multiplier', 1)
            bonus_info = self.profile_repo.start_freebet_tracking(
                active_profile.id, provider_id,
                bonus_amount=bonus_limit,
                min_odds=bonus_min_odds,
                trigger_wagering=deposit_amount * trigger_multiplier,
                deadline_days=bonus_config.get('deadline_days'),
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
            "bonus_type": bonus_type,
            "bonus_limit": bonus_limit if bonus_type else None,
            "wagering_requirement": bonus_info.get("wagering_requirement") if bonus_info else None,
            "min_odds": bonus_info.get("min_odds") if bonus_info else None,
        }

    @staticmethod
    def _compute_action_needed(
        status: str, bonus_type: str | None,
        bonus_amount: float, min_odds: float,
    ) -> str:
        """Compute human-readable action string for a bonus."""
        amt = int(bonus_amount)
        if status == "trigger_needed":
            if bonus_type == "bonusdeposit":
                return f"Trigger at {min_odds}+ odds to unlock {amt}kr bonus"
            return f"Place {amt}kr trigger bet at {min_odds}+ odds"
        elif status == "freebet_available":
            return f"Use {amt}kr freebet"
        elif status == "in_progress":
            return f"Wager at {min_odds}+ odds to clear bonus"
        elif status == "available":
            if bonus_type == "freebet":
                return f"Deposit to activate {amt}kr freebet"
            elif bonus_type == "bonusdeposit":
                return f"Deposit up to {amt}kr for matched bonus"
        return ""

    @staticmethod
    def reset_calculators(profile_id: int | None = None):
        """Reset stake calculator cache (forces reload from DB on next use)."""
        if profile_id and profile_id in _stake_calculators:
            del _stake_calculators[profile_id]
