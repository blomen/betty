"""Tests for Play settle endpoints."""

import pytest
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.models import Base, Profile, Provider
from src.api.routes.opportunities import router as opportunities_router
from src.api.deps import get_db


@pytest.fixture
def settle_db():
    """In-memory SQLite session with all tables.

    StaticPool + check_same_thread=False are required so the same connection
    is reused across threads (FastAPI runs sync dependency overrides in a
    threadpool, which would otherwise get a fresh connection with no tables).
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def active_profile(settle_db):
    """Create an active profile and required providers."""
    profile = Profile(name="test", is_active=True, bankroll=10000.0)
    settle_db.add(profile)

    # Create providers referenced by tests
    for pid in ("unibet", "betsson"):
        settle_db.add(Provider(id=pid, name=pid.title()))
    settle_db.commit()
    return profile


@pytest.fixture
def client(settle_db):
    """TestClient backed by a minimal app with only the opportunities router."""
    test_app = FastAPI()
    test_app.include_router(opportunities_router)

    def _override():
        try:
            yield settle_db
        except Exception:
            settle_db.rollback()
            raise

    test_app.dependency_overrides[get_db] = _override
    with TestClient(test_app, raise_server_exceptions=False) as c:
        yield c


# Alias so tests can use db_session alongside client/active_profile
@pytest.fixture
def db_session(settle_db):
    return settle_db


# ── Task 1: Pending Bets ──────────────────────────────────────────

def test_pending_bets_returns_grouped_by_provider(client, db_session, active_profile):
    """GET /api/opportunities/play/pending-bets returns pending bets grouped by provider."""
    from src.db.models import Bet, Event

    ev1 = Event(id="ev1", home_team="Real Madrid", away_team="Barcelona",
                sport="soccer", start_time=datetime.now(timezone.utc) + timedelta(hours=2))
    ev2 = Event(id="ev2", home_team="Liverpool", away_team="Arsenal",
                sport="soccer", start_time=datetime.now(timezone.utc) + timedelta(hours=3))
    db_session.add_all([ev1, ev2])
    db_session.flush()

    b1 = Bet(profile_id=active_profile.id, event_id="ev1", provider_id="unibet",
             market="1x2", outcome="home", odds=2.10, stake=150.0, result="pending",
             placed_at=datetime.now(timezone.utc) - timedelta(hours=12))
    b2 = Bet(profile_id=active_profile.id, event_id="ev2", provider_id="unibet",
             market="total", outcome="over", odds=1.85, stake=200.0, result="pending",
             placed_at=datetime.now(timezone.utc) - timedelta(hours=6))
    b3 = Bet(profile_id=active_profile.id, event_id="ev1", provider_id="betsson",
             market="1x2", outcome="away", odds=3.40, stake=100.0, result="pending",
             placed_at=datetime.now(timezone.utc) - timedelta(hours=8))
    b4 = Bet(profile_id=active_profile.id, event_id="ev1", provider_id="unibet",
             market="1x2", outcome="draw", odds=3.20, stake=80.0, result="won", payout=256.0,
             placed_at=datetime.now(timezone.utc) - timedelta(hours=24))
    db_session.add_all([b1, b2, b3, b4])
    db_session.commit()

    resp = client.get("/api/opportunities/play/pending-bets")
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_pending"] == 3
    assert data["total_stake"] == 450.0
    assert len(data["providers"]) == 2

    unibet = next(p for p in data["providers"] if p["provider_id"] == "unibet")
    assert unibet["pending_count"] == 2
    assert len(unibet["bets"]) == 2

    betsson = next(p for p in data["providers"] if p["provider_id"] == "betsson")
    assert betsson["pending_count"] == 1


def test_pending_bets_empty(client, db_session, active_profile):
    """GET /api/opportunities/play/pending-bets returns empty when no pending bets."""
    resp = client.get("/api/opportunities/play/pending-bets")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_pending"] == 0
    assert data["providers"] == []


# ── Task 2: Settle Bet ────────────────────────────────────────────

def test_settle_bet_won(client, db_session, active_profile):
    """POST /api/opportunities/play/settle-bet settles a bet as won."""
    from src.db.models import Bet, Event

    ev = Event(id="ev-settle", home_team="PSG", away_team="Bayern",
               sport="soccer", start_time=datetime.now(timezone.utc) + timedelta(hours=1))
    db_session.add(ev)
    db_session.flush()

    bet = Bet(profile_id=active_profile.id, event_id="ev-settle", provider_id="betsson",
              market="1x2", outcome="home", odds=2.50, stake=100.0, result="pending")
    db_session.add(bet)
    db_session.commit()
    bet_id = bet.id

    resp = client.post("/api/opportunities/play/settle-bet",
                       json={"bet_id": bet_id, "result": "won"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["bet_id"] == bet_id
    assert data["result"] == "won"
    assert data["payout"] == 250.0

    db_session.refresh(bet)
    assert bet.result == "won"
    assert bet.payout == 250.0
    assert bet.settled_at is not None


def test_settle_bet_lost(client, db_session, active_profile):
    """POST /api/opportunities/play/settle-bet settles a bet as lost."""
    from src.db.models import Bet, Event

    ev = Event(id="ev-settle2", home_team="Inter", away_team="Milan",
               sport="soccer", start_time=datetime.now(timezone.utc) + timedelta(hours=1))
    db_session.add(ev)
    db_session.flush()

    bet = Bet(profile_id=active_profile.id, event_id="ev-settle2", provider_id="unibet",
              market="1x2", outcome="home", odds=1.90, stake=200.0, result="pending")
    db_session.add(bet)
    db_session.commit()

    resp = client.post("/api/opportunities/play/settle-bet",
                       json={"bet_id": bet.id, "result": "lost"})
    assert resp.status_code == 200
    assert resp.json()["payout"] == 0.0


def test_settle_bet_void(client, db_session, active_profile):
    """POST /api/opportunities/play/settle-bet settles a bet as void (stake returned)."""
    from src.db.models import Bet, Event

    ev = Event(id="ev-settle3", home_team="Ajax", away_team="Feyenoord",
               sport="soccer", start_time=datetime.now(timezone.utc) + timedelta(hours=1))
    db_session.add(ev)
    db_session.flush()

    bet = Bet(profile_id=active_profile.id, event_id="ev-settle3", provider_id="betsson",
              market="spread", outcome="home", odds=1.95, stake=150.0, point=-1.5, result="pending")
    db_session.add(bet)
    db_session.commit()

    resp = client.post("/api/opportunities/play/settle-bet",
                       json={"bet_id": bet.id, "result": "void"})
    assert resp.status_code == 200
    assert resp.json()["payout"] == 150.0


def test_settle_bet_not_found(client, db_session, active_profile):
    """POST /api/opportunities/play/settle-bet returns 404 for unknown bet."""
    resp = client.post("/api/opportunities/play/settle-bet",
                       json={"bet_id": 99999, "result": "won"})
    assert resp.status_code == 404
