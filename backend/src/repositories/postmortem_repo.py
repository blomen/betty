"""Postmortem repository — data access for postmortem tables."""

from sqlalchemy.orm import Session, joinedload

from ..db.models import Bet, Trade, BetPostmortem, TradePostmortem, _utcnow


class PostmortemRepo:
    """Data access for bet and trade postmortems."""

    def __init__(self, db: Session):
        self.db = db

    # ── Bet Postmortems ──

    def get_bet_pm(self, bet_id: int) -> BetPostmortem | None:
        return self.db.query(BetPostmortem).filter(BetPostmortem.bet_id == bet_id).first()

    def upsert_bet_pm(self, bet_id: int, **kwargs) -> BetPostmortem:
        """Create or update a bet postmortem."""
        existing = self.get_bet_pm(bet_id)
        if existing:
            for k, v in kwargs.items():
                setattr(existing, k, v)
            existing.version += 1
            existing.computed_at = _utcnow()
            return existing
        pm = BetPostmortem(bet_id=bet_id, **kwargs)
        self.db.add(pm)
        return pm

    def get_bet_pms_for_profile(self, profile_id: int) -> list[tuple[Bet, BetPostmortem]]:
        """Get all postmortems for a profile (joined with Bet + Event to avoid N+1)."""
        return (
            self.db.query(Bet, BetPostmortem)
            .join(BetPostmortem, Bet.id == BetPostmortem.bet_id)
            .options(joinedload(Bet.event))
            .filter(Bet.profile_id == profile_id)
            .order_by(Bet.placed_at.desc())
            .all()
        )

    def get_uncomputed_bets(self, profile_id: int, algo_version: int) -> list[Bet]:
        """Get settled bets missing postmortem or with outdated version."""
        computed_ids = (
            self.db.query(BetPostmortem.bet_id)
            .filter(BetPostmortem.version >= algo_version)
            .subquery()
        )
        return (
            self.db.query(Bet)
            .filter(
                Bet.profile_id == profile_id,
                Bet.result.in_(["won", "lost"]),
                ~Bet.id.in_(self.db.query(computed_ids))
            )
            .all()
        )

    # ── Trade Postmortems ──

    def get_trade_pm(self, trade_id: int) -> TradePostmortem | None:
        return self.db.query(TradePostmortem).filter(TradePostmortem.trade_id == trade_id).first()

    def upsert_trade_pm(self, trade_id: int, **kwargs) -> TradePostmortem:
        """Create or update a trade postmortem."""
        existing = self.get_trade_pm(trade_id)
        if existing:
            for k, v in kwargs.items():
                setattr(existing, k, v)
            existing.version += 1
            existing.computed_at = _utcnow()
            return existing
        pm = TradePostmortem(trade_id=trade_id, **kwargs)
        self.db.add(pm)
        return pm

    def get_trade_pms_for_account(self, account_id: int) -> list[tuple[Trade, TradePostmortem]]:
        """Get all postmortems for a trading account (joined with Trade)."""
        return (
            self.db.query(Trade, TradePostmortem)
            .join(TradePostmortem, Trade.id == TradePostmortem.trade_id)
            .filter(Trade.account_id == account_id)
            .order_by(Trade.closed_at.desc())
            .all()
        )

    def get_uncomputed_trades(self, account_id: int, algo_version: int) -> list[Trade]:
        """Get closed trades missing postmortem or with outdated version."""
        computed_ids = (
            self.db.query(TradePostmortem.trade_id)
            .filter(TradePostmortem.version >= algo_version)
            .subquery()
        )
        return (
            self.db.query(Trade)
            .filter(
                Trade.account_id == account_id,
                Trade.state.in_(["closed", "reviewed"]),
                ~Trade.id.in_(self.db.query(computed_ids))
            )
            .all()
        )
