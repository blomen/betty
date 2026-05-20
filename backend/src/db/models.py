"""
Arnold Database Models

Database schema for:
- Canonical events (provider-agnostic)
- Odds per provider
- Provider balances
- Manual bet tracking
- User profile settings
- Risk management profiles

Supports PostgreSQL (via DATABASE_URL env var) and SQLite fallback.
"""

import logging
import os
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


def _utcnow():
    """Timezone-aware UTC now for column defaults."""
    return datetime.now(timezone.utc)


from sqlalchemy import (
    JSON,
    BigInteger,
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
    create_engine,
    event,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.pool import NullPool


class RiskLevel(str, Enum):
    """Risk level classification for providers."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class LimitRisk(str, Enum):
    """How aggressively a provider is known to limit winners."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    INSTANT = "instant"


class LimitType(str, Enum):
    """Type of limit imposed by a bookmaker."""

    STAKE_LIMITED = "stake_limited"
    MARKET_RESTRICTED = "market_restricted"
    ODDS_RESTRICTED = "odds_restricted"
    FULLY_BANNED = "fully_banned"


# Database file location (SQLite only — not used in Postgres mode)
from ..paths import get_db_path

try:
    DB_PATH = get_db_path()
except Exception:
    DB_PATH = None

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
    display_home = Column(String, nullable=True)  # Original cased name from provider
    display_away = Column(String, nullable=True)  # Original cased name from provider
    start_time = Column(DateTime)

    # Live score tracking (populated from Pinnacle live data)
    home_score = Column(Integer, nullable=True)  # Current/final home score
    away_score = Column(Integer, nullable=True)  # Current/final away score
    match_status = Column(String, nullable=True)  # "prematch", "live", "finished"
    match_minute = Column(Integer, nullable=True)  # Current match minute
    match_period = Column(Integer, nullable=True)  # Period ID (1=1st half, 2=2nd half, etc.)
    stats_json = Column(Text, nullable=True)  # JSON blob: corners, cards, scoreByQuarter, etc.

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        # Pipeline cache warming: filter by sport + upcoming start_time
        Index("ix_events_sport_start_time", "sport", "start_time"),
        # Finished event detection: filter by match_status
        Index("ix_events_match_status", "match_status"),
        # League-based queries (soft provider filtering, sports.yaml lookups)
        Index("ix_events_league", "league"),
    )

    # Relationships
    odds = relationship("Odds", back_populates="event", cascade="all, delete-orphan")
    bets = relationship("Bet", back_populates="event")


