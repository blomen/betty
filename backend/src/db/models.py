"""
OddOpp Database Models

SQLite schema for:
- Canonical events (provider-agnostic)
- Odds per provider
- Provider balances
- Manual bet tracking
- User profile settings
- Risk management profiles
"""

from datetime import datetime
from pathlib import Path
from enum import Enum

from sqlalchemy import (
    create_engine, Column, Integer, String, Float,
    DateTime, Boolean, ForeignKey, UniqueConstraint, Text, JSON, Index
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship


class RiskLevel(str, Enum):
    """Risk level classification for providers."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

# Database file location
DB_PATH = Path(__file__).parent.parent.parent / "data" / "oddopp.db"

Base = declarative_base()


# ============ Core Models ============

class Event(Base):
    """
    A canonical sporting event.
    
    Events are provider-agnostic - the same match has ONE event row,
    with odds from multiple providers stored in the Odds table.
    """
    __tablename__ = "events"
    
    # Canonical ID: "{sport}:{home_normalized}:{away_normalized}:{date}"
    id = Column(String, primary_key=True)
    
    sport = Column(String, nullable=False)
    league = Column(String)
    home_team = Column(String, nullable=False)  # Normalized name
    away_team = Column(String, nullable=False)  # Normalized name
    start_time = Column(DateTime)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    odds = relationship("Odds", back_populates="event", cascade="all, delete-orphan")
    bets = relationship("Bet", back_populates="event")


class Provider(Base):
    """
    A betting provider (bookmaker).

    Stores runtime state only - extraction logic lives in code.
    """
    __tablename__ = "providers"

    id = Column(String, primary_key=True)       # "unibet"
    name = Column(String, nullable=False)       # "Unibet"
    url = Column(String)                        # "unibet.se"

    is_enabled = Column(Boolean, default=True)  # Can toggle off
    balance = Column(Float, default=0.0)        # Your current balance

    # DEPRECATED: Bonus status is now tracked per-profile in ProfileProviderBonus table
    # This column is kept for backwards compatibility but should not be used
    bonus_status = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    odds = relationship("Odds", back_populates="provider")
    bets = relationship("Bet", back_populates="provider")


class Odds(Base):
    """
    Odds for an event outcome from a specific provider.
    
    Multiple providers can have odds for the same event.
    """
    __tablename__ = "odds"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    event_id = Column(String, ForeignKey("events.id"), nullable=False)
    provider_id = Column(String, ForeignKey("providers.id"), nullable=False)
    
    market = Column(String, nullable=False)     # "1x2", "moneyline"
    outcome = Column(String, nullable=False)    # "home", "away", "draw"
    odds = Column(Float, nullable=False)        # Decimal odds (e.g., 2.10)
    point = Column(Float, nullable=True)        # Reserved for future use
    
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Unique constraint: one odds per event/provider/market/outcome/point combo
    # Includes point to allow multiple lines per market (e.g., over 2.5 vs over 3.0)
    __table_args__ = (
        UniqueConstraint('event_id', 'provider_id', 'market', 'outcome', 'point', name='uq_odds_with_point'),
        # Performance index for common query patterns (arbitrage/value detection)
        Index('ix_odds_event_provider_outcome', 'event_id', 'provider_id', 'outcome'),
    )
    
    # Relationships
    event = relationship("Event", back_populates="odds")
    provider = relationship("Provider", back_populates="odds")


# ============ Bet Tracking ============

class Bet(Base):
    """
    A placed bet (manual entry).

    User enters bets manually, system auto-calculates profit/ROI.
    Extended with behavioral tracking for risk management.
    """
    __tablename__ = "bets"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Profile association (for per-profile bet isolation)
    profile_id = Column(Integer, ForeignKey("profiles.id"), nullable=True)

    # What you bet on
    event_id = Column(String, ForeignKey("events.id"))
    provider_id = Column(String, ForeignKey("providers.id"), nullable=False)
    market = Column(String)                     # "1x2"
    outcome = Column(String)                    # "home"
    odds = Column(Float, nullable=False)        # 2.10

    # Stake
    stake = Column(Float, nullable=False)       # 100.00

    # Bonus tracking
    is_bonus = Column(Boolean, default=False)
    bonus_type = Column(String)                 # "free_bet", "deposit_match", "risk_free"

    # Result (updated when settled)
    result = Column(String, default="pending")  # "pending", "won", "lost", "void"
    payout = Column(Float, default=0.0)         # What you got back

    # Timestamps
    placed_at = Column(DateTime, default=datetime.utcnow)
    settled_at = Column(DateTime)

    # === BEHAVIORAL TRACKING (for risk management) ===
    # Timing patterns
    hour_of_day = Column(Integer, nullable=True)      # 0-23
    day_of_week = Column(Integer, nullable=True)      # 0=Monday, 6=Sunday

    # Stake patterns
    stake_rounded = Column(Boolean, nullable=True)    # Was stake a round number?
    stake_noise_applied = Column(Float, nullable=True)  # Noise amount added

    # Risk metrics at bet time
    risk_score_at_bet = Column(Float, nullable=True)  # Provider risk score (0-1)
    utility_score = Column(Float, nullable=True)      # EV - λ*RiskPenalty
    selection_probability = Column(Float, nullable=True)  # Softmax selection prob

    # CLV tracking (filled post-event)
    closing_odds = Column(Float, nullable=True)       # Odds at event start
    clv_pct = Column(Float, nullable=True)            # Closing line value %

    # EV tracking for mug betting analysis
    ev_at_placement = Column(Float, nullable=True)    # EV score when bet was placed

    # Mug betting fields
    is_mug_bet = Column(Boolean, default=False)       # True if intentional -EV bet for account health
    mug_bet_reason = Column(String, nullable=True)    # "warmup", "ratio_balance", "ongoing"

    # Relationships
    event = relationship("Event", back_populates="bets")
    provider = relationship("Provider", back_populates="bets")
    profile = relationship("Profile", back_populates="bets")

    @property
    def profit(self) -> float:
        """Net profit/loss from this bet."""
        if self.result == "won":
            return self.payout - self.stake
        elif self.result == "lost":
            # Free bets don't lose stake
            return 0.0 if self.is_bonus else -self.stake
        return 0.0

    @property
    def roi_pct(self) -> float:
        """Return on investment percentage."""
        if self.stake == 0:
            return 0.0
        return (self.profit / self.stake) * 100


# ============ User Settings ============

class Profile(Base):
    """User settings for stake calculation and filtering."""
    __tablename__ = "profiles"

    id = Column(Integer, primary_key=True)
    name = Column(String, default="default", unique=True)

    # Bankroll for this profile
    bankroll = Column(Float, default=1000.0)
    currency = Column(String, default="USD")

    # Kelly criterion
    kelly_fraction = Column(Float, default=0.25)    # Quarter Kelly

    # Opportunity thresholds
    min_edge_pct = Column(Float, default=2.0)       # Min edge for value bets
    min_arb_pct = Column(Float, default=0.5)        # Min profit for arbs

    # Risk limits
    max_stake_pct = Column(Float, default=5.0)      # Max % of bankroll per bet

    # Bonus settings
    min_retention_pct = Column(Float, default=80.0)  # Min % for free bet value
    preferred_counterparts = Column(String)          # JSON list: ["bet365", "betsson"]
    bonus_enabled = Column(Boolean, default=True)
    double_deposit = Column(Float, default=0.0)      # Max deposit match (0 = none)

    # Profile state
    is_active = Column(Boolean, default=False)      # Currently selected profile

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

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
    - wagered_amount: Amount wagered so far (only bets with odds >= 1.80 count)
    - When wagered_amount >= wagering_requirement: bonus is "completed"
    """
    __tablename__ = "profile_provider_bonuses"

    id = Column(Integer, primary_key=True)
    profile_id = Column(Integer, ForeignKey("profiles.id"), nullable=False)
    provider_id = Column(String, ForeignKey("providers.id"), nullable=False)

    # 'available' = bonus ready to use
    # 'in_progress' = deposited with double deposit, needs wagering
    # 'completed' = bonus fully wagered, no more min odds restriction
    bonus_status = Column(String, default="available")

    # Bonus wagering tracking
    bonus_amount = Column(Float, default=0.0)           # Bonus received
    wagering_multiplier = Column(Float, default=10.0)   # Wagering requirement multiplier (default 10x)
    wagering_requirement = Column(Float, default=0.0)   # Total wagering required (bonus_amount * multiplier)
    wagered_amount = Column(Float, default=0.0)         # Amount wagered so far (odds >= 1.80 only)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('profile_id', 'provider_id', name='uq_profile_provider_bonus'),
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

    # Manual account opened date for pre-existing accounts
    # Used for dormant account handling - accounts opened before +EV betting
    account_opened_at = Column(DateTime, nullable=True)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('profile_id', 'provider_id', name='uq_profile_provider_balance'),
    )

    # Relationships
    profile = relationship("Profile", back_populates="provider_balances")
    provider = relationship("Provider")


