"""Tests for Mirror Stream API routes — SSE streams and bootstrap endpoints."""

import pytest
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.models import Base, Provider, Bet, BalanceLog, SettlementQueue, PriceCache, Event
from src.api.routes.mirror_stream import router as mirror_stream_router
from src.api.deps import get_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def stream_db():
    """In-memory SQLite session with all tables."""
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
def seeded_db(stream_db):
    """Seed DB with a provider, event, and bet."""
    stream_db.add(Provider(id="unibet", name="Unibet"))
    ev = Event(
        id="ev1",
        home_team="Arsenal",
        away_team="Chelsea",
        sport="soccer",
        start_time=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    stream_db.add(ev)
    stream_db.flush()
    yield stream_db


@pytest.fixture
def client(stream_db):
    """TestClient backed by a minimal FastAPI app with only mirror_stream router."""
    test_app = FastAPI()
    test_app.include_router(mirror_stream_router)

    def _override():
        try:
            yield stream_db
        except Exception:
            stream_db.rollback()
            raise

    test_app.dependency_overrides[get_db] = _override
    with TestClient(test_app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def seeded_client(seeded_db):
    """TestClient with seeded DB."""
    test_app = FastAPI()
    test_app.include_router(mirror_stream_router)

    def _override():
        try:
            yield seeded_db
        except Exception:
            seeded_db.rollback()
            raise

    test_app.dependency_overrides[get_db] = _override
    with TestClient(test_app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# GET /state/{provider_id}
# ---------------------------------------------------------------------------


def test_get_provider_state_no_data(client, stream_db):
    """GET /api/mirror/state/{provider_id} returns nulls when provider has no data."""
    stream_db.add(Provider(id="betsson", name="Betsson"))
    stream_db.commit()

    resp = client.get("/api/mirror/state/betsson")
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider_id"] == "betsson"
    assert data["balance"] is None
    assert data["pending_bets"] == 0
    assert data["pending_settlements"] == 0
    assert data["notification_status"] == "ok"


def test_get_provider_state_with_balance_and_pending(seeded_client, seeded_db):
    """GET /api/mirror/state/{provider_id} returns latest balance and pending counts."""
    # Add balance logs (oldest then newest)
    t1 = datetime.now(timezone.utc) - timedelta(minutes=10)
    t2 = datetime.now(timezone.utc) - timedelta(minutes=5)
    seeded_db.add(BalanceLog(provider_id="unibet", amount=500.0, currency="SEK",
                             source="intercepted", created_at=t1))
    seeded_db.add(BalanceLog(provider_id="unibet", amount=750.0, currency="SEK",
                             source="intercepted", created_at=t2))

    # Add a pending bet and a settled bet
    seeded_db.add(Bet(provider_id="unibet", event_id="ev1", market="1x2",
                      outcome="home", odds=2.10, stake=100.0, result="pending"))
    seeded_db.add(Bet(provider_id="unibet", event_id="ev1", market="1x2",
                      outcome="away", odds=3.50, stake=50.0, result="won"))

    # Add pending settlement
    seeded_db.add(SettlementQueue(provider_id="unibet", result="won",
                                  payout=210.0, status="pending"))
    seeded_db.commit()

    resp = seeded_client.get("/api/mirror/state/unibet")
    assert resp.status_code == 200
    data = resp.json()
    assert data["balance"] == 750.0
    assert data["pending_bets"] == 1
    assert data["pending_settlements"] == 1


# ---------------------------------------------------------------------------
# GET /prices/{provider_id}
# ---------------------------------------------------------------------------


def test_get_cached_prices_empty(seeded_client, seeded_db):
    """GET /api/mirror/prices/{provider_id} returns empty list when no cache entries."""
    resp = seeded_client.get("/api/mirror/prices/unibet")
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider_id"] == "unibet"
    assert data["prices"] == []


def test_get_cached_prices_returns_entries(seeded_client, seeded_db):
    """GET /api/mirror/prices/{provider_id} returns cached price entries."""
    seeded_db.add(PriceCache(
        provider_id="unibet", event_id="ev1",
        market="1x2", outcome="home", odds=2.15, source="intercepted",
    ))
    seeded_db.add(PriceCache(
        provider_id="unibet", event_id="ev1",
        market="1x2", outcome="away", odds=3.40, source="intercepted",
    ))
    seeded_db.commit()

    resp = seeded_client.get("/api/mirror/prices/unibet")
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider_id"] == "unibet"
    assert len(data["prices"]) == 2
    outcomes = {p["outcome"] for p in data["prices"]}
    assert outcomes == {"home", "away"}


# ---------------------------------------------------------------------------
# GET /queue
# ---------------------------------------------------------------------------


def test_get_queue_no_window(client):
    """GET /api/mirror/queue returns no_window status when fire window is closed."""
    from src.services import fire_window as fw
    fw.close_window()

    resp = client.get("/api/mirror/queue")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "no_window"
    assert data["queue"] == []
    assert data["current_provider"] is None


# ---------------------------------------------------------------------------
# POST /settlements/confirm-queue
# ---------------------------------------------------------------------------


def test_confirm_settlement_queue_no_pending(seeded_client, seeded_db):
    """POST /api/mirror/settlements/confirm-queue returns 0 when nothing pending."""
    resp = seeded_client.post(
        "/api/mirror/settlements/confirm-queue",
        json={"provider_id": "unibet"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["confirmed"] == 0
    assert data["provider_id"] == "unibet"


def test_confirm_settlement_queue_updates_bet(seeded_client, seeded_db):
    """POST /api/mirror/settlements/confirm-queue confirms pending settlements and updates bets."""
    bet = Bet(
        provider_id="unibet", event_id="ev1", market="1x2",
        outcome="home", odds=2.10, stake=100.0, result="pending",
    )
    seeded_db.add(bet)
    seeded_db.flush()

    sq = SettlementQueue(
        provider_id="unibet", bet_id=bet.id,
        result="won", payout=210.0, status="pending",
    )
    seeded_db.add(sq)
    seeded_db.commit()

    resp = seeded_client.post(
        "/api/mirror/settlements/confirm-queue",
        json={"provider_id": "unibet"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["confirmed"] == 1

    seeded_db.refresh(sq)
    assert sq.status == "confirmed"
    assert sq.confirmed_at is not None

    seeded_db.refresh(bet)
    assert bet.result == "won"
    assert bet.payout == 210.0
    assert bet.settled_at is not None


def test_confirm_settlement_queue_no_linked_bet(seeded_client, seeded_db):
    """POST /api/mirror/settlements/confirm-queue handles unlinked settlements gracefully."""
    sq = SettlementQueue(
        provider_id="unibet", bet_id=None,
        result="lost", payout=0.0, status="pending",
    )
    seeded_db.add(sq)
    seeded_db.commit()

    resp = seeded_client.post(
        "/api/mirror/settlements/confirm-queue",
        json={"provider_id": "unibet"},
    )
    assert resp.status_code == 200
    assert resp.json()["confirmed"] == 1

    seeded_db.refresh(sq)
    assert sq.status == "confirmed"


def test_confirm_settlement_queue_skips_already_confirmed(seeded_client, seeded_db):
    """POST /api/mirror/settlements/confirm-queue only processes pending (not already confirmed)."""
    sq_confirmed = SettlementQueue(
        provider_id="unibet", bet_id=None,
        result="won", payout=150.0, status="confirmed",
        confirmed_at=datetime.now(timezone.utc),
    )
    sq_pending = SettlementQueue(
        provider_id="unibet", bet_id=None,
        result="lost", payout=0.0, status="pending",
    )
    seeded_db.add_all([sq_confirmed, sq_pending])
    seeded_db.commit()

    resp = seeded_client.post(
        "/api/mirror/settlements/confirm-queue",
        json={"provider_id": "unibet"},
    )
    assert resp.status_code == 200
    assert resp.json()["confirmed"] == 1  # Only the pending one
