"""Market data repository - data access for market sessions and trading signals."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..db.models import MarketSession, TradingSignal, MarketTrade, MarketLevel, MarketContext, SessionMetric, MarketCandle


class MarketRepo:
    """Data access for market session and signal tables.

    Uses the main DB (self.db) for sessions, signals, levels, context, metrics.
    Uses a separate market DB (self._market_db) for high-frequency tick/candle data
    to avoid SQLite write-lock contention with extraction.
    """

    def __init__(self, db: Session, market_db: Session | None = None):
        self.db = db
        self._explicit_market_db = market_db
        self._lazy_market_db: Session | None = None

    @property
    def market_db(self) -> Session:
        """Session for market.db (ticks + candles). Falls back to main DB if unavailable."""
        if self._explicit_market_db is not None:
            return self._explicit_market_db
        if self._lazy_market_db is None:
            try:
                from ..db.models import get_market_session
                self._lazy_market_db = get_market_session()
            except Exception:
                return self.db  # Fallback to main DB
        return self._lazy_market_db

    def close_market_db(self):
        """Close the lazily-created market session if any."""
        if self._lazy_market_db is not None:
            self._lazy_market_db.close()
            self._lazy_market_db = None

    # ---- Sessions ----

    def get_session(self, date: str, symbol: str) -> MarketSession | None:
        return (
            self.db.query(MarketSession)
            .filter(MarketSession.date == date, MarketSession.symbol == symbol)
            .first()
        )

    @staticmethod
    def _sanitize_numpy(val):
        """Convert numpy scalars to native Python types for PostgreSQL."""
        if hasattr(val, 'item'):  # np.float64, np.int64, etc.
            return val.item()
        return val

    def upsert_session(self, date: str, symbol: str, **kwargs) -> MarketSession:
        """Insert or update a market session."""
        kwargs = {k: self._sanitize_numpy(v) for k, v in kwargs.items()}
        existing = self.get_session(date, symbol)
        if existing:
            for k, v in kwargs.items():
                setattr(existing, k, v)
            existing.updated_at = datetime.now(timezone.utc)
            return existing

        session = MarketSession(date=date, symbol=symbol, **kwargs)
        self.db.add(session)
        self.db.flush()
        return session

    def get_previous_session(self, symbol: str, before_date: str | None = None) -> MarketSession | None:
        """Get the most recent session before a given date (or the latest overall)."""
        q = self.db.query(MarketSession).filter(MarketSession.symbol == symbol)
        if before_date:
            q = q.filter(MarketSession.date < before_date)
        return q.order_by(MarketSession.date.desc()).first()

    def list_sessions(self, symbol: str, limit: int = 30) -> list[MarketSession]:
        return (
            self.db.query(MarketSession)
            .filter(MarketSession.symbol == symbol)
            .order_by(MarketSession.date.desc())
            .limit(limit)
            .all()
        )

    # ---- Signals ----

    def get_active_signals(self, symbol: str | None = None) -> list[TradingSignal]:
        q = self.db.query(TradingSignal).filter(TradingSignal.is_active == True)
        if symbol:
            q = q.join(MarketSession).filter(MarketSession.symbol == symbol)
        return q.order_by(TradingSignal.score.desc()).all()

    def create_signal(self, **kwargs) -> TradingSignal:
        signal = TradingSignal(**kwargs)
        self.db.add(signal)
        self.db.flush()
        return signal

    def expire_old_signals(self, max_age_minutes: int = 60) -> int:
        """Expire signals older than max_age_minutes."""
        cutoff = datetime.now(timezone.utc).timestamp() - (max_age_minutes * 60)
        cutoff_dt = datetime.fromtimestamp(cutoff, tz=timezone.utc)

        count = (
            self.db.query(TradingSignal)
            .filter(
                TradingSignal.is_active == True,
                TradingSignal.triggered_at < cutoff_dt,
            )
            .update({
                TradingSignal.is_active: False,
                TradingSignal.expired_at: datetime.now(timezone.utc),
            })
        )
        return count

    def link_signal_to_trade(self, signal_id: int, trade_id: int) -> None:
        signal = self.db.query(TradingSignal).filter(TradingSignal.id == signal_id).first()
        if signal:
            signal.trade_id = trade_id
            signal.is_active = False

    # ---- MarketTrade ----

    def bulk_insert_trades(self, trades: list[dict]):
        """Insert batch of ticks. trades = [{symbol, ts, price, size, side}, ...]"""
        self.market_db.bulk_insert_mappings(MarketTrade, trades)
        self.market_db.commit()

    def prune_trades(self, symbol: str, before: datetime):
        """Delete ticks older than cutoff."""
        self.market_db.query(MarketTrade).filter(
            MarketTrade.symbol == symbol,
            MarketTrade.ts < before,
        ).delete()
        self.market_db.commit()

    def get_trades(self, symbol: str, start: datetime, end: datetime) -> list[MarketTrade]:
        return self.market_db.query(MarketTrade).filter(
            MarketTrade.symbol == symbol,
            MarketTrade.ts >= start,
            MarketTrade.ts <= end,
        ).order_by(MarketTrade.ts).all()

    # ---- MarketCandle ----

    def get_candles(self, symbol: str, interval: str, start: datetime, end: datetime) -> list[MarketCandle]:
        return self.market_db.query(MarketCandle).filter(
            MarketCandle.symbol == symbol,
            MarketCandle.interval == interval,
            MarketCandle.ts >= start,
            MarketCandle.ts <= end,
        ).order_by(MarketCandle.ts).all()

    def get_latest_candle(self, symbol: str, interval: str) -> MarketCandle | None:
        return (
            self.market_db.query(MarketCandle)
            .filter_by(symbol=symbol, interval=interval)
            .order_by(MarketCandle.ts.desc())
            .first()
        )

    def get_oldest_candle(self, symbol: str, interval: str) -> MarketCandle | None:
        return (
            self.market_db.query(MarketCandle)
            .filter_by(symbol=symbol, interval=interval)
            .order_by(MarketCandle.ts.asc())
            .first()
        )

    def upsert_candle(self, symbol: str, interval: str, ts: datetime, o: float, h: float, l: float, c: float, v: int):
        """Insert or replace a single candle (used for live closed-candle writes)."""
        row = self.market_db.query(MarketCandle).filter_by(symbol=symbol, interval=interval, ts=ts).first()
        if row:
            row.o = o
            row.h = h
            row.l = l
            row.c = c
            row.v = v
        else:
            self.market_db.add(MarketCandle(symbol=symbol, interval=interval, ts=ts, o=o, h=h, l=l, c=c, v=v))
        self.market_db.commit()

    def bulk_insert_candles(self, symbol: str, interval: str, bars: list) -> int:
        """Insert bars from Databento backfill, skipping timestamps that already exist.

        bars: list of BarData objects with .timestamp, .open, .high, .low, .close, .volume
        Returns number of rows inserted.
        """
        if not bars:
            return 0
        start, end = bars[0].timestamp, bars[-1].timestamp
        # Normalize existing timestamps to naive UTC for comparison
        # (SQLite stores naive, Databento returns tz-aware UTC)
        existing = {
            row.ts.replace(tzinfo=None) if row.ts.tzinfo else row.ts
            for row in self.market_db.query(MarketCandle.ts).filter(
                MarketCandle.symbol == symbol,
                MarketCandle.interval == interval,
                MarketCandle.ts >= start,
                MarketCandle.ts <= end,
            ).all()
        }
        new_rows = [
            MarketCandle(symbol=symbol, interval=interval, ts=b.timestamp, o=b.open, h=b.high, l=b.low, c=b.close, v=b.volume)
            for b in bars
            if (b.timestamp.replace(tzinfo=None) if b.timestamp.tzinfo else b.timestamp) not in existing
        ]
        if new_rows:
            self.market_db.bulk_save_objects(new_rows)
            self.market_db.commit()
        return len(new_rows)

    # ---- MarketLevel ----

    def upsert_levels(self, symbol: str, date: str, levels: list[dict]):
        """Replace all levels for a session date."""
        self.db.query(MarketLevel).filter(
            MarketLevel.symbol == symbol,
            MarketLevel.date == date,
        ).delete()
        for lv in levels:
            lv["symbol"] = symbol
            lv["date"] = date
        self.db.bulk_insert_mappings(MarketLevel, levels)
        self.db.commit()

    def get_levels(self, symbol: str, date: str) -> list[MarketLevel]:
        return self.db.query(MarketLevel).filter(
            MarketLevel.symbol == symbol,
            MarketLevel.date == date,
        ).all()

    # ---- MarketContext ----

    def get_context(self, symbol: str) -> MarketContext | None:
        return self.db.query(MarketContext).filter(
            MarketContext.symbol == symbol,
        ).first()

    def upsert_context(self, symbol: str, data: dict):
        """Create or update context for a symbol."""
        ctx = self.get_context(symbol)
        if ctx:
            for k, v in data.items():
                if hasattr(ctx, k):
                    setattr(ctx, k, v)
        else:
            ctx = MarketContext(symbol=symbol, **data)
            self.db.add(ctx)
        self.db.commit()
        return ctx

    # ---- SessionMetric ----

    def upsert_session_metric(self, symbol: str, date: str, rf: int, aspr: float):
        """Insert or update session metric for ASPR/RF baselines."""
        existing = self.db.query(SessionMetric).filter(
            SessionMetric.symbol == symbol,
            SessionMetric.date == date,
        ).first()
        if existing:
            existing.rotation_factor = rf
            existing.aspr = aspr
        else:
            self.db.add(SessionMetric(symbol=symbol, date=date, rotation_factor=rf, aspr=aspr))
        self.db.commit()

    def get_historical_asprs(self, symbol: str, limit: int = 20) -> list[float]:
        """Get recent ASPR values for percentile computation."""
        rows = self.db.query(SessionMetric.aspr).filter(
            SessionMetric.symbol == symbol,
            SessionMetric.aspr.isnot(None),
        ).order_by(SessionMetric.date.desc()).limit(limit).all()
        return [r[0] for r in rows]

    def get_historical_ib_ranges(self, symbol: str, limit: int = 20) -> list[float]:
        """Get recent IB ranges for percentile computation."""
        rows = self.db.query(MarketSession.ib_range).filter(
            MarketSession.symbol == symbol,
            MarketSession.ib_range.isnot(None),
            MarketSession.ib_range > 0,
        ).order_by(MarketSession.date.desc()).limit(limit).all()
        return [r[0] for r in rows]

    def get_recent_sessions(self, symbol: str, days: int = 5) -> list:
        """Return last N sessions for composite VA computation."""
        return (
            self.db.query(MarketSession)
            .filter(
                MarketSession.symbol == symbol,
                MarketSession.vah.isnot(None),
                MarketSession.val.isnot(None),
            )
            .order_by(MarketSession.date.desc())
            .limit(days)
            .all()
        )
