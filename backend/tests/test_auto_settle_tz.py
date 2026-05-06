"""Regression test: auto_settle.scan_settlements naive-vs-aware comparison.

Bug: scan_settlements built `now` as a tz-aware datetime and compared it
against `Bet.start_time`, which is a naive `DateTime` column. On SQLite the
comparison happens to work; on Postgres TIMESTAMP WITHOUT TIME ZONE the
behavior depends on driver tz-stripping and was unreliable.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Bet, Profile


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Profile(id=1, name="test", is_active=True))
    session.commit()
    yield session
    session.close()


def _add_bet(db, *, start_time: datetime, result: str = "pending"):
    bet = Bet(
        profile_id=1,
        provider_id="polymarket",
        event_id="evt_1",
        market="moneyline",
        outcome="home",
        odds=2.0,
        stake=10.0,
        currency="USDC",
        result=result,
        confirmation_id="some-slug",
        start_time=start_time,
    )
    db.add(bet)
    db.commit()
    return bet


def _utc_naive() -> datetime:
    """Naive datetime treated as UTC — matches Bet.start_time column type."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_naive_now_filter_matches_past_bet(db):
    """Filter with naive UTC `now` returns past pending bets."""
    past = _utc_naive() - timedelta(hours=1)
    _add_bet(db, start_time=past)

    now = _utc_naive()
    pending = db.query(Bet).filter(Bet.result == "pending", Bet.start_time < now).all()
    assert len(pending) == 1


def test_naive_now_filter_skips_future_bet(db):
    """Future bets are not yet eligible for settlement."""
    future = _utc_naive() + timedelta(hours=1)
    _add_bet(db, start_time=future)

    now = _utc_naive()
    pending = db.query(Bet).filter(Bet.result == "pending", Bet.start_time < now).all()
    assert pending == []
