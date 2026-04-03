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


def test_compute_provider_roi_basic(db_session):
    """Test provider ROI computation with seeded data."""
    from src.db.models import Opportunity, Bet, Event
    from src.ml.analytics.engine import compute_provider_roi

    from src.db.models import Provider
    evt = Event(
        id="football:team_a:team_b:2026-03-13",
        sport="football", league="Test League",
        home_team="team_a", away_team="team_b",
    )
    db_session.add(evt)
    db_session.add(Provider(id="betsson", name="Betsson"))
    db_session.flush()

    for i in range(5):
        db_session.add(Opportunity(
            event_id=evt.id, type="value", market="1x2",
            provider1_id="betsson", odds1=2.5, edge_pct=5.0 + i, is_active=True,
        ))

    db_session.add(Bet(
        event_id=evt.id, provider_id="betsson", market="1x2",
        outcome="home", odds=2.5, stake=100, result="won",
        payout=250, bet_type="value",
    ))
    db_session.add(Bet(
        event_id=evt.id, provider_id="betsson", market="1x2",
        outcome="away", odds=2.5, stake=100, result="lost",
        payout=0, bet_type="value",
    ))
    db_session.commit()

    roi = compute_provider_roi(db_session)
    betsson = next((r for r in roi if r["provider_id"] == "betsson"), None)
    assert betsson is not None
    assert betsson["total_opportunities"] == 5
    assert betsson["avg_edge"] == 7.0  # (5+6+7+8+9) / 5
    assert betsson["total_bets"] == 2
    assert betsson["win_rate"] == 0.5
    assert betsson["net_pnl"] == 50.0  # (250-100) + (0-100)


def test_compute_provider_roi_canonical_grouping(db_session):
    """Test that alias providers group under canonical."""
    from src.db.models import Opportunity, Event, Provider
    from src.ml.analytics.engine import compute_provider_roi

    evt = Event(
        id="football:team_c:team_d:2026-03-13",
        sport="football", league="Test",
        home_team="team_c", away_team="team_d",
    )
    db_session.add(evt)
    for pid in ["unibet", "leovegas", "expekt"]:
        db_session.add(Provider(id=pid, name=pid.title()))
    db_session.flush()

    for pid in ["unibet", "leovegas", "expekt"]:
        db_session.add(Opportunity(
            event_id=evt.id, type="value", market="1x2",
            provider1_id=pid, odds1=2.0, edge_pct=4.0, is_active=True,
        ))
    db_session.commit()

    roi = compute_provider_roi(db_session)
    unibet = next((r for r in roi if r["provider_id"] == "unibet"), None)
    assert unibet is not None
    assert unibet["total_opportunities"] == 3


def test_compute_provider_roi_empty_db(db_session):
    """No data should return empty list."""
    from src.ml.analytics.engine import compute_provider_roi
    roi = compute_provider_roi(db_session)
    assert roi == []


def test_compute_coverage_gaps(db_session):
    """Test coverage gap computation from sport_run_metrics."""
    from src.ml.analytics.engine import compute_coverage_gaps
    from sqlalchemy import text

    db_session.execute(text("""
        INSERT INTO extraction_runs (id, start_time, duration_seconds,
            providers_attempted, providers_succeeded, providers_failed,
            total_events, total_odds, trigger)
        VALUES ('run1', '2026-03-13 10:00:00', 30.0, 2, 2, 0, 200, 1200, 'api_soft')
    """))
    db_session.execute(text("""
        INSERT INTO sport_run_metrics (run_id, provider_id, sport, duration_seconds,
            events_extracted, events_new, events_matched, events_unmatched,
            odds_extracted, odds_new, ml_count, spread_count, total_count, success)
        VALUES
            ('run1', 'betsson', 'football', 10.0, 80, 0, 65, 15, 500, 0, 65, 30, 40, true),
            ('run1', 'betsson', 'tennis', 5.0, 40, 0, 20, 20, 200, 0, 20, 0, 0, true),
            ('run1', 'pinnacle', 'football', 5.0, 100, 0, 100, 0, 800, 0, 100, 90, 95, true),
            ('run1', 'pinnacle', 'tennis', 3.0, 60, 0, 60, 0, 400, 0, 60, 50, 55, true)
    """))
    db_session.commit()

    gaps = compute_coverage_gaps(db_session)
    fb = next((g for g in gaps if g["provider_id"] == "betsson" and g["sport"] == "football"), None)
    assert fb is not None
    assert fb["pinnacle_events"] == 100
    assert fb["matched_events"] == 65
    assert fb["event_coverage_pct"] == 65.0

    tn = next((g for g in gaps if g["provider_id"] == "betsson" and g["sport"] == "tennis"), None)
    assert tn is not None
    assert tn["spread_count"] == 0
    assert tn["pinnacle_spread_count"] == 50


