import pytest
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Base, Bet, Profile, ProfileProviderBalance
from src.repositories.profile_repo import ProfileRepo


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    profile = Profile(id=1, name="test", is_active=True)
    session.add(profile)
    session.commit()
    yield session
    session.close()


def _add_bet(db: Session, profile_id: int, stake: float, days_ago: int):
    """Helper to add a bet N days ago."""
    bet = Bet(
        profile_id=profile_id,
        provider_id="unibet",
        event_id="evt_1",
        market="1x2",
        outcome="Home",
        odds=2.0,
        stake=stake,
        currency="SEK",
        result="pending",
        placed_at=datetime.utcnow() - timedelta(days=days_ago),
    )
    db.add(bet)
    db.commit()


def test_avg_daily_wager_no_history(db):
    repo = ProfileRepo(db)
    result = repo.get_avg_daily_wager(profile_id=1)
    assert result["avg_daily_wager"] == 0.0
    assert result["has_history"] is False
    assert result["days_with_bets"] == 0


def test_avg_daily_wager_with_bets(db):
    repo = ProfileRepo(db)
    _add_bet(db, 1, 500.0, days_ago=1)
    _add_bet(db, 1, 300.0, days_ago=1)
    _add_bet(db, 1, 400.0, days_ago=3)
    result = repo.get_avg_daily_wager(profile_id=1, lookback_days=14)
    assert result["has_history"] is True
    assert result["days_with_bets"] == 2
    assert 85.0 < result["avg_daily_wager"] < 86.0


def test_avg_daily_wager_respects_lookback(db):
    repo = ProfileRepo(db)
    _add_bet(db, 1, 1000.0, days_ago=20)
    _add_bet(db, 1, 200.0, days_ago=5)
    result = repo.get_avg_daily_wager(profile_id=1, lookback_days=14)
    assert 14.0 < result["avg_daily_wager"] < 15.0
    assert result["days_with_bets"] == 1
