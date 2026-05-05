"""Tests for BetRepo."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Bet, Profile
from src.repositories.bet_repo import BetRepo


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Profile(id=1, name="test", is_active=True))
    session.commit()
    yield session
    session.close()


def _add_bet(db, *, is_bonus: bool, result: str = "win"):
    bet = Bet(
        profile_id=1,
        provider_id="unibet",
        event_id="evt_1",
        market="1x2",
        outcome="Home",
        odds=2.0,
        stake=100.0,
        currency="SEK",
        result=result,
        is_bonus=is_bonus,
        placed_at=datetime.now() - timedelta(days=1),
    )
    db.add(bet)
    db.commit()


def test_list_for_profile_excludes_bonus_when_requested(db):
    """Regression: `not Bet.is_bonus` evaluated to a Python constant False
    instead of the SQL expression `~Bet.is_bonus`, returning zero rows."""
    repo = BetRepo(db)
    _add_bet(db, is_bonus=False)
    _add_bet(db, is_bonus=True)

    all_bets = repo.list_for_profile(profile_id=1)
    assert len(all_bets) == 2

    real_only = repo.list_for_profile(profile_id=1, exclude_bonus=True)
    assert len(real_only) == 1
    assert real_only[0].is_bonus is False


def test_list_for_profile_includes_bonus_by_default(db):
    repo = BetRepo(db)
    _add_bet(db, is_bonus=True)
    bets = repo.list_for_profile(profile_id=1)
    assert len(bets) == 1
    assert bets[0].is_bonus is True
