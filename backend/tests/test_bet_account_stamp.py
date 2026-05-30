"""BetRepo.create stamps account_id from (profile_id, provider_id)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db.models import Base, Profile, Provider
from src.repositories.account_repo import AccountRepo
from src.repositories.bet_repo import BetRepo


@pytest.fixture
def session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = Session(eng)
    s.add(Provider(id="polymarket", name="P"))
    s.add(Profile(name="edge", kind="edge", is_active=True))
    s.flush()
    return s


def test_create_stamps_resolved_account(session):
    profile = session.query(Profile).one()
    ar = AccountRepo(session)
    acct = ar.get_or_create("polymarket", "rasmus", "sharp", "USDC")
    ar.link(profile.id, acct.id)
    session.flush()

    repo = BetRepo(session)
    bet = repo.create(profile_id=profile.id, provider_id="polymarket", odds=2.0, stake=10, currency="USDC")
    session.flush()
    assert bet.account_id == acct.id


def test_explicit_account_id_is_preserved(session):
    profile = session.query(Profile).one()
    ar = AccountRepo(session)
    a1 = ar.get_or_create("polymarket", "rasmus", "sharp", "USDC")
    a2 = ar.get_or_create("polymarket", "alt2", "sharp", "USDC")
    ar.link(profile.id, a1.id)
    session.flush()

    repo = BetRepo(session)
    bet = repo.create(
        profile_id=profile.id, provider_id="polymarket", account_id=a2.id, odds=2.0, stake=10, currency="USDC"
    )
    session.flush()
    assert bet.account_id == a2.id  # explicit wins over resolve


def test_create_without_account_is_none_no_crash(session):
    profile = session.query(Profile).one()
    repo = BetRepo(session)
    # provider with no linked account for this profile
    bet = repo.create(profile_id=profile.id, provider_id="polymarket", odds=2.0, stake=10, currency="USDC")
    session.flush()
    assert bet.account_id is None