def test_compute_coverage_gaps_empty(db_session):
    from src.ml.analytics.engine import compute_coverage_gaps
    gaps = compute_coverage_gaps(db_session)
    assert gaps == []


def test_compute_scheduling_efficiency(db_session):
    """Test scheduling efficiency from extraction_runs."""
    from src.ml.analytics.engine import compute_scheduling_efficiency
    from sqlalchemy import text

    db_session.execute(text("""
        INSERT INTO extraction_runs (id, start_time, end_time, duration_seconds,
            providers_attempted, providers_succeeded, providers_failed,
            total_events, total_odds, trigger)
        VALUES
            ('run1', '2026-03-13 10:00:00', '2026-03-13 10:02:30', 150.0, 8, 7, 1, 9000, 35000, 'api_soft'),
            ('run2', '2026-03-13 14:00:00', '2026-03-13 14:02:00', 120.0, 8, 8, 0, 10000, 40000, 'api_soft'),
            ('run3', '2026-03-13 10:00:00', '2026-03-13 10:00:50', 50.0, 2, 2, 0, 2000, 20000, 'sharp')
    """))
    db_session.commit()

    sched = compute_scheduling_efficiency(db_session)
    assert "api_soft" in sched
    assert sched["api_soft"]["runs"] == 2
    assert sched["api_soft"]["avg_duration"] == 135.0
    assert sched["api_soft"]["avg_events"] == 9500.0
    assert "sharp" in sched
    assert sched["sharp"]["runs"] == 1


def test_compute_scheduling_efficiency_empty(db_session):
    from src.ml.analytics.engine import compute_scheduling_efficiency
    sched = compute_scheduling_efficiency(db_session)
    assert sched == {}


def test_diagnose_match_rate_drop():
    """Test match rate drop detection."""
    from src.ml.analytics.diagnostics import diagnose_provider

    provider_data = {
        "provider_id": "dbet",
        "avg_match_rate": 0.55,
        "prev_match_rate": 0.82,
        "avg_events": 166,
        "avg_duration": 42.5,
        "total_opportunities": 20,
        "seconds_per_value_bet": 8.3,
    }

    recommendations = diagnose_provider(provider_data)
    assert len(recommendations) >= 1
    match_rec = next((r for r in recommendations if r["category"] == "match_rate"), None)
    assert match_rec is not None
    assert match_rec["severity"] in ("warning", "critical")
    assert "match rate" in match_rec["message"].lower()


def test_diagnose_zero_spreads():
    """Test missing market detection."""
    from src.ml.analytics.diagnostics import diagnose_provider

    provider_data = {
        "provider_id": "betinia",
        "avg_match_rate": 0.85,
        "spread_count": 0,
        "total_count": 45,
        "avg_events": 67,
        "avg_duration": 16.0,
        "total_opportunities": 15,
    }

    recommendations = diagnose_provider(provider_data)
    market_rec = next((r for r in recommendations if r["category"] == "market_gap"), None)
    assert market_rec is not None
    assert "spread" in market_rec["message"].lower()


def test_diagnose_slow_provider():
    """Test slow extraction detection."""
    from src.ml.analytics.diagnostics import diagnose_provider

    provider_data = {
        "provider_id": "comeon",
        "avg_match_rate": 0.70,
        "avg_events": 42,
        "avg_duration": 180.0,
        "total_opportunities": 2,
        "seconds_per_value_bet": 90.0,
    }

    recommendations = diagnose_provider(provider_data)
    timing_rec = next((r for r in recommendations if r["category"] == "timing"), None)
    assert timing_rec is not None


def test_diagnose_healthy_provider():
    """Healthy provider should get no recommendations."""
    from src.ml.analytics.diagnostics import diagnose_provider

    provider_data = {
        "provider_id": "unibet",
        "avg_match_rate": 0.85,
        "spread_count": 30,
        "total_count": 45,
        "avg_events": 284,
        "avg_duration": 25.0,
        "total_opportunities": 50,
        "seconds_per_value_bet": 2.1,
    }

    recommendations = diagnose_provider(provider_data)
    assert len(recommendations) == 0


