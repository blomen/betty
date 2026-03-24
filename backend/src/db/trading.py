"""Trading, market data, and session models."""

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean,
    ForeignKey, UniqueConstraint, Text, JSON, Index
)
from sqlalchemy.orm import relationship

from .base import Base, _utcnow


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


class TradePostmortem(Base):
    """Post-close classification for a trade. One row per closed trade."""
    __tablename__ = "trade_postmortems"

    trade_id = Column(Integer, ForeignKey("trades.id"), primary_key=True)
    classification = Column(String, nullable=False)  # expected_loss, stop_too_wide, thesis_invalid, expected_win, runner
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

    __table_args__ = (
        Index("ix_trade_pm_classification_version", "classification", "version"),
    )


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

    __table_args__ = (
        UniqueConstraint("date", "symbol", name="uq_market_session_date_symbol"),
    )


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

    __table_args__ = (
        Index("ix_market_trades_symbol_ts", "symbol", "ts"),
    )


class MarketCandle(Base):
    """Persisted OHLCV candle bars — backfilled from Databento + appended live."""
    __tablename__ = "market_candles"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False)
    interval = Column(String, nullable=False)   # "1m" | "5m" | "15m"
    ts = Column(DateTime, nullable=False)        # bucket-start UTC
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
    level_type = Column(String, nullable=False)  # "order_block", "fvg", "ledge", "single_print", "pdh", "pdl", "tokyo_high", etc.
    session = Column(String, nullable=True)  # "tokyo", "london", "ny", null
    price_low = Column(Float, nullable=False)
    price_high = Column(Float, nullable=False)  # = price_low for single-price levels
    direction = Column(String, nullable=True)  # "bullish", "bearish", null
    is_filled = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("ix_market_levels_symbol_date", "symbol", "date", "level_type"),
    )


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
    risk_mode = Column(String, nullable=True)   # "risk_on", "risk_off", "mixed"
    cycle_phase = Column(String, nullable=True) # "early", "mid", "late", "recession"
    # Gate 2: Structure
    structure = Column(String, nullable=True)     # "uptrend", "downtrend", "ranging"
    structure_hl = Column(Float, nullable=True)   # Last confirmed HL (long invalidation below)
    structure_lh = Column(Float, nullable=True)   # Last confirmed LH (short invalidation above)
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

    __table_args__ = (
        UniqueConstraint("symbol", name="uq_market_context_symbol"),
    )


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
