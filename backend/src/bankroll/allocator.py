"""
Fund allocation engine — distributes liquid capital across providers optimally.

Priority stack:
  1. Unclaimed bonuses (highest EV per kr deployed)
  2. Active wagering top-up (keep clearing bonuses)
  3. Value bet coverage (fund providers with +EV opportunities)
  4. Spread across providers (diversification / limit avoidance)
  0. Withdraw suggestions (idle providers with no opportunities)
"""

import logging
from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import get_exchange_rate, get_provider_currency, load_config
from ..db.models import (
    Opportunity,
    Profile,
    ProfileProviderBalance,
    ProfileProviderBonus,
    Provider,
)
from ..repositories import ProfileRepo

logger = logging.getLogger(__name__)

# EV retention estimates (conservative)
FREEBET_EV_RATE = 0.65  # 65% of freebet face value is expected profit
BONUSDEPOSIT_EV_RATE = 0.40  # 40% of bonusdeposit after wagering costs

# Allocation defaults
DEFAULT_ALLOCATION_CAP_PCT = 0.20  # 20% of total bankroll per provider
DEFAULT_MIN_DEPOSIT = 100.0  # Minimum deposit amount (SEK)
LOW_BALANCE_THRESHOLD = 200.0  # Below this = needs top-up for wagering
WAGERING_TOPUP_AMOUNT = 500.0  # Default top-up amount for wagering


@dataclass
class AllocationRecommendation:
    provider_id: str
    provider_name: str
    action: str  # "deposit" | "withdraw"
    amount: float  # Native currency
    amount_sek: float  # Normalized to SEK
    reason: str  # Human-readable explanation
    priority: int  # 0=withdraw, 1=bonus, 2=wagering, 3=value, 4=spread
    expected_ev: float  # Expected value in SEK
    bonus_type: str | None
    current_balance: float
    current_balance_sek: float


@dataclass
class _ProviderContext:
    """Internal: aggregated provider state for allocation decisions."""

    provider_id: str
    provider_name: str
    balance: float
    balance_sek: float
    exchange_rate: float
    currency: str
    bonus_status: str | None  # "available", "in_progress", "trigger_needed", etc.
    bonus_type: str | None  # "freebet" or "bonusdeposit"
    bonus_amount: float  # Face value of bonus
    bonus_config: dict | None  # From providers.yaml
    wagering_remaining: float  # wagering_requirement - wagered_amount
    opp_count: int  # Active opportunities at this provider
    opp_avg_edge: float  # Average edge_pct of active opportunities
    opp_total_edge: float  # Sum of edge_pct (for ranking)
    allocation_cap: float  # Max SEK to hold at this provider


