"""Bet service - bet recording and settlement."""

import logging
from datetime import datetime
from sqlalchemy.orm import Session

from ..repositories import ProfileRepo, BetRepo
from ..db.models import Provider, Bet

logger = logging.getLogger(__name__)


class BetService:
    """Business logic for bet recording, settlement, and balance adjustments."""

    def __init__(self, db: Session):
        self.db = db
        self.profile_repo = ProfileRepo(db)
        self.bet_repo = BetRepo(db)

    def create_bet(
        self,
        event_id: str | None,
        provider_id: str,
        market: str | None,
        outcome: str | None,
        odds: float,
        stake: float,
        is_bonus: bool = False,
        bonus_type: str | None = None,
    ) -> dict:
        """Record a placed bet for active profile."""
        profile = self.profile_repo.get_active()

        # Verify provider exists
        provider = self.db.query(Provider).filter(Provider.id == provider_id).first()
        if not provider:
            return {"error": f"Provider {provider_id} not found"}

        # Validate sufficient balance (unless free bet)
        current_balance = self.profile_repo.get_balance(profile.id, provider_id)
        if not is_bonus and current_balance < stake:
            return {
                "error": f"Insufficient balance: {current_balance:.2f} available, {stake:.2f} required"
            }

        bet = self.bet_repo.create(
            profile_id=profile.id,
            event_id=event_id,
            provider_id=provider_id,
            market=market,
            outcome=outcome,
            odds=odds,
            stake=stake,
            is_bonus=is_bonus,
            bonus_type=bonus_type,
        )

        # Deduct stake from balance (unless free bet)
        if not is_bonus:
            self.profile_repo.adjust_balance(profile.id, provider_id, -stake)

        # Record wagering progress
        wagering_status = self.profile_repo.record_wagering(profile.id, provider_id, stake, odds)

        return {
            "success": True,
            "bet_id": bet.id,
            "profile_id": profile.id,
            "bonus_wagering": wagering_status if wagering_status.get("status") == "in_progress" else None,
        }

    def settle_bet(self, bet_id: int, result: str, payout: float) -> dict:
        """Settle a bet with result."""
        bet = self.bet_repo.get_by_id(bet_id)
        if not bet:
            return {"error": f"Bet {bet_id} not found"}

        bet.result = result
        bet.payout = payout
        bet.settled_at = datetime.utcnow()

        # Add payout to balance
        if bet.profile_id and payout > 0:
            self.profile_repo.adjust_balance(bet.profile_id, bet.provider_id, payout)

        return {"success": True, "profit": bet.profit, "profile_id": bet.profile_id}