class Provider(Base):
    """
    A betting provider (bookmaker).

    Stores runtime state only - extraction logic lives in code.
    """

    __tablename__ = "providers"

    id = Column(String, primary_key=True)  # "unibet"
    name = Column(String, nullable=False)  # "Unibet"
    url = Column(String)  # "unibet.se"

    is_enabled = Column(Boolean, default=True)  # Can toggle off

    # Limit risk (global — how aggressively this provider limits winners)
    limit_risk = Column(String, default="low")  # LimitRisk enum value
    limit_notes = Column(Text, nullable=True)  # Free-form context

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

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

    market = Column(String, nullable=False)  # "1x2", "moneyline"
    outcome = Column(String, nullable=False)  # "home", "away", "draw"
    odds = Column(Float, nullable=False)  # Decimal odds (e.g., 2.10)
    point = Column(Float, nullable=True)  # Spread/total point value (e.g., -1.5, 2.5)
    clob_token_id = Column(String, nullable=True)  # Legacy — no longer populated
    provider_meta = Column(
        JSON, nullable=True
    )  # Provider-specific IDs: {"event_id": "...", "betoffer_id": "...", "outcome_id": "..."}
    bid = Column(Float, nullable=True)  # Best bid price (probability 0-1, CLOB only)
    ask = Column(Float, nullable=True)  # Best ask price (probability 0-1, CLOB only)
    depth_usd = Column(Float, nullable=True)  # Total ask-side depth in USD (CLOB only)

    updated_at = Column(DateTime, default=_utcnow)

    # Unique constraint: one odds per event/provider/market/outcome/point combo
    # Includes point to allow multiple lines per market (e.g., over 2.5 vs over 3.0)
    __table_args__ = (
        # NULLS NOT DISTINCT so (event_id, provider_id, market, outcome, NULL) is unique
        UniqueConstraint(
            "event_id",
            "provider_id",
            "market",
            "outcome",
            "point",
            name="uq_odds_with_point_nd",
            postgresql_nulls_not_distinct=True,
        ),
        # Performance index for common query patterns (arbitrage/value detection)
        Index("ix_odds_event_provider_outcome", "event_id", "provider_id", "outcome"),
        # Index for scanner queries: provider + market filtering
        Index("ix_odds_provider_market", "provider_id", "market"),
        # Index for staleness checks and batch operations
        Index("ix_odds_updated_at", "updated_at"),
        # Index for event-level market grouping (scanner.group_odds)
        Index("ix_odds_event_market_outcome", "event_id", "market", "outcome"),
        Index("ix_odds_event_market_point", "event_id", "market", "point"),
        # Composite key for OddsBatchProcessor flush lookups
        Index("ix_odds_composite_key", "event_id", "provider_id", "market", "outcome", "point"),
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
    market = Column(String)  # "1x2"
    outcome = Column(String)  # "home"
    odds = Column(Float, nullable=False)  # 2.10
    point = Column(Float, nullable=True)  # Spread/total line (e.g., -1.5, 2.5)
    bet_type = Column(String, nullable=True)  # "value", "arb", "reverse", "polymarket", "boost", "mirror"

    # Stake (in native currency: SEK for Swedish providers, USD for Polymarket)
    stake = Column(Float, nullable=False)  # 100.00
    currency = Column(String, default="SEK")  # "SEK" or "USD" — determines stake/payout units

    # Bonus tracking
    is_bonus = Column(Boolean, default=False)
    bonus_type = Column(String)  # "free_bet", "deposit_match", "risk_free"

    # Result (updated when settled)
    result = Column(String, default="pending")  # "pending", "won", "lost", "void"
    payout = Column(Float, default=0.0)  # What you got back

    # Timestamps
    placed_at = Column(DateTime, default=_utcnow)
    settled_at = Column(DateTime)
    settlement_source = Column(String, nullable=True)  # "manual", "auto_tsdb"
    start_time = Column(DateTime, nullable=True)  # Event start time (persisted at placement)

    # === BEHAVIORAL TRACKING (for risk management) ===
    # Timing patterns
    hour_of_day = Column(Integer, nullable=True)  # 0-23
    day_of_week = Column(Integer, nullable=True)  # 0=Monday, 6=Sunday

    # Stake patterns
    stake_rounded = Column(Boolean, nullable=True)  # Was stake a round number?
    stake_noise_applied = Column(Float, nullable=True)  # Noise amount added

    # Risk metrics at bet time
    risk_score_at_bet = Column(Float, nullable=True)  # Provider risk score (0-1)
    utility_score = Column(Float, nullable=True)  # EV - λ*RiskPenalty
    selection_probability = Column(Float, nullable=True)  # Softmax selection prob

    # Bet confirmation
    confirmation_id = Column(String, nullable=True)  # Provider's bet reference
    # provider_bet_id: the provider/coupon/order ID returned by the provider's
    # placement response (Pinnacle betId, Kambi couponId, Polymarket clob order
    # hash, etc.). Used by reconcile/sync_history for exact-ID settlement matching.
    # Distinct from confirmation_id (legacy field, polymarket-specific event_slug).
    # Without this column the BetCreate schema field would crash bet_repo.create
    # with TypeError — schema accepts it, repo unpacks **kwargs into Bet().
    provider_bet_id = Column(String, nullable=True, index=True)
    # arb_group_id: shared id across the two+ legs of one arbitrage position
    # (soft-book anchor + Polymarket/Kalshi counter). NULL until the
    # arb_correlation pass pairs the legs. See 2026-05-20 dedup+linkage spec.
    arb_group_id = Column(String, nullable=True, index=True)
    placement_status = Column(String, default="manual")  # Legacy — always "manual"
    actual_odds_at_placement = Column(Float, nullable=True)  # Odds user confirmed at placement
    placement_latency_ms = Column(Float, nullable=True)  # Legacy — no longer populated

    # Edge tracking (filled at bet entry)
    fair_odds_at_placement = Column(Float, nullable=True)  # De-vigged Pinnacle fair odds when bet placed

    # Boost metadata (filled at placement for boost bets)
    boost_event = Column(String, nullable=True)  # "Arsenal vs Sunderland" — event name at placement
    boost_title = Column(String, nullable=True)  # LLM-simplified English title at placement

    # CLV tracking (filled post-event)
    closing_odds = Column(Float, nullable=True)  # Odds at event start
    clv_pct = Column(Float, nullable=True)  # Closing line value %

    # Provider-specific CLV (e.g., Polymarket closing price — true same-market CLV)
    provider_closing_odds = Column(Float, nullable=True)  # Same-provider odds at event start
    provider_clv_pct = Column(Float, nullable=True)  # (bet.odds / provider_closing_odds - 1) * 100

    __table_args__ = (
        Index("ix_bet_profile_result", "profile_id", "result"),
        Index("ix_bet_event_id", "event_id"),
        Index("ix_bet_provider_id", "provider_id"),
        Index("ix_bet_profile_provider_result", "profile_id", "provider_id", "result"),
        Index("ix_bet_result_placed_at", "result", "placed_at"),
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


class BetTrace(Base):
    """Raw API trace from intercepted bet placement. Append-only."""

    __tablename__ = "bet_traces"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False)
    provider_id = Column(String, nullable=False)
    request_url = Column(String, nullable=False)
    request_body = Column(String, nullable=True)  # JSON string
    response_body = Column(String, nullable=True)  # JSON string
    bet_id = Column(Integer, ForeignKey("bets.id"), nullable=True)
    provider_bet_id = Column(String, nullable=True, index=True)
    parse_status = Column(String, nullable=False)  # "ok", "failed", "unmatched", "rejected"

    bet = relationship("Bet", backref="traces")


class BetPostmortem(Base):
    """Post-settlement classification for a bet. One row per settled bet."""

    __tablename__ = "bet_postmortems"

    bet_id = Column(Integer, ForeignKey("bets.id"), primary_key=True)
    classification = Column(
        String, nullable=False
    )  # expected_loss, edge_erosion, false_edge, sizing_error, expected_win, bonus_win
    edge_at_placement = Column(Float, nullable=True)  # Derived: (odds / fair_odds_at_placement - 1) * 100
    clv_pct = Column(Float, nullable=True)  # Copied from bet.clv_pct
    clv_confirmed = Column(Boolean, default=False)  # True if (start_time - placed_at) <= 12h
    expected_win_pct = Column(Float, nullable=True)  # 1 / fair_odds_at_placement
    kelly_fraction = Column(Float, nullable=True)  # actual_stake / kelly_optimal_stake
    is_oversized = Column(Boolean, default=False)  # kelly_fraction > 1.5
    is_undersized = Column(Boolean, default=False)  # kelly_fraction < 0.5
    variance_score = Column(Float, nullable=True)  # win: 1 - expected_win_pct, loss: expected_win_pct
    computed_at = Column(DateTime, default=_utcnow)
    version = Column(Integer, default=1)

    bet = relationship("Bet")

    __table_args__ = (Index("ix_bet_pm_classification_version", "classification", "version"),)


# ============ Mirror Infrastructure ============


class BalanceLog(Base):
    """Append-only balance log from intercepted provider API responses."""

    __tablename__ = "balance_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_id = Column(String, ForeignKey("providers.id"), nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String(3), nullable=False, default="SEK")
    source = Column(String, nullable=False)  # 'intercepted' | 'api_fetch'
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (Index("ix_balance_log_provider_created", "provider_id", "created_at"),)


class MirrorProviderState(Base):
    """Authoritative per-provider state from the local mirror.

    Phase 2 of the platform rebuild (2026-05-08). Local mirror writes on
    every login/balance/tab change so the frontend reads from the DB
    instead of trying to reconstruct state from in-memory + ephemeral SSE
    + React state. Survives `arnold.bat` restart, browser hard-refresh,
    SSH tunnel wedges. Replaces the brittle state-seeding effects.
    """

    __tablename__ = "mirror_provider_state"

    provider_id = Column(String, primary_key=True)
    logged_in = Column(Boolean, default=False, nullable=False)
    balance = Column(Float, nullable=True)
    balance_currency = Column(String(8), nullable=True)
    tab_url = Column(String, nullable=True)
    tab_open = Column(Boolean, default=False, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class MirrorRunnerState(Base):
    """Per-provider runner state (login_waiting / settling / ready_to_run / etc).

    See MirrorProviderState — same Phase 2 motivation. Mirror writes on
    every state transition so the frontend's card state derives from the
    DB rather than racing against SSE.
    """

    __tablename__ = "mirror_runner_state"

    provider_id = Column(String, primary_key=True)
    state = Column(String, nullable=True)
    mode = Column(String, nullable=True)  # 'arb' | 'value'
    current_arb_group_id = Column(String, nullable=True)
    current_opp_id = Column(Integer, nullable=True)
    last_idle_reason = Column(String, nullable=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class MirrorEventLog(Base):
    """Append-only log of every mirror SSE event for replay + debugging.

    Frontend can fetch events since a timestamp on reconnect to fill the
    gap that the ephemeral SSE broadcaster misses. Operator can also
    grep this log post-mortem to reconstruct what happened in any
    session.
    """

    __tablename__ = "mirror_event_log"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    provider_id = Column(String, nullable=True, index=True)
    event_type = Column(String, nullable=False, index=True)
    data = Column(JSON, nullable=True)
    ts = Column(DateTime, default=_utcnow, nullable=False, index=True)

    __table_args__ = (Index("ix_mirror_event_log_pid_ts", "provider_id", "ts"),)


class MirrorProviderHealth(Base):
    """Per-provider health snapshot — Phase 4 of the platform rebuild (2026-05-08).

    Derived periodically from `mirror_event_log` + reachability probes against
    the provider's home_url. Each row is the latest snapshot for one provider;
    the daily smoke-test cron rewrites it. The frontend §9 capability matrix
    reads from this table (replaces the static markdown that "lied" — i.e.
    showed ✅ for capabilities that broke without anyone noticing).

    Status fields are nullable strings ('green' | 'amber' | 'red' | None) so
    the UI can render mixed states. last_* timestamps come from event_log
    aggregation.
    """

    __tablename__ = "mirror_provider_health"

    provider_id = Column(String, primary_key=True)
    home_url_status = Column(String, nullable=True)  # 'green' | 'amber' | 'red'
    home_url_http_code = Column(Integer, nullable=True)
    last_login_detected_at = Column(DateTime, nullable=True)
    last_balance_intercept_at = Column(DateTime, nullable=True)
    last_placement_at = Column(DateTime, nullable=True)
    last_settled_at = Column(DateTime, nullable=True)
    last_provider_skipped_at = Column(DateTime, nullable=True)
    last_provider_skipped_reason = Column(String, nullable=True)
    overall = Column(String, nullable=True)  # rolled-up 'green' | 'amber' | 'red'
    notes = Column(String, nullable=True)
    checked_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class SettlementQueue(Base):
    """Persistent settlement queue — survives restarts, user confirms before bankroll update."""

    __tablename__ = "settlement_queue"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_id = Column(String, ForeignKey("providers.id"), nullable=False)
    bet_id = Column(Integer, ForeignKey("bets.id"), nullable=True)
    result = Column(String, nullable=False)  # 'won' | 'lost' | 'void'
    payout = Column(Float, nullable=False, default=0.0)
    status = Column(String, nullable=False, default="pending")  # 'pending' | 'confirmed'
    detected_at = Column(DateTime, default=_utcnow)
    confirmed_at = Column(DateTime, nullable=True)

    __table_args__ = (Index("ix_settlement_queue_provider_status", "provider_id", "status"),)


class PriceCache(Base):
    """Live price ticks from intercepted odds responses."""

    __tablename__ = "price_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_id = Column(String, ForeignKey("providers.id"), nullable=False)
    event_id = Column(String, ForeignKey("events.id"), nullable=False)
    market = Column(String, nullable=False)
    outcome = Column(String, nullable=False)
    odds = Column(Float, nullable=False)
    source = Column(String, nullable=False)  # 'intercepted' | 'dom' | 'api'
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("provider_id", "event_id", "market", "outcome", name="uq_price_cache_key"),
        Index("ix_price_cache_provider_event", "provider_id", "event_id"),
    )


# ============ User Settings ============


class Profile(Base):
    """User settings for stake calculation and filtering."""

    __tablename__ = "profiles"

    id = Column(Integer, primary_key=True)
    name = Column(String, default="default", unique=True)

    # Bankroll for this profile
    bankroll = Column(Float, default=1000.0)
    currency = Column(String, default="USD")
    liquid_balance = Column(Float, default=0.0)  # Cash in bank (not at any provider)

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
    # Forward-compat for multi-profile trading: each profile may own one
    # TopstepX account. Trading stats / bankroll filter on this so two
    # profiles can each have their own broker without leaking trades.
    # NULL means "not bound to a TopstepX account" — typical for
    # sports-only profiles.
    topstepx_account_id = Column(Integer, nullable=True)

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
        Index("ix_opp_upsert_unique", "event_id", "market", "outcome1", "provider1_id", "type", unique=True),
        Index("ix_opp_active_edge", "is_active", "edge_pct"),
        Index("ix_opp_type_active", "type", "is_active"),
        Index("ix_opp_provider1_type", "provider1_id", "type"),
        Index("ix_opp_active_type_edge_provider", "is_active", "type", "edge_pct", "provider1_id"),
        # Health endpoint volume-drop query filters by detected_at
        Index("ix_opp_detected_at", "detected_at"),
    )

    id = Column(Integer, primary_key=True)
    type = Column(String, nullable=False)  # "value", "arb", "reverse", "reverse_value", "bonus"

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
    edge_pct = Column(Float, nullable=True)  # For value bets

    # Status
    is_active = Column(Boolean, default=True)
    detected_at = Column(DateTime, default=_utcnow)
    expires_at = Column(DateTime, nullable=True)

    # ML feature columns (populated at scan time, used for training)
    prob_sum = Column(Float, nullable=True)
    odds_ratio = Column(Float, nullable=True)
    odds_age_minutes = Column(Float, nullable=True)
    sharp_age_minutes = Column(Float, nullable=True)
    time_to_start_minutes = Column(Float, nullable=True)
    provider_count = Column(Integer, nullable=True)
    provider_odds_rank = Column(Integer, nullable=True)
    market_consensus_spread = Column(Float, nullable=True)
    pinnacle_overround = Column(Float, nullable=True)
    closing_line_value = Column(Float, nullable=True)

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
    ml_count = Column(Integer, default=0)  # 1x2 + moneyline odds
    spread_count = Column(Integer, default=0)  # spread/handicap odds
    total_count = Column(Integer, default=0)  # over/under odds

    # Performance
    retries = Column(Integer, default=0)
    cache_hits = Column(Integer, default=0)
    avg_response_time = Column(Float, nullable=True)

    # Status
    status = Column(String)  # 'success', 'partial', 'failed', 'timeout'
    error_message = Column(Text)
    circuit_breaker_tripped = Column(Boolean, default=False)
    health_check_passed = Column(Boolean, default=True)

    __table_args__ = (
        # Health endpoint: recent runs per provider sorted by start_time DESC
        Index("ix_prm_provider_start", "provider_id", "start_time"),
        Index("ix_prm_start_time", "start_time"),
    )

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


# ============ Deferred Matching ============


class DeferredEvent(Base):
    """Buffer for soft provider events that couldn't match Pinnacle on first attempt."""

    __tablename__ = "deferred_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_id = Column(Text, nullable=False)
    sport = Column(Text, nullable=False)
    league = Column(Text)
    home_team = Column(Text, nullable=False)
    away_team = Column(Text, nullable=False)
    normalized_home = Column(Text, nullable=False)
    normalized_away = Column(Text, nullable=False)
    start_time = Column(DateTime, nullable=False)
    markets_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    attempt_count = Column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint(
            "provider_id",
            "sport",
            "normalized_home",
            "normalized_away",
            "start_time",
            name="uq_deferred_provider_event",
        ),
        Index("idx_deferred_start", "start_time"),
        Index("idx_deferred_sport", "sport"),
    )

    def to_standard_event(self):
        """Reconstruct StandardEvent from deferred data. Sets _from_deferred to prevent re-deferral."""
        import json

        from src.core.retriever import StandardEvent

        event = StandardEvent(
            id="",
            name=f"{self.home_team} vs {self.away_team}",
            sport=self.sport,
            markets=json.loads(self.markets_json),
            provider=self.provider_id,
            start_time=self.start_time.isoformat() if self.start_time else "",
            home_team=self.home_team,
            away_team=self.away_team,
            league=self.league or "",
        )
        event._from_deferred = True
        return event


