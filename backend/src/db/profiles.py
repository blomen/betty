"""Profile and provider settings models."""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .base import Base, _utcnow


class Profile(Base):
    """User settings for stake calculation and filtering."""

    __tablename__ = "profiles"

    id = Column(Integer, primary_key=True)
    name = Column(String, default="default", unique=True)

    # Bankroll for this profile
    bankroll = Column(Float, default=1000.0)
    currency = Column(String, default="USD")

    # Kelly criterion
    kelly_fraction = Column(Float, default=0.75)  # Dynamic Kelly scales 0.25-0.75 based on edge

    # Opportunity thresholds
    min_edge_pct = Column(Float, default=2.0)  # Min edge for value bets
    min_arb_pct = Column(Float, default=0.5)  # Min profit for arbs

    # Risk limits
    max_stake_pct = Column(Float, default=5.0)  # Max % of bankroll per bet

    # Bonus settings
    min_retention_pct = Column(Float, default=80.0)  # Min % for free bet value
    preferred_counterparts = Column(String)  # JSON list: ["bet365", "betsson"]
    bonus_enabled = Column(Boolean, default=True)
    bonus_deposit = Column(Float, default=0.0)  # Max deposit match (0 = none)
    total_deposited = Column(Float, default=0.0)  # Cumulative real money deposited (for ROI calc)
    total_withdrawn = Column(Float, default=0.0)  # Cumulative real money withdrawn (for ROI calc)

    # Profile state
    is_active = Column(Boolean, default=False)  # Currently selected profile
    chrome_port = Column(Integer, nullable=True)  # CDP port (default: 9221 + id)
    color = Column(String, nullable=True)  # Hex color for Chrome border (auto-assigned)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    bonus_statuses = relationship("ProfileProviderBonus", back_populates="profile", cascade="all, delete-orphan")
    provider_balances = relationship("ProfileProviderBalance", back_populates="profile", cascade="all, delete-orphan")
    bets = relationship("Bet", back_populates="profile")


class ProfileProviderBonus(Base):
    """
    Per-profile bonus status tracking.

    Each profile tracks bonus status independently per provider.
    When switching profiles, the bonus_status shown is from this table,
    not the global Provider.bonus_status field.

    Wagering tracking:
    - bonus_amount: The bonus received (e.g., 1000 kr)
    - wagering_requirement: Total amount to wager (e.g., 10000 kr = 10x bonus)
    - wagered_amount: Amount wagered so far (only bets with odds >= min_odds count)
    - min_odds: Per-provider minimum odds for wagering qualification (from providers.yaml)
    - When wagered_amount >= wagering_requirement: bonus is "completed"
    """

    __tablename__ = "profile_provider_bonuses"

    id = Column(Integer, primary_key=True)
    profile_id = Column(Integer, ForeignKey("profiles.id"), nullable=False)
    provider_id = Column(String, ForeignKey("providers.id"), nullable=False)

    # 'available' = bonus ready to use
    # 'trigger_needed' = freebet: qualifying trigger bet needed
    # 'freebet_available' = freebet: trigger settled, freebet ready to use
    # 'in_progress' = bonusdeposit: deposited with match, needs wagering
    # 'completed' = bonus fully wagered/used, no more min odds restriction
    # 'claimed' = bonus already used (e.g., from previous account), skip in workflows
    bonus_status = Column(String, default="available")
    bonus_type = Column(String, nullable=True)  # "freebet" or "bonusdeposit"

    # Bonus wagering tracking
    bonus_amount = Column(Float, default=0.0)  # Bonus received
    wagering_multiplier = Column(Float, default=10.0)  # Wagering requirement multiplier (default 10x)
    wagering_requirement = Column(Float, default=0.0)  # Total wagering required (bonus_amount * multiplier)
    wagered_amount = Column(Float, default=0.0)  # Amount wagered so far (odds >= min_odds only)
    min_odds = Column(Float, default=1.80)  # Minimum odds for wagering qualification (per-provider)
    main_min_odds = Column(Float, nullable=True)  # Main wagering min_odds (used after trigger phase completes)
    deposit_amount = Column(Float, nullable=True)  # Original deposit (for trigger→main phase wagering calc)
    trigger_mode = Column(String, default="cumulative")  # "single" or "cumulative"

    # Timer tracking
    claimed_at = Column(DateTime, nullable=True)  # When bonus was claimed/wagering started
    expires_at = Column(DateTime, nullable=True)  # Deadline to complete wagering (claimed_at + 60 days)

    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("profile_id", "provider_id", name="uq_profile_provider_bonus"),
        Index("ix_bonus_profile_status", "profile_id", "bonus_status"),
        Index("ix_bonus_profile_provider", "profile_id", "provider_id"),
    )

    # Relationships
    profile = relationship("Profile", back_populates="bonus_statuses")
    provider = relationship("Provider")


class ProfileProviderBalance(Base):
    """
    Per-profile balance tracking.

    Each profile tracks balances independently per provider.
    This allows multiple profiles (e.g., different identity contexts)
    to have separate bankrolls.
    """

    __tablename__ = "profile_provider_balances"

    id = Column(Integer, primary_key=True)
    profile_id = Column(Integer, ForeignKey("profiles.id"), nullable=False)
    provider_id = Column(String, ForeignKey("providers.id"), nullable=False)
    balance = Column(Float, default=0.0)

    wallet_address = Column(String, nullable=True)  # Legacy — no longer populated

    # Manual account opened date for pre-existing accounts
    # Used for dormant account handling - accounts opened before +EV betting
    account_opened_at = Column(DateTime, nullable=True)

    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("profile_id", "provider_id", name="uq_profile_provider_balance"),
        Index("ix_balance_profile_id", "profile_id"),
    )

    # Relationships
    profile = relationship("Profile", back_populates="provider_balances")
    provider = relationship("Provider")


class ProfileProviderLimit(Base):
    """
    Tracks bookmaker-imposed limits per profile+provider.

    Records when a bookmaker limits an account, with an immutable
    snapshot of betting stats at detection time for correlation analysis.
    """

    __tablename__ = "profile_provider_limits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_id = Column(Integer, ForeignKey("profiles.id"), nullable=False)
    provider_id = Column(String, ForeignKey("providers.id"), nullable=False)

    limit_type = Column(String, nullable=False)  # LimitType enum value
    limit_level = Column(Integer, nullable=False)  # 1=minor, 2=moderate, 3=severe, 4=gutted, 5=closed
    detected_at = Column(DateTime, nullable=False, default=_utcnow)
    notes = Column(Text, nullable=True)

    # Immutable betting stats snapshot at detection time
    betting_snapshot = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("profile_id", "provider_id", "limit_type", name="uq_profile_provider_limit_type"),
        Index("ix_limit_profile_provider", "profile_id", "provider_id"),
    )

    # Relationships
    profile = relationship("Profile")
    provider = relationship("Provider")


class ProviderExtractionSetting(Base):
    """Per-profile override for whether a provider is included in extraction.

    If a row exists with enabled=False, the provider is excluded for that profile.
    If no row exists, the provider is enabled by default (YAML active list).
    """

    __tablename__ = "provider_extraction_settings"

    profile_id = Column(Integer, ForeignKey("profiles.id"), primary_key=True)
    provider_id = Column(String, primary_key=True)
    enabled = Column(Boolean, default=True, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
