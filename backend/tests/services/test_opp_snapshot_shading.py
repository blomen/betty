"""Shading-risk + odds-bucket freeze on OppSnapshot at detection time.

Task 3 of the shading-aware diagnostic plan:
- shading_risk is frozen from opp.annotations["shading"]["risk"] at first sighting.
- odds_bucket is frozen from _odds_range(opp.odds1) at first sighting.
- Both columns are additive/diagnostic only; they never affect edge or stake logic.
"""

from datetime import UTC, datetime, timedelta

from src.analysis.patterns import _odds_range
from src.db.models import Base, Event, Odds, Opportunity, OppSnapshot, Provider
from src.services.opp_snapshot_service import OppSnapshotService


def _make_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    for pid in ("pinnacle", "betsson"):
        s.add(Provider(id=pid, name=pid))
    s.commit()
    return s


def _event(session, event_id="evt1"):
    ev = Event(
        id=event_id,
        sport="soccer_epl",
        home_team="A",
        away_team="B",
        start_time=datetime.now(UTC) + timedelta(minutes=120),
    )
    session.add(ev)
    session.commit()
    return ev


def _base_opp(annotations, odds1=3.0):
    """Minimal Opportunity with the given annotations and odds1."""
    return Opportunity(
        event_id="evt1",
        type="value",
        market="1x2",
        outcome1="home",
        provider1_id="betsson",
        odds1=odds1,
        odds2=2.00,
        edge_pct=5.0,
        scope="ft",
        annotations=annotations,
    )


def test_shading_high_freezes_risk_and_bucket():
    """annotations={"shading": {"risk": "high"}}, odds1=3.0 → risk=high, bucket=2.5-4.0."""
    session = _make_session()
    _event(session)
    # Add minimal pinnacle odds so blended_fair_from_rows doesn't crash.
    session.add(Odds(event_id="evt1", provider_id="pinnacle", market="1x2", outcome="home", odds=1.91, scope="ft"))
    session.add(Odds(event_id="evt1", provider_id="pinnacle", market="1x2", outcome="away", odds=1.91, scope="ft"))
    session.commit()

    opp = _base_opp(annotations={"shading": {"risk": "high"}}, odds1=3.0)
    snap = OppSnapshotService(session).upsert_from_opportunity(opp)

    assert snap.shading_risk == "high"
    assert snap.odds_bucket == "2.5-4.0"
    assert _odds_range(3.0) == "2.5-4.0"  # sanity-check the bucketer
    session.close()


def test_annotations_none_risk_is_none_bucket_still_computed():
    """annotations=None → shading_risk is None, odds_bucket still computed from odds1."""
    session = _make_session()
    _event(session)
    session.add(Odds(event_id="evt1", provider_id="pinnacle", market="1x2", outcome="home", odds=1.91, scope="ft"))
    session.add(Odds(event_id="evt1", provider_id="pinnacle", market="1x2", outcome="away", odds=1.91, scope="ft"))
    session.commit()

    opp = _base_opp(annotations=None, odds1=2.0)
    snap = OppSnapshotService(session).upsert_from_opportunity(opp)

    assert snap.shading_risk is None
    assert snap.odds_bucket == _odds_range(2.0)  # "1.5-2.5"
    assert snap.odds_bucket == "1.5-2.5"
    session.close()


def test_annotations_no_shading_key_risk_is_none():
    """annotations without "shading" key → shading_risk is None, bucket still computed."""
    session = _make_session()
    _event(session)
    session.add(Odds(event_id="evt1", provider_id="pinnacle", market="1x2", outcome="home", odds=1.91, scope="ft"))
    session.add(Odds(event_id="evt1", provider_id="pinnacle", market="1x2", outcome="away", odds=1.91, scope="ft"))
    session.commit()

    opp = _base_opp(annotations={"consensus_lean": {"lean": "home", "confidence": 0.6}}, odds1=1.4)
    snap = OppSnapshotService(session).upsert_from_opportunity(opp)

    assert snap.shading_risk is None
    assert snap.odds_bucket == "<1.5"
    assert _odds_range(1.4) == "<1.5"
    session.close()


def test_odds_bucket_boundaries():
    """Confirm all four bucket ranges are assigned correctly."""
    assert _odds_range(1.4) == "<1.5"
    assert _odds_range(2.0) == "1.5-2.5"
    assert _odds_range(3.0) == "2.5-4.0"
    assert _odds_range(5.0) == "4.0+"


def test_shading_elevated_and_high_odds():
    """annotations={"shading": {"risk": "elevated"}}, odds1=5.0 → risk=elevated, bucket=4.0+."""
    session = _make_session()
    _event(session)
    session.add(Odds(event_id="evt1", provider_id="pinnacle", market="1x2", outcome="home", odds=1.91, scope="ft"))
    session.add(Odds(event_id="evt1", provider_id="pinnacle", market="1x2", outcome="away", odds=1.91, scope="ft"))
    session.commit()

    opp = _base_opp(annotations={"shading": {"risk": "elevated", "extra": "data"}}, odds1=5.0)
    snap = OppSnapshotService(session).upsert_from_opportunity(opp)

    assert snap.shading_risk == "elevated"
    assert snap.odds_bucket == "4.0+"
    session.close()
