"""
Bankroll Simulator — Forward simulation engine for Monte Carlo planning.

Simulates provider bonus lifecycles: deposits, wagering, limitations,
bonus clearing, and fund redeployment. Uses stochastic bet resolution
and historical edge distributions for realistic trajectory modeling.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from .edge_sampler import EdgeSampler


# ── Constants ──

MIN_EDGE_PCT = 0.02  # 2% minimum edge
DEFAULT_BONUS_DAYS = 60
WITHDRAWAL_DELAY_DAYS = 3


# ── Status Mapping ──

STATUS_MAP: dict[str, str] = {
    "available": "not_started",
    "trigger_needed": "trigger",
    "freebet_available": "wagering",  # freebet_available=True set separately
    "in_progress": "wagering",
    "completed": "cleared",
    "claimed": "cleared",
}


# ── Limitation Curves ──

LIMITATION_CURVES: dict[str, dict[str, float]] = {
    "aggressive": {"midpoint": 30, "steepness": 0.15},   # Limits at ~30 bets
    "moderate": {"midpoint": 100, "steepness": 0.08},     # Limits at ~100 bets
    "lenient": {"midpoint": 300, "steepness": 0.03},      # Limits at ~300 bets
}


# ── Data Classes ──

@dataclass
class ProviderSimState:
    """Simulation state for a single provider."""

    provider_id: str
    balance: float
    deposited: float
    bonus_status: str  # not_started | trigger | wagering | cleared | limited | expired
    bonus_type: str  # freebet | bonusdeposit
    bonus_amount: float
    wagered_amount: float
    wagering_requirement: float
    min_odds: float
    limitation_risk: float  # 0-1
    bets_placed: int
    days_active: int
    days_until_expiry: int
    freebet_available: bool
    trigger_settled: bool
    provider_type: str  # aggressive | moderate | lenient


@dataclass
class SimState:
    """Full simulation state at a point in time."""

    day: int
    total_wealth: float  # All money including withdrawn
    deployable_capital: float  # Cash available for Kelly sizing (excludes withdrawn)
    undeployed_capital: float  # Not yet deposited anywhere
    providers: dict[str, ProviderSimState] = field(default_factory=dict)
    withdrawn: float = 0.0


# ── Limitation Probability ──

def _logistic(bets: int, midpoint: float, steepness: float) -> float:
    """Logistic CDF: cumulative probability of being limited at `bets` count."""
    return 1.0 / (1.0 + math.exp(-steepness * (bets - midpoint)))


def sim_limitation_prob(bets_placed: int, provider_type: str) -> float:
    """
    Daily probability of getting limited, given current bet count.

    Uses logistic cumulative risk curve. Returns the incremental probability
    (hazard rate) at this exact bet count: P(limited at N) - P(limited at N-1).
    """
    curve = LIMITATION_CURVES.get(provider_type, LIMITATION_CURVES["moderate"])
    midpoint = curve["midpoint"]
    steepness = curve["steepness"]

    cumulative = _logistic(bets_placed, midpoint, steepness)
    previous = _logistic(max(0, bets_placed - 1), midpoint, steepness)

    return max(0.0, cumulative - previous)


# ── Simplified Kelly for Simulation ──

def sim_kelly_stake(edge: float, odds: float, deployable_capital: float) -> float:
    """
    Simplified Kelly stake for simulation (no rounding, no min_stake).

    Kelly fraction scales linearly:
    - edge <= 2%: fraction = 0.25
    - edge >= 6%: fraction = 0.75
    - between: linear interpolation

    MC-optimal low-BR boost: 1.5x at <=5k, taper to 10k.
    MC-optimal low-BR cap: 3% at <=5k, taper to 2% at 10k.
    """
    if edge <= 0 or odds <= 1.0 or deployable_capital <= 0:
        return 0.0

    # Kelly fraction: 0.25 at <=2%, linear to 0.75 at >=6%
    if edge <= 0.02:
        kelly_frac = 0.25
    elif edge >= 0.06:
        kelly_frac = 0.75
    else:
        t = (edge - 0.02) / 0.04
        kelly_frac = 0.25 + t * 0.50

    # Low-bankroll boost (MC-optimal: 1.5x at <=5k, taper to 10k)
    if deployable_capital <= 5000:
        kelly_frac *= 1.5
    elif deployable_capital < 10000:
        t = (deployable_capital - 5000) / 5000
        kelly_frac *= 1.5 - t * 0.5

    raw = deployable_capital * kelly_frac * edge / (odds - 1.0)

    # Dynamic cap: 3% at <=5k, taper to 2% at 10k+
    if deployable_capital <= 5000:
        cap_pct = 0.03
    elif deployable_capital < 10000:
        t = (deployable_capital - 5000) / 5000
        cap_pct = 0.03 - t * 0.01
    else:
        cap_pct = 0.02
    cap = deployable_capital * cap_pct
    return min(raw, cap)


# ── Action Handling ──

def apply_action(state: SimState, action) -> None:
    """
    Apply a planning action to the simulation state.

    Accepts either an Action dataclass or a dict with keys:
        type: DEPOSIT | WITHDRAW | WAIT
        provider_id: str
        amount: float
    """
    # Normalize: accept both Action objects and dicts
    if hasattr(action, "type"):
        action_type = action.type
        _get = lambda key, default=None: getattr(action, key, default)
    else:
        action_type = action.get("type", "WAIT")
        _get = lambda key, default=None: action.get(key, default)

    if action_type == "DEPOSIT":
        provider_id = _get("provider_id")
        amount = _get("amount", 0)

        if amount > state.undeployed_capital:
            amount = state.undeployed_capital

        state.undeployed_capital -= amount

        if provider_id in state.providers:
            prov = state.providers[provider_id]
            prov.balance += amount
            prov.deposited += amount
            # Set bonus status if transitioning from not_started
            if prov.bonus_status == "not_started":
                target_status = _get("bonus_status", "wagering")
                if prov.bonus_type == "freebet" and target_status != "trigger":
                    prov.bonus_status = "trigger"
                else:
                    prov.bonus_status = target_status

        # Update deployable capital
        state.deployable_capital = state.undeployed_capital + sum(
            p.balance for p in state.providers.values()
            if p.bonus_status not in ("limited", "expired")
        )

    elif action_type == "WITHDRAW":
        provider_id = _get("provider_id")

        if provider_id in state.providers:
            prov = state.providers[provider_id]
            withdrawn_amount = prov.balance

            # Model 3-day delay as immediate for simplicity
            state.withdrawn += withdrawn_amount
            state.undeployed_capital += withdrawn_amount
            prov.balance = 0.0

            # Update deployable capital
            state.deployable_capital = state.undeployed_capital + sum(
                p.balance for p in state.providers.values()
                if p.bonus_status not in ("limited", "expired")
            )

    # WAIT is a no-op


# ── Day Simulation ──

def simulate_day(
    state: SimState,
    action,
    edge_sampler: EdgeSampler,
) -> SimState:
    """
    Simulate one day of betting activity.

    Steps:
    1. Apply the planning action (deposit/withdraw/wait)
    2. For each active provider:
       a. Handle trigger phase (settles in 1 day)
       b. Sample daily edge opportunities
       c. Resolve each bet stochastically
       d. Update wagering progress
       e. Roll limitation event
       f. Check bonus completion
       g. Check expiration
    3. Update aggregate capital numbers
    """
    # Step 1: Apply action
    apply_action(state, action)

    # Step 2: Process each provider
    for prov in state.providers.values():
        # Skip inactive providers
        if prov.bonus_status in ("not_started", "limited", "expired"):
            continue

        # 2a. Handle trigger phase — simplified: settles in 1 day
        if prov.bonus_status == "trigger" and not prov.trigger_settled:
            prov.trigger_settled = True
            if prov.bonus_type == "freebet":
                prov.freebet_available = True
                prov.bonus_status = "wagering"
            else:
                prov.bonus_status = "wagering"
            # Trigger bet itself: place a small qualifying bet
            # (abstracted — just mark as settled and continue)

        # 2b. Sample daily opportunities
        n_opps = edge_sampler.get_daily_volume(prov.provider_id)
        opportunities = edge_sampler.sample(prov.provider_id, n_opps)

        # 2c. Process each opportunity
        for opp in opportunities:
            if opp.edge < MIN_EDGE_PCT:
                continue

            # Skip if wagering and odds below min_odds requirement
            if prov.bonus_status == "wagering" and opp.odds < prov.min_odds:
                continue

            # Skip if no balance left
            if prov.balance <= 0:
                break

            # Calculate stake (capped at provider balance)
            stake = sim_kelly_stake(opp.edge, opp.odds, state.deployable_capital)
            stake = min(stake, prov.balance)

            if stake <= 0:
                continue

            # Freebet handling: stake comes from freebet, not balance
            is_freebet = prov.freebet_available and prov.bonus_type == "freebet"
            if is_freebet:
                # Freebet: don't deduct from balance, only profit returned
                prov.freebet_available = False

            # Stochastic resolution
            fair_prob = 1.0 / opp.fair_odds if opp.fair_odds > 0 else 0.5

            if random.random() < fair_prob:
                # Win
                profit = stake * (opp.odds - 1.0)
                if is_freebet:
                    # Freebet win: only profit added (stake not returned)
                    prov.balance += profit
                else:
                    prov.balance += profit
            else:
                # Loss
                if not is_freebet:
                    prov.balance -= stake

            # Update wagering progress (stake counts regardless of outcome)
            if prov.bonus_status == "wagering" and opp.odds >= prov.min_odds:
                prov.wagered_amount += stake

            prov.bets_placed += 1

        # 2d. Roll limitation event
        lim_prob = sim_limitation_prob(prov.bets_placed, prov.provider_type)
        prov.limitation_risk = _logistic(
            prov.bets_placed,
            LIMITATION_CURVES.get(prov.provider_type, LIMITATION_CURVES["moderate"])["midpoint"],
            LIMITATION_CURVES.get(prov.provider_type, LIMITATION_CURVES["moderate"])["steepness"],
        )
        if random.random() < lim_prob:
            prov.bonus_status = "limited"
            continue

        # 2e. Check bonus completion
        if (
            prov.bonus_status == "wagering"
            and prov.wagering_requirement > 0
            and prov.wagered_amount >= prov.wagering_requirement
        ):
            prov.bonus_status = "cleared"
            # Bonusdeposit: add bonus amount to balance on completion
            if prov.bonus_type == "bonusdeposit":
                prov.balance += prov.bonus_amount

        # 2f. Expiration countdown
        if prov.days_until_expiry > 0:
            prov.days_until_expiry -= 1
            if prov.days_until_expiry <= 0 and prov.bonus_status == "wagering":
                prov.bonus_status = "expired"

        prov.days_active += 1

    # Step 3: Update aggregate numbers
    total_in_providers = sum(p.balance for p in state.providers.values())
    state.deployable_capital = state.undeployed_capital + sum(
        p.balance for p in state.providers.values()
        if p.bonus_status not in ("limited", "expired")
    )
    state.total_wealth = total_in_providers + state.undeployed_capital + state.withdrawn
    state.day += 1

    return state


# ── Snapshot from Database ──

def snapshot_current_state(db_session: Session, profile_id: int) -> SimState:
    """
    Build a SimState from the current database state.

    Queries Profile, ProfileProviderBalance, ProfileProviderBonus,
    ProviderRiskProfile, and Bet tables to construct an accurate
    starting point for forward simulation.
    """
    from ..db.models import (
        Bet,
        Profile,
        ProfileProviderBalance,
        ProfileProviderBonus,
        ProviderRiskProfile,
    )

    # Load profile
    profile = db_session.query(Profile).filter_by(id=profile_id).first()
    if not profile:
        raise ValueError(f"Profile {profile_id} not found")

    # Load provider balances
    balances = db_session.query(ProfileProviderBalance).filter_by(
        profile_id=profile_id,
    ).all()

    # Load bonus statuses
    bonuses = db_session.query(ProfileProviderBonus).filter_by(
        profile_id=profile_id,
    ).all()
    bonus_map: dict[str, ProfileProviderBonus] = {b.provider_id: b for b in bonuses}

    # Load risk profiles
    risk_profiles = db_session.query(ProviderRiskProfile).all()
    risk_map: dict[str, ProviderRiskProfile] = {r.provider_id: r for r in risk_profiles}

    # Count bets per provider for this profile
    bet_counts = dict(
        db_session.query(
            Bet.provider_id,
            func.count(Bet.id),
        ).filter(
            Bet.profile_id == profile_id,
        ).group_by(Bet.provider_id).all()
    )

    # Build provider sim states
    providers: dict[str, ProviderSimState] = {}
    now = datetime.now(timezone.utc)

    for bal in balances:
        pid = bal.provider_id
        bonus = bonus_map.get(pid)
        risk = risk_map.get(pid)

        # Map DB bonus status to sim status
        db_status = bonus.bonus_status if bonus else "available"
        sim_status = STATUS_MAP.get(db_status, "not_started")

        # Determine freebet_available flag
        freebet_available = db_status == "freebet_available"

        # Bonus fields
        bonus_type = bonus.bonus_type if bonus and bonus.bonus_type else "bonusdeposit"
        bonus_amount = bonus.bonus_amount if bonus else 0.0
        wagered_amount = bonus.wagered_amount if bonus else 0.0
        wagering_requirement = bonus.wagering_requirement if bonus else 0.0
        min_odds = bonus.min_odds if bonus else 1.80

        # Days until expiry
        days_until_expiry = DEFAULT_BONUS_DAYS
        if bonus and bonus.expires_at:
            delta = bonus.expires_at - now
            days_until_expiry = max(0, delta.days)

        # Days active
        days_active = 0
        if bal.account_opened_at:
            days_active = max(0, (now - bal.account_opened_at).days)
        elif risk and risk.first_bet_date:
            days_active = max(0, (now - risk.first_bet_date).days)

        # Provider type from risk profile
        provider_type = "moderate"
        if risk:
            # Map risk_score to provider_type
            if risk.risk_score >= 0.7:
                provider_type = "aggressive"
            elif risk.risk_score <= 0.3:
                provider_type = "lenient"

        # Bets placed
        bets_placed = bet_counts.get(pid, 0)
        if risk and risk.total_bets_placed and risk.total_bets_placed > bets_placed:
            bets_placed = risk.total_bets_placed

        # Limitation risk (current cumulative)
        curve = LIMITATION_CURVES[provider_type]
        limitation_risk = _logistic(bets_placed, curve["midpoint"], curve["steepness"])

        providers[pid] = ProviderSimState(
            provider_id=pid,
            balance=bal.balance or 0.0,
            deposited=bal.balance or 0.0,  # Approximate: current balance as proxy
            bonus_status=sim_status,
            bonus_type=bonus_type,
            bonus_amount=bonus_amount,
            wagered_amount=wagered_amount,
            wagering_requirement=wagering_requirement,
            min_odds=min_odds,
            limitation_risk=limitation_risk,
            bets_placed=bets_placed,
            days_active=days_active,
            days_until_expiry=days_until_expiry,
            freebet_available=freebet_available,
            trigger_settled=sim_status not in ("trigger",),
            provider_type=provider_type,
        )

    # Calculate capital figures
    total_in_providers = sum(p.balance for p in providers.values())
    bankroll = profile.bankroll or 0.0
    withdrawn = profile.total_withdrawn or 0.0

    # Undeployed = bankroll minus what's sitting in providers
    undeployed = max(0.0, bankroll - total_in_providers)

    # Deployable = everything not withdrawn
    deployable = undeployed + sum(
        p.balance for p in providers.values()
        if p.bonus_status not in ("limited", "expired")
    )

    return SimState(
        day=0,
        total_wealth=bankroll + withdrawn,
        deployable_capital=deployable,
        undeployed_capital=undeployed,
        providers=providers,
        withdrawn=withdrawn,
    )
