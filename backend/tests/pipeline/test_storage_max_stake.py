"""OddsBatchProcessor persists max_stake on Odds rows and updates it on conflict."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Event, Odds, Provider
from src.pipeline.storage import upsert_odds


@pytest.fixture
def db():
    # Postgres-only ON CONFLICT path — use the real prod DB via DATABASE_URL,
    # or skip when Postgres isn't available. For unit-test coverage of the
    # column itself, the individual upsert_odds path works on sqlite.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Provider(id="pinnacle", name="Pinnacle", is_enabled=True))
    session.add(Event(id="evt1", sport="ice_hockey", home_team="A", away_team="B"))
    session.commit()
    yield session
    session.close()


def test_upsert_odds_writes_max_stake(db):
    upsert_odds(
        db,
        event_id="evt1",
        provider="pinnacle",
        market="moneyline",
        outcome="home",
        odds=2.10,
        max_stake=1500.0,
    )
    db.commit()
    row = db.query(Odds).filter_by(event_id="evt1", outcome="home").one()
    assert row.max_stake == 1500.0


def test_upsert_odds_updates_max_stake_on_conflict(db):
    upsert_odds(
        db,
        event_id="evt1",
        provider="pinnacle",
        market="moneyline",
        outcome="home",
        odds=2.10,
        max_stake=1500.0,
    )
    db.commit()
    upsert_odds(
        db,
        event_id="evt1",
        provider="pinnacle",
        market="moneyline",
        outcome="home",
        odds=2.15,
        max_stake=2200.0,
    )
    db.commit()
    row = db.query(Odds).filter_by(event_id="evt1", outcome="home").one()
    assert row.odds == 2.15
    assert row.max_stake == 2200.0


def test_upsert_odds_max_stake_null_when_omitted(db):
    upsert_odds(
        db,
        event_id="evt1",
        provider="pinnacle",
        market="moneyline",
        outcome="home",
        odds=2.10,
    )
    db.commit()
    row = db.query(Odds).filter_by(event_id="evt1", outcome="home").one()
    assert row.max_stake is None