# ============ Boost Extraction Logging ============


class BoostExtractionLog(Base):
    """Per-provider metrics for each oddsboost scrape run."""

    __tablename__ = "boost_extraction_logs"

    id = Column(Integer, primary_key=True)
    run_id = Column(String, nullable=False)  # Groups providers from same run
    scraped_at = Column(DateTime, nullable=False)
    provider_id = Column(String, nullable=False)
    scraper_type = Column(String)  # kambi, altenar, gecko_v2, etc.
    status = Column(String, nullable=False)  # success, failed, skipped
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
    title = Column(String, nullable=False)  # "market_label: selection_label"
    description = Column(Text, default="")
    original_odds = Column(Float, nullable=True)  # Pre-boost odds (if available)
    boosted_odds = Column(Float, nullable=True)
    boost_pct = Column(Float, nullable=True)  # ((boosted / original) - 1) * 100
    max_stake = Column(Float, nullable=True)
    category = Column(String, default="boost")  # "boost" or "superboost"

    # Event context
    sport = Column(String, default="unknown")
    league = Column(String, default="")
    event = Column(String, default="")  # "Arsenal vs Sunderland"
    event_time = Column(String, nullable=True)  # ISO datetime string
    expires_at = Column(String, nullable=True)  # ISO datetime string

    # Source metadata
    url = Column(String, default="")
    source = Column(String, default="")
    market_label = Column(String, default="")
    shared_providers = Column(JSON, nullable=True)  # list of provider IDs

    # Scrape tracking
    scraped_at = Column(String, nullable=False)  # ISO datetime string

    # Boost edge (simple: boosted_odds / original_odds - 1)
    edge_pct = Column(Float, nullable=True)
    is_positive_ev = Column(Boolean, nullable=True)

    # Legacy columns (kept for schema compat, no longer populated)
    fair_odds = Column(Float, nullable=True)
    ev_per_unit = Column(Float, nullable=True)
    matched_event_id = Column(String, nullable=True)
    matched_outcome = Column(String, nullable=True)
    matched_market = Column(String, nullable=True)
    enrichment_method = Column(String, nullable=True)

    # LLM enrichment (AI-estimated probability from Claude Haiku + Brave Search)
    llm_title = Column(String, nullable=True)  # Simplified English title
    llm_probability = Column(Float, nullable=True)  # 0.01-0.99
    llm_fair_odds = Column(Float, nullable=True)  # 1 / llm_probability
    llm_edge_pct = Column(Float, nullable=True)  # (boosted / llm_fair - 1) * 100
    llm_reasoning = Column(Text, nullable=True)  # AI reasoning text
    llm_confidence = Column(String, nullable=True)  # "low", "medium", "high"

    __table_args__ = (
        Index("ix_specials_provider", "provider"),
        Index("ix_specials_sport", "sport"),
        Index("ix_specials_positive_ev", "is_positive_ev"),
        Index("ix_specials_scraped_at", "scraped_at"),
    )


class LlmBoostCache(Base):
    """Persistent cache for LLM research results on odds boosts.

    Keyed by cache_key = md5(title.lower() + "|" + boosted_odds).
    Survives backend restarts and specials table purges.
    Once a boost is researched, it's never re-researched (until expired).
    """

    __tablename__ = "llm_boost_cache"

    cache_key = Column(String, primary_key=True)  # md5 hash

    # Original boost identity (for debugging / human lookup)
    title = Column(String, nullable=False)
    boosted_odds = Column(Float, nullable=False)

    # LLM research results
    llm_title = Column(String, nullable=True)
    llm_probability = Column(Float, nullable=False)
    llm_fair_odds = Column(Float, nullable=True)
    llm_confidence = Column(String, default="low")
    llm_reasoning = Column(Text, nullable=True)
    llm_event_time = Column(String, nullable=True)  # ISO datetime — event start time from LLM

    # Metadata
    created_at = Column(String, nullable=False)  # ISO datetime
    last_used_at = Column(String, nullable=False)  # ISO datetime — updated on carry-forward


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

    event_type = Column(
        String, nullable=False
    )  # "transition", "partial_exit", "move_to_be", "trail_stop", "add_position", "note"
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


class TradePostmortem(Base):
    """Post-close classification for a trade. One row per closed trade."""

    __tablename__ = "trade_postmortems"

    trade_id = Column(Integer, ForeignKey("trades.id"), primary_key=True)
    classification = Column(
        String, nullable=False
    )  # expected_loss, stop_too_wide, thesis_invalid, expected_win, runner
    r_multiple = Column(Float, nullable=True)
    setup_avg_r = Column(Float, nullable=True)
    setup_win_rate = Column(Float, nullable=True)
    stop_quality = Column(String, nullable=True)  # optimal, too_wide
    target_quality = Column(String, nullable=True)  # hit_target, partial_exit_good, missed_runner, exited_early
    streak_position = Column(Integer, nullable=True)  # negative = losing streak
    routine_psych_avg = Column(Float, nullable=True)
    rules_followed = Column(Boolean, nullable=True)
    computed_at = Column(DateTime, default=_utcnow)
    version = Column(Integer, default=1)

    trade = relationship("Trade")

    __table_args__ = (Index("ix_trade_pm_classification_version", "classification", "version"),)


