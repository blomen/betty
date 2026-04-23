"""
Postmortem Service — orchestrates classification, stores results.
Triggered inline after settlement or via manual recompute endpoint.
"""

import logging
import threading

from sqlalchemy.orm import Session

from ..analysis.postmortem import CURRENT_ALGO_VERSION, classify_bet, classify_trade
from ..db.models import Bet, DailyRoutine, Trade, TradeEvent
from ..repositories.postmortem_repo import PostmortemRepo

logger = logging.getLogger(__name__)

_recompute_lock = threading.Lock()


class PostmortemService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = PostmortemRepo(db)

    def compute_bet(self, bet: Bet) -> dict | None:
        """Compute and store postmortem for a single settled bet. Returns None on skip/failure."""
        if bet.result not in ("won", "lost"):
            return None
        try:
            bankroll = bet.profile.bankroll if bet.profile else None
            fields = classify_bet(bet, profile_bankroll=bankroll)
            self.repo.upsert_bet_pm(bet.id, **fields)
            return fields
        except Exception as e:
            logger.warning(f"Postmortem failed for bet {bet.id}: {e}")
            return None

    def compute_trade(self, trade: Trade) -> dict | None:
        """Compute and store postmortem for a single closed trade."""
        if trade.state not in ("closed", "reviewed"):
            return None
        try:
            # Gather all trades for same setup type + account
            setup_trades = (
                self.db.query(Trade)
                .filter(Trade.setup_type == trade.setup_type, Trade.account_id == trade.account_id)
                .all()
            )

            # Compute streak position (count consecutive losses before this trade)
            recent_trades = (
                self.db.query(Trade)
                .filter(
                    Trade.account_id == trade.account_id,
                    Trade.state.in_(["closed", "reviewed"]),
                    Trade.closed_at < trade.closed_at,
                )
                .order_by(Trade.closed_at.desc())
                .limit(20)
                .all()
            )

            streak = 0
            for t in recent_trades:
                if t.r_multiple is not None and t.r_multiple < 0:
                    streak -= 1
                else:
                    break

            # Get routine and events
            routine = None
            if trade.daily_routine_id:
                routine = self.db.query(DailyRoutine).filter(DailyRoutine.id == trade.daily_routine_id).first()
            events = self.db.query(TradeEvent).filter(TradeEvent.trade_id == trade.id).all()

            fields = classify_trade(trade, setup_trades, streak, routine, events)
            self.repo.upsert_trade_pm(trade.id, **fields)
            return fields
        except Exception as e:
            logger.warning(f"Postmortem failed for trade {trade.id}: {e}")
            return None

    def recompute_all_bets(self, profile_id: int) -> int:
        """Recompute postmortems for all uncomputed/outdated bets in a profile."""
        bets = self.repo.get_uncomputed_bets(profile_id, CURRENT_ALGO_VERSION)
        count = 0
        for bet in bets:
            if self.compute_bet(bet):
                count += 1
        self.db.commit()
        return count

    def recompute_all_trades(self, account_id: int) -> int:
        """Recompute postmortems for all uncomputed/outdated trades in an account."""
        trades = self.repo.get_uncomputed_trades(account_id, CURRENT_ALGO_VERSION)
        count = 0
        for trade in trades:
            if self.compute_trade(trade):
                count += 1
        self.db.commit()
        return count

    @staticmethod
    def try_acquire_recompute_lock() -> bool:
        """Try to acquire the recompute lock (non-blocking). Returns True if acquired."""
        return _recompute_lock.acquire(blocking=False)

    @staticmethod
    def release_recompute_lock():
        """Release the recompute lock."""
        _recompute_lock.release()
