"""Account / ProfileAccount model + new-column smoke tests (in-memory SQLite)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db.models import Account, Base, Bet, Profile, ProfileAccount, Provider


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_account_and_link_roundtrip():
    s = _session()
    s.add(Provider(id="polymarket", name="Polymarket"))
    p = Profile(name="edge", kind="edge", is_active=True)
    s.add(p)
    s.flush()
    acct = Account(
        provider_id="polymarket", label="rasmus", kind="sharp", balance=76.29, currency="USDC", is_active=True
    )
    s.add(acct)
    s.flush()
    s.add(ProfileAccount(profile_id=p.id, account_id=acct.id))
    s.flush()
    assert acct.id is not None
    # relationship wiring both directions
    assert p.accounts[0].account.label == "rasmus"
    assert acct.profile_links[0].profile_id == p.id


def test_profile_kind_defaults_edge():
    s = _session()
    p = Profile(name="x")
    s.add(p)
    s.flush()
    assert p.kind == "edge"


def test_bet_account_id_nullable_and_settable():
    s = _session()
    s.add(Provider(id="pinnacle", name="Pinnacle"))
    p = Profile(name="x")
    s.add(p)
    s.flush()
    b = Bet(profile_id=p.id, provider_id="pinnacle", odds=2.0, stake=10.0, currency="SEK", result="pending")
    s.add(b)
    s.flush()
    assert b.account_id is None
    acct = Account(provider_id="pinnacle", label="rasmus", kind="sharp", currency="SEK")
    s.add(acct)
    s.flush()
    b.account_id = acct.id
    s.flush()
    assert b.account_id == acct.id


def test_account_provider_label_unique():
    import pytest
    from sqlalchemy.exc import IntegrityError

    s = _session()
    s.add(Provider(id="kalshi", name="Kalshi"))
    s.flush()
    s.add(Account(provider_id="kalshi", label="rasmus", kind="sharp", currency="USD"))
    s.flush()
    s.add(Account(provider_id="kalshi", label="rasmus", kind="sharp", currency="USD"))
    with pytest.raises(IntegrityError):
        s.flush()