class MarketSession(Base):
    """Computed AMT session data for a symbol/date."""

    __tablename__ = "market_sessions"

    id = Column(Integer, primary_key=True)
    date = Column(String, nullable=False)  # "2026-03-11"
    symbol = Column(String, nullable=False)  # "NQ"

    # Volume profile levels
    poc = Column(Float, nullable=True)
    vah = Column(Float, nullable=True)
    val = Column(Float, nullable=True)

    # VWAP bands
    vwap = Column(Float, nullable=True)
    vwap_1sd_upper = Column(Float, nullable=True)
    vwap_1sd_lower = Column(Float, nullable=True)
    vwap_2sd_upper = Column(Float, nullable=True)
    vwap_2sd_lower = Column(Float, nullable=True)
    vwap_3sd_upper = Column(Float, nullable=True)
    vwap_3sd_lower = Column(Float, nullable=True)

    # Initial balance
    ib_high = Column(Float, nullable=True)
    ib_low = Column(Float, nullable=True)
    ib_range = Column(Float, nullable=True)

    # Overnight range
    overnight_high = Column(Float, nullable=True)
    overnight_low = Column(Float, nullable=True)

    # Delta
    total_delta = Column(Integer, nullable=True)
    delta_divergence = Column(Boolean, default=False)

    # Classifications
    market_type = Column(String, nullable=True)  # "balanced", "trending_up", "trending_down"
    opening_type = Column(String, nullable=True)  # "OD", "OTD", "ORR", "OA"
    poor_high = Column(Boolean, default=False)
    poor_low = Column(Boolean, default=False)

    # Full session analysis JSON
    session_json = Column(JSON, nullable=True)

    # New: session metrics
    rotation_factor = Column(Integer, nullable=True)
    aspr = Column(Float, nullable=True)
    aspr_percentile = Column(Float, nullable=True)
    ib_tpo_count = Column(Integer, nullable=True)
    value_migration = Column(String, nullable=True)  # "up", "down", "overlapping"
    # New: session levels
    pdh = Column(Float, nullable=True)
    pdl = Column(Float, nullable=True)
    tokyo_high = Column(Float, nullable=True)
    tokyo_low = Column(Float, nullable=True)
    london_high = Column(Float, nullable=True)
    london_low = Column(Float, nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    signals = relationship("TradingSignal", back_populates="session")

    __table_args__ = (UniqueConstraint("date", "symbol", name="uq_market_session_date_symbol"),)


class TradingSignal(Base):
    """Scanner-generated trading signal with quality score."""

    __tablename__ = "trading_signals"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("market_sessions.id"), nullable=False)

    # Setup info
    setup_type = Column(String, nullable=False)  # "reversal_vwap_2sd", etc.
    setup_name = Column(String, nullable=True)
    category = Column(String, nullable=True)  # "fabio", "flow_horse"
    direction = Column(String, nullable=True)  # "long", "short"

    # Scoring
    score = Column(Float, nullable=False)  # 0-100 composite score
    conditions = Column(JSON, nullable=True)  # [{name, score, weight, is_auto}, ...]

    # Context at signal time
    price_at_signal = Column(Float, nullable=True)
    suggested_entry = Column(Float, nullable=True)
    suggested_stop = Column(Float, nullable=True)
    suggested_target = Column(Float, nullable=True)

    # Key levels at signal time
    vwap = Column(Float, nullable=True)
    poc = Column(Float, nullable=True)
    vah = Column(Float, nullable=True)
    val = Column(Float, nullable=True)
    ib_high = Column(Float, nullable=True)
    ib_low = Column(Float, nullable=True)
    cumulative_delta = Column(Integer, nullable=True)

    # Lifecycle
    is_active = Column(Boolean, default=True)
    triggered_at = Column(DateTime, default=_utcnow)
    expired_at = Column(DateTime, nullable=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=True)

    # New: multi-target + setup categorization
    suggested_target_2 = Column(Float, nullable=True)
    suggested_target_3 = Column(Float, nullable=True)
    level_touched = Column(String, nullable=True)
    setup_category = Column(String, nullable=True)  # "spring", "sfp", "poor_extreme", etc.
    rr_tp1 = Column(Float, nullable=True)
    rr_tp2 = Column(Float, nullable=True)

    created_at = Column(DateTime, default=_utcnow)

    # Relationships
    session = relationship("MarketSession", back_populates="signals")
    trade = relationship("Trade")

    __table_args__ = (
        Index("ix_trading_signals_active", "is_active", "triggered_at"),
        Index("ix_trading_signals_setup", "setup_type"),
    )


class MarketTrade(Base):
    """Raw tick data from Databento live stream."""

    __tablename__ = "market_trades"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False)
    ts = Column(DateTime, nullable=False)  # UTC from Databento
    price = Column(Float, nullable=False)
    size = Column(Integer, nullable=False)
    side = Column(String, nullable=False)  # "B" (bid aggressor) | "A" (ask aggressor)

    __table_args__ = (Index("ix_market_trades_symbol_ts", "symbol", "ts"),)


class MarketCandle(Base):
    """Persisted OHLCV candle bars — backfilled from Databento + appended live."""

    __tablename__ = "market_candles"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False)
    interval = Column(String, nullable=False)  # "1m" | "5m" | "15m"
    ts = Column(DateTime, nullable=False)  # bucket-start UTC
    o = Column(Float, nullable=False)
    h = Column(Float, nullable=False)
    l = Column(Float, nullable=False)
    c = Column(Float, nullable=False)
    v = Column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("symbol", "interval", "ts", name="uq_market_candle"),
        Index("ix_market_candles_symbol_interval_ts", "symbol", "interval", "ts"),
    )


class MarketLevel(Base):
    """Computed structural level for a session."""

    __tablename__ = "market_levels"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False)
    date = Column(String, nullable=False)
    level_type = Column(
        String, nullable=False
    )  # "order_block", "fvg", "ledge", "single_print", "pdh", "pdl", "tokyo_high", etc.
    session = Column(String, nullable=True)  # "tokyo", "london", "ny", null
    price_low = Column(Float, nullable=False)
    price_high = Column(Float, nullable=False)  # = price_low for single-price levels
    direction = Column(String, nullable=True)  # "bullish", "bearish", null
    is_filled = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (Index("ix_market_levels_symbol_date", "symbol", "date", "level_type"),)


class MarketTPOSession(Base):
    """Pre-computed TPO profile for a Globex session."""

    __tablename__ = "market_tpo_sessions"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False)
    date = Column(String, nullable=False)  # YYYY-MM-DD
    poc = Column(Float, nullable=False)
    vah = Column(Float, nullable=False)
    val = Column(Float, nullable=False)
    ib_high = Column(Float, nullable=True)
    ib_low = Column(Float, nullable=True)
    rotation_factor = Column(Integer, nullable=True)
    profile_shape = Column(String, nullable=True)
    opening_type = Column(String, nullable=True)
    opening_direction = Column(String, nullable=True)
    upper_excess = Column(Integer, default=0)
    lower_excess = Column(Integer, default=0)
    session_high = Column(Float, nullable=True)
    session_low = Column(Float, nullable=True)
    session_json = Column(String, nullable=False)  # Full TPOProfile as JSON
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("symbol", "date", name="uq_market_tpo_session"),
        Index("ix_market_tpo_sessions_symbol_date", "symbol", "date"),
    )


class MarketContext(Base):
    """Manual context gate persistence (Layer A gates)."""

    __tablename__ = "market_context"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    # Gate 1: Macro
    macro_bias = Column(String, nullable=True)  # "bull", "bear", "neutral"
    risk_mode = Column(String, nullable=True)  # "risk_on", "risk_off", "mixed"
    cycle_phase = Column(String, nullable=True)  # "early", "mid", "late", "recession"
    # Gate 2: Structure
    structure = Column(String, nullable=True)  # "uptrend", "downtrend", "ranging"
    structure_hl = Column(Float, nullable=True)  # Last confirmed HL (long invalidation below)
    structure_lh = Column(Float, nullable=True)  # Last confirmed LH (short invalidation above)
    # Gate 3: Day type
    day_type = Column(String, nullable=True)  # "trend", "normal", "normal_variation", "neutral", "composite"
    # VP anchors (Unix timestamps)
    vp_old_macro_start = Column(Integer, nullable=True)  # repurposed as "current" anchor
    vp_ongoing_macro_start = Column(Integer, nullable=True)
    vp_leg_start = Column(Integer, nullable=True)

    @property
    def vp_current_start(self):
        return self.vp_old_macro_start

    @vp_current_start.setter
    def vp_current_start(self, value):
        self.vp_old_macro_start = value

    __table_args__ = (UniqueConstraint("symbol", name="uq_market_context_symbol"),)


class SessionMetric(Base):
    """Permanent session metrics history for ASPR/RF baselines."""

    __tablename__ = "session_metrics"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False)
    date = Column(String, nullable=False)  # YYYY-MM-DD
    rotation_factor = Column(Integer, nullable=True)
    aspr = Column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("symbol", "date", name="uq_session_metrics_symbol_date"),
        Index("ix_session_metrics_symbol", "symbol"),
    )


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


class BetBlacklist(Base):
    """Events blacklisted from the play batch for a profile.

    When a user removes a bet, the event+provider+market+outcome is persisted
    here so it doesn't reappear after re-extraction.
    """

    __tablename__ = "bet_blacklist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_id = Column(Integer, ForeignKey("profiles.id"), nullable=False)
    event_id = Column(String, nullable=False)
    provider_id = Column(String, nullable=False)
    market = Column(String, nullable=True)
    outcome = Column(String, nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("profile_id", "event_id", "provider_id", "market", "outcome", name="uq_bet_blacklist"),
    )


# ============ Database Functions ============

# Singleton engines with connection pooling
_engine = None
_async_engine = None
_AsyncSessionFactory = None


def _is_postgres() -> bool:
    """Check if we're configured for PostgreSQL."""
    return bool(os.environ.get("DATABASE_URL", "").startswith("postgresql"))


def get_engine():
    """Get or create the sync database engine (for Alembic and legacy code)."""
    global _engine
    if _engine is None:
        db_url = os.environ.get("DATABASE_URL")
        if db_url:
            # PostgreSQL — convert async URL to sync for Alembic
            sync_url = db_url.replace("+asyncpg", "+psycopg2")
            pool = int(os.environ.get("DB_POOL_SIZE", "40"))
            overflow = int(os.environ.get("DB_MAX_OVERFLOW", "20"))
            _engine = create_engine(sync_url, pool_size=pool, max_overflow=overflow, pool_pre_ping=True)
        else:
            # SQLite fallback (local dev without Docker)
            from ..paths import get_db_path

            db_path = get_db_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            _engine = create_engine(
                f"sqlite:///{db_path}",
                poolclass=NullPool,
                connect_args={"check_same_thread": False, "timeout": 30},
            )
            with _engine.connect() as conn:
                conn.execute(text("PRAGMA journal_mode=WAL"))
                conn.commit()

            @event.listens_for(_engine, "connect")
            def _set_sqlite_pragmas(dbapi_conn, connection_record):
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.close()

        Base.metadata.create_all(_engine)
        if _is_postgres():
            _run_pg_migrations(_engine)
        else:
            _run_migrations(_engine)
    return _engine


