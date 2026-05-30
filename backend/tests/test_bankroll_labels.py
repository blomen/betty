"""get_bankroll exposes account label + id per provider, with live balances."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Profile, Provider
from src.repositories.profile_repo import ProfileRepo
from src.services.bankroll_service import BankrollService


@pytest.fixture
def session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    Maker = sessionmaker(bind=eng)
    s = Maker()
    s.add(Provider(id="polymarket", name="Poly", is_enabled=True))
    s.add(Profile(name="rasmus-edge", kind="edge", is_active=True))
    s.commit()
    return s


def _provider_entry(out: dict, provider_id: str) -> dict:
    return next(p for p in out["providers"] if p["id"] == provider_id)


def test_get_bankroll_includes_label_and_account_id(session):
    profile = session.query(Profile).one()
    pr = ProfileRepo(session)
    pr.set_balance(profile.id, "polymarket", 76.29)
    session.commit()

    svc = BankrollService(session)
    out = svc.get_bankroll()
    info = _provider_entry(out, "polymarket")
    assert info["label"] == "rasmus"  # sharp pool default label
    assert info["account_id"] is not None
    assert info["balance"] == 76.29  # live account balance, not frozen PPB
    # existing contract preserved
    assert "currency" in info and "exchange_rate_sek" in info and "balance_sek" in info


def test_get_bankroll_label_none_when_no_account(session):
    # An enabled provider the profile never funded has no account yet.
    session.add(Provider(id="betinia", name="Betinia", is_enabled=True))
    session.commit()
    svc = BankrollService(session)
    out = svc.get_bankroll()
    info = _provider_entry(out, "betinia")
    assert info["label"] is None
    assert info["account_id"] is None
    assert info["balance"] == 0.0


def test_get_bankroll_total_reflects_live_balance(session):
    profile = session.query(Profile).one()
    pr = ProfileRepo(session)
    pr.set_balance(profile.id, "polymarket", 10.0)
    session.commit()
    svc = BankrollService(session)
    out = svc.get_bankroll()
    # polymarket is USDC; total is SEK-converted. Just assert it's > native
    # balance (rate >= 1) and tracks the live value, not 0.
    assert out["total"] >= 10.0
