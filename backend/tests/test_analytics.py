# backend/tests/test_analytics.py
"""Tests for extraction analytics engine."""
from sqlalchemy import inspect


def test_provider_recommendations_table_exists(db_session):
    inspector = inspect(db_session.bind)
    assert "provider_recommendations" in inspector.get_table_names()


def test_recommendation_insert_and_query(db_session):
    from src.db.models import ProviderRecommendation
    rec = ProviderRecommendation(
        provider_id="betsson",
        category="match_rate",
        severity="warning",
        message="Match rate dropped from 82% to 62% over last 5 runs",
        diagnostic_data={"before": 0.82, "after": 0.62, "trend": "declining"},
        status="open",
        before_metric=0.82,
        source="rules",
    )
    db_session.add(rec)
    db_session.commit()
    result = db_session.query(ProviderRecommendation).first()
    assert result.provider_id == "betsson"
    assert result.category == "match_rate"
    assert result.status == "open"
    assert result.before_metric == 0.82
    assert result.after_metric is None


def test_recommendation_status_update(db_session):
    from src.db.models import ProviderRecommendation
    from datetime import datetime, timezone
    rec = ProviderRecommendation(
        provider_id="comeon",
        category="timing",
        severity="critical",
        message="SPA stalls on tennis",
        status="open",
        before_metric=52.1,
        source="rules",
    )
    db_session.add(rec)
    db_session.commit()

    result = db_session.query(ProviderRecommendation).first()
    result.status = "acted_on"
    result.acted_on_at = datetime.now(timezone.utc)
    db_session.commit()

    updated = db_session.query(ProviderRecommendation).first()
    assert updated.status == "acted_on"
    assert updated.acted_on_at is not None