def get_async_engine():
    """Get or create the async database engine (for FastAPI routes)."""
    global _async_engine
    if _async_engine is None:
        db_url = os.environ.get("DATABASE_URL")
        if db_url:
            pool = int(os.environ.get("DB_POOL_SIZE", "40"))
            overflow = int(os.environ.get("DB_MAX_OVERFLOW", "20"))
            _async_engine = create_async_engine(db_url, pool_size=pool, max_overflow=overflow)
        else:
            # SQLite async fallback
            from ..paths import get_db_path

            db_path = get_db_path()
            _async_engine = create_async_engine(
                f"sqlite+aiosqlite:///{db_path}",
                connect_args={"check_same_thread": False},
            )
    return _async_engine


def get_async_session_factory():
    """Get or create the async session factory."""
    global _AsyncSessionFactory
    if _AsyncSessionFactory is None:
        _AsyncSessionFactory = async_sessionmaker(
            bind=get_async_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _AsyncSessionFactory


def _run_migrations(engine):
    """Add new columns to existing tables (safe for fresh DBs too)."""
    import sqlite3

    with engine.connect() as conn:
        raw = conn.connection.connection  # Get raw sqlite3 connection
        cursor = raw.cursor()

        # Migrate provider_extraction_settings: add profile_id (global → per-profile)
        try:
            cursor.execute("SELECT profile_id FROM provider_extraction_settings LIMIT 1")
        except sqlite3.OperationalError:
            # Old table without profile_id — drop and let create_all rebuild
            try:
                cursor.execute("DROP TABLE IF EXISTS provider_extraction_settings")
                raw.commit()
                Base.metadata.tables["provider_extraction_settings"].create(engine)
            except sqlite3.OperationalError:
                pass
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

        # Replace non-unique index with unique constraint (prevents duplicate opportunities)
        try:
            cursor.execute("DROP INDEX IF EXISTS ix_opp_upsert")
            # Deduplicate existing rows: keep the one with highest id (most recent)
            cursor.execute("""
                DELETE FROM opportunities WHERE id NOT IN (
                    SELECT MAX(id) FROM opportunities
                    GROUP BY event_id, market, outcome1, provider1_id, type
                )
            """)
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_opp_upsert_unique "
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

        # Add CLOB microstructure columns to odds (Polymarket bid/ask/depth)
        for col, col_type in [("bid", "FLOAT"), ("ask", "FLOAT"), ("depth_usd", "FLOAT")]:
            try:
                cursor.execute(f"ALTER TABLE odds ADD COLUMN {col} {col_type}")
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

        # Add point + settlement_source to bets
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

        # Add main_min_odds to profile_provider_bonuses (two-phase bonus trigger)
        try:
            cursor.execute("SELECT main_min_odds FROM profile_provider_bonuses LIMIT 1")
        except sqlite3.OperationalError:
            try:
                cursor.execute("ALTER TABLE profile_provider_bonuses ADD COLUMN main_min_odds REAL")
                raw.commit()
            except sqlite3.OperationalError:
                pass

        # Add deposit_amount to profile_provider_bonuses (original deposit for trigger→main calc)
        try:
            cursor.execute("SELECT deposit_amount FROM profile_provider_bonuses LIMIT 1")
        except sqlite3.OperationalError:
            try:
                cursor.execute("ALTER TABLE profile_provider_bonuses ADD COLUMN deposit_amount REAL")
                raw.commit()
            except sqlite3.OperationalError:
                pass

        # Add trigger_mode to profile_provider_bonuses ("single" or "cumulative")
        try:
            cursor.execute("SELECT trigger_mode FROM profile_provider_bonuses LIMIT 1")
        except sqlite3.OperationalError:
            try:
                cursor.execute("ALTER TABLE profile_provider_bonuses ADD COLUMN trigger_mode TEXT DEFAULT 'cumulative'")
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
            ("display_home", "TEXT"),
            ("display_away", "TEXT"),
        ]:
            try:
                cursor.execute(f"SELECT {col} FROM events LIMIT 1")
            except sqlite3.OperationalError:
                try:
                    cursor.execute(f"ALTER TABLE events ADD COLUMN {col} {col_type}")
                    raw.commit()
                except sqlite3.OperationalError:
                    pass

        # Add LLM enrichment fields to specials
        for col, col_type in [
            ("llm_probability", "FLOAT"),
            ("llm_fair_odds", "FLOAT"),
            ("llm_edge_pct", "FLOAT"),
            ("llm_reasoning", "TEXT"),
            ("llm_confidence", "TEXT"),
        ]:
            try:
                cursor.execute(f"SELECT {col} FROM specials LIMIT 1")
            except sqlite3.OperationalError:
                try:
                    cursor.execute(f"ALTER TABLE specials ADD COLUMN {col} {col_type}")
                    raw.commit()
                except sqlite3.OperationalError:
                    pass

        # --- Provider limit risk columns ---
        try:
            cursor.execute("SELECT limit_risk FROM providers LIMIT 1")
        except sqlite3.OperationalError:
            try:
                cursor.execute("ALTER TABLE providers ADD COLUMN limit_risk TEXT DEFAULT 'low'")
                raw.commit()
            except sqlite3.OperationalError:
                pass

        try:
            cursor.execute("SELECT limit_notes FROM providers LIMIT 1")
        except sqlite3.OperationalError:
            try:
                cursor.execute("ALTER TABLE providers ADD COLUMN limit_notes TEXT")
                raw.commit()
            except sqlite3.OperationalError:
                pass

        # Add provider CLV columns to bets (Polymarket same-market CLV)
        for col, col_type in [("provider_closing_odds", "FLOAT"), ("provider_clv_pct", "FLOAT")]:
            try:
                cursor.execute(f"SELECT {col} FROM bets LIMIT 1")
            except sqlite3.OperationalError:
                try:
                    cursor.execute(f"ALTER TABLE bets ADD COLUMN {col} {col_type}")
                    raw.commit()
                except sqlite3.OperationalError:
                    pass


# ============ ML Feature Store ============


class MlFeature(Base):
    """
    Generic feature store for ML training and inference.

    Stores feature vectors keyed by domain + source, with optional outcome
    labels populated after the fact for supervised learning.
    """

    __tablename__ = "ml_features"
    __table_args__ = (
        Index("idx_ml_features_domain", "domain"),
        Index("idx_ml_features_source", "source_type", "source_id"),
    )

    id = Column(Integer, primary_key=True)
    domain = Column(String, nullable=False)  # e.g. "betting", "trading"
    source_id = Column(String, nullable=False)  # FK-like ref to source row
    source_type = Column(String, nullable=False)  # e.g. "opportunity", "signal"
    features = Column(JSON, nullable=False)  # serialised feature dict
    feature_version = Column(Integer, default=1)
    outcome = Column(Float, nullable=True)  # continuous label (e.g. CLV)
    outcome_binary = Column(Integer, nullable=True)  # 0/1 classification label
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)


class CandleSnapshot(Base):
    """
    Stores OHLCV candle arrays associated with a trading signal for ML use.
    """

    __tablename__ = "candle_snapshots"

    id = Column(Integer, primary_key=True)
    signal_id = Column(Integer, ForeignKey("trading_signals.id"), nullable=True)
    candles = Column(JSON, nullable=False)  # list of candle dicts
    timeframe = Column(String, default="1m")
    created_at = Column(DateTime, default=_utcnow)

    signal = relationship("TradingSignal")


class EconomicEvent(Base):
    """
    Scheduled macro economic events (e.g. CPI, NFP, FOMC) with consensus data.
    """

    __tablename__ = "economic_events"
    __table_args__ = (Index("idx_econ_events_datetime", "event_datetime"),)

    id = Column(Integer, primary_key=True)
    event_name = Column(String, nullable=False)
    event_datetime = Column(DateTime, nullable=False)
    importance = Column(Integer, nullable=True)  # 1=low, 2=medium, 3=high
    forecast = Column(Float, nullable=True)
    actual = Column(Float, nullable=True)
    previous = Column(Float, nullable=True)
    surprise = Column(Float, nullable=True)  # actual - forecast
    created_at = Column(DateTime, default=_utcnow)

    impacts = relationship("NewsImpact", back_populates="economic_event")


