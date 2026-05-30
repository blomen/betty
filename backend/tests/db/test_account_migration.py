"""Backfill: ProfileProviderBalance -> Account + ProfileAccount + bets.account_id.

Tests `_migrate_provider_balances_to_accounts` directly against an in-memory
engine. create_all builds every column on the fresh test DB, so these tests
exercise only the data-movement logic (not the column-ALTER guards, which only
matter for pre-existing DBs and are covered by prod startup).
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db.models import (
    Account,
    Base,
    Bet,
    Profile,
    ProfileAccount,
    ProfileProviderBalance,
    Provider,
    _migrate_provider_balances_to_accounts,
)


def _engine_with_old_data():
    """Edge + bonus profile sharing one sharp (polymarket) account; bonus also
    holds a soft book (betinia). Two settled bets to backfill."""
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        s.add_all([Provider(id="polymarket", name="Poly"), Provider(id="betinia", name="Betinia")])
        edge = Profile(id=1, name="edge", is_active=True, kind="edge")
        camp = Profile(id=2, name="campaign", is_active=False, kind="bonus")
        s.add_all([edge, camp])
        s.flush()
        s.add_all(
            [
                ProfileProviderBalance(profile_id=1, provider_id="polymarket", balance=76.29),
                ProfileProviderBalance(profile_id=2, provider_id="polymarket", balance=76.29),
                ProfileProviderBalance(profile_id=2, provider_id="betinia", balance=500.0),
            ]
        )
        s.add_all(
            [
                Bet(id=10, profile_id=1, provider_id="polymarket", odds=2.0, stake=10, currency="USDC", result="won"),
                Bet(id=11, profile_id=2, provider_id="betinia", odds=2.0, stake=10, currency="SEK", result="lost"),
            ]
        )
        s.commit()
    return eng


def test_sharp_collapses_to_one_shared_account():
    eng = _engine_with_old_data()
    _migrate_provider_balances_to_accounts(eng)
    with Session(eng) as s:
        poly = s.query(Account).filter_by(provider_id="polymarket").all()
        assert len(poly) == 1
        acct = poly[0]
        assert acct.kind == "sharp"
        assert acct.label == "rasmus"
        assert abs(acct.balance - 76.29) < 1e-6
        # linked to BOTH profiles
        links = s.query(ProfileAccount).filter_by(account_id=acct.id).all()
        assert {link.profile_id for link in links} == {1, 2}


def test_soft_is_per_profile_single_linked():
    eng = _engine_with_old_data()
    _migrate_provider_balances_to_accounts(eng)
    with Session(eng) as s:
        betinia = s.query(Account).filter_by(provider_id="betinia").one()
        assert betinia.kind == "soft"
        assert betinia.label == "campaign"  # labeled from profile name
        links = s.query(ProfileAccount).filter_by(account_id=betinia.id).all()
        assert {link.profile_id for link in links} == {2}


def test_bets_backfilled_to_correct_account():
    eng = _engine_with_old_data()
    _migrate_provider_balances_to_accounts(eng)
    with Session(eng) as s:
        poly = s.query(Account).filter_by(provider_id="polymarket").one()
        betinia = s.query(Account).filter_by(provider_id="betinia").one()
        assert s.get(Bet, 10).account_id == poly.id
        assert s.get(Bet, 11).account_id == betinia.id


def test_idempotent_second_run_is_noop():
    eng = _engine_with_old_data()
    _migrate_provider_balances_to_accounts(eng)
    _migrate_provider_balances_to_accounts(eng)
    with Session(eng) as s:
        assert s.query(Account).count() == 2  # poly + betinia, not duplicated
        assert s.query(ProfileAccount).count() == 3  # poly×2 + betinia×1


def test_empty_db_no_crash():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    _migrate_provider_balances_to_accounts(eng)  # must not raise
    with Session(eng) as s:
        assert s.query(Account).count() == 0
