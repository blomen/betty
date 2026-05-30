"""Regression tests for BetService._pinnacle_fair_odds.

Locks two correctness guards that drive the displayed EST EDGE / PROB columns
(and, when enabled, the Kelly bucket-confidence multiplier):

1. 1x2 is a 3-way market. De-vigging a market that has only 2 of its 3
   outcomes present normalises two implied probabilities to 1.0, understating
   the fair odds and OVERSTATING the displayed edge. The helper must require
   all three outcomes for 1x2.
2. total/spread must filter Pinnacle's ladder by `point`, else the de-vig
   compares against an arbitrary handicap and prints a fantasy edge.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Event, Odds, Provider
from src.services.bet_service import BetService


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Provider(id="pinnacle", name="Pinnacle"))
    session.add(
        Event(
            id="football:home:away:20260601",
            sport="football",
            home_team="home",
            away_team="away",
            start_time=datetime(2026, 6, 1, tzinfo=UTC).replace(tzinfo=None),
        )
    )
    session.commit()
    yield session
    session.close()


def _odds(db, *, market, outcome, odds, point=None):
    db.add(
        Odds(
            event_id="football:home:away:20260601",
            provider_id="pinnacle",
            market=market,
            outcome=outcome,
            odds=odds,
            point=point,
        )
    )
    db.commit()


def test_1x2_requires_all_three_outcomes(db):
    """A 2-of-3 1x2 market must NOT yield a fair price (would overstate edge)."""
    _odds(db, market="1x2", outcome="home", odds=2.10)
    _odds(db, market="1x2", outcome="away", odds=3.50)
    # draw missing
    svc = BetService(db)
    assert svc._pinnacle_fair_odds("football:home:away:20260601", "1x2", "home", None) is None


def test_1x2_complete_market_yields_fair(db):
    """Full 3-way 1x2 de-vigs to a fair price below the raw odds."""
    _odds(db, market="1x2", outcome="home", odds=2.10)
    _odds(db, market="1x2", outcome="draw", odds=3.40)
    _odds(db, market="1x2", outcome="away", odds=3.50)
    svc = BetService(db)
    fair = svc._pinnacle_fair_odds("football:home:away:20260601", "1x2", "home", None)
    assert fair is not None
    # Fair odds must exceed the raw (vig removed) and stay finite/sane.
    assert 2.10 < fair < 2.60


def test_moneyline_two_way_ok_with_two(db):
    """2-way markets (moneyline) are fine with exactly two outcomes."""
    _odds(db, market="moneyline", outcome="home", odds=1.90)
    _odds(db, market="moneyline", outcome="away", odds=1.90)
    svc = BetService(db)
    fair = svc._pinnacle_fair_odds("football:home:away:20260601", "moneyline", "home", None)
    assert fair is not None
    assert 1.90 < fair < 2.05


def test_total_filters_by_point(db):
    """The de-vig must use the bet's own line, not an arbitrary ladder rung."""
    # Our line: over/under 2.5 — tight, near-fair.
    _odds(db, market="total", outcome="over", odds=1.95, point=2.5)
    _odds(db, market="total", outcome="under", odds=1.95, point=2.5)
    # A far ladder line that must be ignored.
    _odds(db, market="total", outcome="over", odds=1.20, point=0.5)
    _odds(db, market="total", outcome="under", odds=4.50, point=0.5)
    svc = BetService(db)
    fair = svc._pinnacle_fair_odds("football:home:away:20260601", "total", "over", 2.5)
    assert fair is not None
    # Must reflect the 2.5 line (~1.95 raw → fair just under 2.0), not the 0.5 rung.
    assert 1.90 < fair < 2.05
