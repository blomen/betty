"""Trading repository - data access for trading models."""

from datetime import date
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from ..db.models import TradingAccount, DailyRoutine, Trade, TradeEvent, TradeReview


class TradingRepo:
    """Data access for all trading tables."""

    def __init__(self, db: Session):
        self.db = db

    # ---- Trading Accounts ----

    def list_accounts(self) -> list[TradingAccount]:
        return self.db.query(TradingAccount).order_by(TradingAccount.id).all()

    def get_account(self, account_id: int) -> TradingAccount | None:
        return self.db.query(TradingAccount).filter(TradingAccount.id == account_id).first()

    def create_account(self, **kwargs) -> TradingAccount:
        acct = TradingAccount(**kwargs)
        self.db.add(acct)
        return acct

    # ---- Daily Routines ----

    def get_routine_by_date(self, d: str) -> DailyRoutine | None:
        return self.db.query(DailyRoutine).filter(DailyRoutine.date == d).first()

    def create_routine(self, d: str) -> DailyRoutine:
        routine = DailyRoutine(date=d)
        self.db.add(routine)
        return routine

    # ---- Trades ----

    def get_trade(self, trade_id: int) -> Trade | None:
        return (
            self.db.query(Trade)
            .options(
                joinedload(Trade.events),
                joinedload(Trade.review),
                joinedload(Trade.account),
            )
            .filter(Trade.id == trade_id)
            .first()
        )

    def list_trades(
        self,
        account_id: int | None = None,
        instrument: str | None = None,
        setup_type: str | None = None,
        state: str | None = None,
        limit: int = 200,
    ) -> list[Trade]:
        q = self.db.query(Trade)
        if account_id is not None:
            q = q.filter(Trade.account_id == account_id)
        if instrument:
            q = q.filter(Trade.instrument == instrument)
        if setup_type:
            q = q.filter(Trade.setup_type == setup_type)
        if state:
            q = q.filter(Trade.state == state)
        return q.order_by(Trade.created_at.desc()).limit(limit).all()

    def create_trade(self, **kwargs) -> Trade:
        trade = Trade(**kwargs)
        self.db.add(trade)
        return trade

    # ---- Trade Events ----

    def add_event(self, **kwargs) -> TradeEvent:
        evt = TradeEvent(**kwargs)
        self.db.add(evt)
        return evt

    # ---- Trade Reviews ----

    def get_review(self, trade_id: int) -> TradeReview | None:
        return self.db.query(TradeReview).filter(TradeReview.trade_id == trade_id).first()

    def create_review(self, **kwargs) -> TradeReview:
        review = TradeReview(**kwargs)
        self.db.add(review)
        return review

    def get_unreviewed_trades(self) -> list[Trade]:
        """Get closed trades without a review."""
        reviewed_ids = self.db.query(TradeReview.trade_id).subquery()
        return (
            self.db.query(Trade)
            .filter(Trade.state == "closed", ~Trade.id.in_(reviewed_ids))
            .order_by(Trade.closed_at.desc())
            .all()
        )
