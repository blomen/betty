"""Market data repository - data access for market sessions and trading signals."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..db.models import MarketSession, TradingSignal


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