def test_recommendation_manager_create(db_session):
    from src.ml.analytics.recommendations import RecommendationManager
    mgr = RecommendationManager(db_session)
    rec = mgr.create(
        provider_id="dbet",
        category="match_rate",
        severity="warning",
        message="Match rate dropped to 55%",
        before_metric=0.55,
    )
    assert rec.id is not None
    assert rec.status == "open"


def test_recommendation_manager_dedup(db_session):
    """Creating same category+provider should not duplicate."""
    from src.ml.analytics.recommendations import RecommendationManager
    mgr = RecommendationManager(db_session)
    rec1 = mgr.create(provider_id="dbet", category="match_rate", severity="warning",
                       message="First message", before_metric=0.55)
    rec2 = mgr.create(provider_id="dbet", category="match_rate", severity="warning",
                       message="Updated message", before_metric=0.50)
    assert rec1.id == rec2.id
    assert rec2.message == "Updated message"
    assert rec2.before_metric == 0.50


def test_recommendation_manager_get_active(db_session):
    from src.ml.analytics.recommendations import RecommendationManager
    mgr = RecommendationManager(db_session)
    mgr.create(provider_id="dbet", category="match_rate", severity="warning",
               message="Test", before_metric=0.55)
    mgr.create(provider_id="comeon", category="timing", severity="critical",
               message="Slow", before_metric=90.0)

    active = mgr.get_active()
    assert len(active) == 2


def test_recommendation_manager_update_status(db_session):
    from src.ml.analytics.recommendations import RecommendationManager
    mgr = RecommendationManager(db_session)
    rec = mgr.create(provider_id="dbet", category="match_rate", severity="warning",
                     message="Test", before_metric=0.55)

    updated = mgr.update_status(rec.id, "acted_on")
    assert updated.status == "acted_on"
    assert updated.acted_on_at is not None


def test_recommendation_manager_resolve(db_session):
    from src.ml.analytics.recommendations import RecommendationManager
    mgr = RecommendationManager(db_session)
    rec = mgr.create(provider_id="dbet", category="match_rate", severity="warning",
                     message="Test", before_metric=0.55)

    resolved = mgr.update_status(rec.id, "resolved", after_metric=0.82)
    assert resolved.status == "resolved"
    assert resolved.after_metric == 0.82
    assert resolved.resolved_at is not None


def test_analytics_refresh(db_session):
    """Test full refresh cycle: compute analytics + generate recommendations."""
    from src.ml.analytics.engine import AnalyticsEngine
    from src.db.models import ProviderRecommendation, Event, Opportunity, Provider
    from sqlalchemy import text

    evt = Event(
        id="football:x:y:2026-03-13", sport="football", league="Test",
        home_team="x", away_team="y",
    )
    db_session.add(evt)
    db_session.add(Provider(id="comeon", name="ComeOn"))
    db_session.flush()

    # Provider with poor match rate (15/42 = 36% < 40% critical threshold)
    db_session.execute(text("""
        INSERT INTO extraction_runs (id, start_time, duration_seconds,
            providers_attempted, providers_succeeded, providers_failed,
            total_events, total_odds, trigger)
        VALUES ('run1', '2026-03-13 10:00:00', 180.0, 1, 1, 0, 42, 200, 'api_soft')
    """))
    # Note: table uses events_processed and odds_processed (not events_extracted/odds_extracted)
    db_session.execute(text("""
        INSERT INTO provider_run_metrics (run_id, provider_id, start_time, end_time,
            duration_seconds, events_processed, events_new, odds_processed, odds_new,
            sports_attempted, sports_succeeded, events_matched, events_unmatched,
            ml_count, spread_count, total_count, status)
        VALUES ('run1', 'comeon', '2026-03-13', '2026-03-13', 180.0, 42, 0, 200, 0,
            5, 5, 15, 27, 0, 0, 0, 'success')
    """))

    db_session.add(Opportunity(
        event_id=evt.id, type="value", market="1x2",
        provider1_id="comeon", odds1=2.0, edge_pct=3.0, is_active=True,
    ))
    db_session.commit()

    engine = AnalyticsEngine()
    result = engine.refresh(db_session, "run1")

    assert "provider_roi" in result
    assert "recommendations" in result

    # comeon should have match_rate recommendation (15/42 = 36%)
    recs = db_session.query(ProviderRecommendation).filter_by(provider_id="comeon").all()
    assert len(recs) >= 1