# ============ Opportunities ============

class Opportunity(Base):
    """
    Detected opportunities (arbitrage, value bets, bonus matches).

    Stores snapshots of opportunities at time of detection.
    Can be marked inactive when odds change.
    """
    __tablename__ = "opportunities"

    id = Column(Integer, primary_key=True)
    type = Column(String, nullable=False)  # "arbitrage", "value", "bonus"

    # Event reference
    event_id = Column(String, ForeignKey("events.id"))
    market = Column(String)

    # Legacy provider details (kept for backwards compat)
    provider1_id = Column(String, ForeignKey("providers.id"))
    provider2_id = Column(String, ForeignKey("providers.id"), nullable=True)

    # Legacy odds at detection
    odds1 = Column(Float)
    odds2 = Column(Float, nullable=True)

    # Legacy outcomes
    outcome1 = Column(String)
    outcome2 = Column(String, nullable=True)

    # NEW: Flexible multi-outcome storage for 3-way arbs and detailed stakes
    # Format: [{"provider": "...", "outcome": "...", "odds": ..., "stake": ..., "return": ...}]
    outcomes = Column(JSON, nullable=True)

    # Reserved for future use
    point = Column(Float, nullable=True)

    # NEW: Recommended total stake for the opportunity
    total_stake = Column(Float, nullable=True)

    # Calculated metrics
    profit_pct = Column(Float, nullable=True)  # For arbitrage
    edge_pct = Column(Float, nullable=True)     # For value bets

    # Status
    is_active = Column(Boolean, default=True)
    detected_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)

    # Relationships
    event = relationship("Event")
    provider1 = relationship("Provider", foreign_keys=[provider1_id])
    provider2 = relationship("Provider", foreign_keys=[provider2_id])


