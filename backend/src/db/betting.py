"""Core betting models: events, providers, odds, bets, opportunities."""

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean,
    ForeignKey, UniqueConstraint, Text, JSON, Index
)
from sqlalchemy.orm import relationship

from .base import Base, _utcnow


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
    home_score = Column(Integer, nullable=True)   # Current/final home score
    away_score = Column(Integer, nullable=True)   # Current/final away score
    match_status = Column(String, nullable=True)  # "prematch", "live", "finished"
    match_minute = Column(Integer, nullable=True)  # Current match minute
    match_period = Column(Integer, nullable=True)  # Period ID (1=1st half, 2=2nd half, etc.)
    stats_json = Column(Text, nullable=True)       # JSON blob: corners, cards, scoreByQuarter, etc.

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

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

    # Limit risk (global — how aggressively this provider limits winners)
    limit_risk = Column(String, default="low")      # LimitRisk enum value
    limit_notes = Column(Text, nullable=True)        # Free-form context

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

    market = Column(String, nullable=False)     # "1x2", "moneyline"
    outcome = Column(String, nullable=False)    # "home", "away", "draw"
    odds = Column(Float, nullable=False)        # Decimal odds (e.g., 2.10)
    point = Column(Float, nullable=True)        # Spread/total point value (e.g., -1.5, 2.5)
    clob_token_id = Column(String, nullable=True)  # Legacy — no longer populated
    provider_meta = Column(JSON, nullable=True)  # Provider-specific IDs: {"event_id": "...", "betoffer_id": "...", "outcome_id": "..."}
    bid = Column(Float, nullable=True)        # Best bid price (probability 0-1, CLOB only)
    ask = Column(Float, nullable=True)        # Best ask price (probability 0-1, CLOB only)
    depth_usd = Column(Float, nullable=True)  # Total ask-side depth in USD (CLOB only)

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
    bet_type = Column(String, nullable=True)    # "value", "arb", "reverse", "polymarket", "boost", "mirror"

    # Stake (in native currency: SEK for Swedish providers, USD for Polymarket)
    stake = Column(Float, nullable=False)       # 100.00
    currency = Column(String, default="SEK")    # "SEK" or "USD" — determines stake/payout units

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
    start_time = Column(DateTime, nullable=True)        # Event start time (persisted at placement)

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

    # Bet confirmation
    confirmation_id = Column(String, nullable=True)          # Provider's bet reference
    placement_status = Column(String, default="manual")      # Legacy — always "manual"
    actual_odds_at_placement = Column(Float, nullable=True)  # Odds user confirmed at placement
    placement_latency_ms = Column(Float, nullable=True)      # Legacy — no longer populated

    # Edge tracking (filled at bet entry)
    fair_odds_at_placement = Column(Float, nullable=True)  # De-vigged Pinnacle fair odds when bet placed

    # Boost metadata (filled at placement for boost bets)
    boost_event = Column(String, nullable=True)   # "Arsenal vs Sunderland" — event name at placement
    boost_title = Column(String, nullable=True)    # LLM-simplified English title at placement

    # CLV tracking (filled post-event)
    closing_odds = Column(Float, nullable=True)       # Odds at event start
    clv_pct = Column(Float, nullable=True)            # Closing line value %

    # Provider-specific CLV (e.g., Polymarket closing price — true same-market CLV)
    provider_closing_odds = Column(Float, nullable=True)  # Same-provider odds at event start
    provider_clv_pct = Column(Float, nullable=True)       # (bet.odds / provider_closing_odds - 1) * 100

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


class BetTrace(Base):
    """Raw API trace from intercepted bet placement. Append-only."""
    __tablename__ = "bet_traces"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False)
    provider_id = Column(String, nullable=False)
    request_url = Column(String, nullable=False)
    request_body = Column(String, nullable=True)   # JSON string
    response_body = Column(String, nullable=True)   # JSON string
    bet_id = Column(Integer, ForeignKey("bets.id"), nullable=True)
    provider_bet_id = Column(String, nullable=True, index=True)
    parse_status = Column(String, nullable=False)  # "ok", "failed", "unmatched", "rejected"

    bet = relationship("Bet", backref="traces")


class BetPostmortem(Base):
    """Post-settlement classification for a bet. One row per settled bet."""
    __tablename__ = "bet_postmortems"

    bet_id = Column(Integer, ForeignKey("bets.id"), primary_key=True)
    classification = Column(String, nullable=False)  # expected_loss, edge_erosion, false_edge, sizing_error, expected_win, bonus_win
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

    __table_args__ = (
        Index("ix_bet_pm_classification_version", "classification", "version"),
    )


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
    edge_pct = Column(Float, nullable=True)     # For value bets

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


# ============ Deferred Events ============

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
            "provider_id", "sport", "normalized_home", "normalized_away", "start_time",
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
