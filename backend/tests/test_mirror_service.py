"""Tests for MirrorService."""
import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.db.models import Base, BetTrace
from src.mirror.service import MirrorService


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


def test_store_trace_ok(db):
    service = MirrorService(provider_id="spelklubben", broadcaster=None)

    trace = service._store_trace(
        db=db,
        url="https://example.com/api/sb/v1/betslip/place",
        request_body='{"stake": 100}',
        response_body='{"data": {"betId": "abc123", "status": "Confirmed"}}',
        parse_status="ok",
        provider_bet_id="abc123",
        bet_id=42,
    )
    db.commit()

    assert trace.id is not None
    assert trace.provider_bet_id == "abc123"
    assert trace.bet_id == 42
    assert trace.parse_status == "ok"


def test_store_trace_rejected(db):
    service = MirrorService(provider_id="spelklubben", broadcaster=None)

    trace = service._store_trace(
        db=db,
        url="https://example.com/api/sb/v1/betslip/place",
        request_body="{}",
        response_body='{"data": {"status": "Rejected"}}',
        parse_status="rejected",
    )
    db.commit()

    assert trace.parse_status == "rejected"
    assert trace.bet_id is None
