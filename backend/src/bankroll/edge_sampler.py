"""
Edge Sampler — Historical edge distribution builder for Monte Carlo simulation.

Queries historical opportunities to build per-provider empirical distributions
of edge%, odds, and fair_odds. Used by the simulator to sample realistic
betting opportunities during forward simulation.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy.orm import Session


@dataclass
class SimOpportunity:
    """A single simulated betting opportunity."""

    edge: float  # Decimal, e.g. 0.05 for 5%
    odds: float  # Decimal odds
    fair_odds: float  # Pinnacle de-vigged odds


@dataclass
class ProviderDistribution:
    """Empirical edge distribution for a single provider."""

    provider_id: str
    avg_opportunities_per_day: float
    historical_data: list[SimOpportunity]


class EdgeSampler:
    """
    Samples realistic betting opportunities from historical edge distributions.

    Builds per-provider empirical distributions from the Opportunity table,
    then provides Poisson-sampled daily volumes and bootstrap-sampled
    opportunities for Monte Carlo simulation.
    """

    MIN_FALLBACK_VOLUME = 3  # Minimum opportunities/day if no history

    def __init__(self, db_session: Session):
        self.distributions: dict[str, ProviderDistribution] = self._build_from_history(db_session)
        self._global_avg: ProviderDistribution | None = self._build_global_average()

    def get_daily_volume(self, provider_id: str) -> int:
        """Return Poisson-sampled daily opportunity count for this provider."""
        dist = self.distributions.get(provider_id)
        if not dist:
            avg = self._global_avg.avg_opportunities_per_day if self._global_avg else self.MIN_FALLBACK_VOLUME
            return max(0, int(np.random.poisson(avg)))
        return max(0, int(np.random.poisson(dist.avg_opportunities_per_day)))

    def sample(self, provider_id: str, n: int) -> list[SimOpportunity]:
        """Sample N opportunities from empirical distribution (bootstrap)."""
        dist = self.distributions.get(provider_id)
        if not dist or not dist.historical_data or n <= 0:
            # Cold start: use global average
            if self._global_avg and self._global_avg.historical_data and n > 0:
                dist = self._global_avg
            else:
                return []
        indices = np.random.randint(0, len(dist.historical_data), size=n)
        return [dist.historical_data[i] for i in indices]

    def _build_from_history(self, db_session: Session) -> dict[str, ProviderDistribution]:
        """Query opportunities table to build per-provider distributions."""
        from ..db.models import Opportunity

        cutoff = datetime.now(timezone.utc) - timedelta(days=30)

        rows = db_session.query(
            Opportunity.provider1_id,
            Opportunity.edge_pct,
            Opportunity.odds1,
            Opportunity.created_at,
        ).filter(
            Opportunity.type == "value",
            Opportunity.edge_pct.isnot(None),
            Opportunity.odds1.isnot(None),
            Opportunity.created_at >= cutoff,
        ).all()

        provider_data: dict[str, list[SimOpportunity]] = defaultdict(list)
        provider_dates: dict[str, set] = defaultdict(set)

        for row in rows:
            provider_id = row.provider1_id
            edge = row.edge_pct / 100.0  # Percentage to decimal
            odds = row.odds1
            fair_odds = odds / (1 + edge) if edge > -1 else odds

            provider_data[provider_id].append(SimOpportunity(
                edge=edge, odds=odds, fair_odds=fair_odds,
            ))
            if row.created_at:
                provider_dates[provider_id].add(row.created_at.date())

        distributions: dict[str, ProviderDistribution] = {}
        for pid, data in provider_data.items():
            n_days = max(1, len(provider_dates[pid]))
            avg_per_day = len(data) / n_days
            distributions[pid] = ProviderDistribution(
                provider_id=pid,
                avg_opportunities_per_day=avg_per_day,
                historical_data=data,
            )

        return distributions

    def _build_global_average(self) -> ProviderDistribution | None:
        """Build a global average distribution as cold-start fallback."""
        all_data: list[SimOpportunity] = []
        total_avg = 0.0
        count = 0

        for dist in self.distributions.values():
            all_data.extend(dist.historical_data)
            total_avg += dist.avg_opportunities_per_day
            count += 1

        if not all_data:
            return None

        return ProviderDistribution(
            provider_id="_global",
            avg_opportunities_per_day=total_avg / count if count else self.MIN_FALLBACK_VOLUME,
            historical_data=all_data,
        )
