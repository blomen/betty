"""Manages provider recommendation lifecycle.

Deduplicates by (provider_id, category, status='open') — only one open
recommendation per provider per category at a time. When a new recommendation
for the same provider+category arrives, the existing one is updated.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class RecommendationManager:
    def __init__(self, session):
        self.session = session

    def create(
        self,
        provider_id: str,
        category: str,
        severity: str,
        message: str,
        before_metric: float | None = None,
        diagnostic_data: dict | None = None,
        source: str = "rules",
    ):
        """Create or update a recommendation. Deduplicates by provider+category for open recs."""
        from src.db.models import ProviderRecommendation

        existing = (
            self.session.query(ProviderRecommendation)
            .filter_by(provider_id=provider_id, category=category, status="open")
            .first()
        )

        if existing:
            existing.message = message
            existing.severity = severity
            existing.before_metric = before_metric
            existing.diagnostic_data = diagnostic_data
            existing.source = source
            self.session.flush()
            return existing

        rec = ProviderRecommendation(
            provider_id=provider_id,
            category=category,
            severity=severity,
            message=message,
            diagnostic_data=diagnostic_data,
            status="open",
            before_metric=before_metric,
            source=source,
        )
        self.session.add(rec)
        self.session.flush()
        return rec

    def get_active(self, provider_id: str | None = None) -> list:
        """Get all open/acted_on recommendations, optionally filtered by provider."""
        from src.db.models import ProviderRecommendation

        q = self.session.query(ProviderRecommendation).filter(
            ProviderRecommendation.status.in_(["open", "acted_on"])
        )
        if provider_id:
            q = q.filter_by(provider_id=provider_id)
        return q.order_by(ProviderRecommendation.created_at.desc()).all()

    def update_status(self, rec_id: int, status: str, after_metric: float | None = None):
        """Update recommendation status."""
        from src.db.models import ProviderRecommendation

        rec = self.session.get(ProviderRecommendation, rec_id)
        if not rec:
            return None

        rec.status = status
        now = datetime.now(timezone.utc)

        if status == "acted_on":
            rec.acted_on_at = now
        elif status == "resolved":
            rec.resolved_at = now
            if after_metric is not None:
                rec.after_metric = after_metric

        self.session.flush()
        return rec

    def get_all(self, limit: int = 50) -> list:
        """Get all recommendations ordered by created_at desc."""
        from src.db.models import ProviderRecommendation
        return (
            self.session.query(ProviderRecommendation)
            .order_by(ProviderRecommendation.created_at.desc())
            .limit(limit)
            .all()
        )
