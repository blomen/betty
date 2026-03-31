"""Limit repository - provider limit data access."""

from sqlalchemy.orm import Session

from ..db.models import ProfileProviderLimit


class LimitRepo:
    """Data access for provider limits."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, limit_id: int) -> ProfileProviderLimit | None:
        return self.db.query(ProfileProviderLimit).filter(
            ProfileProviderLimit.id == limit_id
        ).first()

    def list_limits(
        self,
        profile_id: int | None = None,
        provider_id: str | None = None,
    ) -> list[ProfileProviderLimit]:
        query = self.db.query(ProfileProviderLimit)
        if profile_id is not None:
            query = query.filter(ProfileProviderLimit.profile_id == profile_id)
        if provider_id is not None:
            query = query.filter(ProfileProviderLimit.provider_id == provider_id)
        return query.order_by(ProfileProviderLimit.detected_at.desc()).all()

    def get_banned_providers(self, profile_id: int) -> set[str]:
        """Get provider IDs where account is closed (fully_banned, level 5)."""
        rows = self.db.query(ProfileProviderLimit.provider_id).filter(
            ProfileProviderLimit.profile_id == profile_id,
            ProfileProviderLimit.limit_type == "fully_banned",
            ProfileProviderLimit.limit_level == 5,
        ).all()
        return {r[0] for r in rows}

    def get_existing(
        self, profile_id: int, provider_id: str, limit_type: str
    ) -> ProfileProviderLimit | None:
        return self.db.query(ProfileProviderLimit).filter(
            ProfileProviderLimit.profile_id == profile_id,
            ProfileProviderLimit.provider_id == provider_id,
            ProfileProviderLimit.limit_type == limit_type,
        ).first()

    def create(self, **kwargs) -> ProfileProviderLimit:
        limit = ProfileProviderLimit(**kwargs)
        self.db.add(limit)
        return limit

    def delete(self, limit: ProfileProviderLimit) -> None:
        self.db.delete(limit)
