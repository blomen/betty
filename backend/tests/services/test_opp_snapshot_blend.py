"""Shadow-blend freeze + backfill in OppSnapshotService."""

from datetime import UTC, datetime, timedelta

import pytest

from src.db.models import Base, Event, Odds, Opportunity, OppSnapshot, Provider
from src.services.opp_snapshot_service import OppSnapshotService


@pytest.fixture
def session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    for pid in ("pinnacle", "cloudbet", "betsson"):
        s.add(Provider(id=pid, name=pid))
    s.commit()
    yield s
    s.close()


def _event(session, start_offset_min):
    ev = Event(
        id="evt1",
        sport="soccer_epl",
        home_team="A",
        away_team="B",
        start_time=datetime.now(UTC) + timedelta(minutes=start_offset_min),
    )
    session.add(ev)
    session.commit()
    return ev


def _odds(session, provider, outcome, odds, market="1x2"):
    session.add(Odds(event_id="evt1", provider_id=provider, market=market, outcome=outcome, odds=odds, scope="ft"))


def test_detection_freezes_blended_fair(session):
    _event(session, start_offset_min=120)  # not started
    _odds(session, "pinnacle", "home", 1.91)
    _odds(session, "pinnacle", "away", 1.91)
    _odds(session, "cloudbet", "home", 1.80)
    _odds(session, "cloudbet", "away", 2.20)
    session.commit()

    opp = Opportunity(
        event_id="evt1",
        type="value",
        market="1x2",
        outcome1="home",
        provider1_id="betsson",
        odds1=2.10,
        odds2=2.00,
        edge_pct=5.0,
        scope="ft",
    )
    snap = OppSnapshotService(session).upsert_from_opportunity(opp)
    assert snap.blended_fair1_at_detection is not None
    assert snap.blend_n_sources_at_detection == 2
    assert set(snap.blend_sources) == {"pinnacle", "cloudbet"}


def test_detection_only_pinnacle_records_single_source(session):
    _event(session, start_offset_min=120)
    _odds(session, "pinnacle", "home", 1.91)
    _odds(session, "pinnacle", "away", 1.91)
    session.commit()
    opp = Opportunity(
        event_id="evt1",
        type="value",
        market="1x2",
        outcome1="home",
        provider1_id="betsson",
        odds1=2.10,
        odds2=2.00,
        edge_pct=5.0,
        scope="ft",
    )
    snap = OppSnapshotService(session).upsert_from_opportunity(opp)
    assert snap.blend_n_sources_at_detection == 1
    assert snap.blend_sources == ["pinnacle"]


def test_closing_backfill_computes_blended_clv(session):
    _event(session, start_offset_min=-10)  # already started
    _odds(session, "pinnacle", "home", 2.00)
    _odds(session, "pinnacle", "away", 2.00)
    _odds(session, "cloudbet", "home", 2.00)
    _odds(session, "cloudbet", "away", 2.00)
    session.commit()
    snap = OppSnapshot(
        event_id="evt1",
        type="value",
        market="1x2",
        outcome1="home",
        scope="ft",
        provider1_id="betsson",
        odds1_at_detection=2.20,
        blended_fair1_at_detection=2.05,
        first_detected_at=datetime.now(UTC) - timedelta(hours=2),
        last_detected_at=datetime.now(UTC) - timedelta(hours=2),
    )
    session.add(snap)
    session.commit()

    result = OppSnapshotService(session).compute_closing_clv()
    session.refresh(snap)
    assert result["processed"] == 1
    # Closing blended fair ~2.0 (50/50 devigged); CLV = (2.20/2.00 - 1)*100 = 10%.
    assert snap.blended_closing_fair == pytest.approx(2.0, rel=1e-6)
    assert snap.blended_clv_pct == pytest.approx(10.0, rel=1e-3)


def test_closing_neutral_case_3way_blend_equals_pinnacle(session):
    # 1x2 (3-way) market, ONLY Pinnacle has odds -> blend uses power devig,
    # pinnacle_closing_fair must use the SAME method so the neutral-case
    # invariant holds: blended_closing_fair == pinnacle_closing_fair.
    _event(session, start_offset_min=-10)
    for outcome, odds in (("home", 2.10), ("draw", 3.40), ("away", 3.60)):
        _odds(session, "pinnacle", outcome, odds, market="1x2")
    session.commit()
    snap = OppSnapshot(
        event_id="evt1",
        type="value",
        market="1x2",
        outcome1="home",
        scope="ft",
        provider1_id="betsson",
        odds1_at_detection=2.30,
        first_detected_at=datetime.now(UTC) - timedelta(hours=2),
        last_detected_at=datetime.now(UTC) - timedelta(hours=2),
    )
    session.add(snap)
    session.commit()
    OppSnapshotService(session).compute_closing_clv()
    session.refresh(snap)
    assert snap.pinnacle_closing_fair is not None
    assert snap.blended_closing_fair is not None
    assert snap.blended_closing_fair == pytest.approx(snap.pinnacle_closing_fair, rel=1e-9)
    assert snap.blended_clv_pct == pytest.approx(snap.pinnacle_clv_pct, rel=1e-6)
