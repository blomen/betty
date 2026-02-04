"""
Stochastic Selector

Implements softmax-based probabilistic selection of betting opportunities.

Selection Formula:
    P(select a_i) = exp(U_i / T) / Σ_j exp(U_j / T)

Where:
- U_i is the utility of opportunity i
- T is the temperature parameter
- T=0: deterministic (always pick max utility)
- T=1: standard softmax
- T>1: more random selection

This prevents predictable betting patterns that bookmakers can detect.
"""

from dataclasses import dataclass
from typing import Optional
import math
import random
import logging

from sqlalchemy.orm import Session

from ..db.models import RiskConfig, Profile
from .regularizer import UtilityRegularizer, RegularizedOpportunity

logger = logging.getLogger(__name__)


@dataclass
class RankedOpportunity:
    """Opportunity with selection probability."""

    opportunity: RegularizedOpportunity
    selection_probability: float
    rank: int

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            **self.opportunity.to_dict(),
            "selection_probability": round(self.selection_probability, 4),
            "rank": self.rank,
        }


class StochasticSelector:
    """
    Selects betting opportunities using softmax probabilities.

    Instead of always selecting the highest-EV opportunity (which creates
    predictable patterns), we use a softmax distribution to inject
    controlled randomness.

    The temperature parameter controls randomness:
    - T → 0: Almost deterministic (picks max utility)
    - T = 1: Standard softmax
    - T > 1: More uniform/random selection

    Higher temperatures help preserve behavioral entropy.
    """

    def __init__(self, db: Session):
        self.db = db
        self._regularizer = UtilityRegularizer(db)
        self._config: Optional[RiskConfig] = None

    def _get_config(self) -> RiskConfig:
        """Get risk configuration for active profile."""
        if self._config is not None:
            return self._config

        active_profile = self.db.query(Profile).filter(Profile.is_active == True).first()
        if not active_profile:
            active_profile = self.db.query(Profile).first()

        if active_profile:
            config = (
                self.db.query(RiskConfig)
                .filter(RiskConfig.profile_id == active_profile.id)
                .first()
            )
            if config:
                self._config = config
                return config

        # Return defaults if no config found
        self._config = RiskConfig()
        return self._config

    def rank_opportunities(
        self,
        opportunities: list[dict],
        stake: float,
        temperature: Optional[float] = None,
    ) -> list[RankedOpportunity]:
        """
        Rank opportunities with selection probabilities.

        Args:
            opportunities: List of opportunity dicts with:
                - event_id, provider_id, outcome, odds, fair_odds
            stake: Base stake for utility calculation
            temperature: Override softmax temperature (None = use config)

        Returns:
            List of RankedOpportunity sorted by utility (descending)
        """
        config = self._get_config()
        temp = temperature if temperature is not None else config.softmax_temperature

        # Regularize all opportunities
        regularized = self._regularizer.regularize_batch(opportunities, stake)

        if not regularized:
            return []

        # Calculate softmax probabilities
        probabilities = self._softmax([r.utility for r in regularized], temp)

        # Create ranked list
        ranked = [
            RankedOpportunity(
                opportunity=reg,
                selection_probability=prob,
                rank=i + 1,
            )
            for i, (reg, prob) in enumerate(zip(regularized, probabilities))
        ]

        return ranked

    def select(
        self,
        opportunities: list[dict],
        stake: float,
        temperature: Optional[float] = None,
    ) -> Optional[RankedOpportunity]:
        """
        Probabilistically select one opportunity.

        Uses softmax distribution to select with probability proportional
        to utility, rather than always picking the maximum.

        Args:
            opportunities: List of opportunity dicts
            stake: Base stake for utility calculation
            temperature: Override softmax temperature

        Returns:
            Selected RankedOpportunity, or None if no valid opportunities
        """
        ranked = self.rank_opportunities(opportunities, stake, temperature)

        if not ranked:
            return None

        # Sample from distribution
        rand_val = random.random()
        cumulative = 0.0

        for opp in ranked:
            cumulative += opp.selection_probability
            if rand_val <= cumulative:
                logger.debug(
                    f"Selected {opp.opportunity.provider_id}:{opp.opportunity.outcome} "
                    f"(rank={opp.rank}, prob={opp.selection_probability:.3f})"
                )
                return opp

        # Fallback to highest utility (shouldn't happen)
        return ranked[0]

    def select_deterministic(
        self,
        opportunities: list[dict],
        stake: float,
    ) -> Optional[RankedOpportunity]:
        """
        Deterministically select the highest-utility opportunity.

        Use this when you want to see the "best" option without randomness.
        Note: This creates predictable patterns - use select() for real bets.

        Args:
            opportunities: List of opportunity dicts
            stake: Base stake for utility calculation

        Returns:
            Highest-utility RankedOpportunity, or None if no valid opportunities
        """
        # Use temperature near 0 for near-deterministic selection
        ranked = self.rank_opportunities(opportunities, stake, temperature=0.01)

        if not ranked:
            return None

        return ranked[0]

    def _softmax(self, utilities: list[float], temperature: float) -> list[float]:
        """
        Calculate softmax probabilities.

        P(i) = exp(U_i / T) / Σ_j exp(U_j / T)

        Uses log-sum-exp trick for numerical stability.
        """
        if not utilities:
            return []

        if temperature <= 0:
            # Deterministic: all probability on max
            max_idx = utilities.index(max(utilities))
            return [1.0 if i == max_idx else 0.0 for i in range(len(utilities))]

        # Scale utilities by temperature
        scaled = [u / temperature for u in utilities]

        # Log-sum-exp for numerical stability
        max_scaled = max(scaled)
        exp_values = [math.exp(s - max_scaled) for s in scaled]
        sum_exp = sum(exp_values)

        if sum_exp == 0:
            # All utilities identical - uniform distribution
            return [1.0 / len(utilities)] * len(utilities)

        return [e / sum_exp for e in exp_values]

    def get_entropy(self, probabilities: list[float]) -> float:
        """
        Calculate Shannon entropy of the selection distribution.

        Higher entropy = more random/unpredictable selections.
        Lower entropy = more concentrated on few options.

        Returns entropy in bits (log base 2).
        """
        entropy = 0.0
        for p in probabilities:
            if p > 0:
                entropy -= p * math.log2(p)
        return entropy
