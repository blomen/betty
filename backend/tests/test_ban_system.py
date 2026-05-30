# backend/tests/test_ban_system.py
"""Tests for provider ban system."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import (
    Base,
    Event,
    Opportunity,
    Profile,
    ProfileProviderLimit,
    Provider,
    ProviderExtractionSetting,
)
from src.repositories.limit_repo import LimitRepo
from src.risk.allocator import ProviderAllocator
from src.services.bet_service import BetService
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
        db.add(
            ProfileProviderLimit(
                profile_id=1,
                provider_id="coolbet",
                limit_type="fully_banned",
                limit_level=5,
                detected_at=datetime.now(UTC),
            )
        )
        db.commit()
        repo = LimitRepo(db)
        assert repo.get_banned_providers(profile_id=1) == {"coolbet"}

    def test_level4_not_banned(self, db: Session):
        """Only level 5 (account closed) counts as banned."""
        db.add(
            ProfileProviderLimit(
                profile_id=1,
                provider_id="coolbet",
                limit_type="fully_banned",
                limit_level=4,
                detected_at=datetime.now(UTC),
            )
        )
        db.commit()
        repo = LimitRepo(db)
        assert repo.get_banned_providers(profile_id=1) == set()

    def test_stake_limited_not_banned(self, db: Session):
        db.add(
            ProfileProviderLimit(
                profile_id=1,
                provider_id="coolbet",
                limit_type="stake_limited",
                limit_level=5,
                detected_at=datetime.now(UTC),
            )
        )
        db.commit()
        repo = LimitRepo(db)
        assert repo.get_banned_providers(profile_id=1) == set()

    def test_multiple_bans(self, db: Session):
        for pid in ("coolbet", "snabbare"):
            db.add(
                ProfileProviderLimit(
                    profile_id=1,
                    provider_id=pid,
                    limit_type="fully_banned",
                    limit_level=5,
                    detected_at=datetime.now(UTC),
                )
            )
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
        setting = (
            db.query(ProviderExtractionSetting)
            .filter(
                ProviderExtractionSetting.profile_id == 1,
                ProviderExtractionSetting.provider_id == "coolbet",
            )
            .first()
        )
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
        db.add(ProviderExtractionSetting(profile_id=1, provider_id="coolbet", enabled=True))
        db.commit()
        service = LimitService(db)
        result = service.ban_provider(profile_id=1, provider_id="coolbet")
        assert result["success"] is True

        setting = (
            db.query(ProviderExtractionSetting)
            .filter(
                ProviderExtractionSetting.profile_id == 1,
                ProviderExtractionSetting.provider_id == "coolbet",
            )
            .first()
        )
        assert setting.enabled is False


class TestOpportunityBanFiltering:
    def test_banned_provider_excluded_from_opportunities(self, db: Session):
        """Opportunities with banned provider in provider1_id should be excluded."""
        # Create event + opportunities
        event = Event(
            id="football:teamA:teamB:2026-04-05",
            sport="football",
            home_team="teamA",
            away_team="teamB",
        )
        db.add(event)
        db.add(
            Opportunity(
                event_id=event.id,
                type="value",
                market="1x2",
                outcome1="1",
                provider1_id="coolbet",
                provider2_id="pinnacle",
                odds1=2.5,
                odds2=2.0,
                edge_pct=5.0,
                is_active=True,
            )
        )
        db.add(
            Opportunity(
                event_id=event.id,
                type="value",
                market="1x2",
                outcome1="1",
                provider1_id="unibet",
                provider2_id="pinnacle",
                odds1=2.3,
                odds2=2.0,
                edge_pct=3.0,
                is_active=True,
            )
        )
        db.commit()

        # Ban coolbet
        db.add(
            ProfileProviderLimit(
                profile_id=1,
                provider_id="coolbet",
                limit_type="fully_banned",
                limit_level=5,
                detected_at=datetime.now(UTC),
            )
        )
        db.commit()

        from src.services.opportunity_service import OpportunityService

        service = OpportunityService(db)
        result = service.list_opportunities()

        provider_ids = [o["provider1"] for o in result["opportunities"]]
        assert "coolbet" not in provider_ids
        assert "unibet" in provider_ids


class TestAllocatorBanBlock:
    def test_banned_provider_gets_negative_score(self, db: Session):
        """Banned providers should get score -1 (same as capped)."""
        db.add(
            ProfileProviderLimit(
                profile_id=1,
                provider_id="coolbet",
                limit_type="fully_banned",
                limit_level=5,
                detected_at=datetime.now(UTC),
            )
        )
        db.commit()

        allocator = ProviderAllocator(db, profile_id=1)
        allocator.preload_limits()
        result = allocator.score_provider("coolbet")
        assert result.score == -1
        assert result.is_capped is True
        assert "banned" in result.reason.lower()

    def test_unbanned_provider_gets_normal_score(self, db: Session):
        allocator = ProviderAllocator(db, profile_id=1)
        allocator.preload_limits()
        result = allocator.score_provider("unibet")
        assert result.score >= 0


class TestBetServiceBanGate:
    def test_bet_on_banned_provider_rejected(self, db: Session):
        db.add(
            ProfileProviderLimit(
                profile_id=1,
                provider_id="coolbet",
                limit_type="fully_banned",
                limit_level=5,
                detected_at=datetime.now(UTC),
            )
        )
        db.commit()

        service = BetService(db)
        result = service.create_bet(
            event_id=None,
            provider_id="coolbet",
            market="1x2",
            outcome="1",
            odds=2.5,
            stake=100,
        )
        assert "error" in result
        assert "banned" in result["error"].lower()

    def test_bet_on_active_provider_allowed(self, db: Session):
        from src.repositories.profile_repo import ProfileRepo

        ProfileRepo(db).set_balance(1, "unibet", 1000)
        db.commit()

        service = BetService(db)
        result = service.create_bet(
            event_id=None,
            provider_id="unibet",
            market="1x2",
            outcome="1",
            odds=2.5,
            stake=100,
        )
        assert "error" not in result
