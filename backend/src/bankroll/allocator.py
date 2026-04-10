"""
Fund allocation engine — distributes liquid capital across providers optimally.

Uses the BatchBuilder's capital plan (which knows exactly which bets are missed
due to insufficient balance) combined with bonus priority to recommend deposits.

Priority stack:
  1. Unclaimed bonuses (highest EV per kr deployed)
  2. Active wagering top-up (keep clearing bonuses)
  3. Bet coverage — providers with missed bets due to low balance
  0. Withdraw suggestions (idle providers with no bets)
"""

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..config import get_exchange_rate, load_config
from ..db.models import Profile, ProfileProviderBonus, Provider
from ..repositories import ProfileRepo

logger = logging.getLogger(__name__)

# EV retention estimates (conservative)
FREEBET_EV_RATE = 0.65  # 65% of freebet face value is expected profit
BONUSDEPOSIT_EV_RATE = 0.40  # 40% of bonusdeposit after wagering costs

DEFAULT_MIN_DEPOSIT = 100.0  # Minimum deposit amount (SEK)
LOW_BALANCE_THRESHOLD = 200.0
WAGERING_TOPUP_AMOUNT = 500.0


@dataclass
class AllocationRecommendation:
    provider_id: str
    provider_name: str
    action: str  # "deposit" | "withdraw"
    amount: float  # Native currency
    amount_sek: float  # Normalized to SEK
    reason: str  # Human-readable explanation
    priority: int  # 0=withdraw, 1=bonus, 2=wagering, 3=value
    expected_ev: float  # Expected value in SEK
    bonus_type: str | None
    current_balance: float
    current_balance_sek: float


