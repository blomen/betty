"""
Utility Regularizer

Applies risk penalty to expected value to create a risk-adjusted utility function.

Utility Formula:
    U = EV - λ · RiskPenalty

Where:
- EV = stake * (odds * win_prob - 1)
- RiskPenalty = stake * risk_factor * penalty_multiplier
- penalty_multiplier scales exponentially near threshold

This creates a trade-off between maximizing EV and staying below detection.
"""

from dataclasses import dataclass
from typing import Optional
import logging

from sqlalchemy.orm import Session

from ..db.models import RiskConfig, Profile
from .calculator import RiskCalculator

logger = logging.getLogger(__name__)


@dataclass
class RegularizedOpportunity:
    """An opportunity with risk-adjusted utility."""

    # Original opportunity data
    event_id: str
    provider_id: str
    outcome: str
    odds: float
    fair_odds: float

    # Raw EV calculation
    expected_value: float  # stake * (odds * win_prob - 1)
    edge_pct: float  # (odds / fair_odds - 1) * 100

    # Risk adjustment
    risk_score: float  # Provider risk score (0-1)
    risk_penalty: float  # λ * stake * risk * multiplier
    utility: float  # EV - risk_penalty

    # Stake recommendation
    base_stake: float  # Kelly stake before adjustment
    risk_adjusted_stake: float  # Stake after risk reduction
    stake_multiplier: float  # Applied multiplier

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "event_id": self.event_id,
            "provider_id": self.provider_id,
            "outcome": self.outcome,
            "odds": self.odds,
            "fair_odds": self.fair_odds,
            "expected_value": round(self.expected_value, 2),
            "edge_pct": round(self.edge_pct, 2),
            "risk_score": round(self.risk_score, 3),
            "risk_penalty": round(self.risk_penalty, 2),
            "utility": round(self.utility, 2),
            "base_stake": round(self.base_stake, 2),
            "risk_adjusted_stake": round(self.risk_adjusted_stake, 2),
            "stake_multiplier": round(self.stake_multiplier, 3),
        }


class UtilityRegularizer:
    """
    Applies risk regularization to betting opportunities.

    The regularizer adjusts EV by subtracting a risk penalty:

        U(a) = EV(a) - λ · RiskPenalty(a)

    Where:
    - λ (lambda) is the risk aversion coefficient (0-1)
    - RiskPenalty scales with stake, risk score, and a multiplier
    - The multiplier increases exponentially near critical thresholds

    This creates a Pareto trade-off: some EV is sacrificed for longevity.
    """

    def __init__(self, db: Session):
        self.db = db
        self._risk_calculator = RiskCalculator(db)
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

    def regularize(
        self,
        event_id: str,
        provider_id: str,
        outcome: str,
        odds: float,
        fair_odds: float,
        base_stake: float,
    ) -> RegularizedOpportunity:
        """
        Apply risk regularization to a betting opportunity.

        Args:
            event_id: Event identifier
            provider_id: Provider offering the odds
            outcome: Betting outcome (home/away/draw)
            odds: Provider odds
            fair_odds: True/sharp odds
            base_stake: Kelly-recommended stake before adjustment

        Returns:
            RegularizedOpportunity with adjusted utility and stake
        """
        config = self._get_config()

        # Get provider risk score
        assessment = self._risk_calculator.assess_provider(provider_id)
        risk_score = assessment.risk_score

        # Calculate edge and EV
        win_prob = 1 / fair_odds if fair_odds > 1 else 0.5
        edge_pct = (odds / fair_odds - 1) * 100 if fair_odds > 0 else 0

        # EV = stake * (odds * win_prob - 1)
        expected_value = base_stake * (odds * win_prob - 1)

        # Calculate risk penalty
        # penalty_multiplier increases exponentially near threshold
        excess = max(0, risk_score - config.threshold_high)
        penalty_multiplier = 1 + (excess ** 2) * 10

        risk_penalty = (
            config.lambda_coefficient
            * base_stake
            * risk_score
            * penalty_multiplier
        )

        # Utility = EV - risk penalty
        utility = expected_value - risk_penalty

        # Stake adjustment based on risk score
        # At risk_score=0: multiplier=1.0, at risk_score=1: multiplier=0.5
        stake_multiplier = 1 - (risk_score * 0.5)

        # Additional reduction for high correlation (hedging)
        if assessment.features.outcome_correlation > 0.5:
            correlation_penalty = 1 - (assessment.features.outcome_correlation - 0.5)
            stake_multiplier *= correlation_penalty

        risk_adjusted_stake = base_stake * stake_multiplier

        return RegularizedOpportunity(
            event_id=event_id,
            provider_id=provider_id,
            outcome=outcome,
            odds=odds,
            fair_odds=fair_odds,
            expected_value=expected_value,
            edge_pct=edge_pct,
            risk_score=risk_score,
            risk_penalty=risk_penalty,
            utility=utility,
            base_stake=base_stake,
            risk_adjusted_stake=risk_adjusted_stake,
            stake_multiplier=stake_multiplier,
        )

    def regularize_batch(
        self,
        opportunities: list[dict],
        base_stake: float,
    ) -> list[RegularizedOpportunity]:
        """
        Apply risk regularization to multiple opportunities.

        Args:
            opportunities: List of opportunity dicts with:
                - event_id, provider_id, outcome, odds, fair_odds
            base_stake: Base stake for all opportunities

        Returns:
            List of RegularizedOpportunity sorted by utility (descending)
        """
        regularized = []

        for opp in opportunities:
            try:
                reg = self.regularize(
                    event_id=opp["event_id"],
                    provider_id=opp["provider_id"],
                    outcome=opp["outcome"],
                    odds=opp["odds"],
                    fair_odds=opp["fair_odds"],
                    base_stake=base_stake,
                )
                regularized.append(reg)
            except Exception as e:
                logger.error(f"Failed to regularize opportunity: {e}")

        # Sort by utility descending
        regularized.sort(key=lambda x: x.utility, reverse=True)

        return regularized

    def should_skip_provider(self, provider_id: str) -> tuple[bool, Optional[str]]:
        """
        Check if provider should be skipped due to high risk or cooldown.

        Returns:
            (should_skip, reason) tuple
        """
        assessment = self._risk_calculator.assess_provider(provider_id)

        if assessment.is_on_cooldown:
            return True, f"On cooldown until {assessment.cooldown_until}"

        if assessment.risk_level == "critical":
            return True, f"Risk level critical ({assessment.risk_score:.2f})"

        return False, None
