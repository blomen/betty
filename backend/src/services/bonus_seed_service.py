"""Seed ProfileProviderBonus rows from providers.yaml bonus configs."""

import logging

from sqlalchemy.orm import Session

from ..api.routes.providers import load_provider_bonuses
from ..db.models import ProfileProviderBonus, Provider

logger = logging.getLogger(__name__)


def seed_provider_bonuses(profile_id: int, db: Session) -> int:
    """Insert ProfileProviderBonus rows for every yaml-configured bonus.

    Idempotent: skips providers that already have a row for this profile,
    so existing in-progress bonuses are never disturbed. Yaml orphans
    (providers in yaml but not in DB) are logged and skipped.

    Returns the count of rows inserted.
    """
    yaml_bonuses = load_provider_bonuses()
    if not yaml_bonuses:
        return 0

    valid_provider_ids = {p.id for p in db.query(Provider).filter(Provider.id.in_(yaml_bonuses.keys())).all()}
    yaml_orphans = set(yaml_bonuses.keys()) - valid_provider_ids
    if yaml_orphans:
        logger.warning("Skipping yaml-orphan bonuses (provider not in DB): %s", sorted(yaml_orphans))

    existing = {
        r.provider_id
        for r in db.query(ProfileProviderBonus.provider_id).filter(ProfileProviderBonus.profile_id == profile_id).all()
    }

    inserted = 0
    for provider_id in valid_provider_ids:
        if provider_id in existing:
            continue
        db.add(
            ProfileProviderBonus(
                profile_id=profile_id,
                provider_id=provider_id,
                bonus_status="available",
                bonus_type=yaml_bonuses[provider_id].get("type"),
            )
        )
        inserted += 1

    return inserted