# ============ Extraction Monitoring ============

class ExtractionRun(Base):
    """Historical extraction run tracking."""
    __tablename__ = "extraction_runs"

    id = Column(String, primary_key=True)  # UUID
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=True)
    duration_seconds = Column(Float, nullable=True)

    # Aggregates
    providers_attempted = Column(Integer, default=0)
    providers_succeeded = Column(Integer, default=0)
    providers_failed = Column(Integer, default=0)
    total_events = Column(Integer, default=0)
    total_odds = Column(Integer, default=0)
    polymarket_events = Column(Integer, default=0)

    # Metadata
    trigger = Column(String)  # 'manual', 'scheduled', 'api'
    config = Column(JSON)  # Snapshot of orchestrator config
    notes = Column(Text)

    # Relationships
    provider_metrics = relationship("ProviderRunMetrics", back_populates="extraction_run")
    sport_metrics = relationship("SportRunMetrics", back_populates="extraction_run")


class ProviderRunMetrics(Base):
    """Per-provider metrics for each extraction run."""
    __tablename__ = "provider_run_metrics"

    id = Column(Integer, primary_key=True)
    run_id = Column(String, ForeignKey("extraction_runs.id"))
    provider_id = Column(String, nullable=False)

    # Timing
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=True)
    duration_seconds = Column(Float, nullable=True)

    # Results
    events_processed = Column(Integer, default=0)
    events_new = Column(Integer, default=0)
    odds_processed = Column(Integer, default=0)
    odds_new = Column(Integer, default=0)
    sports_attempted = Column(Integer, default=0)
    sports_succeeded = Column(Integer, default=0)

    # Performance
    retries = Column(Integer, default=0)
    cache_hits = Column(Integer, default=0)
    avg_response_time = Column(Float, nullable=True)

    # Status
    status = Column(String)  # 'success', 'partial', 'failed', 'timeout'
    error_message = Column(Text)
    circuit_breaker_tripped = Column(Boolean, default=False)
    health_check_passed = Column(Boolean, default=True)

    # Relationships
    extraction_run = relationship("ExtractionRun", back_populates="provider_metrics")
    sport_errors = relationship("SportRunMetrics", back_populates="provider_metrics")


