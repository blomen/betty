"""Market data repository - data access for market sessions and trading signals."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..db.models import MarketSession, TradingSignal, MarketTrade, MarketLevel, MarketContext, SessionMetric


class MarketRepo:
    """Data access for market session and signal tables."""

    def __init__(self, db: Session):
        self.db = db

    # ---- Sessions ----

    def get_session(self, date: str, symbol: str) -> MarketSession | None:
        return (
            self.db.query(MarketSession)
            .filter(MarketSession.date == date, MarketSession.symbol == symbol)
            .first()
        )

    def upsert_session(self, date: str, symbol: str, **kwargs) -> MarketSession:
        """Insert or update a market session."""
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
        self.db.bulk_insert_mappings(MarketTrade, trades)
        self.db.commit()

    def prune_trades(self, symbol: str, before: datetime):
        """Delete ticks older than cutoff."""
        self.db.query(MarketTrade).filter(
            MarketTrade.symbol == symbol,
            MarketTrade.ts < before,
        ).delete()
        self.db.commit()

    def get_trades(self, symbol: str, start: datetime, end: datetime) -> list[MarketTrade]:
        return self.db.query(MarketTrade).filter(
            MarketTrade.symbol == symbol,
            MarketTrade.ts >= start,
            MarketTrade.ts <= end,
        ).order_by(MarketTrade.ts).all()

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
