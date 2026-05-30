# backend/tests/test_bet_service_freebet.py
"""Server-side freebet is_bonus derivation in BetService.create_bet.

The mirror records every bet with is_bonus=False. When a provider sits in the
freebet_available phase, the bet being placed IS the freebet, so create_bet
should flag it is_bonus server-side (and the existing block auto-completes the
bonus). Guarded on stake ≈ the freebet amount so a small cash bet placed during
the phase isn't misflagged.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Base, Bet, Profile, ProfileProviderBalance, ProfileProviderBonus, Provider
from src.services.bet_service import BetService


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Profile(id=1, name="test", is_active=True, bankroll=10000, currency="SEK"))
    session.add(Provider(id="unibet", name="Unibet"))
    session.commit()
    yield session
    session.close()


def _freebet_available(provider_id="unibet", amount=1000.0):
    return ProfileProviderBonus(
        profile_id=1,
        provider_id=provider_id,
        bonus_status="freebet_available",
        bonus_type="freebet",
        bonus_amount=amount,
        wagering_requirement=amount,
        wagered_amount=amount,
    )


def test_freebet_placement_flagged_and_completed(db: Session):
    """A full-value bet during freebet_available is flagged is_bonus AND
    auto-completes the bonus — even with zero balance (derivation must run
    before the balance check)."""
    db.add(_freebet_available())
    db.commit()

    service = BetService(db)
    result = service.create_bet(
        event_id=None,
        provider_id="unibet",
        market="1x2",
        outcome="1",
        odds=2.5,
        stake=1000,  # full freebet token value; no balance seeded
        is_bonus=False,
    )

    assert result.get("success") is True, result
    bet = db.query(Bet).filter(Bet.id == result["bet_id"]).first()
    assert bet.is_bonus is True
    bonus = db.query(ProfileProviderBonus).filter(ProfileProviderBonus.provider_id == "unibet").first()
    assert bonus.bonus_status == "completed"


def test_small_cash_bet_during_freebet_not_misflagged(db: Session):
    """A small cash bet placed while freebet_available stays is_bonus=False and
    leaves the bonus untouched (stake < amount * 0.9)."""
    db.add(_freebet_available())
    db.add(ProfileProviderBalance(profile_id=1, provider_id="unibet", balance=1000))
    db.commit()

    service = BetService(db)
    result = service.create_bet(
        event_id=None,
        provider_id="unibet",
        market="1x2",
        outcome="1",
        odds=2.5,
        stake=50,
        is_bonus=False,
    )

    assert result.get("success") is True, result
    bet = db.query(Bet).filter(Bet.id == result["bet_id"]).first()
    assert bet.is_bonus is False
    bonus = db.query(ProfileProviderBonus).filter(ProfileProviderBonus.provider_id == "unibet").first()
    assert bonus.bonus_status == "freebet_available"


def test_normal_bet_without_bonus_row_not_flagged(db: Session):
    """Regression: a normal bet with no bonus row stays is_bonus=False."""
    db.add(ProfileProviderBalance(profile_id=1, provider_id="unibet", balance=2000))
    db.commit()

    service = BetService(db)
    result = service.create_bet(
        event_id=None,
        provider_id="unibet",
        market="1x2",
        outcome="1",
        odds=2.5,
        stake=1000,
        is_bonus=False,
    )

    assert result.get("success") is True, result
    bet = db.query(Bet).filter(Bet.id == result["bet_id"]).first()
    assert bet.is_bonus is False