class NewsImpact(Base):
    """
    Price-impact measurements for economic events, used as ML training labels.
    """

    __tablename__ = "news_impact"

    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("economic_events.id"), nullable=True)
    symbol = Column(String, default="NQ")
    price_before = Column(Float, nullable=True)
    price_1m = Column(Float, nullable=True)
    price_5m = Column(Float, nullable=True)
    price_15m = Column(Float, nullable=True)
    price_30m = Column(Float, nullable=True)
    price_60m = Column(Float, nullable=True)
    immediate_impact_pct = Column(Float, nullable=True)
    sustained_impact_pct = Column(Float, nullable=True)
    reversal_pct = Column(Float, nullable=True)
    vix_at_event = Column(Float, nullable=True)
    delta_1m_after = Column(Float, nullable=True)
    volume_1m_after = Column(Float, nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    economic_event = relationship("EconomicEvent", back_populates="impacts")


class OptionsFlow(Base):
    """
    Daily options market microstructure data (GEX, put/call, VIX, DXY, yields).
    """

    __tablename__ = "options_flow"
    __table_args__ = (UniqueConstraint("date", "symbol", name="idx_options_flow_date"),)

    id = Column(Integer, primary_key=True)
    date = Column(String, nullable=False)
    symbol = Column(String, default="NQ")
    gex = Column(Float, nullable=True)
    gex_flip_level = Column(Float, nullable=True)
    net_options_delta = Column(Float, nullable=True)
    put_call_ratio = Column(Float, nullable=True)
    total_options_volume = Column(Float, nullable=True)
    vix_level = Column(Float, nullable=True)
    vix_1d_change = Column(Float, nullable=True)
    vix_term_structure = Column(String, nullable=True)
    dxy_level = Column(Float, nullable=True)
    dxy_1d_change = Column(Float, nullable=True)
    us10y_level = Column(Float, nullable=True)
    us10y_1d_change = Column(Float, nullable=True)
    us02y_level = Column(Float, nullable=True)
    yield_curve_spread = Column(Float, nullable=True)
    es_nq_ratio = Column(Float, nullable=True)
    created_at = Column(DateTime, default=_utcnow)


class CotData(Base):
    """
    CFTC Commitment of Traders report data for futures positioning analysis.
    """

    __tablename__ = "cot_data"
    __table_args__ = (UniqueConstraint("report_date", "symbol", name="idx_cot_date"),)

    id = Column(Integer, primary_key=True)
    report_date = Column(String, nullable=False)
    symbol = Column(String, default="NQ")
    net_position = Column(Integer, nullable=True)
    net_change = Column(Integer, nullable=True)
    long_pct = Column(Float, nullable=True)
    short_pct = Column(Float, nullable=True)
    open_interest = Column(Integer, nullable=True)
    open_interest_change = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=_utcnow)


class MlModelRegistry(Base):
    """
    Registry of trained ML model artifacts with versioning and performance metrics.
    """

    __tablename__ = "ml_model_registry"

    id = Column(Integer, primary_key=True)
    model_name = Column(String, nullable=False)
    version = Column(Integer, nullable=True)
    file_path = Column(String, nullable=True)
    training_data_count = Column(Integer, nullable=True)
    validation_metric = Column(Float, nullable=True)
    baseline_metric = Column(Float, nullable=True)
    is_active = Column(Integer, default=0)
    created_at = Column(DateTime, default=_utcnow)