class AllocationEngine:
    """Computes optimal fund distribution across providers."""

    def __init__(self, db: Session, profile: Profile):
        self.db = db
        self.profile = profile
        self.profile_repo = ProfileRepo(db)
        self.config = load_config()

    def allocate(self, liquid_amount: float) -> list[AllocationRecommendation]:
        """
        Given liquid capital (cash in bank), return ranked allocation recommendations.

        Total deposit recommendations will not exceed liquid_amount.
        """
        if liquid_amount <= 0:
            return []

        contexts = self._build_provider_contexts()
        if not contexts:
            return []

        total_bankroll = sum(c.balance_sek for c in contexts) + liquid_amount
        recommendations: list[AllocationRecommendation] = []
        liquid_remaining = liquid_amount

        # Update default caps now that we know total bankroll
        for ctx in contexts:
            if ctx.allocation_cap == 0:
                ctx.allocation_cap = total_bankroll * DEFAULT_ALLOCATION_CAP_PCT

        # Priority 1: Unclaimed bonuses
        bonus_providers = sorted(
            [c for c in contexts if c.bonus_status == "available" and c.bonus_config],
            key=lambda c: self._bonus_ev(c),
            reverse=True,
        )
        for ctx in bonus_providers:
            if liquid_remaining < DEFAULT_MIN_DEPOSIT:
                break
            deposit = self._calc_bonus_deposit(ctx, liquid_remaining)
            deposit_sek = deposit * ctx.exchange_rate
            if deposit_sek < DEFAULT_MIN_DEPOSIT:
                continue
            ev = self._bonus_ev(ctx)
            recommendations.append(
                AllocationRecommendation(
                    provider_id=ctx.provider_id,
                    provider_name=ctx.provider_name,
                    action="deposit",
                    amount=round(deposit, 2),
                    amount_sek=round(deposit_sek, 2),
                    reason=self._bonus_reason(ctx),
                    priority=1,
                    expected_ev=round(ev, 2),
                    bonus_type=ctx.bonus_type or (ctx.bonus_config or {}).get("type"),
                    current_balance=ctx.balance,
                    current_balance_sek=ctx.balance_sek,
                )
            )
            liquid_remaining -= deposit_sek
            ctx.balance += deposit
            ctx.balance_sek += deposit_sek

        # Priority 2: Active wagering with low balance
        wagering_providers = sorted(
            [
                c
                for c in contexts
                if c.bonus_status in ("in_progress", "trigger_needed")
                and c.balance_sek < LOW_BALANCE_THRESHOLD
                and c.wagering_remaining > 0
            ],
            key=lambda c: c.bonus_amount,  # Prioritize larger bonuses
            reverse=True,
        )
        for ctx in wagering_providers:
            if liquid_remaining < DEFAULT_MIN_DEPOSIT:
                break
            cap_room = max(0, ctx.allocation_cap - ctx.balance_sek)
            deposit_sek = min(liquid_remaining, WAGERING_TOPUP_AMOUNT, cap_room)
            deposit = deposit_sek / ctx.exchange_rate
            if deposit_sek < DEFAULT_MIN_DEPOSIT:
                continue
            ev = ctx.bonus_amount * BONUSDEPOSIT_EV_RATE * (deposit_sek / max(ctx.wagering_remaining, 1))
            recommendations.append(
                AllocationRecommendation(
                    provider_id=ctx.provider_id,
                    provider_name=ctx.provider_name,
                    action="deposit",
                    amount=round(deposit, 2),
                    amount_sek=round(deposit_sek, 2),
                    reason=f"Low balance, {ctx.wagering_remaining:.0f}kr wagering left",
                    priority=2,
                    expected_ev=round(ev, 2),
                    bonus_type=ctx.bonus_type,
                    current_balance=ctx.balance,
                    current_balance_sek=ctx.balance_sek,
                )
            )
            liquid_remaining -= deposit_sek
            ctx.balance += deposit
            ctx.balance_sek += deposit_sek

        # Priority 3: Value bet coverage
        value_providers = sorted(
            [
                c
                for c in contexts
                if c.opp_count > 0 and c.bonus_status not in ("available", "in_progress", "trigger_needed")
            ],
            key=lambda c: c.opp_total_edge,
            reverse=True,
        )
        for ctx in value_providers:
            if liquid_remaining < DEFAULT_MIN_DEPOSIT:
                break
            cap_room = max(0, ctx.allocation_cap - ctx.balance_sek)
            # Estimate needed: ~2% of bankroll per opportunity (single bet cap)
            needed_sek = ctx.opp_count * total_bankroll * 0.02
            deposit_sek = min(liquid_remaining, needed_sek - ctx.balance_sek, cap_room)
            if deposit_sek < DEFAULT_MIN_DEPOSIT:
                continue
            deposit = deposit_sek / ctx.exchange_rate
            ev = ctx.opp_avg_edge / 100 * deposit_sek * 0.5  # Conservative: half deployed
            recommendations.append(
                AllocationRecommendation(
                    provider_id=ctx.provider_id,
                    provider_name=ctx.provider_name,
                    action="deposit",
                    amount=round(deposit, 2),
                    amount_sek=round(deposit_sek, 2),
                    reason=f"{ctx.opp_count} bets avg {ctx.opp_avg_edge:.1f}% edge",
                    priority=3,
                    expected_ev=round(ev, 2),
                    bonus_type=None,
                    current_balance=ctx.balance,
                    current_balance_sek=ctx.balance_sek,
                )
            )
            liquid_remaining -= deposit_sek
            ctx.balance += deposit
            ctx.balance_sek += deposit_sek

        # Priority 4: Spread remaining across providers with opportunities
        if liquid_remaining > DEFAULT_MIN_DEPOSIT * 2:
            spread_providers = [
                c
                for c in contexts
                if c.opp_count > 0
                and c.balance_sek < c.allocation_cap
                and not any(r.provider_id == c.provider_id for r in recommendations)
            ]
            if spread_providers:
                per_provider = liquid_remaining / len(spread_providers)
                for ctx in spread_providers:
                    cap_room = max(0, ctx.allocation_cap - ctx.balance_sek)
                    deposit_sek = min(per_provider, cap_room)
                    if deposit_sek < DEFAULT_MIN_DEPOSIT:
                        continue
                    deposit = deposit_sek / ctx.exchange_rate
                    recommendations.append(
                        AllocationRecommendation(
                            provider_id=ctx.provider_id,
                            provider_name=ctx.provider_name,
                            action="deposit",
                            amount=round(deposit, 2),
                            amount_sek=round(deposit_sek, 2),
                            reason=f"Spread allocation ({ctx.opp_count} opportunities)",
                            priority=4,
                            expected_ev=round(ctx.opp_avg_edge / 100 * deposit_sek * 0.3, 2),
                            bonus_type=None,
                            current_balance=ctx.balance,
                            current_balance_sek=ctx.balance_sek,
                        )
                    )
                    liquid_remaining -= deposit_sek

        # Priority 0: Withdraw suggestions (idle providers)
        for ctx in contexts:
            if (
                ctx.balance_sek > 10
                and ctx.opp_count == 0
                and ctx.bonus_status not in ("available", "in_progress", "trigger_needed")
                and not any(r.provider_id == ctx.provider_id for r in recommendations)
            ):
                recommendations.append(
                    AllocationRecommendation(
                        provider_id=ctx.provider_id,
                        provider_name=ctx.provider_name,
                        action="withdraw",
                        amount=round(ctx.balance, 2),
                        amount_sek=round(ctx.balance_sek, 2),
                        reason="No active bets or bonus",
                        priority=0,
                        expected_ev=0,
                        bonus_type=None,
                        current_balance=ctx.balance,
                        current_balance_sek=ctx.balance_sek,
                    )
                )

        # Sort: deposits by priority ASC then EV DESC, withdrawals last
        deposits = [r for r in recommendations if r.action == "deposit"]
        withdrawals = [r for r in recommendations if r.action == "withdraw"]
        deposits.sort(key=lambda r: (r.priority, -r.expected_ev))
        withdrawals.sort(key=lambda r: -r.amount_sek)

        return deposits + withdrawals

    def _build_provider_contexts(self) -> list[_ProviderContext]:
        """Build context for each enabled provider."""
        providers = self.db.query(Provider).filter(Provider.is_enabled == True).all()
        provider_configs = {p.id: self.config.get_provider(p.id) for p in providers}

        # Batch query: balances
        balances = {
            b.provider_id: b.balance
            for b in self.db.query(ProfileProviderBalance)
            .filter(ProfileProviderBalance.profile_id == self.profile.id)
            .all()
        }

        # Batch query: bonus statuses
        bonuses = {
            b.provider_id: b
            for b in self.db.query(ProfileProviderBonus)
            .filter(ProfileProviderBonus.profile_id == self.profile.id)
            .all()
        }

        # Batch query: active opportunities per provider
        # Active value opportunities per provider
        opp_data = {}
        for pid, count, avg_edge, total_edge in (
            self.db.query(
                Opportunity.provider1_id,
                func.count(Opportunity.id),
                func.avg(Opportunity.edge_pct),
                func.sum(Opportunity.edge_pct),
            )
            .filter(Opportunity.is_active == True, Opportunity.type == "value")
            .group_by(Opportunity.provider1_id)
            .all()
        ):
            opp_data[pid] = (count, avg_edge or 0, total_edge or 0)

        contexts = []
        for p in providers:
            cfg = provider_configs.get(p.id)
            if not cfg or cfg.sharp:
                continue  # Skip sharp sources (Pinnacle) — not for allocation

            balance = balances.get(p.id, 0.0)
            rate = get_exchange_rate(p.id)
            currency = get_provider_currency(p.id)
            bonus = bonuses.get(p.id)
            bonus_cfg = cfg.bonus if cfg else None
            opp_count, opp_avg, opp_total = opp_data.get(p.id, (0, 0, 0))

            # Wagering remaining
            wagering_remaining = 0.0
            if bonus and bonus.bonus_status in ("in_progress", "trigger_needed"):
                wagering_remaining = max(0, bonus.wagering_requirement - bonus.wagered_amount)

            # Allocation cap from config or default
            cap = 0  # Will be set to default after total_bankroll is known

            contexts.append(
                _ProviderContext(
                    provider_id=p.id,
                    provider_name=p.name or p.id,
                    balance=balance,
                    balance_sek=balance * rate,
                    exchange_rate=rate,
                    currency=currency,
                    bonus_status=bonus.bonus_status if bonus else ("available" if bonus_cfg else None),
                    bonus_type=bonus.bonus_type if bonus else None,
                    bonus_amount=bonus.bonus_amount if bonus else (bonus_cfg.get("amount", 0) if bonus_cfg else 0),
                    bonus_config=bonus_cfg,
                    wagering_remaining=wagering_remaining,
                    opp_count=opp_count,
                    opp_avg_edge=opp_avg,
                    opp_total_edge=opp_total,
                    allocation_cap=cap,
                )
            )

        return contexts

    def _calc_bonus_deposit(self, ctx: _ProviderContext, liquid_remaining: float) -> float:
        """Calculate deposit amount for a bonus provider (returns native currency)."""
        bonus_cfg = ctx.bonus_config or {}
        bonus_amount = bonus_cfg.get("amount", 0)
        # bonus_amount is in native currency — convert to SEK for comparison
        bonus_amount_sek = bonus_amount * ctx.exchange_rate
        deposit_sek = min(liquid_remaining, bonus_amount_sek)
        return deposit_sek / ctx.exchange_rate

    def _bonus_ev(self, ctx: _ProviderContext) -> float:
        """Estimate EV from claiming this bonus."""
        bonus_cfg = ctx.bonus_config or {}
        bonus_amount = bonus_cfg.get("amount", 0)
        bonus_type = bonus_cfg.get("type", "bonusdeposit")
        if bonus_type == "freebet":
            return bonus_amount * FREEBET_EV_RATE
        return bonus_amount * BONUSDEPOSIT_EV_RATE

    def _bonus_reason(self, ctx: _ProviderContext) -> str:
        """Human-readable reason for bonus deposit."""
        bonus_cfg = ctx.bonus_config or {}
        bonus_type = bonus_cfg.get("type", "bonusdeposit")
        bonus_amount = bonus_cfg.get("amount", 0)
        if bonus_type == "freebet":
            return f"Freebet {bonus_amount:.0f}kr available"
        wagering_mult = bonus_cfg.get("wagering_multiplier", 0)
        trigger_mult = bonus_cfg.get("trigger_multiplier", 0)
        if trigger_mult:
            return f"Bonus {bonus_amount:.0f}kr ({trigger_mult}x trigger + {wagering_mult}x wager)"
        return f"Bonus {bonus_amount:.0f}kr ({wagering_mult}x wagering)"
