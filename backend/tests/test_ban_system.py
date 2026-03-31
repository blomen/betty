# backend/tests/test_ban_system.py
"""Tests for provider ban system."""

import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Base, Profile, Provider, ProfileProviderLimit, ProviderExtractionSetting
from src.repositories.limit_repo import LimitRepo
from src.services.limit_service import LimitService


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    # Seed: one profile, two providers
    session.add(Profile(id=1, name="test", is_active=True, bankroll=10000, currency="SEK"))
    session.add(Provider(id="coolbet", name="Coolbet"))
    session.add(Provider(id="snabbare", name="Snabbare"))
    session.add(Provider(id="unibet", name="Unibet"))
    session.commit()
    yield session
    session.close()


class TestGetBannedProviders:
    def test_no_bans_returns_empty(self, db: Session):
        repo = LimitRepo(db)
        assert repo.get_banned_providers(profile_id=1) == set()

    def test_fully_banned_level5_returned(self, db: Session):
        db.add(ProfileProviderLimit(
            profile_id=1, provider_id="coolbet",
            limit_type="fully_banned", limit_level=5,
            detected_at=datetime.now(timezone.utc),
        ))
        db.commit()
        repo = LimitRepo(db)
        assert repo.get_banned_providers(profile_id=1) == {"coolbet"}

    def test_level4_not_banned(self, db: Session):
        """Only level 5 (account closed) counts as banned."""
        db.add(ProfileProviderLimit(
            profile_id=1, provider_id="coolbet",
            limit_type="fully_banned", limit_level=4,
            detected_at=datetime.now(timezone.utc),
        ))
        db.commit()
        repo = LimitRepo(db)
        assert repo.get_banned_providers(profile_id=1) == set()

    def test_stake_limited_not_banned(self, db: Session):
        db.add(ProfileProviderLimit(
            profile_id=1, provider_id="coolbet",
            limit_type="stake_limited", limit_level=5,
            detected_at=datetime.now(timezone.utc),
        ))
        db.commit()
        repo = LimitRepo(db)
        assert repo.get_banned_providers(profile_id=1) == set()

    def test_multiple_bans(self, db: Session):
        for pid in ("coolbet", "snabbare"):
            db.add(ProfileProviderLimit(
                profile_id=1, provider_id=pid,
                limit_type="fully_banned", limit_level=5,
                detected_at=datetime.now(timezone.utc),
            ))
        db.commit()
        repo = LimitRepo(db)
        assert repo.get_banned_providers(profile_id=1) == {"coolbet", "snabbare"}


class TestBanProvider:
    def test_ban_records_limit_and_disables_extraction(self, db: Session):
        service = LimitService(db)
        result = service.ban_provider(
            profile_id=1,
            provider_id="coolbet",
            notes="Account closed — Coolbet dialog",
        )
        assert result["success"] is True

        # Verify limit recorded
        repo = LimitRepo(db)
        assert "coolbet" in repo.get_banned_providers(profile_id=1)

        # Verify extraction disabled
        setting = db.query(ProviderExtractionSetting).filter(
            ProviderExtractionSetting.profile_id == 1,
            ProviderExtractionSetting.provider_id == "coolbet",
        ).first()
        assert setting is not None
        assert setting.enabled is False

    def test_ban_already_banned_returns_error(self, db: Session):
        service = LimitService(db)
        service.ban_provider(profile_id=1, provider_id="coolbet")
        result = service.ban_provider(profile_id=1, provider_id="coolbet")
        assert result["success"] is False
        assert "already" in result["error"].lower()

    def test_ban_invalid_provider_returns_error(self, db: Session):
        service = LimitService(db)
        result = service.ban_provider(profile_id=1, provider_id="nonexistent")
        assert result["success"] is False

    def test_ban_updates_existing_extraction_setting(self, db: Session):
        """If extraction setting already exists as enabled, flip it to False."""
        db.add(ProviderExtractionSetting(
            profile_id=1, provider_id="coolbet", enabled=True
        ))
        db.commit()
        service = LimitService(db)
        result = service.ban_provider(profile_id=1, provider_id="coolbet")
        assert result["success"] is True

        setting = db.query(ProviderExtractionSetting).filter(
            ProviderExtractionSetting.profile_id == 1,
            ProviderExtractionSetting.provider_id == "coolbet",
        ).first()
        assert setting.enabled is False
