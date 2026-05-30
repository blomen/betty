"""Tests for Stats page per-profile account styles."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api import app
from src.api.deps import get_db
from src.db.models import Base, Bet, Profile  # noqa: F401


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    s.add(Profile(id=1, name="test", is_active=True))
    s.commit()
    yield s
    s.close()


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_profile_has_style_default_personal(db_session):
    p = Profile(name="t_style_default")
    db_session.add(p)
    db_session.commit()
    assert p.style == "personal"


def test_profile_style_settable(db_session):
    p = Profile(name="t_style_bonus", style="bonus_extraction")
    db_session.add(p)
    db_session.commit()
    assert p.style == "bonus_extraction"
