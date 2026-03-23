import json
import pytest
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.db.models import DeferredEvent


@pytest.fixture
def deferred_session():
    """Minimal in-memory session with only deferred_events table."""
    from src.db.base import Base

    engine = create_engine("sqlite:///:memory:")
    DeferredEvent.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


def test_deferred_event_to_standard_event():
    """DeferredEvent can store and retrieve market data."""
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
        start_time=datetime(2026, 3, 25, 15, 0),
        odds_snapshot=markets,
    )
    assert de.provider_id == "betsson"
    assert de.sport == "football"
    assert de.home_team == "Arsenal"
    assert de.away_team == "Chelsea"
    assert de.odds_snapshot == markets


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
    assert result.home_team == "Arsenal"
    assert result.away_team == "Chelsea"
    assert result.status == "pending"
    snapshot = result.odds_snapshot
    # SQLite JSON column may return string or dict depending on driver
    if isinstance(snapshot, str):
        snapshot = json.loads(snapshot)
    assert snapshot[0]["type"] == "moneyline"


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
    snapshot = result.odds_snapshot
    if isinstance(snapshot, str):
        snapshot = json.loads(snapshot)
    # Should have the updated odds
    assert snapshot[0]["outcomes"][0]["odds"] == 1.90