class AllocationEngine:
    """Computes optimal fund distribution using the batch builder's capital plan."""

    def __init__(self, db: Session, profile: Profile):
        self.db = db
        self.profile = profile
        self.profile_repo = ProfileRepo(db)
        self.config = load_config()

    def allocate(self, liquid_amount: float) -> list[AllocationRecommendation]:
        """
        Given liquid capital (cash in bank), return ranked allocation recommendations.

        Runs the batch builder to get the real play batch + capital plan,
        then layers on bonus priorities and liquid budget constraints.
        """
        if liquid_amount <= 0:
            return []

        # Get the real play batch with capital plan from BatchBuilder
        from ..services.batch_builder import BatchBuilder

        builder = BatchBuilder(self.db)
        batch_result = builder.build(self.profile.id)

        capital_plan = batch_result.get("capital_plan", {})
        actions = capital_plan.get("actions", [])
        provider_balances_map = batch_result.get("provider_balances", {})

        # Load bonus state and provider configs
        bonuses = {
            b.provider_id: b
            for b in self.db.query(ProfileProviderBonus)
            .filter(ProfileProviderBonus.profile_id == self.profile.id)
            .all()
        }
        providers = {p.id: p for p in self.db.query(Provider).filter(Provider.is_enabled == True).all()}

        recommendations: list[AllocationRecommendation] = []
        liquid_remaining = liquid_amount
        handled_providers: set[str] = set()

        # ── Priority 1: Unclaimed bonuses ──
        # These are the highest-EV deployments — deposit to claim bonus
        for pid, p in providers.items():
            if liquid_remaining < DEFAULT_MIN_DEPOSIT:
                break
            cfg = self.config.get_provider(pid)
            if not cfg or cfg.sharp or not cfg.bonus:
                continue
            bonus = bonuses.get(pid)
            bonus_status = bonus.bonus_status if bonus else "available"
            if bonus_status != "available":
                continue

            bonus_cfg = cfg.bonus
            bonus_amount = bonus_cfg.get("amount", 0)
            bonus_type = bonus_cfg.get("type", "bonusdeposit")
            rate = get_exchange_rate(pid)
            bonus_amount_sek = bonus_amount * rate
            deposit_sek = min(liquid_remaining, bonus_amount_sek)
            deposit = deposit_sek / rate
            if deposit_sek < DEFAULT_MIN_DEPOSIT:
                continue

            ev = bonus_amount * (FREEBET_EV_RATE if bonus_type == "freebet" else BONUSDEPOSIT_EV_RATE)
            balance = provider_balances_map.get(pid, 0.0)

            reason = f"Freebet {bonus_amount:.0f}kr" if bonus_type == "freebet" else f"Bonus {bonus_amount:.0f}kr"
            wager_mult = bonus_cfg.get("wagering_multiplier", 0)
            trigger_mult = bonus_cfg.get("trigger_multiplier", 0)
            if trigger_mult:
                reason += f" ({trigger_mult}x trigger)"
            elif wager_mult:
                reason += f" ({wager_mult}x wager)"

            recommendations.append(
                AllocationRecommendation(
                    provider_id=pid,
                    provider_name=p.name or pid,
                    action="deposit",
                    amount=round(deposit, 2),
                    amount_sek=round(deposit_sek, 2),
                    reason=reason,
                    priority=1,
                    expected_ev=round(ev, 2),
                    bonus_type=bonus_type,
                    current_balance=balance,
                    current_balance_sek=round(balance * rate, 2),
                )
            )
            liquid_remaining -= deposit_sek
            handled_providers.add(pid)

        # ── Priority 2: Active wagering with low balance ──
        for pid, bonus in bonuses.items():
            if liquid_remaining < DEFAULT_MIN_DEPOSIT:
                break
            if pid in handled_providers:
                continue
            if bonus.bonus_status not in ("in_progress", "trigger_needed"):
                continue
            rate = get_exchange_rate(pid)
            balance = provider_balances_map.get(pid, 0.0)
            balance_sek = balance * rate
            if balance_sek >= LOW_BALANCE_THRESHOLD:
                continue
            wager_left = max(0, (bonus.wagering_requirement or 0) - (bonus.wagered_amount or 0))
            if wager_left <= 0:
                continue

            deposit_sek = min(liquid_remaining, WAGERING_TOPUP_AMOUNT)
            deposit = deposit_sek / rate
            ev = bonus.bonus_amount * BONUSDEPOSIT_EV_RATE * min(1.0, deposit_sek / max(wager_left, 1))
            p = providers.get(pid)

            recommendations.append(
                AllocationRecommendation(
                    provider_id=pid,
                    provider_name=(p.name if p else pid),
                    action="deposit",
                    amount=round(deposit, 2),
                    amount_sek=round(deposit_sek, 2),
                    reason=f"Low balance, {wager_left:.0f}kr wagering left",
                    priority=2,
                    expected_ev=round(ev, 2),
                    bonus_type=bonus.bonus_type,
                    current_balance=balance,
                    current_balance_sek=round(balance_sek, 2),
                )
            )
            liquid_remaining -= deposit_sek
            handled_providers.add(pid)

        # ── Priority 3: Bet coverage from capital plan ──
        # The batch builder already computed which providers need deposits
        # and how much, based on actual missed bets with Kelly stakes
        deposit_actions = [a for a in actions if a.get("type") == "deposit"]
        # Sort by expected_ev descending (highest value first)
        deposit_actions.sort(key=lambda a: a.get("expected_ev", 0), reverse=True)

        for action in deposit_actions:
            if liquid_remaining < DEFAULT_MIN_DEPOSIT:
                break
            pid = action.get("provider_id", "")
            if pid in handled_providers:
                continue

            rate = get_exchange_rate(pid)
            currency = action.get("currency", "SEK")
            amount_native = action.get("amount", 0)
            needed_sek = amount_native * rate if currency != "SEK" else amount_native
            if needed_sek < DEFAULT_MIN_DEPOSIT:
                continue

            deposit_sek = min(liquid_remaining, needed_sek)
            deposit = deposit_sek / rate
            missed_count = action.get("unlocks", 0)
            missed_ev = action.get("expected_ev", 0)
            balance = provider_balances_map.get(pid, 0.0)
            p = providers.get(pid)

            reason = f"{missed_count} missed bets"
            if missed_ev > 0:
                reason += f", +{missed_ev:.0f}kr EV"

            recommendations.append(
                AllocationRecommendation(
                    provider_id=pid,
                    provider_name=(p.name if p else pid),
                    action="deposit",
                    amount=round(deposit, 2),
                    amount_sek=round(deposit_sek, 2),
                    reason=reason,
                    priority=3,
                    expected_ev=round(missed_ev, 2),
                    bonus_type=None,
                    current_balance=balance,
                    current_balance_sek=round(balance * rate, 2),
                )
            )
            liquid_remaining -= deposit_sek
            handled_providers.add(pid)

        # ── Priority 0: Withdraw suggestions ──
        withdraw_actions = [a for a in actions if a.get("type") == "withdraw"]
        for action in withdraw_actions:
            pid = action.get("provider_id", "")
            if pid in handled_providers:
                continue
            rate = get_exchange_rate(pid)
            balance = provider_balances_map.get(pid, 0.0)
            balance_sek = balance * rate
            if balance_sek < 10:
                continue
            p = providers.get(pid)

            recommendations.append(
                AllocationRecommendation(
                    provider_id=pid,
                    provider_name=(p.name if p else pid),
                    action="withdraw",
                    amount=round(balance, 2),
                    amount_sek=round(balance_sek, 2),
                    reason="No active bets — withdraw to recycle",
                    priority=0,
                    expected_ev=0,
                    bonus_type=None,
                    current_balance=balance,
                    current_balance_sek=round(balance_sek, 2),
                )
            )

        # Sort: deposits by priority ASC then EV DESC, withdrawals last
        deposits = [r for r in recommendations if r.action == "deposit"]
        withdrawals = [r for r in recommendations if r.action == "withdraw"]
        deposits.sort(key=lambda r: (r.priority, -r.expected_ev))
        withdrawals.sort(key=lambda r: -r.amount_sek)

        return deposits + withdrawals
