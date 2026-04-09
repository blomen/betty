"""Tests for BalanceLog, SettlementQueue, and PriceCache models."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, BalanceLog, SettlementQueue, PriceCache, Provider, Event


@pytest.fixture(scope="module")
def engine():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    Session = sessionmaker(bind=engine)
    sess = Session()
    # Insert a minimal provider and event row for FK references
    if not sess.get(Provider, "test_provider"):
        prov = Provider(id="test_provider", name="Test Provider")
        sess.add(prov)
    if not sess.query(Event).filter_by(id="test_event_1").first():
        evt = Event(
            id="test_event_1",
            sport="soccer",
            league="Test League",
            home_team="Home",
            away_team="Away",
            start_time=datetime.now(timezone.utc),
        )
        sess.add(evt)
    sess.commit()
    yield sess
    sess.rollback()
    sess.close()


def test_balance_log_insert(session):
    log = BalanceLog(
        provider_id="test_provider",
        amount=1234.56,
        currency="SEK",
        source="intercepted",
    )
    session.add(log)
    session.commit()

    fetched = session.query(BalanceLog).filter_by(provider_id="test_provider").first()
    assert fetched is not None
    assert fetched.amount == 1234.56
    assert fetched.currency == "SEK"
    assert fetched.source == "intercepted"
    assert fetched.created_at is not None


def test_settlement_queue_lifecycle(session):
    entry = SettlementQueue(
        provider_id="test_provider",
        result="won",
        payout=500.0,
        status="pending",
    )
    session.add(entry)
    session.commit()

    fetched = session.query(SettlementQueue).filter_by(
        provider_id="test_provider", status="pending"
    ).first()
    assert fetched is not None
    assert fetched.result == "won"
    assert fetched.payout == 500.0
    assert fetched.status == "pending"
    assert fetched.confirmed_at is None

    # Confirm it
    fetched.status = "confirmed"
    fetched.confirmed_at = datetime.now(timezone.utc)
    session.commit()

    confirmed = session.get(SettlementQueue, fetched.id)
    assert confirmed.status == "confirmed"
    assert confirmed.confirmed_at is not None


def test_price_cache_insert(session):
    tick = PriceCache(
        provider_id="test_provider",
        event_id="test_event_1",
        market="1x2",
        outcome="home",
        odds=2.10,
        source="intercepted",
    )
    session.add(tick)
    session.commit()

    fetched = session.query(PriceCache).filter_by(
        provider_id="test_provider",
        event_id="test_event_1",
        market="1x2",
        outcome="home",
    ).first()
    assert fetched is not None
    assert fetched.odds == 2.10
    assert fetched.source == "intercepted"
    assert fetched.updated_at is not None
