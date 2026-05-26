"""
Risk Score Calculator

Computes overall risk scores from behavioral features and determines
risk levels for providers.

Risk Score Formula:
    R = Σ (weight_i * feature_i)

Where features are normalized 0-1 and weights sum to 1.0.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..db.models import Profile, ProviderRiskProfile, RiskConfig, RiskLevel
from .features import BehavioralFeatures, FeatureExtractor

logger = logging.getLogger(__name__)


@dataclass
class RiskAssessment:
    """Complete risk assessment for a provider."""

    provider_id: str
    risk_score: float  # 0.0-1.0
    risk_level: str  # "low", "medium", "high", "critical"
    features: BehavioralFeatures
    recommendations: list[str] = field(default_factory=list)
    is_on_cooldown: bool = False
    cooldown_until: datetime | None = None
    cooldown_reason: str | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "provider_id": self.provider_id,
            "risk_score": round(self.risk_score, 3),
            "risk_level": self.risk_level,
            "features": self.features.to_dict(),
            "recommendations": self.recommendations,
            "is_on_cooldown": self.is_on_cooldown,
            "cooldown_until": self.cooldown_until.isoformat() if self.cooldown_until else None,
            "cooldown_reason": self.cooldown_reason,
        }


class RiskCalculator:
    """
    Calculates risk scores from behavioral features.

    The risk score is a weighted sum of normalized features,
    where each feature represents a pattern that bookmakers
    use to identify sharp bettors.

    Default weights (configurable via RiskConfig):
    - stake_entropy: 0.15 (uniform stakes are suspicious)
    - market_diversity: 0.10 (concentration on few sports)
    - timing_regularity: 0.15 (predictable betting times)
    - outcome_correlation: 0.20 (hedging detection)
    - bonus_usage: 0.15 (bonus exploitation)
    - clv: 0.15 (beating closing lines)
    - win_rate: 0.10 (winning more than expected)
    """

    def __init__(self, db: Session):
        self.db = db
        self._feature_extractor: FeatureExtractor | None = None
        self._config: RiskConfig | None = None

    def _get_config(self) -> RiskConfig:
        """Get or create risk configuration for active profile."""
        if self._config is not None:
            return self._config

        # Get active profile
        active_profile = self.db.query(Profile).filter(Profile.is_active).first()
        if not active_profile:
            # Create default profile if none exists
            active_profile = self.db.query(Profile).first()
            if not active_profile:
                active_profile = Profile(name="default", is_active=True)
                self.db.add(active_profile)
                self.db.commit()

        # Get or create risk config
        config = self.db.query(RiskConfig).filter(RiskConfig.profile_id == active_profile.id).first()

        if not config:
            config = RiskConfig(profile_id=active_profile.id)
            self.db.add(config)
            self.db.commit()

        self._config = config
        return config

    def _get_feature_extractor(self) -> FeatureExtractor:
        """Get feature extractor with configured window."""
        if self._feature_extractor is None:
            config = self._get_config()
            self._feature_extractor = FeatureExtractor(self.db, window_days=config.rolling_window_days)
        return self._feature_extractor

    def assess_provider(self, provider_id: str) -> RiskAssessment:
        """
        Perform complete risk assessment for a provider.

        Args:
            provider_id: Provider to assess

        Returns:
            RiskAssessment with score, level, features, and recommendations
        """
        config = self._get_config()
        extractor = self._get_feature_extractor()

        # Extract features
        features = extractor.extract_for_provider(provider_id)

        # Calculate weighted score
        risk_score = self._calculate_score(features, config)

        # ML limit prediction (M2) — best-effort blend
        try:
            from src.ml.serving.predictor import get_predictor

            predictor = get_predictor()
            if predictor.is_loaded("limit_predictor"):
                from src.ml.features.limit_features import extract_limit_features

                limit_features = extract_limit_features(
                    stake_entropy=features.stake_entropy,
                    market_diversity=features.market_diversity,
                    timing_regularity=features.timing_regularity,
                    outcome_correlation=features.outcome_correlation,
                    bonus_usage_ratio=features.bonus_usage_ratio,
                    clv_score=features.clv_score,
                    win_rate_deviation=features.win_rate_deviation,
                    total_bets=features.total_bets_all_time,
                    account_age_days=features.account_age_days,
                    total_turnover=0,
                    provider_id=provider_id,
                    similar_platform_limits=0,
                )
                ml_risk = predictor.predict("limit_predictor", limit_features)
                if ml_risk is not None:
                    risk_score = 0.7 * ml_risk + 0.3 * risk_score
        except Exception:
            pass

        # Determine risk level
        risk_level = self._determine_level(risk_score, config)

        # Get cooldown status
        profile = self._get_or_create_risk_profile(provider_id)

        # Generate recommendations
        recommendations = self._generate_recommendations(features, risk_score, risk_level)

        # Update stored profile
        self._update_risk_profile(provider_id, risk_score, risk_level, features)

        return RiskAssessment(
            provider_id=provider_id,
            risk_score=risk_score,
            risk_level=risk_level,
            features=features,
            recommendations=recommendations,
            is_on_cooldown=profile.is_on_cooldown,
            cooldown_until=profile.cooldown_until,
            cooldown_reason=profile.cooldown_reason,
        )

    def _calculate_score(self, features: BehavioralFeatures, config: RiskConfig) -> float:
        """Calculate weighted risk score from features."""
        score = (
            config.weight_stake_entropy * features.stake_entropy
            + config.weight_market_diversity * features.market_diversity
            + config.weight_timing_regularity * features.timing_regularity
            + config.weight_outcome_correlation * features.outcome_correlation
            + config.weight_bonus_usage * features.bonus_usage_ratio
            + config.weight_clv * features.clv_score
            + config.weight_win_rate * features.win_rate_deviation
        )

        return min(1.0, max(0.0, score))

    def _determine_level(self, score: float, config: RiskConfig) -> str:
        """Determine risk level from score."""
        if score < config.threshold_low:
            return RiskLevel.LOW.value
        elif score < config.threshold_medium:
            return RiskLevel.MEDIUM.value
        elif score < config.threshold_high:
            return RiskLevel.HIGH.value
        else:
            return RiskLevel.CRITICAL.value

    def _get_or_create_risk_profile(self, provider_id: str) -> ProviderRiskProfile:
        """Get or create risk profile for provider."""
        profile = self.db.query(ProviderRiskProfile).filter(ProviderRiskProfile.provider_id == provider_id).first()

        if not profile:
            profile = ProviderRiskProfile(provider_id=provider_id)
            self.db.add(profile)
            self.db.commit()

        return profile

    def _update_risk_profile(
        self,
        provider_id: str,
        risk_score: float,
        risk_level: str,
        features: BehavioralFeatures,
    ) -> None:
        """Update stored risk profile with new assessment."""
        profile = self._get_or_create_risk_profile(provider_id)

        profile.risk_score = risk_score
        profile.risk_level = risk_level
        profile.stake_entropy = features.stake_entropy
        profile.market_diversity = features.market_diversity
        profile.timing_regularity = features.timing_regularity
        profile.outcome_correlation = features.outcome_correlation
        profile.bonus_usage_ratio = features.bonus_usage_ratio
        profile.clv_score = features.clv_score
        profile.win_rate_deviation = features.win_rate_deviation
        profile.total_bets_placed = features.total_bets_all_time
        profile.bets_analyzed = features.bets_analyzed
        profile.last_calculated_at = datetime.now(UTC)

        # Update first_bet_date if we have account age info and it's not set
        if features.account_age_days > 0 and profile.first_bet_date is None:
            from datetime import timedelta

            profile.first_bet_date = datetime.now(UTC) - timedelta(days=features.account_age_days)

        # Auto-cooldown if score exceeds threshold
        config = self._get_config()
        if risk_score >= config.cooldown_trigger_score and not profile.is_on_cooldown:
            from datetime import timedelta

            profile.is_on_cooldown = True
            profile.cooldown_until = datetime.now(UTC) + timedelta(hours=config.cooldown_duration_hours)
            profile.cooldown_reason = (
                f"Auto-cooldown: risk score {risk_score:.2f} >= {config.cooldown_trigger_score:.2f}"
            )
            logger.warning(
                f"Provider {provider_id} placed on auto-cooldown: "
                f"score={risk_score:.2f}, until={profile.cooldown_until}"
            )

        self.db.commit()

    def _generate_recommendations(
        self,
        features: BehavioralFeatures,
        score: float,
        level: str,
    ) -> list[str]:
        """Generate actionable recommendations based on risk factors."""
        recommendations = []

        if features.stake_entropy > 0.6:
            recommendations.append("Vary stake amounts more - avoid round numbers and consistent patterns")

        if features.market_diversity > 0.6:
            recommendations.append("Spread bets across more sports and leagues")

        if features.timing_regularity > 0.6:
            recommendations.append("Vary betting times - avoid predictable patterns")

        if features.outcome_correlation > 0.6:
            recommendations.append("Reduce hedging across providers on same events")

        if features.bonus_usage_ratio > 0.5:
            recommendations.append("Reduce bonus bet ratio - place more regular bets")

        if features.clv_score > 0.6:
            recommendations.append("Consider taking slightly worse lines occasionally")

        if features.win_rate_deviation > 0.6:
            recommendations.append("High win rate detected - consider manual cooldown")

        if level in (RiskLevel.HIGH.value, RiskLevel.CRITICAL.value):
            recommendations.insert(
                0, f"ALERT: Risk level is {level.upper()} - consider taking a break from this provider"
            )

        return recommendations

    def get_all_assessments(self) -> dict[str, RiskAssessment]:
        """Get risk assessments for all providers with bet history."""
        from ..db.models import Bet

        # Get providers with recent bets
        provider_ids = self.db.query(Bet.provider_id).distinct().all()

        assessments = {}
        for (provider_id,) in provider_ids:
            try:
                assessments[provider_id] = self.assess_provider(provider_id)
            except Exception as e:
                logger.error(f"Failed to assess provider {provider_id}: {e}")

        return assessments

    def calculate_brier_score(self, provider_id: str) -> float | None:
        """
        Calculate Brier score for calibration tracking.

        Brier = (1/N) × Σ(predicted_prob - outcome)²

        Where:
        - predicted_prob = 1/fair_odds (our estimate)
        - outcome = 1 if won, 0 if lost
        - Lower = better calibrated (0.0 = perfect)
        """
        from ..db.models import Bet

        bets = self.db.query(Bet).filter(Bet.provider_id == provider_id).filter(Bet.result.in_(["won", "lost"])).all()

        if len(bets) < 10:
            return None

        total_squared_error = 0.0
        for bet in bets:
            # Estimate fair probability from odds
            # This is approximate - ideally we'd store fair_odds at bet time
            predicted_prob = 1 / bet.odds if bet.odds > 1 else 0.5
            outcome = 1 if bet.result == "won" else 0
            total_squared_error += (predicted_prob - outcome) ** 2

        brier_score = total_squared_error / len(bets)

        # Update profile
        profile = self._get_or_create_risk_profile(provider_id)
        profile.brier_score = brier_score
        self.db.commit()

        return brier_score
