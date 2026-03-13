"""
Monte Carlo Planner — Decision engine for bankroll deployment.

Evaluates candidate actions (deposit, withdraw, wait) by simulating
forward trajectories from the current state. Picks the action that
maximizes expected terminal wealth over a rolling horizon.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

from .edge_sampler import EdgeSampler
from .simulator import ProviderSimState, SimState, apply_action, simulate_day


@dataclass
class Action:
    type: str  # DEPOSIT | WITHDRAW | USE_FREEBET | WAIT
    provider_id: str | None = None
    amount: float | None = None
    bet_id: str | None = None  # For USE_FREEBET

    def __hash__(self):
        return hash((self.type, self.provider_id, self.amount))

    def __eq__(self, other):
        if not isinstance(other, Action):
            return False
        return (self.type, self.provider_id, self.amount) == (
            other.type,
            other.provider_id,
            other.amount,
        )

    def to_dict(self) -> dict:
        return {
            k: v
            for k, v in {
                "type": self.type,
                "provider_id": self.provider_id,
                "amount": self.amount,
                "bet_id": self.bet_id,
            }.items()
            if v is not None
        }


@dataclass
class PlannerRecommendation:
    primary_action: Action
    routing_priority: list[str]  # Provider IDs ranked for bet routing tiebreaker
    simulated_growth: float  # Expected bankroll growth % over horizon
    confidence: float  # 0-1, sigmoid-bounded
    downside_p10: float  # 10th percentile terminal wealth
    all_results: dict  # Action → {"mean", "std", "p10"}
    generated_at: datetime

    def to_dict(self) -> dict:
        return {
            "primary_action": self.primary_action.to_dict(),
            "routing_priority": self.routing_priority,
            "simulated_growth": round(self.simulated_growth, 2),
            "confidence": round(self.confidence, 3),
            "downside_p10": round(self.downside_p10, 2),
            "generated_at": self.generated_at.isoformat(),
            "alternatives": [
                {
                    "action": a.to_dict(),
                    "mean": round(r["mean"], 2),
                    "std": round(r["std"], 2),
                    "p10": round(r["p10"], 2),
                }
                for a, r in self.all_results.items()
            ],
        }


def generate_candidates(state: SimState, bonus_configs: dict) -> list[Action]:
    """Generate candidate actions from current state."""
    candidates = [Action(type="WAIT")]

    # DEPOSIT: only for not_started providers with undeployed capital
    for p in state.providers.values():
        if p.bonus_status == "not_started" and state.undeployed_capital > 0:
            cfg = bonus_configs.get(p.provider_id)
            if not cfg:
                continue
            deposit_amount = min(cfg.get("amount", 1000), state.undeployed_capital)
            min_deposit = cfg.get("min_deposit", 100)
            if deposit_amount < min_deposit:
                continue
            candidates.append(
                Action(type="DEPOSIT", provider_id=p.provider_id, amount=deposit_amount)
            )

    # WITHDRAW: only for cleared/limited providers with balance > 0
    for p in state.providers.values():
        if p.bonus_status in ("cleared", "limited") and p.balance > 0:
            candidates.append(
                Action(
                    type="WITHDRAW",
                    provider_id=p.provider_id,
                    amount=round(p.balance, 2),
                )
            )

    return candidates


class MonteCarloPlanner:
    """Evaluate candidate actions via Monte Carlo forward simulation."""

    def __init__(
        self,
        edge_sampler: EdgeSampler,
        n_trajectories: int = 1000,
        horizon_days: int = 30,
    ):
        self.edge_sampler = edge_sampler
        self.n_trajectories = n_trajectories
        self.horizon_days = horizon_days
        self._plan_lock = asyncio.Lock()

    async def plan(
        self, current_state: SimState, bonus_configs: dict
    ) -> PlannerRecommendation:
        """Run Monte Carlo planning (async wrapper, runs in thread pool)."""
        async with self._plan_lock:
            return await asyncio.to_thread(
                self._plan_sync, current_state, bonus_configs
            )

    def _plan_sync(
        self, current_state: SimState, bonus_configs: dict
    ) -> PlannerRecommendation:
        candidates = generate_candidates(current_state, bonus_configs)
        results: dict[Action, dict] = {}

        for action in candidates:
            terminal_bankrolls = []
            for _ in range(self.n_trajectories):
                state = deepcopy(current_state)
                apply_action(state, action)
                for _day in range(self.horizon_days):
                    greedy_action = self._greedy_policy(state, bonus_configs)
                    simulate_day(state, greedy_action, self.edge_sampler)
                terminal_bankrolls.append(state.total_wealth)

            arr = np.array(terminal_bankrolls)
            results[action] = {
                "mean": float(np.mean(arr)),
                "std": float(np.std(arr)),
                "p10": float(np.percentile(arr, 10)),
            }

        best_action = max(results, key=lambda a: results[a]["mean"])
        best_stats = results[best_action]

        routing_priority = self._compute_routing_priority(current_state, bonus_configs)

        return PlannerRecommendation(
            primary_action=best_action,
            routing_priority=routing_priority,
            simulated_growth=(
                best_stats["mean"] / max(1, current_state.total_wealth) - 1
            )
            * 100,
            confidence=1 / (1 + best_stats["std"] / max(1, best_stats["mean"])),
            downside_p10=best_stats["p10"],
            all_results=results,
            generated_at=datetime.now(timezone.utc),
        )

    def _greedy_policy(self, state: SimState, bonus_configs: dict) -> Action:
        """Default policy for future days: withdraw cleared -> deposit best -> wait."""
        # Priority 1: Withdraw from cleared/limited providers
        for p in state.providers.values():
            if p.bonus_status in ("cleared", "limited") and p.balance > 0:
                return Action(
                    type="WITHDRAW", provider_id=p.provider_id, amount=p.balance
                )

        # Priority 2: Deposit at next best provider (highest bonus density)
        if state.undeployed_capital > 0:
            not_started = [
                p for p in state.providers.values() if p.bonus_status == "not_started"
            ]
            if not_started:

                def bonus_density(p: ProviderSimState) -> float:
                    cfg = bonus_configs.get(p.provider_id, {})
                    amount = cfg.get("amount", 0)
                    wager_req = cfg.get("amount", 1) * cfg.get(
                        "wagering_multiplier", 10
                    )
                    return amount / max(1, wager_req)

                not_started.sort(key=bonus_density, reverse=True)
                best = not_started[0]
                cfg = bonus_configs.get(best.provider_id, {})
                amount = min(cfg.get("amount", 1000), state.undeployed_capital)
                return Action(
                    type="DEPOSIT", provider_id=best.provider_id, amount=amount
                )

        return Action(type="WAIT")

    def _compute_routing_priority(
        self, state: SimState, bonus_configs: dict
    ) -> list[str]:
        """Rank active wagering providers by urgency for bet routing tiebreaker."""
        active = [
            p for p in state.providers.values() if p.bonus_status == "wagering"
        ]

        def urgency_score(p: ProviderSimState) -> float:
            remaining_pct = 1 - (p.wagered_amount / max(1, p.wagering_requirement))
            deadline_factor = 1 / max(1, p.days_until_expiry)
            lim_factor = p.limitation_risk
            return remaining_pct * deadline_factor + lim_factor

        active.sort(key=urgency_score, reverse=True)
        return [p.provider_id for p in active]

    def recommend_freebet_usage(
        self, state: SimState, opportunities: list[dict]
    ) -> Action | None:
        """Find highest-edge opportunity at a freebet-ready provider."""
        for p in state.providers.values():
            if not p.freebet_available:
                continue
            provider_opps = [
                o
                for o in opportunities
                if o.get("provider_id") == p.provider_id
                or o.get("provider1_id") == p.provider_id
            ]
            if not provider_opps:
                continue
            best = max(provider_opps, key=lambda o: o.get("edge_pct", 0))
            return Action(
                type="USE_FREEBET",
                provider_id=p.provider_id,
                bet_id=str(best.get("id", "")),
            )
        return None


def fallback_routing(
    opportunities: list[dict], active_bonuses: list
) -> list[dict]:
    """Simple heuristic fallback: max EV, tiebreak on wagering urgency."""
    bonus_map: dict[str, object] = {}
    for b in active_bonuses:
        pid = b.provider_id if hasattr(b, "provider_id") else b.get("provider_id")
        bonus_map[pid] = b

    def sort_key(opp: dict) -> float:
        edge = opp.get("edge_pct", 0)
        pid = opp.get("provider_id") or opp.get("provider1_id", "")
        bonus = bonus_map.get(pid)
        urgency = 0.0
        if bonus:
            status = (
                bonus.bonus_status
                if hasattr(bonus, "bonus_status")
                else bonus.get("bonus_status")
            )
            if status == "in_progress":
                req = (
                    bonus.wagering_requirement
                    if hasattr(bonus, "wagering_requirement")
                    else bonus.get("wagering_requirement", 0)
                )
                wagered = (
                    bonus.wagered_amount
                    if hasattr(bonus, "wagered_amount")
                    else bonus.get("wagered_amount", 0)
                )
                remaining = max(0, req - wagered)
                urgency = remaining * 0.0001  # Tiny tiebreaker
        return -edge + urgency

    return sorted(opportunities, key=sort_key)