class SportRunMetrics(Base):
    """Per-sport metrics for troubleshooting."""
    __tablename__ = "sport_run_metrics"

    id = Column(Integer, primary_key=True)
    run_id = Column(String, ForeignKey("extraction_runs.id"))
    provider_run_id = Column(Integer, ForeignKey("provider_run_metrics.id"))
    provider_id = Column(String, nullable=False)
    sport = Column(String, nullable=False)

    # Results
    events_extracted = Column(Integer, default=0)
    odds_extracted = Column(Integer, default=0)
    duration_seconds = Column(Float, nullable=True)

    # Status
    success = Column(Boolean, default=False)
    error_type = Column(String)  # 'timeout', 'extraction_error', 'validation_error'
    error_message = Column(Text)

    # Relationships
    extraction_run = relationship("ExtractionRun", back_populates="sport_metrics")
    provider_metrics = relationship("ProviderRunMetrics", back_populates="sport_errors")


# ============ Risk Management ============

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
    stake_entropy = Column(Float, default=0.0)        # CV of stakes + round number ratio
    market_diversity = Column(Float, default=0.0)     # Sports/leagues spread
    timing_regularity = Column(Float, default=0.0)    # Hour/day concentration
    outcome_correlation = Column(Float, default=0.0)  # Hedge detection
    bonus_usage_ratio = Column(Float, default=0.0)    # Bonus bet percentage
    clv_score = Column(Float, default=0.0)            # Average closing line value
    win_rate_deviation = Column(Float, default=0.0)   # Actual vs expected

    # Brier score for calibration tracking (lower = better)
    brier_score = Column(Float, nullable=True)

    # Account warmup tracking for mug betting
    first_bet_date = Column(DateTime, nullable=True)  # Date of first bet on this provider
    total_bets_placed = Column(Integer, default=0)    # All-time bet count

    # New feature scores for mug betting detection
    ev_quality_ratio = Column(Float, default=0.0)       # % of +EV bets (high = suspicious)
    account_freshness_risk = Column(Float, default=0.0)  # New account risk (high = risky)

    # Cooldown tracking
    is_on_cooldown = Column(Boolean, default=False)
    cooldown_until = Column(DateTime, nullable=True)
    cooldown_reason = Column(String, nullable=True)

    # Metadata
    last_calculated_at = Column(DateTime, default=datetime.utcnow)
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
    lambda_coefficient = Column(Float, default=0.3)      # Risk aversion (0=ignore, 1=very conservative)
    stake_noise_pct = Column(Float, default=5.0)         # Max % noise on stakes
    softmax_temperature = Column(Float, default=1.0)     # Selection randomness (T=0 deterministic)

    # Feature weights (must sum to 1.0 for normalized scoring)
    weight_stake_entropy = Column(Float, default=0.12)
    weight_market_diversity = Column(Float, default=0.08)
    weight_timing_regularity = Column(Float, default=0.12)
    weight_outcome_correlation = Column(Float, default=0.15)
    weight_bonus_usage = Column(Float, default=0.12)
    weight_clv = Column(Float, default=0.13)
    weight_win_rate = Column(Float, default=0.08)
    weight_ev_quality = Column(Float, default=0.10)          # EV ratio contribution
    weight_account_freshness = Column(Float, default=0.10)   # Account age contribution

    # Account warmup thresholds
    warmup_days_threshold = Column(Integer, default=14)   # Days until account is "warmed up"
    warmup_bets_threshold = Column(Integer, default=20)   # Bets needed for warmup

    # Risk level thresholds
    threshold_low = Column(Float, default=0.3)           # < this = low risk
    threshold_medium = Column(Float, default=0.5)        # < this = medium risk
    threshold_high = Column(Float, default=0.7)          # < this = high risk
    # >= threshold_high = critical

    # Behavioral parameters
    rolling_window_days = Column(Integer, default=30)    # Feature calculation window
    cooldown_trigger_score = Column(Float, default=0.75) # Auto-cooldown threshold
    cooldown_duration_hours = Column(Integer, default=24)  # Default cooldown length

    # Mug bet configuration
    mug_bet_max_edge_pct = Column(Float, default=-1.0)      # Max edge (negative = -EV)
    mug_bet_min_edge_pct = Column(Float, default=-10.0)     # Min edge (too -EV is wasteful)
    mug_bet_min_implied_prob = Column(Float, default=0.60)  # Favorites only (odds < 1.67)
    mug_bet_stake_pct = Column(Float, default=1.5)          # % of bankroll per mug bet
    mug_bet_warmup_count = Column(Integer, default=7)       # Mug bets during warmup phase
    mug_bet_ongoing_ratio = Column(Integer, default=5)      # 1 mug per X value bets

    # Updated timestamp
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    profile = relationship("Profile")


