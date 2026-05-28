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


def test_reconciliation_purges_pinnacle_ghost_handicap(session):
    """When Pinnacle drops a mainline (e.g. total 2.5 → 2.75), the row from
    the previous pass must be purged on the next pass, not left as a ghost.
    """
    # Pass 1: Pinnacle ships totals at 2.5 AND 2.75.
    with OddsBatchProcessor(session) as b1:
        for outcome, odds_v in [("over", 1.40), ("under", 2.78)]:
            b1.add("evt:t1", "pinnacle", "total", outcome, odds_v, point=2.5)
        for outcome, odds_v in [("over", 1.48), ("under", 2.51)]:
            b1.add("evt:t1", "pinnacle", "total", outcome, odds_v, point=2.75)
    session.commit()
    assert session.execute(text("SELECT COUNT(*) FROM odds WHERE point=2.5")).scalar() == 2

    # Pass 2: Pinnacle's mainline shifted; only 2.75 ships. 2.5 must go.
    with OddsBatchProcessor(session) as b2:
        for outcome, odds_v in [("over", 1.43), ("under", 2.67)]:
            b2.add("evt:t1", "pinnacle", "total", outcome, odds_v, point=2.75)
    session.commit()
    assert session.execute(text("SELECT COUNT(*) FROM odds WHERE point=2.5")).scalar() == 0
    assert session.execute(text("SELECT COUNT(*) FROM odds WHERE point=2.75")).scalar() == 2


def test_reconciliation_skips_soft_providers(session):
    """Soft books rely on the user-in-browser live odds check; reconciliation
    must NOT delete soft rows the extractor's current pass didn't ship.
    """
    session.add(Provider(id="betinia", name="Betinia"))
    session.commit()

    with OddsBatchProcessor(session) as b1:
        b1.add("evt:t1", "betinia", "total", "over", 1.60, point=2.5)
        b1.add("evt:t1", "betinia", "total", "under", 2.10, point=2.5)
    session.commit()

    with OddsBatchProcessor(session) as b2:
        b2.add("evt:t1", "betinia", "total", "over", 1.65, point=2.75)
        b2.add("evt:t1", "betinia", "total", "under", 2.05, point=2.75)
    session.commit()

    # Soft books unfiltered: both lines survive (placement-time check covers staleness)
    rows = session.execute(text("SELECT point FROM odds WHERE provider_id='betinia' ORDER BY point")).fetchall()
    assert {r.point for r in rows} == {2.5, 2.75}


def test_reconciliation_preserves_untouched_markets(session):
    """If a pass doesn't ship a market at all (extractor partial-failed or the
    market was pulled wholesale), pre-existing rows in that market must stay —
    the staleness gate handles that case, not reconciliation.
    """
    with OddsBatchProcessor(session) as b1:
        b1.add("evt:t1", "pinnacle", "total", "over", 1.40, point=2.5)
        b1.add("evt:t1", "pinnacle", "spread", "home", 1.95, point=-0.5)
        b1.add("evt:t1", "pinnacle", "spread", "away", 1.95, point=0.5)
    session.commit()

    # Pass 2 ships ONLY totals (e.g. spread endpoint errored).
    with OddsBatchProcessor(session) as b2:
        b2.add("evt:t1", "pinnacle", "total", "over", 1.43, point=2.75)
        b2.add("evt:t1", "pinnacle", "total", "under", 2.67, point=2.75)
    session.commit()

    # Spread rows survive — pass 2 didn't touch the (spread, ft) slot at all.
    assert session.execute(text("SELECT COUNT(*) FROM odds WHERE market='spread'")).scalar() == 2
    # Total 2.5 was in a slot we DID touch this pass → purged.
    assert session.execute(text("SELECT COUNT(*) FROM odds WHERE market='total' AND point=2.5")).scalar() == 0


def test_reconciliation_scoped_to_event(session):
    """Reconciliation must not touch other events sharing the provider."""
    session.add(Event(id="evt:t2", sport="ice_hockey", home_team="C", away_team="D"))
    session.commit()
    with OddsBatchProcessor(session) as b1:
        b1.add("evt:t1", "pinnacle", "total", "over", 1.40, point=2.5)
        b1.add("evt:t2", "pinnacle", "total", "over", 1.50, point=3.0)
    session.commit()

    # Pass 2 only re-extracts evt:t1, ships a different handicap there.
    with OddsBatchProcessor(session) as b2:
        b2.add("evt:t1", "pinnacle", "total", "over", 1.43, point=2.75)
    session.commit()

    # evt:t2 untouched — its row remains.
    assert session.execute(text("SELECT COUNT(*) FROM odds WHERE event_id='evt:t2'")).scalar() == 1
    # evt:t1 reconciled — 2.5 gone, 2.75 present.
    rows = session.execute(text("SELECT point FROM odds WHERE event_id='evt:t1' ORDER BY point")).fetchall()
    assert [r.point for r in rows] == [2.75]


def test_reconciliation_handles_null_point_market(session):
    """1x2 / moneyline has no point. The IS NULL branch in the keep-clause
    must work so a previously-shipped outcome that's no longer in the feed
    gets purged."""
    with OddsBatchProcessor(session) as b1:
        b1.add("evt:t1", "pinnacle", "1x2", "home", 2.22)
        b1.add("evt:t1", "pinnacle", "1x2", "draw", 4.02)
        b1.add("evt:t1", "pinnacle", "1x2", "away", 2.54)
    session.commit()

    # Pass 2 drops the draw (e.g. live status changes 3-way to 2-way).
    with OddsBatchProcessor(session) as b2:
        b2.add("evt:t1", "pinnacle", "1x2", "home", 2.20)
        b2.add("evt:t1", "pinnacle", "1x2", "away", 2.56)
    session.commit()

    rows = session.execute(text("SELECT outcome FROM odds WHERE market='1x2' ORDER BY outcome")).fetchall()
    assert {r.outcome for r in rows} == {"home", "away"}
