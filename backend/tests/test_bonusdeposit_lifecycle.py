# backend/tests/test_bonusdeposit_lifecycle.py
"""Bonusdeposit state-machine lifecycle: two-phase, wager-first, single-phase."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Base, Bet, Profile, ProfileProviderBalance, ProfileProviderBonus, Provider
from src.repositories import ProfileRepo
from src.services.bet_service import BetService


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(Profile(id=1, name="t", is_active=True, bankroll=100000, currency="SEK"))
    for pid in ("betinia", "leovegas", "speedybet"):
        s.add(Provider(id=pid, name=pid))
    s.commit()
    yield s
    s.close()


def _bal(repo, pid):
    return repo.get_balance(1, pid)


def test_two_phase_trigger_then_main(db: Session):
    repo = ProfileRepo(db)
    db.add(ProfileProviderBalance(profile_id=1, provider_id="betinia", balance=1000))
    db.commit()
    repo.start_bonus_trigger(
        1,
        "betinia",
        bonus_amount=1000,
        trigger_wagering=1000,
        trigger_min_odds=1.50,
        main_wagering_multiplier=8,
        main_min_odds=1.80,
        deposit_amount=1000,
    )
    st = repo.get_bonus_status(1, "betinia")
    assert st["status"] == "trigger_needed"
    assert st["wagering_requirement"] == 1000

    repo.record_wagering(1, "betinia", stake=1000, odds=2.0)
    st = repo.get_bonus_status(1, "betinia")
    assert st["status"] == "in_progress"
    assert _bal(repo, "betinia") == 2000
    assert st["wagering_requirement"] == 8000
    assert st["min_odds"] == 1.80

    repo.record_wagering(1, "betinia", stake=8000, odds=2.0)
    assert repo.get_bonus_status(1, "betinia")["status"] == "completed"


def test_wager_first_completes_at_trigger(db: Session):
    repo = ProfileRepo(db)
    db.add(ProfileProviderBalance(profile_id=1, provider_id="leovegas", balance=600))
    db.commit()
    repo.start_bonus_trigger(
        1,
        "leovegas",
        bonus_amount=600,
        trigger_wagering=3600,
        trigger_min_odds=1.80,
        main_wagering_multiplier=0,
        main_min_odds=1.80,
        deposit_amount=600,
    )
    repo.record_wagering(1, "leovegas", stake=3600, odds=1.9)
    st = repo.get_bonus_status(1, "leovegas")
    assert st["status"] == "completed"
    assert _bal(repo, "leovegas") == 1200


def test_single_phase_immediate(db: Session):
    repo = ProfileRepo(db)
    repo.start_bonus_wagering(1, "speedybet", bonus_amount=500, wagering_multiplier=10, min_odds=1.80)
    st = repo.get_bonus_status(1, "speedybet")
    assert st["status"] == "in_progress"
    assert st["wagering_requirement"] == 5000
    repo.record_wagering(1, "speedybet", stake=5000, odds=2.0)
    assert repo.get_bonus_status(1, "speedybet")["status"] == "completed"


def test_min_odds_gate_blocks_low_odds_bets(db: Session):
    repo = ProfileRepo(db)
    repo.start_bonus_trigger(
        1,
        "betinia",
        bonus_amount=1000,
        trigger_wagering=1000,
        trigger_min_odds=1.80,
        main_wagering_multiplier=8,
        main_min_odds=1.80,
        deposit_amount=1000,
    )
    repo.record_wagering(1, "betinia", stake=1000, odds=1.50)
    st = repo.get_bonus_status(1, "betinia")
    assert st["status"] == "trigger_needed"
    assert st["wagered_amount"] == 0


def test_no_double_credit_on_repeated_record(db: Session):
    repo = ProfileRepo(db)
    db.add(ProfileProviderBalance(profile_id=1, provider_id="betinia", balance=1000))
    db.commit()
    repo.start_bonus_trigger(
        1,
        "betinia",
        bonus_amount=1000,
        trigger_wagering=1000,
        trigger_min_odds=1.50,
        main_wagering_multiplier=8,
        main_min_odds=1.80,
        deposit_amount=1000,
    )
    repo.record_wagering(1, "betinia", stake=1000, odds=2.0)
    assert _bal(repo, "betinia") == 2000
    repo.record_wagering(1, "betinia", stake=2000, odds=2.0)
    assert _bal(repo, "betinia") == 2000


def test_is_bonus_not_misfired_for_bonusdeposit_in_progress(db: Session):
    db.add(ProfileProviderBalance(profile_id=1, provider_id="betinia", balance=10000))
    db.add(
        ProfileProviderBonus(
            profile_id=1,
            provider_id="betinia",
            bonus_status="in_progress",
            bonus_type="bonusdeposit",
            bonus_amount=1000,
            wagering_requirement=8000,
            wagered_amount=0.0,
            min_odds=1.80,
        )
    )
    db.commit()
    result = BetService(db).create_bet(
        event_id=None,
        provider_id="betinia",
        market="1x2",
        outcome="1",
        odds=2.0,
        stake=1000,
        is_bonus=False,
    )
    assert result.get("success") is True, result
    bet = db.query(Bet).filter(Bet.id == result["bet_id"]).first()
    assert bet.is_bonus is False
