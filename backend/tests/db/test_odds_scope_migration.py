"""Tests for the odds.scope column added 2026-05-25."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Odds, Event, Provider, _run_pg_migrations


@pytest.fixture
def pg_engine():
    """Real Postgres engine — required since the migration uses Postgres-only SQL.
    Skips if DATABASE_URL not set to a Postgres URL.
    """
    import os
    url = os.environ.get("TEST_DATABASE_URL")
    if not url or not url.startswith("postgresql"):
        pytest.skip("TEST_DATABASE_URL not set to a Postgres URL")
    eng = create_engine(url)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


def test_odds_scope_column_exists_after_migration(pg_engine):
    _run_pg_migrations(pg_engine)
    insp = inspect(pg_engine)
    cols = {c["name"]: c for c in insp.get_columns("odds")}
    assert "scope" in cols, "odds.scope column missing after migration"
    assert cols["scope"]["nullable"] is False
    assert cols["scope"]["default"] is not None  # has a server default


def test_odds_scope_defaults_to_ft(pg_engine):
    _run_pg_migrations(pg_engine)
    Session = sessionmaker(bind=pg_engine)
    with Session() as s:
        s.add(Provider(id="testprovider", name="Test"))
        s.add(Event(id="evt:test", sport="ice_hockey", home_team="A", away_team="B"))
        s.flush()
        # Insert without specifying scope — should default to 'ft'
        s.execute(text(
            "INSERT INTO odds (event_id, provider_id, market, outcome, odds, point) "
            "VALUES ('evt:test', 'testprovider', 'total', 'over', 1.85, 4.5)"
        ))
        s.commit()
        row = s.execute(text("SELECT scope FROM odds WHERE event_id = 'evt:test'")).first()
        assert row.scope == "ft"


def test_pinnacle_period_6_hockey_backfills_to_reg(pg_engine):
    """The migration backfills existing Pinnacle period=6 hockey rows to scope='reg'."""
    Session = sessionmaker(bind=pg_engine)
    with Session() as s:
        s.add(Provider(id="pinnacle", name="Pinnacle"))
        s.add(Event(id="evt:hockey", sport="ice_hockey", home_team="A", away_team="B"))
        s.add(Event(id="evt:football", sport="football", home_team="C", away_team="D"))
        s.flush()
        # Seed pre-migration rows: pinnacle period 6 hockey + period 6 football
        # (the football one should NOT backfill — only hockey gets reg)
        s.execute(text(
            "INSERT INTO odds (event_id, provider_id, market, outcome, odds, point, provider_meta) "
            "VALUES "
            "('evt:hockey',   'pinnacle', 'total', 'over',  1.85, 4.5, '{\"period\": 6}'::json), "
            "('evt:football', 'pinnacle', 'total', 'over',  1.85, 2.5, '{\"period\": 6}'::json)"
        ))
        s.commit()
    # Re-run migration (idempotency check — re-runs against now-populated DB)
    _run_pg_migrations(pg_engine)
    with Session() as s:
        hockey = s.execute(text("SELECT scope FROM odds WHERE event_id='evt:hockey'")).first()
        football = s.execute(text("SELECT scope FROM odds WHERE event_id='evt:football'")).first()
        assert hockey.scope == "reg", "hockey period=6 must backfill to 'reg'"
        assert football.scope == "ft", "non-hockey rows must stay 'ft'"


def test_migration_is_idempotent(pg_engine):
    _run_pg_migrations(pg_engine)
    _run_pg_migrations(pg_engine)  # Second run must not error
    insp = inspect(pg_engine)
    cols = {c["name"]: c for c in insp.get_columns("odds")}
    assert "scope" in cols
