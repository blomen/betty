"""Integration test for the mirror flow: parse → dedup → store trace → create bet."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Bet, BetTrace, Event, Provider, Profile, ProfileProviderBalance
from src.mirror.service import MirrorService


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Required provider
    session.add(Provider(id="spelklubben", name="Spelklubben", url="https://spelklubben.se"))

    # Active profile with balance
    profile = Profile(name="test", is_active=True)
    session.add(profile)
    session.flush()
    session.add(ProfileProviderBalance(
        profile_id=profile.id,
        provider_id="spelklubben",
        balance=10000.0,
    ))

    # Matching event (start_time in the future so _match_event picks it up)
    session.add(Event(
        id="football:virginia united:north lakes united:2026-03-21",
        sport="football",
        home_team="virginia united",
        away_team="north lakes united",
        start_time=datetime.now(timezone.utc) + timedelta(hours=2),
    ))
    session.commit()

    yield session
    session.close()
    engine.dispose()


def _patch_get_session(session):
    """Return a mock get_session that yields our test session without closing it."""
    # _process_bet_sync calls db.close() in the finally block.
    # We wrap the session so close() is a no-op, keeping the session alive for assertions.
    mock_session = MagicMock(wraps=session)
    mock_session.close = MagicMock()  # suppress close so assertions still work
    return mock_session


def _make_service(provider_id: str = "spelklubben") -> MirrorService:
    return MirrorService(provider_id=provider_id)


def test_process_confirmed_bet(db):
    """Confirmed bet → trace stored, bet created, and event matched."""
    service = _make_service()
    mock_session = _patch_get_session(db)

    parsed = {
        "confirmation_id": "BET-001",
        "event_name": "Virginia United vs North Lakes United",
        "home_team": "virginia united",
        "away_team": "north lakes united",
        "market": "1x2",
        "outcome": "home",
        "odds": 2.10,
        "stake": 100.0,
        "point": None,
    }

    with patch("src.mirror.service.get_session", return_value=mock_session):
        result = service._process_bet_sync(
            url="https://www.spelklubben.se/api/sb/v1/betslip/place",
            request_body='{"stake": 100}',
            response_body='{"betslipId": "BET-001"}',
            parsed=parsed,
        )

    assert result["status"] == "ok", f"Unexpected status: {result}"
    assert result["confirmation_id"] == "BET-001"
    assert result["matched"] is True

    # Bet row created with confirmation_id
    bet = db.query(Bet).filter(Bet.confirmation_id == "BET-001").first()
    assert bet is not None
    assert bet.provider_id == "spelklubben"

    # Trace row stored
    trace = db.query(BetTrace).filter(BetTrace.provider_bet_id == "BET-001").first()
    assert trace is not None
    assert trace.parse_status == "ok"
    assert trace.bet_id == bet.id


def test_dedup_prevents_double_logging(db):
    """Same confirmation_id sent twice → second call returns 'duplicate'."""
    service = _make_service()

    parsed = {
        "confirmation_id": "BET-DUP",
        "event_name": "Virginia United vs North Lakes United",
        "home_team": "virginia united",
        "away_team": "north lakes united",
        "market": "1x2",
        "outcome": "home",
        "odds": 1.85,
        "stake": 50.0,
        "point": None,
    }

    mock_session = _patch_get_session(db)
    with patch("src.mirror.service.get_session", return_value=mock_session):
        first = service._process_bet_sync(
            url="https://www.spelklubben.se/api/sb/v1/betslip/place",
            request_body='{"stake": 50}',
            response_body='{"betslipId": "BET-DUP"}',
            parsed=parsed,
        )

    assert first["status"] == "ok", f"First call failed: {first}"

    # Second call: a fresh mock wrapping the same (still-open) db session
    mock_session2 = _patch_get_session(db)
    with patch("src.mirror.service.get_session", return_value=mock_session2):
        second = service._process_bet_sync(
            url="https://www.spelklubben.se/api/sb/v1/betslip/place",
            request_body='{"stake": 50}',
            response_body='{"betslipId": "BET-DUP"}',
            parsed=parsed,
        )

    assert second["status"] == "duplicate"
    assert second["confirmation_id"] == "BET-DUP"

    # Only one Bet row should exist
    bets = db.query(Bet).filter(Bet.confirmation_id == "BET-DUP").all()
    assert len(bets) == 1
