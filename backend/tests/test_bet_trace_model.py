"""Tests for BetTrace model."""
import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.db.models import Base, BetTrace


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


def test_create_bet_trace(db):
    trace = BetTrace(
        timestamp=datetime.now(timezone.utc),
        provider_id="spelklubben",
        request_url="https://www.spelklubben.se/api/sb/v1/betslip/place",
        request_body='{"stake": 100}',
        response_body='{"betId": "abc123"}',
        provider_bet_id="abc123",
        parse_status="ok",
    )
    db.add(trace)
    db.commit()

    result = db.query(BetTrace).first()
    assert result.provider_id == "spelklubben"
    assert result.provider_bet_id == "abc123"
    assert result.parse_status == "ok"
    assert result.bet_id is None


def test_bet_trace_rejected_status(db):
    trace = BetTrace(
        timestamp=datetime.now(timezone.utc),
        provider_id="spelklubben",
        request_url="https://example.com/api/sb/v1/betslip/place",
        request_body="{}",
        response_body='{"error": "odds changed"}',
        parse_status="rejected",
    )
    db.add(trace)
    db.commit()

    result = db.query(BetTrace).first()
    assert result.parse_status == "rejected"
