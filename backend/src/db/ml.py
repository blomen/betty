"""ML feature store, analytics, and level touch models."""

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean,
    ForeignKey, UniqueConstraint, Text, JSON, Index
)
from sqlalchemy.orm import relationship

from .base import Base, _utcnow


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
    domain = Column(String, nullable=False)          # e.g. "betting", "trading"
    source_id = Column(String, nullable=False)       # FK-like ref to source row
    source_type = Column(String, nullable=False)     # e.g. "opportunity", "signal"
    features = Column(JSON, nullable=False)          # serialised feature dict
    feature_version = Column(Integer, default=1)
    outcome = Column(Float, nullable=True)           # continuous label (e.g. CLV)
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
    candles = Column(JSON, nullable=False)           # list of candle dicts
    timeframe = Column(String, default="1m")
    created_at = Column(DateTime, default=_utcnow)

    signal = relationship("TradingSignal")


class EconomicEvent(Base):
    """
    Scheduled macro economic events (e.g. CPI, NFP, FOMC) with consensus data.
    """
    __tablename__ = "economic_events"
    __table_args__ = (
        Index("idx_econ_events_datetime", "event_datetime"),
    )

    id = Column(Integer, primary_key=True)
    event_name = Column(String, nullable=False)
    event_datetime = Column(DateTime, nullable=False)
    importance = Column(Integer, nullable=True)      # 1=low, 2=medium, 3=high
    forecast = Column(Float, nullable=True)
    actual = Column(Float, nullable=True)
    previous = Column(Float, nullable=True)
    surprise = Column(Float, nullable=True)          # actual - forecast
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
    __table_args__ = (
        UniqueConstraint("date", "symbol", name="idx_options_flow_date"),
    )

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
    __table_args__ = (
        UniqueConstraint("report_date", "symbol", name="idx_cot_date"),
    )

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
    dutch_opportunities_found = Column(Integer, nullable=True)
    reverse_opportunities_found = Column(Integer, nullable=True)
    total_opportunity_value = Column(Float, nullable=True)
    bets_placed_from_run = Column(Integer, nullable=True)
    avg_clv_from_run = Column(Float, nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("idx_extraction_features_run", "run_id"),
    )


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

    __table_args__ = (
        Index("idx_provider_value_run", "run_id", "provider_id"),
    )


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
    category = Column(String, nullable=False)      # match_rate, coverage, timing, roi, market_gap
    severity = Column(String, nullable=False)       # critical, warning, info
    message = Column(String, nullable=False)
    diagnostic_data = Column(JSON, nullable=True)
    status = Column(String, nullable=False, default="open")  # open, acted_on, resolved, wont_fix
    acted_on_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    before_metric = Column(Float, nullable=True)
    after_metric = Column(Float, nullable=True)
    source = Column(String, default="rules")        # rules or ml
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


class LevelTouchFeature(Base):
    __tablename__ = "level_touch_features"

    id = Column(Integer, primary_key=True)
    touch_outcome_id = Column(Integer, ForeignKey("level_touch_outcomes.id"), nullable=False)
    features = Column(Text, nullable=False)
    feature_version = Column(Integer, default=1)
    created_at = Column(Float)
