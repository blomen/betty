"""Integration tests: profile creation seeds bonus rows; manual seed endpoint."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.deps import get_db
from src.api.routes.profiles import router as profiles_router
from src.db.models import Base, ProfileProviderBonus, Provider


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    session.add_all(
        [
            Provider(id="unibet", name="Unibet", is_enabled=True),
            Provider(id="leovegas", name="LeoVegas", is_enabled=True),
        ]
    )
    session.commit()

    def _override_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    monkeypatch.setattr(
        "src.api.routes.providers.load_provider_bonuses",
        lambda: {
            "unibet": {"type": "freebet", "amount": 1000},
            "leovegas": {"type": "bonusdeposit", "amount": 600},
        },
    )

    app = FastAPI()
    app.include_router(profiles_router)
    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as c:
        yield c, SessionLocal
    session.close()


def test_create_profile_seeds_bonus_rows(client):
    c, SessionLocal = client
    resp = c.post("/api/profiles", json={"name": "Audit"})
    assert resp.status_code == 200
    pid = resp.json()["profile"]["id"]

    s = SessionLocal()
    try:
        rows = s.query(ProfileProviderBonus).filter_by(profile_id=pid).all()
        assert {r.provider_id for r in rows} == {"unibet", "leovegas"}
        assert all(r.bonus_status == "available" for r in rows)
    finally:
        s.close()


def test_seed_endpoint_is_idempotent(client):
    c, SessionLocal = client
    pid = c.post("/api/profiles", json={"name": "Audit"}).json()["profile"]["id"]
    resp = c.post(f"/api/profiles/{pid}/seed-bonuses")
    assert resp.status_code == 200
    assert resp.json()["inserted"] == 0  # already seeded by create

    s = SessionLocal()
    try:
        assert s.query(ProfileProviderBonus).filter_by(profile_id=pid).count() == 2
    finally:
        s.close()


def test_create_bonus_profile_shares_edge_sharp_pool(client):
    """End-to-end: an edge profile funds a sharp account; a bonus profile created
    with use_shared_sharp links the SAME account (kind persisted, no copy)."""
    from src.db.models import Account, Provider
    from src.repositories.account_repo import AccountRepo
    from src.repositories.profile_repo import ProfileRepo

    c, SessionLocal = client

    # First profile is the edge profile (auto-active). Fund a sharp account.
    edge = c.post("/api/profiles", json={"name": "edge", "kind": "edge"}).json()["profile"]
    assert edge["kind"] == "edge"
    s = SessionLocal()
    try:
        s.add(Provider(id="polymarket", name="Poly", is_enabled=True))
        s.commit()
        ProfileRepo(s).set_balance(edge["id"], "polymarket", 76.29)
        s.commit()
    finally:
        s.close()

    # Bonus profile with shared sharp — should link the SAME polymarket account.
    camp = c.post(
        "/api/profiles",
        json={"name": "camp", "kind": "bonus", "use_shared_sharp": True},
    ).json()["profile"]
    assert camp["kind"] == "bonus"

    s = SessionLocal()
    try:
        ar = AccountRepo(s)
        shared = ar.resolve(camp["id"], "polymarket")
        assert shared is not None
        assert shared.id == ar.resolve(edge["id"], "polymarket").id
        assert shared.balance == 76.29  # shared, not a zeroed copy
        # exactly one polymarket account exists (shared, not duplicated)
        assert s.query(Account).filter_by(provider_id="polymarket").count() == 1
    finally:
        s.close()
