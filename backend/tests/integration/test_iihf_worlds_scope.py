"""End-to-end regression: the 2026-05-25 Slovenia v Italy IIHF false-arb bug.

Seeds the DB with the exact odds set that produced the false +3.66% arb,
runs the value/arb scanner, and asserts zero opportunities for that event.

Why this matters:
    Pinnacle stored the IIHF Worlds totals at period=6 (regulation-only, scope="reg").
    Betinia stored the same totals including OT+penalties (scope="ft").
    The scanner was comparing reg-Pinnacle vs ft-Betinia at the same point (4.5 goals),
    which is structurally invalid: under 4.5 reg goals can coexist with over 4.5 ft
    goals. Pre-fix, this produced a phantom +3.66% arb.

    Post-fix: group_odds() filters all odds that don't match the sport's canonical scope
    ("ft" for ice_hockey). Pinnacle's period=6 rows (scope="reg") are dropped, leaving
    no Pinnacle reference → no value/arb surfaces.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.analysis.scanner import OpportunityScanner
from src.db.models import Base, Event, Odds, Provider, _run_pg_migrations


@pytest.fixture
def db_session():
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("TEST_DATABASE_URL not set to a Postgres URL — skipping integration test")

    eng = create_engine(url)
    Base.metadata.create_all(eng)
    _run_pg_migrations(eng)

    Session = sessionmaker(bind=eng)
    s = Session()

    # Minimal provider rows so FK constraints are satisfied
    for pid, name in [("pinnacle", "Pinnacle"), ("betinia", "Betinia")]:
        if not s.query(Provider).filter_by(id=pid).first():
            s.add(Provider(id=pid, name=name))
    s.commit()

    yield s

    s.rollback()
    # Clean up only the rows we inserted (don't drop shared tables)
    s.query(Odds).filter(Odds.event_id == "evt:iihf:slov:ita:20260525").delete()
    s.query(Event).filter_by(id="evt:iihf:slov:ita:20260525").delete()
    s.commit()
    s.close()


def test_iihf_slovenia_italy_false_arb_does_not_surface(db_session):
    """Replays the 2026-05-25 fixture.

    Pinnacle period=6 (scope="reg") vs Betinia ft (scope="ft") must NOT produce
    any total-market opportunity — they cover structurally different goal counts.
    """
    now = datetime.now(timezone.utc)
    s = db_session

    s.add(
        Event(
            id="evt:iihf:slov:ita:20260525",
            sport="ice_hockey",
            league="IIHF World Championship",
            home_team="slovenia",
            away_team="italy",
            start_time=now + timedelta(hours=1),
        )
    )
    s.flush()

    # Pinnacle period=6 (regulation-only) — the actual production rows that caused the bug
    s.add(
        Odds(
            event_id="evt:iihf:slov:ita:20260525",
            provider_id="pinnacle",
            market="total",
            outcome="over",
            odds=1.8547,
            point=4.5,
            scope="reg",
            provider_meta={"period": 6},
            updated_at=now,
        )
    )
    s.add(
        Odds(
            event_id="evt:iihf:slov:ita:20260525",
            provider_id="pinnacle",
            market="total",
            outcome="under",
            odds=2.00,
            point=4.5,
            scope="reg",
            provider_meta={"period": 6},
            updated_at=now,
        )
    )
    # Betinia (Altenar typeId 412) — OT + penalties included (scope="ft")
    s.add(
        Odds(
            event_id="evt:iihf:slov:ita:20260525",
            provider_id="betinia",
            market="total",
            outcome="over",
            odds=1.6061,
            point=4.5,
            scope="ft",
            updated_at=now,
        )
    )
    s.add(
        Odds(
            event_id="evt:iihf:slov:ita:20260525",
            provider_id="betinia",
            market="total",
            outcome="under",
            odds=2.35,
            point=4.5,
            scope="ft",
            updated_at=now,
        )
    )
    s.commit()

    scanner = OpportunityScanner(session=s)
    event = s.query(Event).filter_by(id="evt:iihf:slov:ita:20260525").one()

    # Both methods must return empty for total markets on this event.
    # Pre-fix: scan_value returned a value bet; scan_arb returned a +3.66% arb.
    # Post-fix: Pinnacle's scope="reg" rows are filtered in group_odds() because
    #           ice_hockey canonical scope is "ft". No sharp reference → nothing surfaces.
    value_bets = scanner.scan_value(min_edge_pct=0.0, events=[event])
    arbs = scanner.scan_arb(min_edge_pct=0.0, events=[event])

    total_value = [vb for vb in value_bets if "total" in vb.market]
    total_arbs = [a for a in arbs if "total" in a.market]

    assert not total_value, f"BUG REGRESSION: value bet surfaced for cross-scope hockey total: {total_value}"
    assert not total_arbs, f"BUG REGRESSION: arb surfaced for cross-scope hockey total: {total_arbs}"
