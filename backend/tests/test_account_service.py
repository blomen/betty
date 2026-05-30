"""Tests for AccountService — profile provisioning + delete GC."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db.models import Account, Base, Bet, Profile, Provider
from src.repositories.account_repo import AccountRepo
from src.services.account_service import AccountService


@pytest.fixture
def session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = Session(eng)
    s.add_all(
        [
            Provider(id="polymarket", name="P"),
            Provider(id="pinnacle", name="Pin"),
            Provider(id="kalshi", name="K"),
            Provider(id="cloudbet", name="C"),
            Provider(id="betinia", name="B"),
        ]
    )
    edge = Profile(name="edge", kind="edge", is_active=True)
    s.add(edge)
    s.flush()
    # Edge profile already holds the shared sharp pool.
    ar = AccountRepo(s)
    for prov in ("polymarket", "pinnacle", "kalshi", "cloudbet"):
        acct = ar.get_or_create(prov, "rasmus", "sharp", "USDC")
        ar.link(edge.id, acct.id)
    s.flush()
    return s, edge


def test_use_shared_sharp_links_existing_no_new_rows(session):
    s, edge = session
    before = s.query(Account).filter(Account.kind == "sharp").count()
    camp = Profile(name="camp", kind="bonus")
    s.add(camp)
    s.flush()
    AccountService(s).provision(camp, use_shared_sharp=True, fresh_sharp_label=None, soft_providers=["betinia"])
    s.flush()
    after = s.query(Account).filter(Account.kind == "sharp").count()
    assert after == before  # no new sharp accounts created
    ar = AccountRepo(s)
    # camp sees the SAME shared poly account as edge
    assert ar.resolve(camp.id, "polymarket").id == ar.resolve(edge.id, "polymarket").id
    # and got its own soft betinia account
    assert ar.resolve(camp.id, "betinia").kind == "soft"
    assert ar.resolve(camp.id, "betinia").label == "camp"


def test_fresh_sharp_creates_isolated_accounts(session):
    s, edge = session
    camp = Profile(name="camp", kind="bonus")
    s.add(camp)
    s.flush()
    AccountService(s).provision(camp, use_shared_sharp=False, fresh_sharp_label="alt2", soft_providers=[])
    s.flush()
    ar = AccountRepo(s)
    assert ar.resolve(camp.id, "polymarket").label == "alt2"
    assert ar.resolve(edge.id, "polymarket").label == "rasmus"  # unchanged
    # not visible to each other: edge still resolves only its own
    assert ar.resolve(camp.id, "polymarket").id != ar.resolve(edge.id, "polymarket").id


def test_delete_gc_shared_survives_softdeletes_with_bets(session):
    s, edge = session
    camp = Profile(name="camp", kind="bonus")
    s.add(camp)
    s.flush()
    svc = AccountService(s)
    svc.provision(camp, use_shared_sharp=True, fresh_sharp_label=None, soft_providers=["betinia"])
    s.flush()
    ar = AccountRepo(s)
    betinia = ar.resolve(camp.id, "betinia")
    poly = ar.resolve(camp.id, "polymarket")
    # give the soft account a bet so it must be soft-deleted, not removed
    s.add(
        Bet(
            profile_id=camp.id,
            provider_id="betinia",
            account_id=betinia.id,
            odds=2.0,
            stake=10,
            currency="SEK",
            result="won",
            payout=20.0,
        )
    )
    s.flush()
    svc.delete_profile_accounts(camp)
    s.flush()
    # shared poly survives (still linked to edge)
    assert s.get(Account, poly.id).is_active is True
    # betinia has bets -> soft-deleted, not removed
    assert s.get(Account, betinia.id).is_active is False


def test_delete_gc_betless_soft_is_hard_deleted(session):
    s, edge = session
    camp = Profile(name="camp", kind="bonus")
    s.add(camp)
    s.flush()
    svc = AccountService(s)
    svc.provision(camp, use_shared_sharp=True, fresh_sharp_label=None, soft_providers=["betinia"])
    s.flush()
    betinia_id = AccountRepo(s).resolve(camp.id, "betinia").id
    svc.delete_profile_accounts(camp)
    s.flush()
    assert s.get(Account, betinia_id) is None  # bet-less orphan hard-deleted


def test_fresh_sharp_betless_orphan_hard_deleted_on_delete(session):
    s, edge = session
    camp = Profile(name="camp", kind="bonus")
    s.add(camp)
    s.flush()
    svc = AccountService(s)
    svc.provision(camp, use_shared_sharp=False, fresh_sharp_label="alt2", soft_providers=[])
    s.flush()
    poly_alt = AccountRepo(s).resolve(camp.id, "polymarket").id
    svc.delete_profile_accounts(camp)
    s.flush()
    assert s.get(Account, poly_alt) is None  # fresh sharp, no bets, single-linked -> gone
