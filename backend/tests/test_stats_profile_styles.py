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


def test_profile_to_dict_includes_style(db_session):
    from src.api.routes.profiles import profile_to_dict
    from src.repositories import ProfileRepo

    p = Profile(name="t_dict_style", style="bonus_extraction")
    db_session.add(p)
    db_session.commit()
    d = profile_to_dict(p, ProfileRepo(db_session))
    assert d["style"] == "bonus_extraction"


def test_profile_repo_get_returns_by_id(db_session):
    from src.repositories import ProfileRepo

    p = Profile(name="t_repo_get")
    db_session.add(p)
    db_session.commit()
    assert ProfileRepo(db_session).get(p.id).id == p.id


def test_profile_repo_get_none_returns_active(db_session):
    from src.repositories import ProfileRepo

    repo = ProfileRepo(db_session)
    assert repo.get(None).id == repo.get_active().id


def test_get_stats_profile_id_matches_active_when_omitted(db_session):
    from src.services import BankrollService

    svc = BankrollService(db_session)
    active_id = svc.profile_repo.get_active().id
    assert svc.get_stats()["profile_id"] == active_id
    assert svc.get_stats(active_id)["profile_id"] == active_id
