"""
Betty Database Models

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
from datetime import UTC, datetime
from enum import StrEnum

logger = logging.getLogger(__name__)


def _utcnow():
    """Timezone-aware UTC now for column defaults."""
    return datetime.now(UTC)


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
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker
from sqlalchemy.pool import NullPool


class RiskLevel(StrEnum):
    """Risk level classification for providers."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class LimitRisk(StrEnum):
    """How aggressively a provider is known to limit winners."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    INSTANT = "instant"


class LimitType(StrEnum):
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

    # Enhanced inversion detection (2026-05-26): set True only when storage
    # has verified the soft book's home/away assignment agrees with Pinnacle
    # (after swap if needed). False means the scanner must skip this event's
    # soft odds because we don't trust the side mapping. Defaults to True so
    # historical events without the check still surface.
    home_away_validated = Column(Boolean, nullable=False, server_default=text("true"), default=True)

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
    # Period/structural scope of this market (e.g. "ft", "reg", "1h", "set_1").
    # Set by each extractor from its native scope identifier (Pinnacle period,
    # Altenar typeId, Gecko market_template). Default 'ft' for backward compat.
    # The scanner only joins odds at matching scope — see SPORT_CANONICAL_SCOPE.
    scope = Column(String(16), nullable=False, server_default="ft", default="ft")
    # Pinnacle exposes a per-market-line maxRiskStake in USD. Captured for
    # the arb-table liquidity filter — soft books calibrate their per-account
    # caps proportionally to this. Null for non-Pinnacle providers and for
    # rows extracted before this column shipped (backfills naturally on the
    # next Pinnacle cycle).
    max_stake = Column(Float, nullable=True)
    bid = Column(Float, nullable=True)  # Best bid price (probability 0-1, CLOB only)
    ask = Column(Float, nullable=True)  # Best ask price (probability 0-1, CLOB only)
    depth_usd = Column(Float, nullable=True)  # Total ask-side depth in USD (CLOB only)

    updated_at = Column(DateTime, default=_utcnow)

    # Unique constraint: one odds per event/provider/market/outcome/point/scope combo
    # Includes point to allow multiple lines per market (e.g., over 2.5 vs over 3.0)
    # Includes scope to allow same market at different structural scopes (ft vs reg)
    __table_args__ = (
        # NULLS NOT DISTINCT so (event_id, provider_id, market, outcome, NULL, scope) is unique
        UniqueConstraint(
            "event_id",
            "provider_id",
            "market",
            "outcome",
            "point",
            "scope",
            name="uq_odds_with_point_scope",
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
        # Composite key for OddsBatchProcessor flush lookups (now includes scope)
        Index("ix_odds_composite_key", "event_id", "provider_id", "market", "outcome", "point", "scope"),
        # Scanner join index — finds canonical-scope rows for an event/market/line fast
        Index("ix_odds_event_market_point_scope", "event_id", "market", "point", "scope"),
    )

    # Relationships
    event = relationship("Event", back_populates="odds")
    provider = relationship("Provider", back_populates="odds")


class OddsMovement(Base):
    """Append-only log of significant odds changes for steam-move detection.

    Written by `OddsBatchProcessor` only when `STEAM_DETECTOR_ENABLED=1` is
    set in env — keeps the hot path overhead-free in default deployments.
    A row is emitted only when the implied-probability delta on an upsert
    exceeds `STEAM_DELTA_PP_MIN` (default 0.5 percentage points), so small
    noise upserts (e.g. odds flicker) are filtered out at write time.

    Steam detection (`backend/src/analysis/steam_detector.py`) groups
    movements by `(event_id, market, outcome, point, scope)` over a short
    rolling window and counts how many distinct providers moved in the
    same direction — that's the syndicate-style signal we exploit.
    """

    __tablename__ = "odds_movements"

    id = Column(Integer, primary_key=True, autoincrement=True)

    event_id = Column(String, ForeignKey("events.id"), nullable=False)
    provider_id = Column(String, ForeignKey("providers.id"), nullable=False)

    market = Column(String, nullable=False)
    outcome = Column(String, nullable=False)
    point = Column(Float, nullable=True)
    scope = Column(String(16), nullable=False, server_default="ft", default="ft")

    prev_odds = Column(Float, nullable=False)
    new_odds = Column(Float, nullable=False)
    # Signed implied-probability delta in percentage points
    # (positive = probability increased = price shortened).
    delta_implied_pp = Column(Float, nullable=False)
    # 'up' = implied probability increased, 'down' = decreased. Stored as
    # a string for query simplicity (steam_detector groups by direction).
    direction = Column(String(4), nullable=False)

    recorded_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        # Detector hot path: group recent movements by (event, market, outcome, point, scope)
        Index(
            "ix_odds_movements_event_market",
            "event_id",
            "market",
            "outcome",
            "point",
            "scope",
            "recorded_at",
        ),
        # Retention sweep + time-window queries
        Index("ix_odds_movements_recorded_at", "recorded_at"),
        # Per-provider analytics (which books are leading vs trailing)
        Index("ix_odds_movements_provider", "provider_id", "recorded_at"),
    )


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

    # Which real account this bet was placed from (shared sharp pool or a
    # per-campaign soft account). Source of truth for account attribution;
    # provider_id is retained for all existing readers. SET NULL so a GC'd
    # soft account doesn't orphan-delete bet history.
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True)

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
    + React state. Survives `betty.bat` restart, browser hard-refresh,
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
    style = Column(String, nullable=False, default="personal")  # "personal" | "bonus_extraction"

    # Purpose — drives ROI bucketing (Rule B). "edge" profiles hold the genuine
    # edge volume that defines true ROI; "bonus" profiles are bonus-extraction
    # campaigns whose bets (both the soft free-bet leg AND the real-money sharp
    # hedge leg) are excluded from true ROI and summed into a separate bonus
    # profit total. See docs/spec/2026-05-30-multi-profile-sharp-accounts-bonus-roi.md
    kind = Column(String, default="edge", nullable=False)  # "edge" | "bonus"

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    bonus_statuses = relationship("ProfileProviderBonus", back_populates="profile", cascade="all, delete-orphan")
    provider_balances = relationship("ProfileProviderBalance", back_populates="profile", cascade="all, delete-orphan")
    accounts = relationship("ProfileAccount", back_populates="profile", cascade="all, delete-orphan")
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


class Account(Base):
    """One real account the user owns at a provider.

    Sharp accounts (pinnacle/polymarket/kalshi/cloudbet) are SHARED: a single
    row referenced by many profiles via `profile_accounts`. Spending a hedge
    leg in any profile updates this one real `balance`, so every profile that
    links it sees the change. Soft accounts are per-campaign (single-linked).

    This is the balance source of truth going forward; `ProfileProviderBalance`
    is retained read-only for the one-time migration backfill (see
    `_migrate_provider_balances_to_accounts`) and is no longer written.
    """

    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_id = Column(String, ForeignKey("providers.id"), nullable=False)
    label = Column(String, nullable=False)  # "rasmus", "alt2", "campaign-7"
    kind = Column(String, nullable=False)  # "sharp" | "soft"
    balance = Column(Float, default=0.0)
    currency = Column(String, default="SEK")  # native currency for conversion

    # Manual account opened date for pre-existing accounts (dormant-account
    # handling — carried over from ProfileProviderBalance).
    account_opened_at = Column(DateTime, nullable=True)

    is_active = Column(Boolean, default=True, nullable=False)  # soft-delete flag

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("provider_id", "label", name="uq_account_provider_label"),
        Index("ix_account_provider", "provider_id"),
    )

    # Relationships
    provider = relationship("Provider")
    profile_links = relationship("ProfileAccount", back_populates="account", cascade="all, delete-orphan")


class ProfileAccount(Base):
    """Explicit visibility link: a profile sees exactly the accounts linked here.

    A fresh sharp account is linked only to the profile that created it, so it
    does NOT leak into other profiles. A shared sharp account gets one link row
    per profile that uses it.
    """

    __tablename__ = "profile_accounts"

    profile_id = Column(Integer, ForeignKey("profiles.id"), primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), primary_key=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (UniqueConstraint("profile_id", "account_id", name="uq_profile_account"),)

    # Relationships
    account = relationship("Account", back_populates="profile_links")
    profile = relationship("Profile", back_populates="accounts")


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
        # Unique upsert index now includes scope so F5/Q1/1H opportunities on the
        # same event/market/provider can coexist with the full-game ft row.
        Index(
            "ix_opp_upsert_unique",
            "event_id",
            "market",
            "outcome1",
            "provider1_id",
            "type",
            "scope",
            unique=True,
        ),
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
    # Period scope (ft / f5 / 1h / q1 / etc). Mirrors odds.scope. Default 'ft' so
    # all existing opportunity rows continue to represent full-game markets. The
    # analyzer tags non-ft rows when scanning period-scoped odds.
    scope = Column(String(16), nullable=False, server_default="ft", default="ft")

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

    # Diagnostic annotations populated by the analyzer at upsert time.
    # Shape: {"key_number": {...} | None,
    #         "steam_signal": {...} | None,
    #         "consensus_lean": {...} | None}
    # Frontend reads this to render per-opportunity indicator badges.
    # See backend/src/analysis/{key_numbers,steam_detector,consensus_lean}.py
    annotations = Column(JSON, nullable=True)

    # Relationships
    event = relationship("Event")
    provider1 = relationship("Provider", foreign_keys=[provider1_id])
    provider2 = relationship("Provider", foreign_keys=[provider2_id])


class OppSnapshot(Base):
    """
    Frozen detection-time record of every opportunity surfaced by the scanner,
    with closing-line value backfilled once the event starts.

    Sister table to `opportunities`: the live `opportunities` table is ephemeral
    (wiped on each scan cycle); this table persists one row per logical opp
    instance (uniqueness mirrors `opportunities`) for retrospective CLV analysis.

    Detection-time fields are frozen on first sighting; re-detections only bump
    `last_detected_at` and `detection_count`. CLV fields are NULL until the
    backfill job runs after event start_time.
    """

    __tablename__ = "opp_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "market",
            "outcome1",
            "provider1_id",
            "type",
            "scope",
            name="uq_opp_snapshot",
        ),
        Index("ix_opp_snap_provider_type_first", "provider1_id", "type", "first_detected_at"),
        Index("ix_opp_snap_first_detected_at", "first_detected_at"),
        # Partial index for backfill job (Postgres only — SQLite ignores the
        # postgresql_where kwarg and creates a plain index, which is fine).
        Index("ix_opp_snap_clv_pending", "event_id", postgresql_where=text("clv_computed_at IS NULL")),
    )

    id = Column(Integer, primary_key=True)
    event_id = Column(String, ForeignKey("events.id"), nullable=False)
    type = Column(String, nullable=False)  # value | arb | reverse_value
    market = Column(String, nullable=False)
    outcome1 = Column(String, nullable=False)
    point = Column(Float, nullable=True)
    scope = Column(String(16), nullable=False, server_default="ft", default="ft")

    # Leg 1 (always present)
    provider1_id = Column(String, ForeignKey("providers.id"), nullable=False)
    odds1_at_detection = Column(Float, nullable=False)
    fair_odds1_at_detection = Column(Float, nullable=True)
    edge_pct_at_detection = Column(Float, nullable=True)

    # Leg 2 (arb-only; NULL for value/reverse_value)
    provider2_id = Column(String, ForeignKey("providers.id"), nullable=True)
    outcome2 = Column(String, nullable=True)
    odds2_at_detection = Column(Float, nullable=True)

    # Lifecycle
    first_detected_at = Column(DateTime, nullable=False, default=_utcnow)
    last_detected_at = Column(DateTime, nullable=False, default=_utcnow)
    detection_count = Column(Integer, nullable=False, default=1, server_default="1")
    time_to_start_minutes_at_detection = Column(Float, nullable=True)

    # Backfilled at event start (NULL until then)
    provider1_closing_odds = Column(Float, nullable=True)
    provider1_closing_age_minutes = Column(Float, nullable=True)
    provider2_closing_odds = Column(Float, nullable=True)
    provider2_closing_age_minutes = Column(Float, nullable=True)
    pinnacle_closing_fair = Column(Float, nullable=True)
    pinnacle_closing_age_minutes = Column(Float, nullable=True)
    provider_clv_pct = Column(Float, nullable=True)
    pinnacle_clv_pct = Column(Float, nullable=True)
    closing_prob_sum = Column(Float, nullable=True)  # arbs only
    was_arb_at_close = Column(Boolean, nullable=True)  # arbs only
    clv_computed_at = Column(DateTime, nullable=True)

    # ---- Multi-book sharp blend (shadow). Frozen at detection / backfilled at close. ----
    blended_fair1_at_detection = Column(Float, nullable=True)
    blend_n_sources_at_detection = Column(Integer, nullable=True)
    blend_sources = Column(JSON, nullable=True)  # list[str] of contributing providers
    blended_closing_fair = Column(Float, nullable=True)
    blended_clv_pct = Column(Float, nullable=True)

    # ---- Shading-aware diagnostic (shadow). Frozen at detection time. ----
    shading_risk = Column(String, nullable=True)  # "low" | "elevated" | "high" | None
    odds_bucket = Column(String, nullable=True)  # "<1.5" | "1.5-2.5" | "2.5-4.0" | "4.0+"

    event = relationship("Event")


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
        # One-time, idempotent backfill of the Account layer from the legacy
        # per-profile balance table. Runs after columns are ensured on both
        # backends; no-ops once accounts/links/bets are populated.
        _migrate_provider_balances_to_accounts(_engine)
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

        # 2026-05-26 — opportunities.scope column + rebuild upsert index to
        # include scope. Idempotent: skip if column already exists.
        try:
            cursor.execute("SELECT scope FROM opportunities LIMIT 1")
        except sqlite3.OperationalError:
            try:
                cursor.execute("ALTER TABLE opportunities ADD COLUMN scope TEXT NOT NULL DEFAULT 'ft'")
                cursor.execute("DROP INDEX IF EXISTS ix_opp_upsert_unique")
                cursor.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_opp_upsert_unique "
                    "ON opportunities (event_id, market, outcome1, provider1_id, type, scope)"
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

        # Add style to profiles (Stats per-profile account styles)
        try:
            cursor.execute("SELECT style FROM profiles LIMIT 1")
        except sqlite3.OperationalError:
            try:
                cursor.execute("ALTER TABLE profiles ADD COLUMN style TEXT NOT NULL DEFAULT 'personal'")
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

        # 2026-05-30 — Account layer. profiles.kind (edge/bonus ROI bucketing)
        # + bets.account_id (real-account attribution). New tables
        # accounts/profile_accounts are created by create_all; the data backfill
        # runs in _migrate_provider_balances_to_accounts after this function.
        try:
            cursor.execute("SELECT kind FROM profiles LIMIT 1")
        except sqlite3.OperationalError:
            try:
                cursor.execute("ALTER TABLE profiles ADD COLUMN kind VARCHAR NOT NULL DEFAULT 'edge'")
                raw.commit()
            except sqlite3.OperationalError:
                pass
        try:
            cursor.execute("SELECT account_id FROM bets LIMIT 1")
        except sqlite3.OperationalError:
            try:
                cursor.execute("ALTER TABLE bets ADD COLUMN account_id INTEGER")
                raw.commit()
            except sqlite3.OperationalError:
                pass


def _migrate_provider_balances_to_accounts(engine) -> None:
    """One-time, idempotent backfill: ProfileProviderBalance -> Account layer.

    Sharp providers (UNLIMITED_PROVIDERS) collapse to ONE shared account each
    (balance from the active, else lowest-id, profile), linked to every profile
    that held a balance row. Other providers become per-profile soft accounts
    labeled from the profile name. Finally backfills bets.account_id for any
    bet whose (profile_id, provider_id) resolves to a linked account.

    ProfileProviderBalance is left intact as a read-only fallback — this
    function never writes to it. Safe to run on every startup: once accounts
    and links exist (and bets carry account_id), all loops no-op.
    """
    from ..config import get_provider_currency
    from ..constants import UNLIMITED_PROVIDERS

    with Session(engine) as session:
        ppbs = session.query(ProfileProviderBalance).all()
        if not ppbs:
            return

        # Defensive: a freshly-added kind column may be NULL on old rows.
        session.query(Profile).filter(Profile.kind.is_(None)).update({Profile.kind: "edge"}, synchronize_session=False)

        # Truth profile for shared sharp balances: active first, else lowest id.
        truth = (
            session.query(Profile).filter(Profile.is_active.is_(True)).order_by(Profile.id).first()
            or session.query(Profile).order_by(Profile.id).first()
        )
        truth_id = truth.id if truth else None

        name_by_pid: dict[int, str] = {}

        def _label(profile_id: int) -> str:
            if profile_id not in name_by_pid:
                p = session.get(Profile, profile_id)
                name_by_pid[profile_id] = p.name if p and p.name else f"p{profile_id}"
            return name_by_pid[profile_id]

        def _label_for(ppb) -> str:
            return "rasmus" if ppb.provider_id in UNLIMITED_PROVIDERS else _label(ppb.profile_id)

        # 1) Ensure an Account exists for every (provider, label).
        for ppb in ppbs:
            prov = ppb.provider_id
            label = _label_for(ppb)
            acct = session.query(Account).filter_by(provider_id=prov, label=label).first()
            if acct is not None:
                continue
            if prov in UNLIMITED_PROVIDERS:
                truth_row = next((b for b in ppbs if b.provider_id == prov and b.profile_id == truth_id), None)
                src = truth_row or ppb
                kind = "sharp"
                # Sharp providers collapse to ONE shared account. If pre-migration
                # per-profile copies of this sharp balance had drifted apart, only
                # `src` survives — surface the discarded values rather than lose
                # them silently (they reconcile to the live shared balance after).
                distinct = {round(b.balance or 0.0, 2) for b in ppbs if b.provider_id == prov}
                if len(distinct) > 1:
                    logger.warning(
                        "account migration: %s had divergent per-profile balances %s; "
                        "collapsing to shared account at %.2f (from profile %s)",
                        prov,
                        sorted(distinct),
                        src.balance or 0.0,
                        src.profile_id,
                    )
            else:
                src = ppb
                kind = "soft"
            session.add(
                Account(
                    provider_id=prov,
                    label=label,
                    kind=kind,
                    balance=src.balance or 0.0,
                    currency=get_provider_currency(prov),
                    account_opened_at=src.account_opened_at,
                    is_active=True,
                )
            )
        session.flush()

        # 2) Ensure a ProfileAccount link exists for every balance row.
        for ppb in ppbs:
            acct = session.query(Account).filter_by(provider_id=ppb.provider_id, label=_label_for(ppb)).first()
            if acct is None:
                continue
            link = session.query(ProfileAccount).filter_by(profile_id=ppb.profile_id, account_id=acct.id).first()
            if link is None:
                session.add(ProfileAccount(profile_id=ppb.profile_id, account_id=acct.id))
        session.flush()

        # 3) Backfill bets.account_id where resolvable and currently NULL.
        resolver: dict[tuple[int, str], int] = {}
        for link in session.query(ProfileAccount).all():
            acct = session.get(Account, link.account_id)
            if acct:
                resolver[(link.profile_id, acct.provider_id)] = acct.id
        for bet in session.query(Bet).filter(Bet.account_id.is_(None), Bet.profile_id.isnot(None)).all():
            aid = resolver.get((bet.profile_id, bet.provider_id))
            if aid:
                bet.account_id = aid

        session.commit()


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
        # extraction_features: renamed dutch_opportunities_found → arb_opportunities_found
        # in code; existing prod DB has only the old name.
        ("extraction_features", "arb_opportunities_found", "INTEGER"),
        # bets.provider_bet_id: BetCreate schema accepts this since the mirror
        # workflow split (early 2026); the column was never added to Bet,
        # silently crashing every /api/bets POST with TypeError. Adding now.
        ("bets", "provider_bet_id", "VARCHAR"),
        # 2026-05-20 — arb leg linkage. Pairs the soft anchor + Polymarket
        # counter of one arbitrage so per-arb guaranteed profit is verifiable.
        ("bets", "arb_group_id", "VARCHAR"),
        # 2026-05-25 — period/structural scope dimension for canonical odds.
        # Added so the scanner can refuse to pair regulation-only with OT-inclusive
        # odds (the IIHF Slovenia v Italy false-arb bug).
        ("odds", "scope", "VARCHAR(16) NOT NULL DEFAULT 'ft'"),
        # 2026-05-26 — enhanced inversion detector flag on events. Default true
        # so historical events without verification stay visible until the next
        # extraction cycle revalidates them.
        ("events", "home_away_validated", "BOOLEAN NOT NULL DEFAULT TRUE"),
        # 2026-05-26 — opportunity-level diagnostic annotations (key_number,
        # steam_signal, consensus_lean) populated by analyzer at upsert time.
        # Read by the frontend to render per-opportunity indicator badges.
        ("opportunities", "annotations", "JSON"),
        # 2026-05-26 — period scope on opportunities. Mirrors odds.scope so
        # F5/1H/Q1 opportunities can coexist with the full-game ft row on the
        # same event/market/provider. Unique-upsert index rebuilt below.
        ("opportunities", "scope", "VARCHAR(16) NOT NULL DEFAULT 'ft'"),
        # 2026-05-28 — Pinnacle per-line max risk stake (USD). Null on
        # non-Pinnacle rows and on any row predating this column.
        ("odds", "max_stake", "DOUBLE PRECISION"),
        # 2026-05-29 — multi-book sharp blend shadow columns on opp_snapshots.
        # All nullable; frozen at detection / backfilled at close by
        # OppSnapshotService. Edge math unaffected (shadow only).
        ("opp_snapshots", "blended_fair1_at_detection", "DOUBLE PRECISION"),
        ("opp_snapshots", "blend_n_sources_at_detection", "INTEGER"),
        ("opp_snapshots", "blend_sources", "JSON"),
        ("opp_snapshots", "blended_closing_fair", "DOUBLE PRECISION"),
        ("opp_snapshots", "blended_clv_pct", "DOUBLE PRECISION"),
        # 2026-05-30 — Stats per-profile account styles. "personal" vs
        # "bonus_extraction" drives the adaptive Stats layout. Default 'personal'
        # so existing prod profiles keep the standard performance view. Without
        # this, the SQLite ALTER + Alembic 006 don't reach prod (container runs
        # uvicorn directly; create_all never ALTERs the existing profiles table).
        ("profiles", "style", "VARCHAR NOT NULL DEFAULT 'personal'"),
        # 2026-05-30 — shading-aware diagnostic columns on opp_snapshots.
        # Frozen at detection time; diagnostic only, no effect on edge/stake.
        ("opp_snapshots", "shading_risk", "VARCHAR"),
        ("opp_snapshots", "odds_bucket", "VARCHAR"),
        # 2026-05-30 — Account layer. profiles.kind drives Rule-B ROI bucketing;
        # bets.account_id attributes a bet to a real account. New tables
        # (accounts, profile_accounts) are created by create_all. The data
        # backfill from profile_provider_balances runs in
        # _migrate_provider_balances_to_accounts after this function.
        ("profiles", "kind", "VARCHAR NOT NULL DEFAULT 'edge'"),
        ("bets", "account_id", "INTEGER"),
    ]

    # Tables dropped during the 2026-05-25 strip-trading work. Idempotent —
    # DROP TABLE IF EXISTS is a no-op when the table is already gone.
    trading_tables_to_drop = [
        "broker_trades",
        "stock_signals",
        "account_snapshots",
        "shadow_predictions",
        "trading_accounts",
        "daily_routines",
        "trades",
        "trade_events",
        "trade_reviews",
        "trade_postmortems",
        "market_sessions",
        "trading_signals",
        "market_trades",
        "market_candles",
        "market_levels",
        "market_tpo_sessions",
        "market_context",
        "session_metrics",
        "ml_features",
        "candle_snapshots",
        "economic_events",
        "news_impact",
        "options_flow",
        "cot_data",
        "level_touch_outcomes",
        "level_touch_features",
    ]

    with engine.begin() as conn:
        # Drop trading tables first (CASCADE so dependent FKs go too)
        for tbl in trading_tables_to_drop:
            sp = conn.begin_nested()
            try:
                conn.execute(text(f"DROP TABLE IF EXISTS {tbl} CASCADE"))
                sp.commit()
            except Exception:
                sp.rollback()
                logger.warning("pg migration: DROP TABLE %s failed", tbl, exc_info=True)

        # Drop profile column added for TopstepX binding (no-op if missing)
        sp = conn.begin_nested()
        try:
            conn.execute(text("ALTER TABLE profiles DROP COLUMN IF EXISTS topstepx_account_id"))
            sp.commit()
        except Exception:
            sp.rollback()
            logger.warning("pg migration: DROP COLUMN profiles.topstepx_account_id failed", exc_info=True)

        # Each ALTER runs inside its own SAVEPOINT so a single failure
        # doesn't put the outer transaction into a broken state.
        for table, col, col_type in additions:
            sp = conn.begin_nested()
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}"))
                sp.commit()
            except Exception:
                sp.rollback()
                logger.warning("pg migration: %s.%s failed", table, col, exc_info=True)

        # 2026-05-25 — backfill scope on existing Pinnacle hockey period=6 rows.
        # All other rows keep the column default 'ft'. Idempotent: re-running
        # is a no-op because period=6 hockey rows will already have scope='reg'.
        sp = conn.begin_nested()
        try:
            conn.execute(
                text("""
                UPDATE odds
                SET scope = 'reg'
                WHERE provider_id = 'pinnacle'
                  AND scope = 'ft'
                  AND provider_meta::jsonb->>'period' = '6'
                  AND event_id IN (SELECT id FROM events WHERE sport = 'ice_hockey')
            """)
            )
            sp.commit()
        except Exception:
            sp.rollback()
            logger.warning("pg migration: odds.scope backfill failed", exc_info=True)

        # 2026-05-25 — rebuild unique constraint to include scope.
        # Drop old then add new; both wrapped in SAVEPOINT for safety.
        sp = conn.begin_nested()
        try:
            conn.execute(text("ALTER TABLE odds DROP CONSTRAINT IF EXISTS uq_odds_with_point_nd"))
            sp.commit()
        except Exception:
            sp.rollback()
            logger.warning("pg migration: drop uq_odds_with_point_nd failed", exc_info=True)

        sp = conn.begin_nested()
        try:
            conn.execute(
                text("""
                ALTER TABLE odds
                ADD CONSTRAINT uq_odds_with_point_scope
                UNIQUE NULLS NOT DISTINCT (event_id, provider_id, market, outcome, point, scope)
            """)
            )
            sp.commit()
        except Exception:
            sp.rollback()
            logger.warning("pg migration: add uq_odds_with_point_scope failed", exc_info=True)

        # 2026-05-25 — scanner-side join index for canonical-scope lookups.
        sp = conn.begin_nested()
        try:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_odds_event_market_point_scope "
                    "ON odds (event_id, market, point, scope)"
                )
            )
            sp.commit()
        except Exception:
            sp.rollback()
            logger.warning("pg migration: ix_odds_event_market_point_scope failed", exc_info=True)

        # Index for provider_bet_id lookups during settlement reconciliation
        sp = conn.begin_nested()
        try:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_bets_provider_bet_id ON bets(provider_bet_id)"))
            sp.commit()
        except Exception:
            sp.rollback()
            logger.warning("pg migration: bets.provider_bet_id index failed", exc_info=True)

        # 2026-05-30 — Account layer. bets.account_id is added via the `additions`
        # list above as a bare INTEGER (ADD COLUMN can't carry the FK). Add the FK
        # (ON DELETE SET NULL, matching the ORM model) + lookup index here so
        # existing Postgres DBs match a fresh create_all. Guarded: re-running is a
        # no-op once the constraint/index exist (or accounts isn't present yet).
        sp = conn.begin_nested()
        try:
            conn.execute(
                text(
                    "ALTER TABLE bets ADD CONSTRAINT fk_bets_account_id "
                    "FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE SET NULL"
                )
            )
            sp.commit()
        except Exception:
            sp.rollback()  # already exists, or accounts table not yet created
            logger.warning("pg migration: bets.account_id FK add skipped", exc_info=True)

        sp = conn.begin_nested()
        try:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_bet_account_id ON bets(account_id)"))
            sp.commit()
        except Exception:
            sp.rollback()
            logger.warning("pg migration: bets.account_id index failed", exc_info=True)

        # 2026-05-26 — opportunities upsert index rebuilt to include scope so
        # F5/1H/Q1 opportunities can coexist with the ft row on the same
        # event/market/provider. Drop old, then add new under SAVEPOINTs.
        sp = conn.begin_nested()
        try:
            conn.execute(text("DROP INDEX IF EXISTS ix_opp_upsert_unique"))
            sp.commit()
        except Exception:
            sp.rollback()
            logger.warning("pg migration: drop old ix_opp_upsert_unique failed", exc_info=True)

        sp = conn.begin_nested()
        try:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_opp_upsert_unique "
                    "ON opportunities (event_id, market, outcome1, provider1_id, type, scope)"
                )
            )
            sp.commit()
        except Exception:
            sp.rollback()
            logger.warning("pg migration: create ix_opp_upsert_unique (with scope) failed", exc_info=True)

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