# ============ Database Functions ============

# Singleton engine with connection pooling
_engine = None


def get_engine():
    """
    Get or create the singleton database engine.

    Uses connection pooling with:
    - pool_size=5: Keep 5 connections ready
    - pool_recycle=3600: Recycle connections after 1 hour
    - pool_pre_ping=True: Verify connections before use
    """
    global _engine
    if _engine is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{DB_PATH}",
            pool_size=5,
            pool_recycle=3600,
            pool_pre_ping=True,
            # SQLite-specific: enable WAL mode for better concurrency
            connect_args={"check_same_thread": False},
        )
        # Create tables on first engine creation
        Base.metadata.create_all(_engine)
    return _engine


def init_db() -> None:
    """Initialize database and create tables."""
    return get_engine()


# Session factory - created once, reused
_SessionFactory = None


def get_session_factory():
    """Get or create the session factory."""
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine())
    return _SessionFactory


def get_session():
    """
    Get a database session.

    Note: Caller is responsible for closing the session.
    For FastAPI, use the get_db() dependency from api/deps.py instead.
    """
    factory = get_session_factory()
    return factory()


# ============ Per-Profile Balance Helpers ============

def get_profile_balance(db, profile_id: int, provider_id: str) -> float:
    """Get balance for a specific profile and provider."""
    record = db.query(ProfileProviderBalance).filter(
        ProfileProviderBalance.profile_id == profile_id,
        ProfileProviderBalance.provider_id == provider_id
    ).first()
    return record.balance if record else 0.0


def set_profile_balance(db, profile_id: int, provider_id: str, balance: float) -> None:
    """Set balance for a specific profile and provider."""
    record = db.query(ProfileProviderBalance).filter(
        ProfileProviderBalance.profile_id == profile_id,
        ProfileProviderBalance.provider_id == provider_id
    ).first()

    if record:
        record.balance = balance
        record.updated_at = datetime.utcnow()
    else:
        record = ProfileProviderBalance(
            profile_id=profile_id,
            provider_id=provider_id,
            balance=balance
        )
        db.add(record)


def adjust_profile_balance(db, profile_id: int, provider_id: str, amount: float) -> float:
    """Adjust balance for a specific profile and provider. Returns new balance."""
    record = db.query(ProfileProviderBalance).filter(
        ProfileProviderBalance.profile_id == profile_id,
        ProfileProviderBalance.provider_id == provider_id
    ).first()

    if record:
        record.balance += amount
        record.updated_at = datetime.utcnow()
        return record.balance
    else:
        record = ProfileProviderBalance(
            profile_id=profile_id,
            provider_id=provider_id,
            balance=amount
        )
        db.add(record)
        return amount


def get_total_profile_bankroll(db, profile_id: int) -> float:
    """Get total bankroll for a profile (sum of all provider balances)."""
    records = db.query(ProfileProviderBalance).filter(
        ProfileProviderBalance.profile_id == profile_id
    ).all()
    return sum(r.balance for r in records)


def get_active_profile(db):
    """Get the currently active profile, creating default if none exists."""
    profile = db.query(Profile).filter(Profile.is_active == True).first()
    if not profile:
        profile = db.query(Profile).first()
        if profile:
            profile.is_active = True
            db.commit()
        else:
            profile = Profile(name="default", is_active=True)
            db.add(profile)
            db.commit()
    return profile


# ============ Bonus Wagering Helpers ============

# Minimum odds for bonus wagering (bets below this don't count)
BONUS_MIN_ODDS = 1.80


