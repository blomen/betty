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
