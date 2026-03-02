"""
BankrollBBQ Database Models

SQLite schema for:
- Canonical events (provider-agnostic)
- Odds per provider
- Provider balances
- Manual bet tracking
- User profile settings
- Risk management profiles
"""

from datetime import datetime, timezone
from enum import Enum


def _utcnow():
    """Timezone-aware UTC now for column defaults."""
    return datetime.now(timezone.utc)

from sqlalchemy import (
    create_engine, event, Column, Integer, String, Float,
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
from ..paths import get_db_path
DB_PATH = get_db_path()

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

    # Live score tracking (populated from Pinnacle live data)
    home_score = Column(Integer, nullable=True)   # Current/final home score
    away_score = Column(Integer, nullable=True)   # Current/final away score
    match_status = Column(String, nullable=True)  # "prematch", "live", "finished"
    match_minute = Column(Integer, nullable=True)  # Current match minute
    match_period = Column(Integer, nullable=True)  # Period ID (1=1st half, 2=2nd half, etc.)
    stats_json = Column(Text, nullable=True)       # JSON blob: corners, cards, scoreByQuarter, etc.

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=_utcnow)

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

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=_utcnow)

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
    clob_token_id = Column(String, nullable=True)  # Polymarket CLOB token ID for order book
    provider_meta = Column(JSON, nullable=True)  # Provider-specific IDs for placement: {"event_id": "...", "betoffer_id": "...", "outcome_id": "..."}

    updated_at = Column(DateTime, default=_utcnow)
    
    # Unique constraint: one odds per event/provider/market/outcome/point combo
    # Includes point to allow multiple lines per market (e.g., over 2.5 vs over 3.0)
    __table_args__ = (
        UniqueConstraint('event_id', 'provider_id', 'market', 'outcome', 'point', name='uq_odds_with_point'),
        # Performance index for common query patterns (arbitrage/value detection)
        Index('ix_odds_event_provider_outcome', 'event_id', 'provider_id', 'outcome'),
        # Index for scanner queries: provider + market filtering
        Index('ix_odds_provider_market', 'provider_id', 'market'),
        # Index for staleness checks and batch operations
        Index('ix_odds_updated_at', 'updated_at'),
        # Index for event-level market grouping (scanner.group_odds)
        Index('ix_odds_event_market_outcome', 'event_id', 'market', 'outcome'),
        Index('ix_odds_event_market_point', 'event_id', 'market', 'point'),
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
    point = Column(Float, nullable=True)        # Spread/total line (e.g., -1.5, 2.5)

    # Stake
    stake = Column(Float, nullable=False)       # 100.00

    # Bonus tracking
    is_bonus = Column(Boolean, default=False)
    bonus_type = Column(String)                 # "free_bet", "deposit_match", "risk_free"

    # Result (updated when settled)
    result = Column(String, default="pending")  # "pending", "won", "lost", "void"
    payout = Column(Float, default=0.0)         # What you got back

    # Timestamps
    placed_at = Column(DateTime, default=_utcnow)
    settled_at = Column(DateTime)
    settlement_source = Column(String, nullable=True)  # "manual", "auto_tsdb"

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

    # Placement tracking (auto-filled by PlacementService)
    confirmation_id = Column(String, nullable=True)          # Provider's bet reference
    placement_status = Column(String, default="manual")      # "manual" | "submitted" | "confirmed" | "failed"
    actual_odds_at_placement = Column(Float, nullable=True)  # Odds when actually placed
    placement_latency_ms = Column(Float, nullable=True)      # Time from request to confirmation

    # Edge tracking (filled at placement)
    fair_odds_at_placement = Column(Float, nullable=True)  # De-vigged Pinnacle fair odds when bet placed

    # CLV tracking (filled post-event)
    closing_odds = Column(Float, nullable=True)       # Odds at event start
    clv_pct = Column(Float, nullable=True)            # Closing line value %

    __table_args__ = (
        Index('ix_bet_profile_result', 'profile_id', 'result'),
        Index('ix_bet_event_id', 'event_id'),
        Index('ix_bet_provider_id', 'provider_id'),
        Index('ix_bet_profile_provider_result', 'profile_id', 'provider_id', 'result'),
        Index('ix_bet_result_placed_at', 'result', 'placed_at'),
    )

    # Relationships
    event = relationship("Event", back_populates="bets")
    provider = relationship("Provider", back_populates="bets")
    profile = relationship("Profile", back_populates="bets")

    @property
    def profit(self) -> float:
        """Net profit/loss from this bet."""
        if self.result == "won":
            # Freebets: full payout is profit (stake was free money)
            return self.payout if self.is_bonus else self.payout - self.stake
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
    kelly_fraction = Column(Float, default=0.75)    # Dynamic Kelly scales 0.25-0.75 based on edge

    # Opportunity thresholds
    min_edge_pct = Column(Float, default=2.0)       # Min edge for value bets
    min_arb_pct = Column(Float, default=0.5)        # Min profit for arbs

    # Risk limits
    max_stake_pct = Column(Float, default=5.0)      # Max % of bankroll per bet

    # Bonus settings
    min_retention_pct = Column(Float, default=80.0)  # Min % for free bet value
    preferred_counterparts = Column(String)          # JSON list: ["bet365", "betsson"]
    bonus_enabled = Column(Boolean, default=True)
    bonus_deposit = Column(Float, default=0.0)       # Max deposit match (0 = none)

    # Profile state
    is_active = Column(Boolean, default=False)      # Currently selected profile
    chrome_port = Column(Integer, nullable=True)     # CDP port (default: 9221 + id)
    color = Column(String, nullable=True)            # Hex color for Chrome border (auto-assigned)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=_utcnow)

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
    bonus_type = Column(String, nullable=True)          # "freebet" or "bonusdeposit"

    # Bonus wagering tracking
    bonus_amount = Column(Float, default=0.0)           # Bonus received
    wagering_multiplier = Column(Float, default=10.0)   # Wagering requirement multiplier (default 10x)
    wagering_requirement = Column(Float, default=0.0)   # Total wagering required (bonus_amount * multiplier)
    wagered_amount = Column(Float, default=0.0)         # Amount wagered so far (odds >= min_odds only)
    min_odds = Column(Float, default=1.80)              # Minimum odds for wagering qualification (per-provider)

    # Timer tracking
    claimed_at = Column(DateTime, nullable=True)        # When bonus was claimed/wagering started
    expires_at = Column(DateTime, nullable=True)        # Deadline to complete wagering (claimed_at + 60 days)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint('profile_id', 'provider_id', name='uq_profile_provider_bonus'),
        Index('ix_bonus_profile_status', 'profile_id', 'bonus_status'),
        Index('ix_bonus_profile_provider', 'profile_id', 'provider_id'),
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

    # Polymarket wallet address (0x...) for API-based portfolio sync
    wallet_address = Column(String, nullable=True)

    # Manual account opened date for pre-existing accounts
    # Used for dormant account handling - accounts opened before +EV betting
    account_opened_at = Column(DateTime, nullable=True)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint('profile_id', 'provider_id', name='uq_profile_provider_balance'),
        Index('ix_balance_profile_id', 'profile_id'),
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
    One row per (event, market, outcome, provider) combination.
    """
    __tablename__ = "opportunities"
    __table_args__ = (
        Index("ix_opp_upsert", "event_id", "market", "outcome1", "provider1_id", "type"),
        Index("ix_opp_active_edge", "is_active", "edge_pct"),
        Index("ix_opp_type_active", "type", "is_active"),
        Index("ix_opp_provider1_type", "provider1_id", "type"),
    )

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
    detected_at = Column(DateTime, default=_utcnow)
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
    report = Column(Text)  # Human-readable extraction summary

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

    # Matching (soft providers: how many events matched Pinnacle)
    events_matched = Column(Integer, default=0)
    events_unmatched = Column(Integer, default=0)

    # Market breakdown (actionable: shows spread/total gaps)
    ml_count = Column(Integer, default=0)      # 1x2 + moneyline odds
    spread_count = Column(Integer, default=0)   # spread/handicap odds
    total_count = Column(Integer, default=0)    # over/under odds

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
    events_new = Column(Integer, default=0)
    odds_extracted = Column(Integer, default=0)
    odds_new = Column(Integer, default=0)
    duration_seconds = Column(Float, nullable=True)

    # Matching (how many events matched Pinnacle vs created new)
    events_matched = Column(Integer, default=0)
    events_unmatched = Column(Integer, default=0)

    # Market breakdown per sport (diagnose spread/total gaps)
    ml_count = Column(Integer, default=0)
    spread_count = Column(Integer, default=0)
    total_count = Column(Integer, default=0)

    # Status
    success = Column(Boolean, default=False)
    error_type = Column(String)  # 'timeout', 'extraction_error', 'validation_error'
    error_message = Column(Text)

    # Relationships
    extraction_run = relationship("ExtractionRun", back_populates="sport_metrics")
    provider_metrics = relationship("ProviderRunMetrics", back_populates="sport_errors")


# ============ Boost Extraction Logging ============

class BoostExtractionLog(Base):
    """Per-provider metrics for each oddsboost scrape run."""
    __tablename__ = "boost_extraction_logs"

    id = Column(Integer, primary_key=True)
    run_id = Column(String, nullable=False)        # Groups providers from same run
    scraped_at = Column(DateTime, nullable=False)
    provider_id = Column(String, nullable=False)
    scraper_type = Column(String)                   # kambi, altenar, gecko_v2, etc.
    status = Column(String, nullable=False)         # success, failed, skipped
    duration_seconds = Column(Float, default=0.0)
    boosts_found = Column(Integer, default=0)
    error_message = Column(Text)

    # Run-level totals (denormalized for easy querying — same for all rows in a run)
    run_total_boosts = Column(Integer, default=0)
    run_duration_seconds = Column(Float, default=0.0)


class SpecialOdds(Base):
    """Odds boosts / specials stored from provider scrapes with pre-computed EV."""
    __tablename__ = "specials"

    id = Column(Integer, primary_key=True)

    # Core boost data (from Special dataclass in scrape_specials.py)
    provider = Column(String, nullable=False)
    title = Column(String, nullable=False)        # "market_label: selection_label"
    description = Column(Text, default="")
    original_odds = Column(Float, nullable=True)   # Pre-boost odds (if available)
    boosted_odds = Column(Float, nullable=True)
    boost_pct = Column(Float, nullable=True)       # ((boosted / original) - 1) * 100
    max_stake = Column(Float, nullable=True)
    category = Column(String, default="boost")     # "boost" or "superboost"

    # Event context
    sport = Column(String, default="unknown")
    league = Column(String, default="")
    event = Column(String, default="")              # "Arsenal vs Sunderland"
    event_time = Column(String, nullable=True)      # ISO datetime string
    expires_at = Column(String, nullable=True)      # ISO datetime string

    # Source metadata
    url = Column(String, default="")
    source = Column(String, default="")
    market_label = Column(String, default="")
    shared_providers = Column(JSON, nullable=True)  # list of provider IDs

    # Scrape tracking
    scraped_at = Column(String, nullable=False)     # ISO datetime string

    # EV enrichment (computed at scrape time vs Pinnacle fair odds)
    edge_pct = Column(Float, nullable=True)
    fair_odds = Column(Float, nullable=True)
    ev_per_unit = Column(Float, nullable=True)
    is_positive_ev = Column(Boolean, nullable=True)
    matched_event_id = Column(String, nullable=True)
    matched_outcome = Column(String, nullable=True)  # "home", "away", "draw"
    matched_market = Column(String, nullable=True)   # "1x2", "moneyline"
    enrichment_method = Column(String, nullable=True)  # How fair_odds was determined

    __table_args__ = (
        Index("ix_specials_provider", "provider"),
        Index("ix_specials_sport", "sport"),
        Index("ix_specials_positive_ev", "is_positive_ev"),
        Index("ix_specials_scraped_at", "scraped_at"),
    )


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

    # Account tracking
    first_bet_date = Column(DateTime, nullable=True)  # Date of first bet on this provider
    total_bets_placed = Column(Integer, default=0)    # All-time bet count

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
    lambda_coefficient = Column(Float, default=0.3)      # Risk aversion (0=ignore, 1=very conservative)
    stake_noise_pct = Column(Float, default=5.0)         # Max % noise on stakes
    softmax_temperature = Column(Float, default=1.0)     # Selection randomness (T=0 deterministic)

    # Feature weights (must sum to 1.0 for normalized scoring)
    weight_stake_entropy = Column(Float, default=0.12)
    weight_market_diversity = Column(Float, default=0.08)
    weight_timing_regularity = Column(Float, default=0.12)
    weight_outcome_correlation = Column(Float, default=0.15)
    weight_bonus_usage = Column(Float, default=0.12)
    weight_clv = Column(Float, default=0.15)
    weight_win_rate = Column(Float, default=0.10)

    # Risk level thresholds
    threshold_low = Column(Float, default=0.3)           # < this = low risk
    threshold_medium = Column(Float, default=0.5)        # < this = medium risk
    threshold_high = Column(Float, default=0.7)          # < this = high risk
    # >= threshold_high = critical

    # Behavioral parameters
    rolling_window_days = Column(Integer, default=30)    # Feature calculation window
    cooldown_trigger_score = Column(Float, default=0.75) # Auto-cooldown threshold
    cooldown_duration_hours = Column(Integer, default=24)  # Default cooldown length

    # Updated timestamp
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=_utcnow)

    # Relationships
    profile = relationship("Profile")


# ============ Trading Models ============

class TradingAccount(Base):
    """Sub-account for trading (intraday, swing, hodl)."""
    __tablename__ = "trading_accounts"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    account_type = Column(String, nullable=False)  # "intraday", "swing", "hodl"

    # Balances
    balance = Column(Float, default=0.0)
    equity = Column(Float, default=0.0)
    realized_pnl = Column(Float, default=0.0)
    daily_pnl = Column(Float, default=0.0)
    weekly_pnl = Column(Float, default=0.0)

    # Risk policy
    risk_per_trade_pct = Column(Float, default=1.0)
    max_daily_loss_pct = Column(Float, default=3.0)
    max_weekly_loss_pct = Column(Float, default=7.0)
    max_trades_per_day = Column(Integer, default=5)
    stop_after_consecutive_losses = Column(Integer, default=3)

    # Daily counters (reset daily)
    trades_today = Column(Integer, default=0)
    consecutive_losses = Column(Integer, default=0)
    is_daily_locked = Column(Boolean, default=False)
    is_weekly_locked = Column(Boolean, default=False)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    trades = relationship("Trade", back_populates="account")


class DailyRoutine(Base):
    """One per trading day — checklist, bias, psych gate."""
    __tablename__ = "daily_routines"

    id = Column(Integer, primary_key=True)
    date = Column(String, nullable=False, unique=True)  # "2026-02-20"

    # Macro notes
    macro_notes = Column(JSON, nullable=True)  # {"calendar": "...", "dxy": "...", ...}

    # Session context
    overnight_high = Column(Float, nullable=True)
    overnight_low = Column(Float, nullable=True)
    key_levels = Column(JSON, nullable=True)  # [{"label": "POC", "price": 21500}, ...]
    prev_value_area = Column(JSON, nullable=True)  # {"vah": ..., "val": ..., "poc": ...}

    # Bias
    bias_text = Column(Text, nullable=True)
    bias_direction = Column(String, nullable=True)  # "bullish", "bearish", "neutral"
    bias_confidence = Column(Integer, nullable=True)  # 1-5

    # Psych gate
    sleep_score = Column(Integer, nullable=True)  # 1-10
    focus_score = Column(Integer, nullable=True)  # 1-10
    emotional_score = Column(Integer, nullable=True)  # 1-10
    psych_average = Column(Float, nullable=True)
    psych_override = Column(Text, nullable=True)  # Override reason text

    # Checklist completion tracking
    checklist_completion = Column(JSON, nullable=True)  # {"macro_0": true, "session_2": false, ...}
    is_complete = Column(Boolean, default=False)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    trades = relationship("Trade", back_populates="daily_routine")


class Trade(Base):
    """A single trade with full lifecycle tracking."""
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("trading_accounts.id"), nullable=False)
    daily_routine_id = Column(Integer, ForeignKey("daily_routines.id"), nullable=True)

    # Instrument & direction
    instrument = Column(String, nullable=False)  # "NQ", "ES", "MNQ"
    direction = Column(String, nullable=False)  # "long", "short"
    setup_type = Column(String, nullable=False)  # "trend_continuation", etc.

    # Levels
    entry_price = Column(Float, nullable=True)
    stop_price = Column(Float, nullable=True)
    be_price = Column(Float, nullable=True)  # Breakeven price after move-to-BE
    targets = Column(JSON, nullable=True)  # [{"price": 21600, "contracts": 1}, ...]

    # Position
    contracts = Column(Integer, default=1)
    risk_amount = Column(Float, nullable=True)  # Dollar risk
    rr_ratio = Column(Float, nullable=True)  # Reward/risk ratio
    r_multiple = Column(Float, nullable=True)  # Actual R earned (filled on close)

    # Confirmations checked
    confirmations = Column(JSON, nullable=True)  # {"confirmation_text": true/false, ...}

    # State machine
    state = Column(String, default="created")  # TRADE_STATES

    # Result
    realized_pnl = Column(Float, nullable=True)
    commission = Column(Float, default=0.0)
    notes = Column(Text, nullable=True)

    # Timestamps per state
    armed_at = Column(DateTime, nullable=True)
    triggered_at = Column(DateTime, nullable=True)
    opened_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    account = relationship("TradingAccount", back_populates="trades")
    daily_routine = relationship("DailyRoutine", back_populates="trades")
    events = relationship("TradeEvent", back_populates="trade", cascade="all, delete-orphan")
    review = relationship("TradeReview", back_populates="trade", uselist=False, cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_trades_account_state", "account_id", "state"),
        Index("ix_trades_setup", "setup_type"),
        Index("ix_trades_created", "created_at"),
    )


class TradeEvent(Base):
    """Timeline entry for a trade (state transitions, notes, partial exits)."""
    __tablename__ = "trade_events"

    id = Column(Integer, primary_key=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=False)

    event_type = Column(String, nullable=False)  # "transition", "partial_exit", "move_to_be", "trail_stop", "add_position", "note"
    from_state = Column(String, nullable=True)
    to_state = Column(String, nullable=True)
    details = Column(JSON, nullable=True)  # Flexible payload
    notes = Column(Text, nullable=True)

    timestamp = Column(DateTime, default=_utcnow)

    # Relationships
    trade = relationship("Trade", back_populates="events")


class TradeReview(Base):
    """Post-close journal review for a trade."""
    __tablename__ = "trade_reviews"

    id = Column(Integer, primary_key=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=False, unique=True)

    thesis_recap = Column(Text, nullable=True)
    followed_rules = Column(Boolean, nullable=True)
    what_to_improve = Column(Text, nullable=True)
    grade = Column(Integer, nullable=True)  # 1-5

    created_at = Column(DateTime, default=_utcnow)

    # Relationships
    trade = relationship("Trade", back_populates="review")


# ============ Recorder Models ============

class RecordingSession(Base):
    """A recorded browsing session on a bookmaker site."""
    __tablename__ = "recording_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)

    provider_id = Column(String, nullable=True)
    action_type = Column(String, nullable=False, default="general")
    label = Column(String, nullable=True)

    started_at = Column(DateTime, nullable=False, default=_utcnow)
    ended_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    action_count = Column(Integer, default=0)

    cdp_url = Column(String, nullable=True)
    status = Column(String, default="recording")
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=_utcnow)

    actions = relationship(
        "RecordedAction",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="RecordedAction.sequence",
    )

    __table_args__ = (
        Index("ix_recording_provider", "provider_id"),
        Index("ix_recording_status", "status"),
    )


class RecordedAction(Base):
    """A single recorded browser action within a session."""
    __tablename__ = "recorded_actions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("recording_sessions.id"), nullable=False)

    action_type = Column(String, nullable=False)
    timestamp = Column(DateTime, nullable=False, default=_utcnow)
    sequence = Column(Integer, nullable=False)

    url = Column(String, nullable=True)
    page_title = Column(String, nullable=True)
    provider_id = Column(String, nullable=True)

    css_selector = Column(String, nullable=True)
    xpath = Column(String, nullable=True)
    element_tag = Column(String, nullable=True)
    element_text = Column(String, nullable=True)
    element_id = Column(String, nullable=True)
    element_class = Column(String, nullable=True)

    x = Column(Float, nullable=True)
    y = Column(Float, nullable=True)
    viewport_width = Column(Integer, nullable=True)
    viewport_height = Column(Integer, nullable=True)

    input_value = Column(String, nullable=True)
    input_type = Column(String, nullable=True)

    request_method = Column(String, nullable=True)
    request_url = Column(String, nullable=True)
    response_status = Column(Integer, nullable=True)

    meta = Column(JSON, nullable=True)

    session = relationship("RecordingSession", back_populates="actions")

    __table_args__ = (
        Index("ix_action_session_seq", "session_id", "sequence"),
        Index("ix_action_type", "action_type"),
    )


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
            # SQLite-specific: allow multi-thread access + 30s busy timeout
            connect_args={
                "check_same_thread": False,
                "timeout": 30,  # Wait up to 30s for locks instead of default 5s
            },
        )

        # Enable WAL mode + busy timeout on every new connection
        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

        # Create tables on first engine creation
        Base.metadata.create_all(_engine)
        # Migrate existing tables (add new columns)
        _run_migrations(_engine)
    return _engine


def _run_migrations(engine):
    """Add new columns to existing tables (safe for fresh DBs too)."""
    import sqlite3
    with engine.connect() as conn:
        raw = conn.connection.connection  # Get raw sqlite3 connection
        cursor = raw.cursor()
        # Add min_odds to profile_provider_bonuses if missing
        try:
            cursor.execute("SELECT min_odds FROM profile_provider_bonuses LIMIT 1")
        except sqlite3.OperationalError:
            try:
                cursor.execute("ALTER TABLE profile_provider_bonuses ADD COLUMN min_odds FLOAT DEFAULT 1.80")
                raw.commit()
            except sqlite3.OperationalError:
                pass  # Column already exists or table doesn't exist

        # Add claimed_at to profile_provider_bonuses if missing
        try:
            cursor.execute("SELECT claimed_at FROM profile_provider_bonuses LIMIT 1")
        except sqlite3.OperationalError:
            try:
                cursor.execute("ALTER TABLE profile_provider_bonuses ADD COLUMN claimed_at DATETIME")
                raw.commit()
            except sqlite3.OperationalError:
                pass

        # Add expires_at to profile_provider_bonuses if missing
        try:
            cursor.execute("SELECT expires_at FROM profile_provider_bonuses LIMIT 1")
        except sqlite3.OperationalError:
            try:
                cursor.execute("ALTER TABLE profile_provider_bonuses ADD COLUMN expires_at DATETIME")
                raw.commit()
            except sqlite3.OperationalError:
                pass

        # Add index for per-provider opportunity upsert lookups
        try:
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS ix_opp_upsert "
                "ON opportunities (event_id, market, outcome1, provider1_id, type)"
            )
            raw.commit()
        except sqlite3.OperationalError:
            pass

        # Add bonus_type to profile_provider_bonuses if missing
        try:
            cursor.execute("SELECT bonus_type FROM profile_provider_bonuses LIMIT 1")
        except sqlite3.OperationalError:
            try:
                cursor.execute("ALTER TABLE profile_provider_bonuses ADD COLUMN bonus_type TEXT")
                raw.commit()
            except sqlite3.OperationalError:
                pass

        # Add clob_token_id to odds if missing (Polymarket CLOB order book)
        try:
            cursor.execute("SELECT clob_token_id FROM odds LIMIT 1")
        except sqlite3.OperationalError:
            try:
                cursor.execute("ALTER TABLE odds ADD COLUMN clob_token_id TEXT")
                raw.commit()
            except sqlite3.OperationalError:
                pass

        # Add provider_meta to odds if missing (provider-specific IDs for placement)
        try:
            cursor.execute("SELECT provider_meta FROM odds LIMIT 1")
        except sqlite3.OperationalError:
            try:
                cursor.execute("ALTER TABLE odds ADD COLUMN provider_meta JSON")
                raw.commit()
            except sqlite3.OperationalError:
                pass

        # Add placement columns to bets if missing
        for col, col_type, default in [
            ("confirmation_id", "TEXT", None),
            ("placement_status", "TEXT", "'manual'"),
            ("actual_odds_at_placement", "FLOAT", None),
            ("placement_latency_ms", "FLOAT", None),
        ]:
            try:
                cursor.execute(f"SELECT {col} FROM bets LIMIT 1")
            except sqlite3.OperationalError:
                try:
                    default_clause = f" DEFAULT {default}" if default else ""
                    cursor.execute(f"ALTER TABLE bets ADD COLUMN {col} {col_type}{default_clause}")
                    raw.commit()
                except sqlite3.OperationalError:
                    pass

        # Add point + settlement_source to bets (for auto-settlement)
        for col, col_type in [("point", "FLOAT"), ("settlement_source", "TEXT")]:
            try:
                cursor.execute(f"SELECT {col} FROM bets LIMIT 1")
            except sqlite3.OperationalError:
                try:
                    cursor.execute(f"ALTER TABLE bets ADD COLUMN {col} {col_type}")
                    raw.commit()
                except sqlite3.OperationalError:
                    pass

        # Add chrome_port to profiles (multi-profile CDP support)
        try:
            cursor.execute("SELECT chrome_port FROM profiles LIMIT 1")
        except sqlite3.OperationalError:
            try:
                cursor.execute("ALTER TABLE profiles ADD COLUMN chrome_port INTEGER")
                raw.commit()
            except sqlite3.OperationalError:
                pass

        # Add wallet_address to profile_provider_balances (Polymarket wallet sync)
        try:
            cursor.execute("SELECT wallet_address FROM profile_provider_balances LIMIT 1")
        except sqlite3.OperationalError:
            try:
                cursor.execute("ALTER TABLE profile_provider_balances ADD COLUMN wallet_address TEXT")
                raw.commit()
            except sqlite3.OperationalError:
                pass

        # Add live score/status fields to events (Pinnacle live data)
        for col, col_type in [
            ("home_score", "INTEGER"),
            ("away_score", "INTEGER"),
            ("match_status", "TEXT"),
            ("match_minute", "INTEGER"),
            ("match_period", "INTEGER"),
            ("stats_json", "TEXT"),
        ]:
            try:
                cursor.execute(f"SELECT {col} FROM events LIMIT 1")
            except sqlite3.OperationalError:
                try:
                    cursor.execute(f"ALTER TABLE events ADD COLUMN {col} {col_type}")
                    raw.commit()
                except sqlite3.OperationalError:
                    pass


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


# ============ Constants ============

# Minimum odds for bonus wagering (bets below this don't count)
BONUS_MIN_ODDS = 1.80


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at: {DB_PATH}")
