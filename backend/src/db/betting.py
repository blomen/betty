"""Core betting models: events, providers, odds, bets, opportunities."""

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean,
    ForeignKey, UniqueConstraint, Text, JSON, Index
)
from sqlalchemy.orm import relationship

from .base import Base, _utcnow, LimitRisk


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

    id = Column(String, primary_key=True)  # e.g. "pinnacle", "kambi"
    name = Column(String, nullable=False)
    provider_type = Column(String)  # "api" or "browser"
    is_active = Column(Boolean, default=True)
    last_extraction = Column(DateTime)
    events_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    last_error = Column(String)

    # Provider balance
    balance = Column(Float, default=0.0)
    initial_deposit = Column(Float, default=0.0)
    total_deposited = Column(Float, default=0.0)
    total_withdrawn = Column(Float, default=0.0)

    # Limit tracking
    limit_risk = Column(String, default="low")
    limit_notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    odds = relationship("Odds", back_populates="provider")


class Odds(Base):
    """
    Odds for a specific outcome from a specific provider.

    One row per (event, provider, market, outcome) combination.
    Updated in-place when new odds arrive.
    """
    __tablename__ = "odds"

    id = Column(Integer, primary_key=True)
    event_id = Column(String, ForeignKey("events.id"), nullable=False)
    provider_id = Column(String, ForeignKey("providers.id"), nullable=False)

    market = Column(String, nullable=False)   # "1x2", "moneyline", "spread", "total"
    outcome = Column(String, nullable=False)  # "home", "draw", "away", "over", "under"
    odds = Column(Float, nullable=False)
    point = Column(Float, nullable=True)      # Handicap/total line value

    # Polymarket CLOB order book
    clob_token_id = Column(Text, nullable=True)
    # Provider-specific IDs for placement
    provider_meta = Column(JSON, nullable=True)

    # Inversion detection
    is_inverted = Column(Boolean, default=False)

    extracted_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    event = relationship("Event", back_populates="odds")
    provider = relationship("Provider", back_populates="odds")

    __table_args__ = (
        UniqueConstraint("event_id", "provider_id", "market", "outcome", "point",
                        name="uq_odds_event_provider_market_outcome_point"),
        Index("ix_odds_event_market", "event_id", "market"),
        Index("ix_odds_provider", "provider_id"),
    )


class Bet(Base):
    """Manual bet tracking with full lifecycle."""
    __tablename__ = "bets"

    id = Column(Integer, primary_key=True)
    event_id = Column(String, ForeignKey("events.id"), nullable=True)
    profile_id = Column(Integer, ForeignKey("profiles.id"), nullable=True)
    provider_id = Column(String, nullable=False)  # Which bookmaker

    market = Column(String)       # "1x2", "moneyline", "spread", "total"
    outcome = Column(String)      # "home", "draw", "away", "over", "under"
    odds = Column(Float, nullable=False)
    stake = Column(Float, nullable=False)
    point = Column(Float, nullable=True)  # Spread/total line

    # Result tracking
    result = Column(String, default="pending")  # "pending", "won", "lost", "void", "half_won", "half_lost"
    payout = Column(Float, nullable=True)
    profit = Column(Float, nullable=True)

    # Closing line value (CLV)
    closing_odds = Column(Float, nullable=True)
    clv_pct = Column(Float, nullable=True)
    provider_closing_odds = Column(Float, nullable=True)
    provider_clv_pct = Column(Float, nullable=True)

    # Fair odds at time of placement (from Pinnacle devig)
    fair_odds_at_placement = Column(Float, nullable=True)

    # Value tracking
    edge_at_placement = Column(Float, nullable=True)

    # Bonus tracking
    is_bonus = Column(Boolean, default=False)
    bonus_type = Column(String, nullable=True)  # "trigger", "freebet", "deposit_match", "cashback"

    # Bet type
    bet_type = Column(String, default="value")  # "value", "dutch", "reverse", "boost"

    # Selection metadata (from risk-aware allocation)
    utility_score = Column(Float, nullable=True)
    selection_probability = Column(Float, nullable=True)

    # Boost tracking
    boost_event = Column(String, nullable=True)  # Original boost event name
    boost_title = Column(String, nullable=True)   # Boost promo title

    # Placement metadata
    confirmation_id = Column(Text, nullable=True)
    placement_status = Column(Text, default="manual")
    actual_odds_at_placement = Column(Float, nullable=True)
    placement_latency_ms = Column(Float, nullable=True)

    # Settlement
    settlement_source = Column(Text, nullable=True)

    # Event timing
    start_time = Column(DateTime, nullable=True)

    placed_at = Column(DateTime, default=_utcnow)
    settled_at = Column(DateTime, nullable=True)

    # Relationships
    event = relationship("Event", back_populates="bets")
    profile = relationship("Profile", back_populates="bets")

    __table_args__ = (
        Index("ix_bets_provider", "provider_id"),
        Index("ix_bets_result", "result"),
        Index("ix_bets_placed_at", "placed_at"),
    )


