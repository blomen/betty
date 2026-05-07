"""Tests for MarketRepo.bulk_insert_candles concurrency safety."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, MarketCandle
from src.repositories.market_repo import MarketRepo


@dataclass
class _Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@pytest.fixture
def market_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()
    engine.dispose()


def _bar(ts: datetime, c: float = 100.0) -> _Bar:
    return _Bar(timestamp=ts, open=c, high=c + 1, low=c - 1, close=c, volume=10)


def test_bulk_insert_inserts_new_rows(market_session):
    repo = MarketRepo(db=market_session, market_db=market_session)
    base = datetime(2026, 1, 1, 0, 0, 0)
    bars = [_bar(base + timedelta(minutes=i), c=100 + i) for i in range(5)]
    n = repo.bulk_insert_candles("NQ", "1m", bars)
    assert n == 5
    assert market_session.query(MarketCandle).count() == 5


def test_bulk_insert_is_idempotent(market_session):
    """Second insert of the same bars must be a no-op (ON CONFLICT DO NOTHING)."""
    repo = MarketRepo(db=market_session, market_db=market_session)
    base = datetime(2026, 1, 1, 0, 0, 0)
    bars = [_bar(base + timedelta(minutes=i)) for i in range(3)]
    assert repo.bulk_insert_candles("NQ", "1m", bars) == 3
    # second call: every row conflicts on uq_market_candle → 0 inserted
    assert repo.bulk_insert_candles("NQ", "1m", bars) == 0
    assert market_session.query(MarketCandle).count() == 3


def test_bulk_insert_partial_overlap(market_session):
    """Mixing already-existing and new bars inserts only the new ones."""
    repo = MarketRepo(db=market_session, market_db=market_session)
    base = datetime(2026, 1, 1, 0, 0, 0)
    repo.bulk_insert_candles("NQ", "1m", [_bar(base + timedelta(minutes=i)) for i in range(3)])
    # bars 0..4 — 0,1,2 exist, 3,4 are new
    n = repo.bulk_insert_candles("NQ", "1m", [_bar(base + timedelta(minutes=i)) for i in range(5)])
    assert n == 2
    assert market_session.query(MarketCandle).count() == 5


def test_bulk_insert_handles_tz_aware_timestamps(market_session):
    """Databento returns tz-aware UTC; column is naive — must be normalized."""
    repo = MarketRepo(db=market_session, market_db=market_session)
    aware = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    n = repo.bulk_insert_candles("NQ", "1m", [_bar(aware)])
    assert n == 1
    # Re-insert with the naive equivalent must dedupe
    naive = datetime(2026, 1, 1, 0, 0, 0)
    n2 = repo.bulk_insert_candles("NQ", "1m", [_bar(naive)])
    assert n2 == 0


def test_bulk_insert_empty(market_session):
    repo = MarketRepo(db=market_session, market_db=market_session)
    assert repo.bulk_insert_candles("NQ", "1m", []) == 0
