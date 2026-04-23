"""Risk management models."""

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from .base import Base, _utcnow


class ProviderRiskProfile(Base):
    """
    Tracks behavioral risk metrics per provider.

    Risk scores are computed from betting patterns that may trigger
    bookmaker detection algorithms.
    """

    __tablename__ = "provider_risk_profiles"

    id = Column(Integer, primary_key=True)
    provider_id = Column(String, ForeignKey("providers.id"), nullable=False, unique=True)

    # Overall risk score (0.0 = safe, 1.0 = high risk)
    risk_score = Column(Float, default=0.0)
    risk_level = Column(String, default="low")  # "low", "medium", "high", "critical"

    # Individual feature scores (0.0-1.0, higher = more suspicious)
    stake_entropy = Column(Float, default=0.0)  # CV of stakes + round number ratio
    market_diversity = Column(Float, default=0.0)  # Sports/leagues spread
    timing_regularity = Column(Float, default=0.0)  # Hour/day concentration
    outcome_correlation = Column(Float, default=0.0)  # Hedge detection
    bonus_usage_ratio = Column(Float, default=0.0)  # Bonus bet percentage
    clv_score = Column(Float, default=0.0)  # Average closing line value
    win_rate_deviation = Column(Float, default=0.0)  # Actual vs expected

    # Brier score for calibration tracking (lower = better)
    brier_score = Column(Float, nullable=True)

    # Account tracking
    first_bet_date = Column(DateTime, nullable=True)  # Date of first bet on this provider
    total_bets_placed = Column(Integer, default=0)  # All-time bet count

    # Cooldown tracking
    is_on_cooldown = Column(Boolean, default=False)
    cooldown_until = Column(DateTime, nullable=True)
    cooldown_reason = Column(String, nullable=True)

    # Metadata
    last_calculated_at = Column(DateTime, default=_utcnow)
    bets_analyzed = Column(Integer, default=0)  # Number of bets in calculation window

    # Relationships
    provider = relationship("Provider")


class RiskConfig(Base):
    """
    Configurable risk parameters per profile.

    These control how aggressively the system penalizes risky behavior.
    """

    __tablename__ = "risk_configs"

    id = Column(Integer, primary_key=True)
    profile_id = Column(Integer, ForeignKey("profiles.id"), nullable=False, unique=True)

    # Core parameters
    lambda_coefficient = Column(Float, default=0.3)  # Risk aversion (0=ignore, 1=very conservative)
    stake_noise_pct = Column(Float, default=5.0)  # Max % noise on stakes
    softmax_temperature = Column(Float, default=1.0)  # Selection randomness (T=0 deterministic)

    # Feature weights (must sum to 1.0 for normalized scoring)
    weight_stake_entropy = Column(Float, default=0.12)
    weight_market_diversity = Column(Float, default=0.08)
    weight_timing_regularity = Column(Float, default=0.12)
    weight_outcome_correlation = Column(Float, default=0.15)
    weight_bonus_usage = Column(Float, default=0.12)
    weight_clv = Column(Float, default=0.15)
    weight_win_rate = Column(Float, default=0.10)

    # Risk level thresholds
    threshold_low = Column(Float, default=0.3)  # < this = low risk
    threshold_medium = Column(Float, default=0.5)  # < this = medium risk
    threshold_high = Column(Float, default=0.7)  # < this = high risk
    # >= threshold_high = critical

    # Behavioral parameters
    rolling_window_days = Column(Integer, default=30)  # Feature calculation window
    cooldown_trigger_score = Column(Float, default=0.75)  # Auto-cooldown threshold
    cooldown_duration_hours = Column(Integer, default=24)  # Default cooldown length

    # Provider allocation
    daily_bet_cap = Column(Integer, default=0)  # 0 = no cap; >0 = max bets per day per platform group

    # Updated timestamp
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    profile = relationship("Profile")
