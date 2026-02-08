"""Odds repository - odds data access."""

from sqlalchemy.orm import Session

from ..db.models import Odds


class OddsRepo:
    """Data access for odds."""

    def __init__(self, db: Session):
        self.db = db

    def get_for_event(
        self,
        event_id: str,
        market: str | None = None,
        exclude_outcome: str | None = None,
        exclude_provider: str | None = None,
    ) -> list[Odds]:
        """Get odds for an event with optional filtering."""
        query = self.db.query(Odds).filter(Odds.event_id == event_id)

        if market:
            query = query.filter(Odds.market == market)
        if exclude_outcome:
            query = query.filter(Odds.outcome != exclude_outcome)
        if exclude_provider:
            query = query.filter(Odds.provider_id != exclude_provider)

        return query.all()

    def get_for_event_filtered(
        self,
        event_id: str,
        market: str,
        exclude_outcome: str,
        exclude_provider: str,
        provider_ids: list[str] | None = None,
    ) -> list[Odds]:
        """Get opposing odds for hedging (filtered by providers)."""
        query = self.db.query(Odds).filter(
            Odds.event_id == event_id,
            Odds.market == market,
            Odds.outcome != exclude_outcome,
            Odds.provider_id != exclude_provider,
        )

        if provider_ids:
            query = query.filter(Odds.provider_id.in_(provider_ids))

        return query.all()
