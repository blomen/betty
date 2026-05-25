"""
Postmortem Service — orchestrates classification, stores results.
Triggered inline after settlement or via manual recompute endpoint.
"""

import logging
import threading

from sqlalchemy.orm import Session

from ..analysis.postmortem import CURRENT_ALGO_VERSION, classify_bet
from ..db.models import Bet
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

    def recompute_all_bets(self, profile_id: int) -> int:
        """Recompute postmortems for all uncomputed/outdated bets in a profile."""
        bets = self.repo.get_uncomputed_bets(profile_id, CURRENT_ALGO_VERSION)
        count = 0
        for bet in bets:
            if self.compute_bet(bet):
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
