"""Bet repository - bet data access."""

from typing import Optional
from sqlalchemy.orm import Session

from ..db.models import Bet, Provider


class BetRepo:
    """Data access for bets."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, bet_id: int) -> Bet | None:
        """Get bet by ID."""
        return self.db.query(Bet).filter(Bet.id == bet_id).first()

    def get_settled(self, profile_id: int) -> list[Bet]:
        """Get settled bets for a profile."""
        return self.db.query(Bet).filter(
            Bet.result != "pending",
            Bet.profile_id == profile_id,
        ).all()

    def get_pending_for_provider(self, provider_id: str, profile_id: int) -> list[Bet]:
        """Get pending bets for a provider and profile."""
        return self.db.query(Bet).filter(
            Bet.provider_id == provider_id,
            Bet.profile_id == profile_id,
            Bet.result == "pending",
        ).all()

    def list_for_profile(
        self,
        profile_id: int,
        status: str | None = None,
        exclude_bonus: bool = False,
        limit: int = 50,
    ) -> list[Bet]:
        """List bets for a profile with optional status filter."""
        query = self.db.query(Bet).filter(Bet.profile_id == profile_id)
        if status:
            query = query.filter(Bet.result == status)
        if exclude_bonus:
            query = query.filter(Bet.is_bonus != True)
        return query.order_by(Bet.placed_at.desc()).limit(limit).all()

    def create(self, **kwargs) -> Bet:
        """Create a new bet record."""
        bet = Bet(**kwargs)
        self.db.add(bet)
        return bet
