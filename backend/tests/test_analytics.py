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

    evt = Event(
        id="football:team_a:team_b:2026-03-13",
        sport="football", league="Test League",
        home_team="team_a", away_team="team_b",
    )
    db_session.add(evt)

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
    from src.db.models import Opportunity, Event
    from src.ml.analytics.engine import compute_provider_roi

    evt = Event(
        id="football:team_c:team_d:2026-03-13",
        sport="football", league="Test",
        home_team="team_c", away_team="team_d",
    )
    db_session.add(evt)

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
        INSERT INTO sport_run_metrics (run_id, provider_id, sport, duration_seconds,
            events_extracted, events_new, events_matched, events_unmatched,
            odds_extracted, odds_new, ml_count, spread_count, total_count, success)
        VALUES
            ('run1', 'betsson', 'football', 10.0, 80, 0, 65, 15, 500, 0, 65, 30, 40, 1),
            ('run1', 'betsson', 'tennis', 5.0, 40, 0, 20, 20, 200, 0, 20, 0, 0, 1),
            ('run1', 'pinnacle', 'football', 5.0, 100, 0, 100, 0, 800, 0, 100, 90, 95, 1),
            ('run1', 'pinnacle', 'tennis', 3.0, 60, 0, 60, 0, 400, 0, 60, 50, 55, 1)
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
