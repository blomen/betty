"""ProfileRepo balance methods resolve through the shared Account layer."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db.models import Account, Base, Profile, Provider
from src.repositories.account_repo import AccountRepo
from src.repositories.profile_repo import ProfileRepo


@pytest.fixture
def session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = Session(eng)
    s.add_all(
        [
            Provider(id="polymarket", name="P"),
            Provider(id="pinnacle", name="Pin"),
            Provider(id="betinia", name="B"),
        ]
    )
    s.add_all([Profile(name="edge", kind="edge", is_active=True), Profile(name="camp", kind="bonus")])
    s.flush()
    return s


def _ids(s):
    edge = s.query(Profile).filter_by(name="edge").one()
    camp = s.query(Profile).filter_by(name="camp").one()
    return edge.id, camp.id


def test_set_balance_creates_shared_sharp_account(session):
    edge_id, camp_id = _ids(session)
    pr = ProfileRepo(session)
    pr.set_balance(edge_id, "polymarket", 76.29)
    session.flush()
    # one shared sharp account labeled rasmus
    accts = session.query(Account).filter_by(provider_id="polymarket").all()
    assert len(accts) == 1
    assert accts[0].kind == "sharp" and accts[0].label == "rasmus"


def test_sharp_balance_shared_after_linking_both_profiles(session):
    edge_id, camp_id = _ids(session)
    pr = ProfileRepo(session)
    pr.set_balance(edge_id, "polymarket", 76.29)
    session.flush()
    # link the shared account to camp (as AccountService would for use-shared-sharp)
    ar = AccountRepo(session)
    shared = ar.resolve(edge_id, "polymarket")
    ar.link(camp_id, shared.id)
    session.flush()
    # setting under camp updates the SAME balance edge sees
    pr.set_balance(camp_id, "polymarket", 90.0)
    session.flush()
    assert pr.get_balance(edge_id, "polymarket") == 90.0


def test_soft_balance_is_per_profile(session):
    edge_id, camp_id = _ids(session)
    pr = ProfileRepo(session)
    pr.set_balance(edge_id, "betinia", 100.0)
    pr.set_balance(camp_id, "betinia", 500.0)
    session.flush()
    # two distinct soft accounts (one per profile)
    accts = session.query(Account).filter_by(provider_id="betinia").all()
    assert len(accts) == 2
    assert pr.get_balance(edge_id, "betinia") == 100.0
    assert pr.get_balance(camp_id, "betinia") == 500.0


def test_total_and_stake_bankroll_with_currency(session):
    edge_id, _ = _ids(session)
    pr = ProfileRepo(session)
    # SEK soft 1000 + a sharp USD-ish provider; get_exchange_rate handles conversion.
    pr.set_balance(edge_id, "betinia", 1000.0)  # SEK -> rate 1.0
    pr.set_balance(edge_id, "pinnacle", 200.0)  # pinnacle SEK account for this user
    session.flush()
    # total includes soft; stake excludes soft (betinia)
    total = pr.get_total_bankroll(edge_id)
    pr2 = ProfileRepo(session)  # fresh repo to avoid 30s cache from prior call
    stake = pr2.get_stake_bankroll(edge_id)
    assert total >= 1200.0  # 1000 soft + 200 pinnacle (rates >=1)
    assert stake < total  # soft betinia excluded from stake basis


def test_adjust_balance_returns_new_balance(session):
    edge_id, _ = _ids(session)
    pr = ProfileRepo(session)
    pr.set_balance(edge_id, "pinnacle", 100.0)
    session.flush()
    new = pr.adjust_balance(edge_id, "pinnacle", 50.0)
    assert new == 150.0


def test_copy_balances_links_shared_accounts(session):
    edge_id, camp_id = _ids(session)
    pr = ProfileRepo(session)
    pr.set_balance(edge_id, "polymarket", 76.29)
    session.flush()
    n = pr.copy_balances(edge_id, camp_id)
    session.flush()
    assert n == 1
    # camp now resolves to the SAME shared account
    ar = AccountRepo(session)
    assert ar.resolve(camp_id, "polymarket").id == ar.resolve(edge_id, "polymarket").id
