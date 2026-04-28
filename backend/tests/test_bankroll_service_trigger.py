"""Tests for the new bonus_trigger_amount field on /api/bankroll."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Profile, ProfileProviderBalance, ProfileProviderBonus, Provider
from src.services.bankroll_service import BankrollService


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Profile(id=1, name="Audit", is_active=True))
    session.add_all(
        [
            Provider(id="unibet", name="Unibet", is_enabled=True),
            Provider(id="leovegas", name="LeoVegas", is_enabled=True),
            Provider(id="pinnacle", name="Pinnacle", is_enabled=True),
        ]
    )
    session.add_all(
        [
            ProfileProviderBonus(profile_id=1, provider_id="unibet", bonus_status="available"),
            ProfileProviderBonus(profile_id=1, provider_id="leovegas", bonus_status="available"),
        ]
    )
    session.commit()
    monkeypatch.setattr(
        "src.api.routes.providers.load_provider_bonuses",
        lambda: {
            "unibet": {"type": "freebet", "amount": 1000},
            "leovegas": {"type": "bonusdeposit", "amount": 600},
        },
    )
    yield session
    session.close()


def test_trigger_populated_when_balance_zero_and_available(db):
    out = BankrollService(db).get_bankroll()
    by_id = {p["id"]: p for p in out["providers"]}
    assert by_id["unibet"]["bonus_trigger_amount"] == 1000
    assert by_id["unibet"]["bonus_currency"] == "SEK"
    assert by_id["leovegas"]["bonus_trigger_amount"] == 600


def test_trigger_null_when_no_bonus_in_yaml(db):
    out = BankrollService(db).get_bankroll()
    by_id = {p["id"]: p for p in out["providers"]}
    assert by_id["pinnacle"]["bonus_trigger_amount"] is None


def test_trigger_null_when_balance_already_covers_amount(db):
    db.add(ProfileProviderBalance(profile_id=1, provider_id="leovegas", balance=600))
    db.commit()
    out = BankrollService(db).get_bankroll()
    by_id = {p["id"]: p for p in out["providers"]}
    assert by_id["leovegas"]["bonus_trigger_amount"] is None


def test_trigger_null_when_bonus_not_available(db):
    bonus = db.query(ProfileProviderBonus).filter_by(provider_id="unibet").one()
    bonus.bonus_status = "in_progress"
    db.commit()
    out = BankrollService(db).get_bankroll()
    by_id = {p["id"]: p for p in out["providers"]}
    assert by_id["unibet"]["bonus_trigger_amount"] is None
