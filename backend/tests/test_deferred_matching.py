import json
import pytest
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.db.models import Base, DeferredEvent


@pytest.fixture
def deferred_session():
    """Minimal in-memory session with deferred_events table."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


def test_deferred_event_to_standard_event():
    """DeferredEvent.to_standard_event() reconstructs a valid StandardEvent."""
    markets = [
        {"type": "moneyline", "outcomes": [
            {"name": "home", "odds": 1.85},
            {"name": "away", "odds": 2.05},
        ]}
    ]
    de = DeferredEvent(
        provider_id="betsson",
        sport="football",
        league="Premier League",
        home_team="Arsenal",
        away_team="Chelsea",
        normalized_home="arsenal",
        normalized_away="chelsea",
        start_time=datetime(2026, 3, 25, 15, 0),
        markets_json=json.dumps(markets),
    )
    event = de.to_standard_event()
    assert event.sport == "football"
    assert event.home_team == "Arsenal"
    assert event.away_team == "Chelsea"
    assert event.markets == markets
    assert event.provider == "betsson"
    assert event._from_deferred is True


def test_store_deferred_event_creates_record(deferred_session):
    """Unmatched event is stored in deferred_events table."""
    from src.pipeline.storage import _store_deferred_event
    from src.core.retriever import StandardEvent

    event = StandardEvent(
        id="", name="Arsenal vs Chelsea", sport="football",
        markets=[{"type": "moneyline", "outcomes": [{"name": "home", "odds": 1.85}]}],
        provider="betsson",
        start_time="2026-03-25T15:00:00",
        home_team="Arsenal", away_team="Chelsea", league="PL",
    )
    _store_deferred_event(deferred_session, event, "betsson")
    deferred_session.commit()

    result = deferred_session.query(DeferredEvent).one()
    assert result.provider_id == "betsson"
    assert result.sport == "football"
    assert result.normalized_home == "arsenal"
    assert result.normalized_away == "chelsea"
    assert "moneyline" in result.markets_json


def test_store_deferred_event_upserts_on_duplicate(deferred_session):
    """Re-extracting same event updates odds, doesn't duplicate."""
    from src.pipeline.storage import _store_deferred_event
    from src.core.retriever import StandardEvent

    event1 = StandardEvent(
        id="", name="Arsenal vs Chelsea", sport="football",
        markets=[{"type": "moneyline", "outcomes": [{"name": "home", "odds": 1.85}]}],
        provider="betsson", start_time="2026-03-25T15:00:00",
        home_team="Arsenal", away_team="Chelsea", league="PL",
    )
    event2 = StandardEvent(
        id="", name="Arsenal vs Chelsea", sport="football",
        markets=[{"type": "moneyline", "outcomes": [{"name": "home", "odds": 1.90}]}],
        provider="betsson", start_time="2026-03-25T15:00:00",
        home_team="Arsenal", away_team="Chelsea", league="PL",
    )
    _store_deferred_event(deferred_session, event1, "betsson")
    deferred_session.commit()
    _store_deferred_event(deferred_session, event2, "betsson")
    deferred_session.commit()

    assert deferred_session.query(DeferredEvent).count() == 1
    result = deferred_session.query(DeferredEvent).one()
    assert "1.9" in result.markets_json
