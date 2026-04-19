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
from ..services.batch_builder import BatchBuilder

logger = logging.getLogger(__name__)

# EV retention estimates (conservative)
FREEBET_EV_RATE = 0.65  # 65% of freebet face value is expected profit
BONUSDEPOSIT_EV_RATE = 0.40  # 40% of bonusdeposit after wagering costs

DEFAULT_MIN_DEPOSIT = 100.0  # Minimum deposit amount (SEK)
LOW_BALANCE_THRESHOLD = 200.0
WAGERING_TOPUP_AMOUNT = 500.0
WITHDRAW_MIN_SEK = 50.0  # floor for suggesting a withdrawal (was 10)


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

    def allocate(self, liquid_amount: float | None) -> dict:
        """Given liquid capital (or None for unbounded), return an ordered allocation envelope.

        Phases:
          A. Collect withdrawals from idle providers → add to effective budget.
          B. Run priority tiers (bonus claims → wagering top-ups → unlimited valuebets)
             against the effective budget.
          C. Leftover becomes keep_liquid.
        """
        current_liquid = float(self.profile.liquid_balance or 0.0)
        deposit_input = None if liquid_amount is None else max(0.0, float(liquid_amount))

        builder = BatchBuilder(self.db)
        batch_result = builder.build(self.profile.id)
        capital_plan = batch_result.get("capital_plan", {})
        actions = capital_plan.get("actions", [])
        provider_balances_map = batch_result.get("provider_balances", {})

        bonuses = {
            b.provider_id: b
            for b in self.db.query(ProfileProviderBonus)
            .filter(ProfileProviderBonus.profile_id == self.profile.id)
            .all()
        }
        providers = {p.id: p for p in self.db.query(Provider).filter(Provider.is_enabled == True).all()}

        # ── Phase A: withdrawals ──
        withdrawals: list[dict] = []
        withdrawal_total_sek = 0.0
        withdraw_actions = [a for a in actions if a.get("type") == "withdraw"]
        for action in withdraw_actions:
            pid = action.get("provider_id", "")
            rate = get_exchange_rate(pid)
            if rate <= 0:
                continue
            balance = provider_balances_map.get(pid, 0.0)
            balance_sek = balance * rate
            if balance_sek < WITHDRAW_MIN_SEK:
                continue
            p = providers.get(pid)
            withdrawals.append(
                {
                    "provider_id": pid,
                    "provider_name": p.name if p else pid,
                    "amount": round(balance, 2),
                    "amount_sek": round(balance_sek, 2),
                    "reason": "No active bets — withdraw to recycle",
                }
            )
            withdrawal_total_sek += balance_sek

        # Effective budget
        unbounded = deposit_input is None
        if unbounded:
            effective_budget = float("inf")
        else:
            effective_budget = current_liquid + deposit_input + withdrawal_total_sek

        liquid_remaining = effective_budget
        deposits: list[dict] = []
        handled_providers: set[str] = set()

        def _append_deposit(**kwargs) -> None:
            nonlocal liquid_remaining
            deposits.append(kwargs)
            liquid_remaining -= kwargs["amount_sek"]

        # ── Tier 1: Unclaimed bonus claims (trigger amount only, sort by EV/kr) ──
        tier1_candidates: list[tuple[float, dict]] = []
        for pid, p in providers.items():
            cfg = self.config.get_provider(pid)
            if not cfg or cfg.sharp or not cfg.bonus:
                continue
            bonus = bonuses.get(pid)
            bonus_status = bonus.bonus_status if bonus else "available"
            if bonus_status != "available":
                continue
            bonus_cfg = cfg.bonus
            bonus_amount = bonus_cfg.get("amount", 0)
            trigger_amount = bonus_cfg.get("trigger_amount", bonus_amount)  # fallback to face value
            bonus_type = bonus_cfg.get("type", "bonusdeposit")
            rate = get_exchange_rate(pid)
            if rate <= 0:
                continue
            trigger_sek = trigger_amount * rate
            if trigger_sek < DEFAULT_MIN_DEPOSIT:
                continue
            ev = bonus_amount * (FREEBET_EV_RATE if bonus_type == "freebet" else BONUSDEPOSIT_EV_RATE)
            ev_per_kr = ev / max(trigger_sek, 1.0)
            reason = f"Freebet {bonus_amount:.0f}kr" if bonus_type == "freebet" else f"Bonus {bonus_amount:.0f}kr"
            wager_mult = bonus_cfg.get("wagering_multiplier", 0)
            trigger_mult = bonus_cfg.get("trigger_multiplier", 0)
            if trigger_mult:
                reason += f" ({trigger_mult}x trigger)"
            elif wager_mult:
                reason += f" ({wager_mult}x wager)"
            tier1_candidates.append(
                (
                    ev_per_kr,
                    {
                        "priority": 1,
                        "provider_id": pid,
                        "provider_name": p.name or pid,
                        "amount": round(trigger_amount, 2),
                        "amount_sek": round(trigger_sek, 2),
                        "unlocks": "bonus_claim",
                        "expected_ev": round(ev, 2),
                        "reason": reason,
                        "bonus_type": bonus_type,
                    },
                )
            )
        tier1_candidates.sort(key=lambda x: -x[0])
        for _, dep in tier1_candidates:
            if liquid_remaining < dep["amount_sek"]:
                continue
            _append_deposit(**dep)
            handled_providers.add(dep["provider_id"])

        # ── Tier 2: Wagering top-ups ──
        for pid, bonus in bonuses.items():
            if liquid_remaining < DEFAULT_MIN_DEPOSIT:
                break
            if pid in handled_providers:
                continue
            if bonus.bonus_status not in ("in_progress", "trigger_needed"):
                continue
            rate = get_exchange_rate(pid)
            if rate <= 0:
                continue
            balance = provider_balances_map.get(pid, 0.0)
            balance_sek = balance * rate
            if balance_sek >= LOW_BALANCE_THRESHOLD:
                continue
            wager_left = max(0, (bonus.wagering_requirement or 0) - (bonus.wagered_amount or 0))
            if wager_left <= 0:
                continue
            topup_sek = min(liquid_remaining, WAGERING_TOPUP_AMOUNT)
            topup = topup_sek / rate
            ev = bonus.bonus_amount * BONUSDEPOSIT_EV_RATE * min(1.0, topup_sek / max(wager_left, 1))
            p = providers.get(pid)
            _append_deposit(
                priority=2,
                provider_id=pid,
                provider_name=(p.name if p else pid),
                amount=round(topup, 2),
                amount_sek=round(topup_sek, 2),
                unlocks="wagering_topup",
                expected_ev=round(ev, 2),
                reason=f"Low balance, {wager_left:.0f}kr wagering left",
                bonus_type=bonus.bonus_type,
            )
            handled_providers.add(pid)

        # ── Tier 3: Unlimited valuebet coverage (capital_plan deposits, sorted by EV) ──
        deposit_actions = [a for a in actions if a.get("type") == "deposit"]
        deposit_actions.sort(key=lambda a: a.get("expected_ev", 0), reverse=True)
        for action in deposit_actions:
            if liquid_remaining < DEFAULT_MIN_DEPOSIT:
                break
            pid = action.get("provider_id", "")
            if pid in handled_providers:
                continue
            rate = get_exchange_rate(pid)
            if rate <= 0:
                continue
            currency = action.get("currency", "SEK")
            amount_native = action.get("amount", 0)
            needed_sek = amount_native * rate if currency != "SEK" else amount_native
            if needed_sek < DEFAULT_MIN_DEPOSIT:
                continue
            deposit_sek = min(liquid_remaining, needed_sek)
            if deposit_sek < DEFAULT_MIN_DEPOSIT:
                continue
            deposit = deposit_sek / rate
            missed_count = action.get("unlocks", 0)
            missed_ev = action.get("expected_ev", 0)
            p = providers.get(pid)
            reason = f"{missed_count} missed bets"
            if missed_ev > 0:
                reason += f", +{missed_ev:.0f}kr EV"
            _append_deposit(
                priority=3,
                provider_id=pid,
                provider_name=(p.name if p else pid),
                amount=round(deposit, 2),
                amount_sek=round(deposit_sek, 2),
                unlocks=f"{missed_count} valuebets",
                expected_ev=round(missed_ev, 2),
                reason=reason,
                bonus_type=None,
            )
            handled_providers.add(pid)

        deposits.sort(key=lambda d: (d["priority"], -d["expected_ev"]))
        recommended_total = round(sum(d["amount_sek"] for d in deposits), 2)
        keep_liquid = 0.0 if unbounded else max(0.0, round(liquid_remaining, 2))

        return {
            "current_liquid": round(current_liquid, 2),
            "deposit_input": deposit_input,
            "withdrawals": withdrawals,
            "effective_budget": effective_budget if unbounded else round(effective_budget, 2),
            "deposits": deposits,
            "keep_liquid": keep_liquid,
            "recommended_total": recommended_total,
        }