class BetTrace(Base):
    """Audit trail for bet placement — records every placement attempt and result."""
    __tablename__ = "bet_traces"

    id = Column(Integer, primary_key=True)
    bet_id = Column(Integer, ForeignKey("bets.id"), nullable=True)
    provider_id = Column(String, nullable=False)
    action = Column(String, nullable=False)  # "place", "confirm", "reject", "timeout", "error"
    details = Column(JSON, nullable=True)    # Flexible payload (odds, stake, error msg, etc.)
    created_at = Column(DateTime, default=_utcnow)

    bet = relationship("Bet")

    __table_args__ = (
        Index("ix_bet_traces_bet_id", "bet_id"),
        Index("ix_bet_traces_provider", "provider_id"),
    )


class BetPostmortem(Base):
    """Post-settlement classification for a bet. One row per settled bet."""
    __tablename__ = "bet_postmortems"

    bet_id = Column(Integer, ForeignKey("bets.id"), primary_key=True)
    classification = Column(String, nullable=False)  # expected_loss, bad_line, soft_close, edge_held, closing_edge
    edge_at_placement = Column(Float, nullable=True)
    closing_edge = Column(Float, nullable=True)
    clv_pct = Column(Float, nullable=True)
    odds_movement = Column(Float, nullable=True)
    time_to_kickoff_hrs = Column(Float, nullable=True)
    stake = Column(Float, nullable=True)
    profit = Column(Float, nullable=True)
    computed_at = Column(DateTime, default=_utcnow)
    version = Column(Integer, default=1)

    bet = relationship("Bet")

    __table_args__ = (
        Index("ix_bet_pm_classification_version", "classification", "version"),
    )


class Opportunity(Base):
    """Pre-computed betting opportunity (value, dutch, reverse, bonus)."""
    __tablename__ = "opportunities"

    id = Column(Integer, primary_key=True)
    event_id = Column(String, ForeignKey("events.id"), nullable=False)
    type = Column(String, nullable=False)  # "value", "dutch", "reverse", "bonus"
    market = Column(String, nullable=False)

    # Primary side
    outcome1 = Column(String, nullable=False)
    provider1_id = Column(String, nullable=False)
    odds1 = Column(Float, nullable=False)
    point1 = Column(Float, nullable=True)

    # Counter side (for arb/dutch) or Pinnacle fair odds
    outcome2 = Column(String, nullable=True)
    provider2_id = Column(String, nullable=True)
    odds2 = Column(Float, nullable=True)
    point2 = Column(Float, nullable=True)

    # Value metrics
    edge_pct = Column(Float, nullable=True)
    margin = Column(Float, nullable=True)
    fair_odds = Column(Float, nullable=True)

    # Kelly / stake suggestion
    kelly_fraction = Column(Float, nullable=True)
    recommended_stake = Column(Float, nullable=True)

    # State
    is_active = Column(Boolean, default=True)
    found_at = Column(DateTime, default=_utcnow)
    expired_at = Column(DateTime, nullable=True)

    # Relationships
    event = relationship("Event")

    __table_args__ = (
        UniqueConstraint("event_id", "market", "outcome1", "provider1_id", "type",
                        name="ix_opp_upsert_unique"),
        Index("ix_opportunities_active", "is_active", "type"),
    )


class DeferredEvent(Base):
    """Soft-provider event that couldn't be matched at extraction time.

    Stored so the matcher can retry once more Pinnacle events arrive.
    """
    __tablename__ = "deferred_events"

    id = Column(Integer, primary_key=True)

    # Provider info
    provider_id = Column(String, nullable=False)
    sport = Column(String, nullable=False)
    league = Column(String, nullable=True)
    home_team = Column(String, nullable=False)
    away_team = Column(String, nullable=False)
    start_time = Column(DateTime, nullable=True)

    # Raw odds snapshot (JSON list of {market, outcome, odds, point?})
    odds_snapshot = Column(JSON, nullable=False)

    # Lifecycle
    status = Column(String, default="pending")  # pending | matched | expired
    matched_event_id = Column(String, nullable=True)
    retry_count = Column(Integer, default=0)

    created_at = Column(DateTime, default=_utcnow)
    expires_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_deferred_status", "status"),
        Index("ix_deferred_provider_sport", "provider_id", "sport"),
    )
