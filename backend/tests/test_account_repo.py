"""Tests for AccountRepo — shared-account resolver and link queries."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db.models import Base, Bet, Profile, Provider
from src.repositories.account_repo import AccountRepo


@pytest.fixture
def repo_and_profiles():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = Session(eng)
    s.add_all([Provider(id="polymarket", name="P"), Provider(id="betinia", name="B")])
    edge = Profile(name="edge", kind="edge", is_active=True)
    camp = Profile(name="camp", kind="bonus")
    s.add_all([edge, camp])
    s.flush()
    return AccountRepo(s), edge, camp


def test_get_or_create_is_idempotent(repo_and_profiles):
    repo, edge, _ = repo_and_profiles
    a1 = repo.get_or_create(provider_id="polymarket", label="rasmus", kind="sharp", currency="USDC")
    a2 = repo.get_or_create(provider_id="polymarket", label="rasmus", kind="sharp", currency="USDC")
    assert a1.id == a2.id


def test_shared_sharp_resolves_same_account_across_profiles(repo_and_profiles):
    repo, edge, camp = repo_and_profiles
    acct = repo.get_or_create(provider_id="polymarket", label="rasmus", kind="sharp", currency="USDC")
    repo.link(edge.id, acct.id)
    repo.link(camp.id, acct.id)
    repo.db.flush()
    assert repo.resolve(edge.id, "polymarket").id == repo.resolve(camp.id, "polymarket").id


def test_set_balance_is_shared(repo_and_profiles):
    repo, edge, camp = repo_and_profiles
    acct = repo.get_or_create(provider_id="polymarket", label="rasmus", kind="sharp", currency="USDC")
    repo.link(edge.id, acct.id)
    repo.link(camp.id, acct.id)
    repo.db.flush()
    repo.set_balance(acct.id, 80.0)
    repo.db.flush()
    assert repo.resolve(camp.id, "polymarket").balance == 80.0


def test_distinct_accounts_dedupes_shared(repo_and_profiles):
    repo, edge, camp = repo_and_profiles
    acct = repo.get_or_create(provider_id="polymarket", label="rasmus", kind="sharp", currency="USDC")
    repo.link(edge.id, acct.id)
    repo.link(camp.id, acct.id)
    repo.db.flush()
    assert len(repo.distinct_accounts()) == 1


def test_fresh_account_not_visible_to_other_profile(repo_and_profiles):
    repo, edge, camp = repo_and_profiles
    fresh = repo.get_or_create(provider_id="polymarket", label="alt2", kind="sharp", currency="USDC")
    repo.link(camp.id, fresh.id)
    repo.db.flush()
    assert repo.resolve(edge.id, "polymarket") is None
    assert repo.resolve(camp.id, "polymarket").label == "alt2"


def test_accounts_for_profile_excludes_inactive(repo_and_profiles):
    repo, edge, _ = repo_and_profiles
    a = repo.get_or_create(provider_id="polymarket", label="rasmus", kind="sharp", currency="USDC")
    b = repo.get_or_create(provider_id="betinia", label="edge", kind="soft", currency="SEK")
    repo.link(edge.id, a.id)
    repo.link(edge.id, b.id)
    b.is_active = False
    repo.db.flush()
    provs = {acct.provider_id for acct in repo.accounts_for_profile(edge.id)}
    assert provs == {"polymarket"}


def test_resolve_ignores_inactive_accounts(repo_and_profiles):
    repo, edge, _ = repo_and_profiles
    a = repo.get_or_create(provider_id="polymarket", label="rasmus", kind="sharp", currency="USDC")
    repo.link(edge.id, a.id)
    a.is_active = False
    repo.db.flush()
    assert repo.resolve(edge.id, "polymarket") is None


def test_link_count_and_has_bets(repo_and_profiles):
    repo, edge, camp = repo_and_profiles
    a = repo.get_or_create(provider_id="polymarket", label="rasmus", kind="sharp", currency="USDC")
    repo.link(edge.id, a.id)
    repo.link(camp.id, a.id)
    repo.db.flush()
    assert repo.link_count(a.id) == 2
    assert repo.has_bets(a.id) is False
    repo.db.add(Bet(profile_id=edge.id, provider_id="polymarket", account_id=a.id, odds=2.0, stake=5, currency="USDC"))
    repo.db.flush()
    assert repo.has_bets(a.id) is True


def test_unlink(repo_and_profiles):
    repo, edge, camp = repo_and_profiles
    a = repo.get_or_create(provider_id="polymarket", label="rasmus", kind="sharp", currency="USDC")
    repo.link(edge.id, a.id)
    repo.link(camp.id, a.id)
    repo.db.flush()
    repo.unlink(edge.id, a.id)
    repo.db.flush()
    assert repo.link_count(a.id) == 1
    assert repo.resolve(edge.id, "polymarket") is None


def test_link_rejects_second_active_account_for_same_provider(repo_and_profiles):
    """resolve() assumes ≤1 active account per (profile, provider); linking a
    second active account for the same provider must raise, not silently corrupt
    which account balance reads/writes route to."""
    import pytest

    repo, edge, _ = repo_and_profiles
    a = repo.get_or_create(provider_id="polymarket", label="rasmus", kind="sharp", currency="USDC")
    b = repo.get_or_create(provider_id="polymarket", label="alt2", kind="sharp", currency="USDC")
    repo.link(edge.id, a.id)
    repo.db.flush()
    with pytest.raises(ValueError):
        repo.link(edge.id, b.id)


def test_link_same_account_twice_is_noop(repo_and_profiles):
    repo, edge, _ = repo_and_profiles
    a = repo.get_or_create(provider_id="polymarket", label="rasmus", kind="sharp", currency="USDC")
    repo.link(edge.id, a.id)
    repo.link(edge.id, a.id)  # idempotent — must not raise
    repo.db.flush()
    assert repo.link_count(a.id) == 1
