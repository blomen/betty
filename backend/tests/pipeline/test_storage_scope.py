"""Scope flows from market dict through OddsBatchProcessor to DB rows."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Event, Provider, _run_pg_migrations
from src.pipeline.storage import OddsBatchProcessor


@pytest.fixture
def session():
    import os

    url = os.environ.get("TEST_DATABASE_URL")
    if not url or not url.startswith("postgresql"):
        pytest.skip("TEST_DATABASE_URL not set to a Postgres URL")
    eng = create_engine(url)
    Base.metadata.create_all(eng)
    _run_pg_migrations(eng)
    Session = sessionmaker(bind=eng)
    s = Session()
    s.add(Provider(id="pinnacle", name="Pinnacle"))
    s.add(Event(id="evt:t1", sport="ice_hockey", home_team="A", away_team="B"))
    s.commit()
    yield s
    s.rollback()
    s.close()
    Base.metadata.drop_all(eng)


def test_batch_processor_persists_scope(session):
    batch = OddsBatchProcessor(session)
    batch.add(
        event_id="evt:t1",
        provider="pinnacle",
        market="total",
        outcome="over",
        odds=1.85,
        point=4.5,
        scope="reg",
    )
    batch.flush()
    row = session.execute(text("SELECT scope FROM odds WHERE event_id='evt:t1' AND outcome='over'")).first()
    assert row.scope == "reg"


def test_batch_processor_defaults_scope_to_ft(session):
    batch = OddsBatchProcessor(session)
    batch.add(
        event_id="evt:t1",
        provider="pinnacle",
        market="total",
        outcome="over",
        odds=1.85,
        point=4.5,  # scope intentionally omitted
    )
    batch.flush()
    row = session.execute(text("SELECT scope FROM odds WHERE event_id='evt:t1' AND outcome='over'")).first()
    assert row.scope == "ft"


def test_same_event_different_scopes_coexist(session):
    """Two rows with same (event, provider, market, outcome, point) but
    different scope must coexist (unique constraint includes scope)."""
    batch = OddsBatchProcessor(session)
    batch.add(
        event_id="evt:t1",
        provider="pinnacle",
        market="total",
        outcome="over",
        odds=1.85,
        point=4.5,
        scope="reg",
    )
    batch.add(
        event_id="evt:t1",
        provider="pinnacle",
        market="total",
        outcome="over",
        odds=1.90,
        point=4.5,
        scope="ft",
    )
    batch.flush()
    rows = session.execute(text("SELECT scope, odds FROM odds WHERE event_id='evt:t1' ORDER BY scope")).fetchall()
    assert len(rows) == 2
    assert {r.scope for r in rows} == {"ft", "reg"}