class ExtractionFeature(Base):
    """Per-extraction-run feature snapshot for M10 optimization."""

    __tablename__ = "extraction_features"

    id = Column(Integer, primary_key=True)
    run_id = Column(String, nullable=False)
    trigger = Column(String, nullable=False)
    hour_of_day = Column(Integer, nullable=True)
    day_of_week = Column(Integer, nullable=True)
    minutes_since_last_sharp = Column(Float, nullable=True)
    minutes_since_last_soft = Column(Float, nullable=True)
    events_starting_next_2h = Column(Integer, nullable=True)
    events_starting_next_6h = Column(Integer, nullable=True)
    providers_attempted = Column(Integer, nullable=True)
    providers_succeeded = Column(Integer, nullable=True)
    providers_failed = Column(Integer, nullable=True)
    circuit_breakers_open = Column(Integer, nullable=True)
    total_events = Column(Integer, nullable=True)
    total_odds = Column(Integer, nullable=True)
    avg_match_rate = Column(Float, nullable=True)
    value_bets_found = Column(Integer, nullable=True)
    avg_edge_pct = Column(Float, nullable=True)
    arb_opportunities_found = Column(Integer, nullable=True)
    reverse_opportunities_found = Column(Integer, nullable=True)
    total_opportunity_value = Column(Float, nullable=True)
    bets_placed_from_run = Column(Integer, nullable=True)
    avg_clv_from_run = Column(Float, nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (Index("idx_extraction_features_run", "run_id"),)


class ProviderValueLog(Base):
    """Per-provider-per-run attribution — connects extraction to value outcomes."""

    __tablename__ = "provider_value_log"

    id = Column(Integer, primary_key=True)
    run_id = Column(String, nullable=False)
    provider_id = Column(String, nullable=False)
    events_extracted = Column(Integer, nullable=True)
    odds_extracted = Column(Integer, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    match_rate = Column(Float, nullable=True)
    spread_count = Column(Integer, nullable=True)
    total_count = Column(Integer, nullable=True)
    value_bets_from_provider = Column(Integer, nullable=True)
    avg_edge_from_provider = Column(Float, nullable=True)
    exclusive_events = Column(Integer, nullable=True)
    clv_avg_from_provider = Column(Float, nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (Index("idx_provider_value_run", "run_id", "provider_id"),)


class PinnacleCoverageLog(Base):
    """Per-provider per-sport Pinnacle coverage delta."""

    __tablename__ = "pinnacle_coverage_log"

    id = Column(Integer, primary_key=True)
    run_id = Column(String, nullable=False)
    provider_id = Column(String, nullable=False)
    sport = Column(String, nullable=False)
    pinnacle_events = Column(Integer, nullable=False)
    pinnacle_ml_events = Column(Integer, default=0)
    pinnacle_spread_events = Column(Integer, default=0)
    pinnacle_total_events = Column(Integer, default=0)
    provider_matched_events = Column(Integer, default=0)
    provider_ml_events = Column(Integer, default=0)
    provider_spread_events = Column(Integer, default=0)
    provider_total_events = Column(Integer, default=0)
    event_coverage_pct = Column(Float, nullable=True)
    ml_coverage_pct = Column(Float, nullable=True)
    spread_coverage_pct = Column(Float, nullable=True)
    total_coverage_pct = Column(Float, nullable=True)
    missing_events = Column(Integer, nullable=True)
    missing_spread = Column(Integer, nullable=True)
    missing_total = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("idx_pinnacle_coverage_run", "run_id"),
        Index("idx_pinnacle_coverage_provider", "provider_id", "sport"),
    )


class ProviderRecommendation(Base):
    """Diagnostic recommendation for a provider with lifecycle tracking."""

    __tablename__ = "provider_recommendations"

    id = Column(Integer, primary_key=True)
    provider_id = Column(String, nullable=False)
    category = Column(String, nullable=False)  # match_rate, coverage, timing, roi, market_gap
    severity = Column(String, nullable=False)  # critical, warning, info
    message = Column(String, nullable=False)
    diagnostic_data = Column(JSON, nullable=True)
    status = Column(String, nullable=False, default="open")  # open, acted_on, resolved, wont_fix
    acted_on_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    before_metric = Column(Float, nullable=True)
    after_metric = Column(Float, nullable=True)
    source = Column(String, default="rules")  # rules or ml
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("idx_recommendations_provider", "provider_id"),
        Index("idx_recommendations_status", "status"),
    )


class LevelTouchOutcome(Base):
    __tablename__ = "level_touch_outcomes"

    id = Column(Integer, primary_key=True)
    symbol = Column(Text, nullable=False)
    touch_ts = Column(Float, nullable=False)
    level_name = Column(Text, nullable=False)
    level_type = Column(Text, nullable=False)
    level_price = Column(Float, nullable=False)
    approach_direction = Column(Text, nullable=False)
    outcome = Column(Text)
    max_continuation_ticks = Column(Float)
    max_reversal_ticks = Column(Float)
    outcome_measured_at = Column(Float)
    session_date = Column(Text, nullable=False)
    is_backfill = Column(Integer, default=0)
    prediction = Column(Text)
    prediction_confidence = Column(Float)

    __table_args__ = (
        Index("ix_level_touch_outcomes_symbol_ts", "symbol", "touch_ts"),
        Index("ix_level_touch_outcomes_touch_ts", "touch_ts"),
    )


class LevelTouchFeature(Base):
    __tablename__ = "level_touch_features"

    id = Column(Integer, primary_key=True)
    touch_outcome_id = Column(Integer, ForeignKey("level_touch_outcomes.id"), nullable=False)
    features = Column(Text, nullable=False)
    feature_version = Column(Integer, default=1)
    created_at = Column(Float)

    __table_args__ = (Index("ix_level_touch_features_outcome_id", "touch_outcome_id"),)


def _run_pg_migrations(engine) -> None:
    """Lightweight column-additive migrations for Postgres.

    `Base.metadata.create_all` creates new tables but does not ALTER existing
    ones to add columns. This applies any missing columns using Postgres's
    ADD COLUMN IF NOT EXISTS, which is idempotent and safe to re-run.

    Only add columns that were introduced AFTER the table first shipped —
    brand-new tables are handled by create_all. Each entry is
    (table, column, type_sql) with type_sql in Postgres dialect.
    """
    additions: list[tuple[str, str, str]] = [
        # broker_trades: decision context added 2026-04-24
        ("broker_trades", "tp_price", "DOUBLE PRECISION"),
        ("broker_trades", "was_stop", "BOOLEAN"),
        ("broker_trades", "trail_count", "INTEGER"),
        ("broker_trades", "stop_ticks", "INTEGER"),
        ("broker_trades", "signal_trigger", "VARCHAR"),
        ("broker_trades", "signal_cont_p", "DOUBLE PRECISION"),
        ("broker_trades", "signal_rev_p", "DOUBLE PRECISION"),
        ("broker_trades", "orderflow_score", "DOUBLE PRECISION"),
        # stock_signals: full observation snapshot for training feedback (2026-04-25)
        ("stock_signals", "observation_b64", "TEXT"),
        ("stock_signals", "observation_dim", "INTEGER"),
        # broker_trades + stock_signals: structured "why we took it" tags (2026-04-27)
        ("broker_trades", "reasoning", "JSONB"),
        ("stock_signals", "reasoning", "JSONB"),
        # extraction_features: renamed dutch_opportunities_found → arb_opportunities_found
        # in code; existing prod DB has only the old name. Add new column so the
        # current code's INSERT/UPDATE doesn't error every cycle (was spamming
        # 'column "arb_opportunities_found" does not exist' on every extraction).
        ("extraction_features", "arb_opportunities_found", "INTEGER"),
        # bets.provider_bet_id: BetCreate schema accepts this since the mirror
        # workflow split (early 2026); the column was never added to Bet,
        # silently crashing every /api/bets POST with TypeError. Adding now.
        ("bets", "provider_bet_id", "VARCHAR"),
        # 2026-04-29 — profile↔TopstepX account binding so trading stats and
        # bankroll are scoped per-profile (forward-compat for multi-profile).
        ("broker_trades", "profile_id", "INTEGER"),
        ("profiles", "topstepx_account_id", "INTEGER"),
        # 2026-05-05 — exit_reason for closed-trade chart labels. Distinguishes
        # STOP / EE_LOCK / FLIP / EOD / MANUAL / etc. — was_stop alone covers
        # the binary STOP-vs-not but the user wants chart-side visibility into
        # which non-stop pathway closed the trade.
        ("broker_trades", "exit_reason", "VARCHAR"),
        # 2026-05-07 — TopstepX broker order ids for the entry + closing
        # legs. Stored as the unambiguous join key against /api/Trade/search
        # so backfill / realignment scripts can pin a broker_trade row to
        # exact broker fill records (price+side+size matching is too
        # ambiguous when trades cluster). NULL on legacy rows.
        ("broker_trades", "entry_order_id", "BIGINT"),
        ("broker_trades", "exit_order_id", "BIGINT"),
        # 2026-05-08 — DQN raw action q-values, persisted at signal time so we
        # can analyze action margin and calibration without re-running inference.
        ("stock_signals", "q_values", "JSONB"),
        # 2026-05-08 — actual placed stop at exit time (BE-lock + cont-trail
        # walks). Used by the chart widget to draw a trail line at the
        # final stop while keeping the original stop_price band visible
        # for the R:R label. NULL on legacy rows.
        ("broker_trades", "final_stop_price", "DOUBLE PRECISION"),
        # 2026-05-20 — arb leg linkage. Pairs the soft anchor + Polymarket
        # counter of one arbitrage so per-arb guaranteed profit is verifiable.
        ("bets", "arb_group_id", "VARCHAR"),
    ]
    with engine.begin() as conn:
        # Each ALTER runs inside its own SAVEPOINT so a single failure
        # doesn't put the outer transaction into a broken state. Without
        # this, one bad ALTER would silently abort every subsequent
        # statement with Postgres "current transaction is aborted,
        # commands ignored until end of transaction block" — the failure
        # mode that ate the realign_broker_trade_timestamps script's
        # session init on 2026-05-07.
        for table, col, col_type in additions:
            sp = conn.begin_nested()
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}"))
                sp.commit()
            except Exception:
                sp.rollback()
                logger.warning("pg migration: %s.%s failed", table, col, exc_info=True)

        # Index for provider_bet_id lookups during settlement reconciliation
        sp = conn.begin_nested()
        try:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_bets_provider_bet_id ON bets(provider_bet_id)"))
            sp.commit()
        except Exception:
            sp.rollback()
            logger.warning("pg migration: bets.provider_bet_id index failed", exc_info=True)

        # Index for per-profile broker-trades lookups (stats + bankroll filter)
        sp = conn.begin_nested()
        try:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_broker_trades_profile ON broker_trades(profile_id)"))
            sp.commit()
        except Exception:
            sp.rollback()
            logger.warning("pg migration: broker_trades.profile_id index failed", exc_info=True)

        # 2026-05-07 — level_touch_outcomes/features had zero indexes despite
        # symbol/touch_ts filters, ORDER BY touch_ts, and the FK join on
        # touch_outcome_id. Audit item #14. Sequential scans on a growing
        # training table are O(n) per query.
        for idx_sql in (
            "CREATE INDEX IF NOT EXISTS ix_level_touch_outcomes_symbol_ts ON level_touch_outcomes(symbol, touch_ts)",
            "CREATE INDEX IF NOT EXISTS ix_level_touch_outcomes_touch_ts ON level_touch_outcomes(touch_ts)",
            "CREATE INDEX IF NOT EXISTS ix_level_touch_features_outcome_id ON level_touch_features(touch_outcome_id)",
        ):
            sp = conn.begin_nested()
            try:
                conn.execute(text(idx_sql))
                sp.commit()
            except Exception:
                sp.rollback()
                logger.warning("pg migration: %s failed", idx_sql, exc_info=True)

        # 2026-05-08 — broker_trades.profile_id and stock_signals.trade_id
        # have always been plain Integer columns (no FK constraint). Audit
        # item #50. ON DELETE SET NULL so deleting a profile or a broker
        # trade row doesn't error out callers that still hold the reference;
        # they just see NULL on the next read. Idempotent: skip if the
        # constraint already exists. Each ALTER runs in a SAVEPOINT so a
        # surprise orphan row doesn't abort the rest of the migration.
        fk_migrations = [
            (
                "fk_broker_trades_profile",
                "ALTER TABLE broker_trades ADD CONSTRAINT fk_broker_trades_profile "
                "FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL",
            ),
            (
                "fk_stock_signals_trade",
                "ALTER TABLE stock_signals ADD CONSTRAINT fk_stock_signals_trade "
                "FOREIGN KEY (trade_id) REFERENCES broker_trades(id) ON DELETE SET NULL",
            ),
        ]
        for cname, ddl in fk_migrations:
            sp = conn.begin_nested()
            try:
                exists = conn.execute(
                    text("SELECT 1 FROM pg_constraint WHERE conname = :n"),
                    {"n": cname},
                ).first()
                if exists:
                    sp.commit()
                    continue
                conn.execute(text(ddl))
                sp.commit()
            except Exception:
                sp.rollback()
                logger.warning("pg migration: %s failed", cname, exc_info=True)

        # 2026-04-25 — slip_odds_ticks for slip-streaming observability
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS slip_odds_ticks (
                  id BIGSERIAL PRIMARY KEY,
                  ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  provider_id TEXT NOT NULL,
                  event_id TEXT NOT NULL,
                  market TEXT NOT NULL,
                  outcome TEXT NOT NULL,
                  scraped_odds REAL NOT NULL,
                  scanner_odds REAL,
                  drift_pct REAL
                );
                """
            )
        )
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_slip_odds_event ON slip_odds_ticks(event_id, market, outcome);")
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_slip_odds_ts ON slip_odds_ticks(ts);"))

        # 2026-05-17 — shadow_predictions: side-by-side logging of multiple
        # models' predictions on the same observation so production vs. shadow
        # model agreement/disagreement can be analysed before switching.
        sp = conn.begin_nested()
        try:
            conn.execute(
                text("""
                    CREATE TABLE IF NOT EXISTS shadow_predictions (
                        id SERIAL PRIMARY KEY,
                        request_id VARCHAR(64) NOT NULL,
                        model_name VARCHAR(32) NOT NULL,
                        is_production BOOLEAN NOT NULL DEFAULT FALSE,
                        ts TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                        p_cont DOUBLE PRECISION NOT NULL,
                        p_rev DOUBLE PRECISION NOT NULL,
                        p_skip DOUBLE PRECISION NOT NULL,
                        expected_r DOUBLE PRECISION NOT NULL,
                        win_probability DOUBLE PRECISION NOT NULL,
                        duration_bars DOUBLE PRECISION NOT NULL,
                        uncertainty DOUBLE PRECISION NOT NULL,
                        confidence DOUBLE PRECISION NOT NULL,
                        action VARCHAR(16) NOT NULL,
                        zone_id INTEGER,
                        zone_center DOUBLE PRECISION,
                        broker_trade_id INTEGER REFERENCES broker_trades(id)
                    )
                """)
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_shadow_predictions_request_model"
                    " ON shadow_predictions(request_id, model_name)"
                )
            )
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_shadow_predictions_ts ON shadow_predictions(ts)"))
            sp.commit()
            logger.info("shadow_predictions table ready")
        except Exception:
            sp.rollback()
            logger.exception("shadow_predictions migration failed")


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


# ── Market DB (separate file for high-frequency tick/candle writes) ────────

_market_engine = None
_market_async_engine = None
_MarketSessionFactory = None
_MarketAsyncSessionFactory = None

# Tables that live in market.db — high-frequency writes that must not
# contend with extraction/analysis for SQLite's single-writer lock.
MARKET_DB_TABLES = {MarketTrade.__table__, MarketCandle.__table__}


def get_market_engine():
    """Get or create the market-data database engine.

    Uses MARKET_DATABASE_URL env var for PostgreSQL, or SQLite fallback.
    Separate from main DB so Databento tick writes (hundreds/sec) never
    block extraction commits or frontend API queries.
    """
    global _market_engine
    if _market_engine is None:
        db_url = os.environ.get("MARKET_DATABASE_URL")
        if db_url:
            sync_url = db_url.replace("+asyncpg", "+psycopg2")
            _market_engine = create_engine(sync_url)
        else:
            from ..paths import get_market_db_path

            market_path = get_market_db_path()
            market_path.parent.mkdir(parents=True, exist_ok=True)
            _market_engine = create_engine(
                f"sqlite:///{market_path}",
                poolclass=NullPool,
                connect_args={
                    "check_same_thread": False,
                    "timeout": 10,
                },
            )
            with _market_engine.connect() as conn:
                conn.execute(text("PRAGMA journal_mode=WAL"))
                conn.commit()

            @event.listens_for(_market_engine, "connect")
            def _set_market_pragmas(dbapi_conn, connection_record):
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA busy_timeout=10000")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.close()

        for table in MARKET_DB_TABLES:
            table.create(_market_engine, checkfirst=True)
    return _market_engine


def get_market_session_factory():
    """Get or create session factory for market DB."""
    global _MarketSessionFactory
    if _MarketSessionFactory is None:
        _MarketSessionFactory = sessionmaker(bind=get_market_engine())
    return _MarketSessionFactory


def get_market_session():
    """Get a database session for market DB (ticks + candles).

    Caller is responsible for closing the session.
    """
    factory = get_market_session_factory()
    return factory()


# ============ Constants ============

# Minimum odds for bonus wagering (bets below this don't count)
BONUS_MIN_ODDS = 1.80


class BrokerTrade(Base):
    """Automated trade execution log with full decision context.

    Captures not just the trade mechanics (entry/exit/pnl) but also the
    signal that produced it (confidence, probabilities, zone, orderflow)
    and the exit trajectory (trail count, was-stop) so future retraining
    has the full (context, action, outcome) tuple in a single row. Richer
    observation context (zone_members, model_type, etc) is queryable via
    stock_signals join on trade_id after the nightly correlate cron runs.
    """

    __tablename__ = "broker_trades"

    id = Column(Integer, primary_key=True)
    ts = Column(DateTime, nullable=False, default=_utcnow)
    # Owning sports-betting profile. Lets us scope trading stats / equity
    # curve to the active profile so two profiles trading different
    # TopstepX accounts don't see each other's trades. NULL only for
    # legacy rows pre-migration (backfilled to the rasmus profile).
    profile_id = Column(
        Integer,
        ForeignKey("profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    session_date = Column(String, nullable=False)
    symbol = Column(String, nullable=False)
    side = Column(String, nullable=False)
    size = Column(Integer, nullable=False)

    # Trade mechanics
    entry_price = Column(Float, nullable=False)
    stop_price = Column(Float, nullable=True)
    # Actual placed stop at exit time — captures BE-lock + cont-trail walks.
    # NULL on rows pre-2026-05-08 migration; widget falls back to "no trail line".
    final_stop_price = Column(Float, nullable=True)
    tp_price = Column(Float, nullable=True)
    exit_price = Column(Float, nullable=True)
    stop_ticks = Column(Integer, nullable=True)
    was_stop = Column(Boolean, nullable=True)
    trail_count = Column(Integer, nullable=True)

    # Outcome
    pnl_dollars = Column(Float, nullable=True)
    pnl_r = Column(Float, nullable=True)
    fill_latency_ms = Column(Float, nullable=True)
    slippage_ticks = Column(Float, nullable=True)
    # STOP / EE_LOCK / FLIP / FLIP_REVERSAL / EOD / MANUAL / SHUTDOWN /
    # ADVERSE_SLIP / SIZE_MISMATCH / etc. Captured from broker_adapter's
    # last flatten() reason at close time, or "STOP" when was_stop=true.
    exit_reason = Column(String, nullable=True)

    # Signal context at entry
    signal_action = Column(String, nullable=True)
    signal_confidence = Column(Float, nullable=True)
    signal_zone = Column(Float, nullable=True)
    signal_trigger = Column(String, nullable=True)
    signal_cont_p = Column(Float, nullable=True)
    signal_rev_p = Column(Float, nullable=True)
    orderflow_score = Column(Float, nullable=True)
    # Why we took it — derived structured tags + 1-line summary.
    # JSONB in Postgres so factors can be queried with ? / ->>.
    reasoning = Column(JSON, nullable=True)

    closed_at = Column(DateTime, nullable=True)

    # TopstepX broker order ids for the entry leg and the closing leg.
    # Stored so the backfill / realignment script can join unambiguously
    # against /api/Trade/search records (price+side+size matching is too
    # ambiguous when trades cluster). NULL on legacy rows pre-2026-05-07.
    entry_order_id = Column(BigInteger, nullable=True, index=True)
    exit_order_id = Column(BigInteger, nullable=True, index=True)

    __table_args__ = (
        Index("ix_broker_trades_session", "session_date"),
        Index("ix_broker_trades_ts", "ts"),
    )


class StockSignal(Base):
    """Every signal the LevelMonitor emits, persisted for later correlation
    with realized broker_trades. This is the training-feedback foundation —
    joined against broker_trades by ts + entry_price proximity to produce
    labelled (signal_context, realized_outcome) pairs.

    `observation_b64` captures the 279-dim observation vector the model saw
    at signal time, base64-encoded float32 numpy bytes. Together with the
    eventual realized PnL on the linked trade, this gives the trainer a
    ground-truth (obs, action, reward) tuple to learn from — not just a
    simulator estimate.
    """

    __tablename__ = "stock_signals"

    id = Column(Integer, primary_key=True)
    ts = Column(DateTime, nullable=False, default=_utcnow, index=True)
    symbol = Column(String, nullable=False, default="NQ")
    # Signal context
    action = Column(String, nullable=False)  # enter_long / enter_short / SKIP
    price = Column(Float, nullable=False)  # tick price when signal fired
    confidence = Column(Float, nullable=True)
    cont_p = Column(Float, nullable=True)
    rev_p = Column(Float, nullable=True)
    stop_price = Column(Float, nullable=True)
    stop_ticks = Column(Integer, nullable=True)
    zone_center = Column(Float, nullable=True)
    zone_members = Column(Integer, nullable=True)
    model_type = Column(String, nullable=True)  # "gbt+dqn", "dqn", etc.
    # Full observation vector — base64(np.float32[279].tobytes()).
    # ~1.5 KB per signal, decoded back to numpy with np.frombuffer.
    observation_b64 = Column(Text, nullable=True)
    observation_dim = Column(Integer, nullable=True)
    # Outcome linkage (filled by the correlate step when a matching trade closes)
    trade_id = Column(
        Integer,
        ForeignKey("broker_trades.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Why we emitted the signal — same shape as broker_trades.reasoning.
    reasoning = Column(JSON, nullable=True)
    # Raw DQN action q-values [q_continuation, q_reversal, q_skip] (or 2-elem
    # [q_cont, q_rev] for legacy GBT-only paths). Lets us analyze action margin
    # and calibration drift across model versions without re-running inference.
    q_values = Column(JSON, nullable=True)

    __table_args__ = (Index("ix_stock_signals_ts_price", "ts", "price"),)


class AccountSnapshot(Base):
    """Time-series of TopstepX account state. Written by the snapshot_account.py
    cron every 5 minutes so we can reconstruct equity curves, drawdown profiles,
    and overlay account state with trade outcomes."""

    __tablename__ = "account_snapshots"

    id = Column(Integer, primary_key=True)
    ts = Column(DateTime, nullable=False, default=_utcnow, index=True)
    account_id = Column(BigInteger, nullable=False)
    balance = Column(Float, nullable=True)
    equity = Column(Float, nullable=True)
    unrealized_pnl = Column(Float, nullable=True)
    daily_pnl = Column(Float, nullable=True)
    open_position_size = Column(Integer, nullable=True)
    source = Column(String, nullable=False, default="topstepx_account_search")

    __table_args__ = (Index("ix_account_snapshots_account_ts", "account_id", "ts"),)


class ShadowPrediction(Base):
    """Side-by-side log of multiple models' predictions on the same obs.

    Production model's prediction is what gets dispatched. Shadow model's
    prediction is logged here for comparison. Both rows share the same
    request_id so we can compare them later.
    """

    __tablename__ = "shadow_predictions"

    id = Column(Integer, primary_key=True)
    request_id = Column(String(64), nullable=False, index=True)  # uuid per zone touch
    model_name = Column(String(32), nullable=False, index=True)  # 'gbt_v5' or 'ft_v1'
    is_production = Column(Boolean, nullable=False, default=False)
    ts = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    # The prediction itself
    p_cont = Column(Float, nullable=False)
    p_rev = Column(Float, nullable=False)
    p_skip = Column(Float, nullable=False)
    expected_R = Column("expected_r", Float, nullable=False)
    win_probability = Column(Float, nullable=False)
    duration_bars = Column(Float, nullable=False)
    uncertainty = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)
    action = Column(String(16), nullable=False)  # "CONTINUATION"/"REVERSAL"/"SKIP"

    # Context for joining to outcomes
    zone_id = Column(Integer, nullable=True)
    zone_center = Column(Float, nullable=True)

    # FK to the realized broker_trade if one was placed (production only)
    broker_trade_id = Column(Integer, ForeignKey("broker_trades.id"), nullable=True)

    __table_args__ = (Index("ix_shadow_predictions_request_model", "request_id", "model_name"),)


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at: {DB_PATH}")
