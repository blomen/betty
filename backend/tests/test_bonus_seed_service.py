"""Tests for the ProfileProviderBonus seeding helper."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Profile, ProfileProviderBonus, Provider
from src.services.bonus_seed_service import seed_provider_bonuses


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    profile = Profile(id=1, name="Audit", is_active=False)
    session.add(profile)
    # Both providers exist in DB; only "unibet" + "leovegas" have bonus blocks in yaml.
    session.add_all([
        Provider(id="unibet", name="Unibet", is_enabled=True),
        Provider(id="leovegas", name="LeoVegas", is_enabled=True),
        Provider(id="pinnacle", name="Pinnacle", is_enabled=True),  # no bonus
    ])
    session.commit()
    yield session
    session.close()


def _yaml_bonuses():
    """Stub of providers.yaml bonus configs for tests."""
    return {
        "unibet":   {"type": "freebet", "amount": 1000, "trigger_mode": "single"},
        "leovegas": {"type": "bonusdeposit", "amount": 600, "trigger_multiplier": 6,
                     "trigger_odds": 1.80, "trigger_mode": "cumulative"},
    }


def test_seed_creates_one_row_per_yaml_bonus(db, monkeypatch):
    monkeypatch.setattr(
        "src.services.bonus_seed_service.load_provider_bonuses",
        _yaml_bonuses,
    )
    inserted = seed_provider_bonuses(profile_id=1, db=db)
    db.commit()

    rows = db.query(ProfileProviderBonus).filter_by(profile_id=1).all()
    assert {r.provider_id for r in rows} == {"unibet", "leovegas"}
    assert all(r.bonus_status == "available" for r in rows)
    assert inserted == 2


def test_seed_skips_yaml_orphans(db, monkeypatch):
    """A yaml bonus for a provider not in the providers table is skipped, not raised."""
    yaml_with_orphan = dict(_yaml_bonuses(), ghost={"type": "freebet", "amount": 500})
    monkeypatch.setattr(
        "src.services.bonus_seed_service.load_provider_bonuses",
        lambda: yaml_with_orphan,
    )
    inserted = seed_provider_bonuses(profile_id=1, db=db)
    db.commit()

    rows = db.query(ProfileProviderBonus).filter_by(profile_id=1).all()
    assert {r.provider_id for r in rows} == {"unibet", "leovegas"}
    assert inserted == 2  # ghost not counted


def test_seed_is_idempotent(db, monkeypatch):
    monkeypatch.setattr(
        "src.services.bonus_seed_service.load_provider_bonuses",
        _yaml_bonuses,
    )
    seed_provider_bonuses(profile_id=1, db=db)
    db.commit()
    inserted_second = seed_provider_bonuses(profile_id=1, db=db)
    db.commit()

    rows = db.query(ProfileProviderBonus).filter_by(profile_id=1).all()
    assert len(rows) == 2
    assert inserted_second == 0


def test_seed_respects_existing_in_progress_bonus(db, monkeypatch):
    """Pre-existing non-available row for one provider is left untouched."""
    monkeypatch.setattr(
        "src.services.bonus_seed_service.load_provider_bonuses",
        _yaml_bonuses,
    )
    db.add(ProfileProviderBonus(
        profile_id=1, provider_id="unibet",
        bonus_status="in_progress", bonus_type="freebet",
    ))
    db.commit()

    inserted = seed_provider_bonuses(profile_id=1, db=db)
    db.commit()

    unibet_row = db.query(ProfileProviderBonus).filter_by(
        profile_id=1, provider_id="unibet").one()
    assert unibet_row.bonus_status == "in_progress"  # untouched
    assert inserted == 1  # only leovegas was new
