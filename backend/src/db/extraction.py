"""Extraction pipeline, specials, and boost models."""

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean,
    ForeignKey, UniqueConstraint, Text, JSON, Index
)
from sqlalchemy.orm import relationship

from .base import Base, _utcnow


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
    llm_title = Column(String, nullable=True)         # Simplified English title
    llm_probability = Column(Float, nullable=True)    # 0.01-0.99
    llm_fair_odds = Column(Float, nullable=True)      # 1 / llm_probability
    llm_edge_pct = Column(Float, nullable=True)       # (boosted / llm_fair - 1) * 100
    llm_reasoning = Column(Text, nullable=True)       # AI reasoning text
    llm_confidence = Column(String, nullable=True)    # "low", "medium", "high"

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

    cache_key = Column(String, primary_key=True)    # md5 hash

    # Original boost identity (for debugging / human lookup)
    title = Column(String, nullable=False)
    boosted_odds = Column(Float, nullable=False)

    # LLM research results
    llm_title = Column(String, nullable=True)
    llm_probability = Column(Float, nullable=False)
    llm_fair_odds = Column(Float, nullable=True)
    llm_confidence = Column(String, default="low")
    llm_reasoning = Column(Text, nullable=True)
    llm_event_time = Column(String, nullable=True)     # ISO datetime — event start time from LLM

    # Metadata
    created_at = Column(String, nullable=False)       # ISO datetime
    last_used_at = Column(String, nullable=False)      # ISO datetime — updated on carry-forward
