"""
Stake Noise Injector

Adds controlled randomness to stake amounts to preserve behavioral entropy.

Key principles:
1. Avoid round numbers (100, 50, 25) - these are easy to track
2. Use natural-looking endings (3, 7, 9, 1)
3. Scale noise with risk score (higher risk = more noise)
4. Maintain stake within reasonable bounds
"""

from dataclasses import dataclass
from typing import Optional
import random
import logging

from sqlalchemy.orm import Session

from ..db.models import RiskConfig, Profile

logger = logging.getLogger(__name__)


# Round numbers that look "professional" and should be avoided
SUSPICIOUS_ENDINGS = {0, 5}  # Stakes ending in 0 or 5 are suspicious
NATURAL_ENDINGS = [1, 2, 3, 4, 6, 7, 8, 9]  # More natural endings

# Exact amounts that are clearly calculated
ROUND_NUMBERS = {
    10, 20, 25, 50, 75, 100, 150, 200, 250,
    300, 400, 500, 750, 1000, 1500, 2000, 2500, 5000,
}


@dataclass
class NoisyStake:
    """Stake with applied noise."""

    original_stake: float
    final_stake: float
    noise_applied: float
    noise_pct: float
    was_rounded: bool  # Original was a round number
    reason: str

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "original_stake": round(self.original_stake, 2),
            "final_stake": round(self.final_stake, 2),
            "noise_applied": round(self.noise_applied, 2),
            "noise_pct": round(self.noise_pct, 2),
            "was_rounded": self.was_rounded,
            "reason": self.reason,
        }


class StakeNoiseInjector:
    """
    Injects controlled noise into stake amounts.

    Bookmakers track betting patterns including:
    - Consistent use of round numbers (100, 50, etc.)
    - Stakes that are clearly calculated (Kelly outputs)
    - Predictable stake sizing patterns

    This injector adds noise to make stakes look more recreational:
    1. Adds random noise within configured percentage
    2. Adjusts endings to avoid suspicious patterns
    3. Ensures final stake doesn't exceed limits
    """

    def __init__(self, db: Session):
        self.db = db
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

    def inject_noise(
        self,
        stake: float,
        risk_score: float = 0.0,
        max_stake: Optional[float] = None,
        min_stake: float = 1.0,
    ) -> NoisyStake:
        """
        Add noise to a stake amount.

        Args:
            stake: Original stake amount
            risk_score: Provider risk score (0-1, higher = more noise)
            max_stake: Maximum allowed stake
            min_stake: Minimum allowed stake

        Returns:
            NoisyStake with adjusted amount
        """
        config = self._get_config()

        if stake <= 0:
            return NoisyStake(
                original_stake=stake,
                final_stake=0,
                noise_applied=0,
                noise_pct=0,
                was_rounded=False,
                reason="Zero stake",
            )

        # Check if original is a round number
        was_rounded = self._is_round_number(stake)

        # Scale noise with risk score
        # Base noise + additional noise for high risk
        base_noise_pct = (config.stake_noise_pct or 5.0) / 100
        risk_bonus = risk_score * 0.05  # Extra 5% at max risk
        total_noise_pct = base_noise_pct + risk_bonus

        # Generate random noise within range
        noise_factor = random.uniform(-total_noise_pct, total_noise_pct)
        noise_amount = stake * noise_factor

        # Apply noise
        noisy_stake = stake + noise_amount

        # Snap to natural amount with adaptive granularity
        noisy_stake = self._snap_to_natural(noisy_stake)

        # Enforce bounds
        if max_stake is not None:
            noisy_stake = min(noisy_stake, max_stake)
        noisy_stake = max(noisy_stake, min_stake)

        # Round to 2 decimal places
        noisy_stake = round(noisy_stake, 2)

        actual_noise = noisy_stake - stake
        actual_noise_pct = (actual_noise / stake * 100) if stake > 0 else 0

        reason = self._generate_reason(was_rounded, actual_noise_pct, risk_score)

        return NoisyStake(
            original_stake=stake,
            final_stake=noisy_stake,
            noise_applied=actual_noise,
            noise_pct=actual_noise_pct,
            was_rounded=was_rounded,
            reason=reason,
        )

    def _is_round_number(self, stake: float) -> bool:
        """Check if stake is a suspicious round number."""
        # Check exact matches
        if stake in ROUND_NUMBERS:
            return True

        # Check if ending in 0 or 5 (for stakes > 10)
        if stake >= 10:
            last_digit = int(stake) % 10
            if last_digit in SUSPICIOUS_ENDINGS:
                return True

        return False

    def _snap_to_natural(self, stake: float) -> float:
        """
        Snap stake to natural amount that a human would actually type.

        Recreational bettors stake clean amounts (50, 100, 150, 175, 200),
        not calculated amounts (63, 171, 173). This method snaps to the
        nearest natural value - no artificial noise needed since Kelly
        outputs vary naturally based on odds/bankroll.

        Granularity scales with stake size:
        - < 10: round to nearest integer
        - < 100: nearest 10
        - 100-500: closest value from natural stakes list
        - 500+: nearest 50
        """
        if stake < 10:
            return round(stake)

        if stake < 100:
            # Small stakes: nearest 10
            return round(stake / 10) * 10

        if stake <= 500:
            # Medium stakes: pick closest natural amount
            natural_stakes = [
                100, 110, 120, 125, 130, 140, 150,
                160, 170, 175, 180, 190, 200, 225,
                250, 275, 300, 350, 400, 450, 500
            ]
            return min(natural_stakes, key=lambda x: abs(x - stake))

        # Large stakes: nearest 50
        return round(stake / 50) * 50

    def _generate_reason(
        self,
        was_rounded: bool,
        noise_pct: float,
        risk_score: float,
    ) -> str:
        """Generate explanation for the noise applied."""
        parts = []

        if was_rounded:
            parts.append("Adjusted from round number")

        if abs(noise_pct) > 3:
            direction = "increased" if noise_pct > 0 else "decreased"
            parts.append(f"{direction} {abs(noise_pct):.1f}%")

        if risk_score > 0.5:
            parts.append(f"extra variance for risk={risk_score:.2f}")

        if not parts:
            parts.append("Minor adjustment for natural appearance")

        return "; ".join(parts)

    def batch_inject(
        self,
        stakes: list[tuple[str, float]],  # [(provider_id, stake), ...]
        risk_scores: dict[str, float],  # {provider_id: risk_score}
        max_stake: Optional[float] = None,
    ) -> dict[str, NoisyStake]:
        """
        Inject noise into multiple stakes.

        Args:
            stakes: List of (provider_id, stake) tuples
            risk_scores: Dict mapping provider_id to risk score
            max_stake: Maximum allowed stake

        Returns:
            Dict mapping provider_id to NoisyStake
        """
        results = {}

        for provider_id, stake in stakes:
            risk_score = risk_scores.get(provider_id, 0.0)
            results[provider_id] = self.inject_noise(
                stake=stake,
                risk_score=risk_score,
                max_stake=max_stake,
            )

        return results