def get_bonus_status(db, profile_id: int, provider_id: str) -> dict:
    """
    Get bonus status and wagering progress for a provider.

    Returns:
        {
            "status": "available" | "in_progress" | "completed",
            "bonus_amount": float,
            "wagering_requirement": float,
            "wagered_amount": float,
            "progress_pct": float,
            "is_cleared": bool,  # True if no restriction on min odds
        }
    """
    record = db.query(ProfileProviderBonus).filter(
        ProfileProviderBonus.profile_id == profile_id,
        ProfileProviderBonus.provider_id == provider_id
    ).first()

    if not record:
        return {
            "status": "available",
            "bonus_amount": 0.0,
            "wagering_requirement": 0.0,
            "wagered_amount": 0.0,
            "progress_pct": 100.0,
            "is_cleared": True,
        }

    is_cleared = (
        record.bonus_status == "completed" or
        record.bonus_status == "available" or
        (record.wagering_requirement > 0 and record.wagered_amount >= record.wagering_requirement)
    )

    progress_pct = 0.0
    if record.wagering_requirement > 0:
        progress_pct = min(100.0, record.wagered_amount / record.wagering_requirement * 100)

    return {
        "status": record.bonus_status,
        "bonus_amount": record.bonus_amount,
        "wagering_requirement": record.wagering_requirement,
        "wagered_amount": record.wagered_amount,
        "progress_pct": progress_pct,
        "is_cleared": is_cleared,
    }


def record_wagering(db, profile_id: int, provider_id: str, stake: float, odds: float) -> dict:
    """
    Record a bet toward wagering requirement.

    Only bets with odds >= 1.80 count toward wagering.
    Automatically updates bonus_status to 'completed' when requirement is met.

    Returns:
        Updated bonus status dict
    """
    if odds < BONUS_MIN_ODDS:
        return get_bonus_status(db, profile_id, provider_id)

    record = db.query(ProfileProviderBonus).filter(
        ProfileProviderBonus.profile_id == profile_id,
        ProfileProviderBonus.provider_id == provider_id
    ).first()

    if not record or record.bonus_status != "in_progress":
        return get_bonus_status(db, profile_id, provider_id)

    # Update wagered amount
    record.wagered_amount = (record.wagered_amount or 0.0) + stake
    record.updated_at = datetime.utcnow()

    # Check if wagering requirement is met
    if record.wagering_requirement > 0 and record.wagered_amount >= record.wagering_requirement:
        record.bonus_status = "completed"

    return get_bonus_status(db, profile_id, provider_id)


def start_bonus_wagering(
    db,
    profile_id: int,
    provider_id: str,
    bonus_amount: float,
    wagering_multiplier: float = 10.0
) -> dict:
    """
    Start tracking bonus wagering for a provider.

    Args:
        db: Database session
        profile_id: Profile ID
        provider_id: Provider ID
        bonus_amount: Bonus received
        wagering_multiplier: Times bonus must be wagered (default 10x)

    Returns:
        Updated bonus status dict
    """
    record = db.query(ProfileProviderBonus).filter(
        ProfileProviderBonus.profile_id == profile_id,
        ProfileProviderBonus.provider_id == provider_id
    ).first()

    wagering_requirement = bonus_amount * wagering_multiplier

    if record:
        record.bonus_status = "in_progress"
        record.bonus_amount = bonus_amount
        record.wagering_multiplier = wagering_multiplier
        record.wagering_requirement = wagering_requirement
        record.wagered_amount = 0.0
        record.updated_at = datetime.utcnow()
    else:
        record = ProfileProviderBonus(
            profile_id=profile_id,
            provider_id=provider_id,
            bonus_status="in_progress",
            bonus_amount=bonus_amount,
            wagering_multiplier=wagering_multiplier,
            wagering_requirement=wagering_requirement,
            wagered_amount=0.0,
        )
        db.add(record)

    return get_bonus_status(db, profile_id, provider_id)


def copy_profile_balances(db, from_profile_id: int, to_profile_id: int) -> int:
    """Copy all balances from one profile to another. Returns count of balances copied."""
    source_balances = db.query(ProfileProviderBalance).filter(
        ProfileProviderBalance.profile_id == from_profile_id
    ).all()

    count = 0
    for source in source_balances:
        existing = db.query(ProfileProviderBalance).filter(
            ProfileProviderBalance.profile_id == to_profile_id,
            ProfileProviderBalance.provider_id == source.provider_id
        ).first()

        if not existing:
            new_balance = ProfileProviderBalance(
                profile_id=to_profile_id,
                provider_id=source.provider_id,
                balance=source.balance
            )
            db.add(new_balance)
            count += 1

    return count


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at: {DB_PATH}")
