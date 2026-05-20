"""Tests for arb leg correlation."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Bet, Event, Profile
from src.services.arb_correlation import correlate_arbs


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(Profile(id=1, name="t", is_active=True))
    s.add(Event(id="evt1", sport="tennis", home_team="ruud", away_team="brooksby"))
    s.commit()
    yield s
    s.close()


def _bet(s, **kw):
    base = dict(profile_id=1, odds=2.0, stake=10.0, result="pending", placed_at=datetime.utcnow())
    base.update(kw)
    b = Bet(**base)
    s.add(b)
    s.commit()
    return b


def test_high_confidence_pairs_same_event_complementary(db):
    anchor = _bet(db, provider_id="betinia", event_id="evt1", outcome="home")
    counter = _bet(
        db,
        provider_id="polymarket",
        event_id="evt1",
        outcome="away",
        bet_type="arb_counter",
        provider_bet_id="0xCID",
    )
    out = correlate_arbs(db)
    assert out["linked"] == 1
    db.refresh(anchor)
    db.refresh(counter)
    assert anchor.arb_group_id is not None
    assert anchor.arb_group_id == counter.arb_group_id
    assert anchor.bet_type == "arb_anchor"


def test_medium_confidence_pairs_by_title(db):
    anchor = _bet(db, provider_id="betinia", event_id="evt1", outcome="home")
    counter = _bet(
        db,
        provider_id="polymarket",
        event_id=None,
        outcome="",
        bet_type="arb_counter",
        provider_bet_id="0xCID2",
        boost_event="Geneva Open: Jenson Brooksby vs Casper Ruud",
    )
    out = correlate_arbs(db)
    assert out["linked"] == 1
    db.refresh(anchor)
    db.refresh(counter)
    assert counter.arb_group_id == anchor.arb_group_id


def test_no_link_outside_time_window(db):
    now = datetime.utcnow()
    _bet(db, provider_id="betinia", event_id="evt1", outcome="home", placed_at=now)
    _bet(
        db,
        provider_id="polymarket",
        event_id="evt1",
        outcome="away",
        bet_type="arb_counter",
        provider_bet_id="0xCID3",
        placed_at=now + timedelta(hours=6),
    )
    out = correlate_arbs(db)
    assert out["linked"] == 0


def test_ambiguous_high_matches_left_unlinked(db):
    _bet(db, provider_id="betinia", event_id="evt1", outcome="home")
    _bet(db, provider_id="bethard", event_id="evt1", outcome="home")
    _bet(
        db,
        provider_id="polymarket",
        event_id="evt1",
        outcome="away",
        bet_type="arb_counter",
        provider_bet_id="0xCID4",
    )
    out = correlate_arbs(db)
    assert out["linked"] == 0


def test_already_grouped_legs_skipped(db):
    _bet(db, provider_id="betinia", event_id="evt1", outcome="home", arb_group_id="existing")
    _bet(
        db,
        provider_id="polymarket",
        event_id="evt1",
        outcome="away",
        bet_type="arb_counter",
        provider_bet_id="0xCID5",
        arb_group_id="existing",
    )
    out = correlate_arbs(db)
    assert out["linked"] == 0
