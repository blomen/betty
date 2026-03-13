"""
Bankroll Planner Service — orchestrates Monte Carlo planning.

Manages the planner lifecycle: snapshot current state, run simulations,
cache recommendations, and provide the latest plan to the API/scanner.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..api.routes.providers import load_provider_bonuses
from ..bankroll.edge_sampler import EdgeSampler
from ..bankroll.planner import MonteCarloPlanner, PlannerRecommendation
from ..bankroll.simulator import snapshot_current_state

logger = logging.getLogger(__name__)


class BankrollPlannerService:
    """Orchestrates Monte Carlo bankroll planning with in-memory caching."""

    CACHE_TTL = timedelta(hours=6)

    # Class-level cache shared across instances (same process)
    _cache: dict[int, PlannerRecommendation] = {}

    def __init__(self, db_session: Session):
        self.db = db_session

    async def run_planner(self, profile_id: int) -> PlannerRecommendation:
        """Run Monte Carlo planning from current state and cache the result."""
        logger.info(f"[Planner] Starting planning for profile {profile_id}")

        edge_sampler = EdgeSampler(self.db)
        planner = MonteCarloPlanner(edge_sampler)
        current_state = snapshot_current_state(self.db, profile_id)
        bonus_configs = load_provider_bonuses()

        recommendation = await planner.plan(current_state, bonus_configs)
        self._cache[profile_id] = recommendation

        logger.info(
            f"[Planner] Plan complete: action={recommendation.primary_action.type} "
            f"provider={recommendation.primary_action.provider_id} "
            f"growth={recommendation.simulated_growth:.1f}% "
            f"confidence={recommendation.confidence:.2f}"
        )
        return recommendation

    def get_latest_recommendation(self, profile_id: int) -> PlannerRecommendation | None:
        """Return cached recommendation if fresh (< TTL), else None."""
        cached = self._cache.get(profile_id)
        if cached and datetime.now(timezone.utc) - cached.generated_at < self.CACHE_TTL:
            return cached
        return None

    @classmethod
    def invalidate_cache(cls, profile_id: int) -> None:
        """Clear cached recommendation for a profile (triggers re-plan on next request)."""
        cls._cache.pop(profile_id, None)
