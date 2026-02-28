"""Event repository - event data access."""

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from ..db.models import Event, Odds


class EventRepo:
    """Data access for events."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, event_id: str) -> Event | None:
        """Get event by canonical ID."""
        return self.db.query(Event).filter(Event.id == event_id).first()

    def get_by_ids(self, event_ids: list[str]) -> dict[str, Event]:
        """Get multiple events by ID. Returns {id: Event} dict."""
        if not event_ids:
            return {}
        events = self.db.query(Event).filter(Event.id.in_(event_ids)).all()
        return {e.id: e for e in events}

    def get_multi_provider_events(self, min_providers: int = 2) -> list[Event]:
        """Get events with odds from N+ distinct providers, eager-loading odds."""
        event_ids = (
            self.db.query(Event.id)
            .join(Odds)
            .group_by(Event.id)
            .having(func.count(func.distinct(Odds.provider_id)) >= min_providers)
            .all()
        )
        ids = [eid for (eid,) in event_ids]
        if not ids:
            return []
        return (
            self.db.query(Event)
            .options(joinedload(Event.odds))
            .filter(Event.id.in_(ids))
            .all()
        )

    def get_events_with_provider(self, provider_id: str) -> list[Event]:
        """Get events where a specific provider has odds, eager-loading odds."""
        event_ids = (
            self.db.query(Event.id)
            .join(Odds)
            .filter(Odds.provider_id == provider_id)
            .distinct()
            .all()
        )
        ids = [eid for (eid,) in event_ids]
        if not ids:
            return []
        return (
            self.db.query(Event)
            .options(joinedload(Event.odds))
            .filter(Event.id.in_(ids))
            .all()
        )
