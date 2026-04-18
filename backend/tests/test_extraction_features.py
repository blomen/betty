"""Test extraction feature logging for M10 extraction optimizer."""
from sqlalchemy import inspect


def test_extraction_features_table_exists(db_session):
    inspector = inspect(db_session.bind)
    assert "extraction_features" in inspector.get_table_names()


def test_provider_value_log_table_exists(db_session):
    inspector = inspect(db_session.bind)
    assert "provider_value_log" in inspector.get_table_names()


def test_pinnacle_coverage_log_table_exists(db_session):
    inspector = inspect(db_session.bind)
    assert "pinnacle_coverage_log" in inspector.get_table_names()


def test_extraction_features_insert(db_session):
    from src.db.models import ExtractionFeature
    row = ExtractionFeature(
        run_id="run-abc-123",
        trigger="api_soft",
        hour_of_day=14,
        day_of_week=2,
        minutes_since_last_sharp=5.0,
        providers_attempted=12,
        providers_succeeded=11,
        providers_failed=1,
        total_events=450,
        total_odds=3200,
        avg_match_rate=0.82,
    )
    db_session.add(row)
    db_session.commit()
    result = db_session.query(ExtractionFeature).first()
    assert result.trigger == "api_soft"
    assert result.value_bets_found is None


def test_provider_value_log_insert(db_session):
    from src.db.models import ProviderValueLog
    row = ProviderValueLog(
        run_id="run-abc-123",
        provider_id="betsson",
        events_extracted=85,
        odds_extracted=650,
        duration_seconds=42.5,
        match_rate=0.88,
        spread_count=30,
        total_count=45,
    )
    db_session.add(row)
    db_session.commit()
    result = db_session.query(ProviderValueLog).first()
    assert result.provider_id == "betsson"
    assert result.value_bets_from_provider is None


def test_pinnacle_coverage_log_insert(db_session):
    from src.db.models import PinnacleCoverageLog
    row = PinnacleCoverageLog(
        run_id="run-abc-123",
        provider_id="betsson",
        sport="football",
        pinnacle_events=120,
        pinnacle_ml_events=120,
        pinnacle_spread_events=95,
        pinnacle_total_events=110,
        provider_matched_events=78,
        provider_ml_events=78,
        provider_spread_events=45,
        provider_total_events=60,
        event_coverage_pct=65.0,
        ml_coverage_pct=65.0,
        spread_coverage_pct=47.4,
        total_coverage_pct=54.5,
        missing_events=42,
        missing_spread=50,
        missing_total=50,
    )
    db_session.add(row)
    db_session.commit()
    result = db_session.query(PinnacleCoverageLog).first()
    assert result.provider_id == "betsson"
    assert result.sport == "football"
    assert result.pinnacle_events == 120
    assert result.provider_matched_events == 78
    assert result.event_coverage_pct == 65.0
    assert result.missing_events == 42


def test_extraction_features_outcome_resolution(db_session):
    from src.db.models import ExtractionFeature
    row = ExtractionFeature(
        run_id="run-xyz",
        trigger="browser_soft",
        hour_of_day=10,
        day_of_week=5,
        providers_attempted=6,
        providers_succeeded=5,
        total_events=200,
        total_odds=1500,
    )
    db_session.add(row)
    db_session.commit()

    result = db_session.query(ExtractionFeature).filter_by(run_id="run-xyz").first()
    result.value_bets_found = 47
    result.avg_edge_pct = 8.2
    result.arb_opportunities_found = 12
    db_session.commit()

    updated = db_session.query(ExtractionFeature).filter_by(run_id="run-xyz").first()
    assert updated.value_bets_found == 47
    assert updated.avg_edge_pct == 8.2


# --- Task 15: Feature extractor tests ---

def test_extract_extraction_features():
    from src.ml.features.extraction_features import extract_extraction_features
    from datetime import datetime, timezone

    features = extract_extraction_features(
        run_id="run-123",
        trigger="api_soft",
        providers_attempted=12,
        providers_succeeded=11,
        providers_failed=1,
        total_events=450,
        total_odds=3200,
        avg_match_rate=0.82,
        circuit_breakers_open=0,
        last_sharp_run_time=datetime(2026, 3, 12, 14, 25, tzinfo=timezone.utc),
        last_soft_run_time=datetime(2026, 3, 12, 13, 0, tzinfo=timezone.utc),
    )

    assert features["run_id"] == "run-123"
    assert features["trigger"] == "api_soft"
    assert features["providers_attempted"] == 12
    assert features["hour_of_day"] is not None
    assert features["day_of_week"] is not None
    assert "minutes_since_last_sharp" in features
    assert "minutes_since_last_soft" in features


def test_extract_provider_value_features():
    from src.ml.features.extraction_features import extract_provider_value

    features = extract_provider_value(
        run_id="run-123",
        provider_id="betsson",
        events_extracted=85,
        odds_extracted=650,
        duration_seconds=42.5,
        match_rate=0.88,
        spread_count=30,
        total_count=45,
        value_bets_from_provider=8,
        avg_edge_from_provider=7.2,
    )

    assert features["provider_id"] == "betsson"
    assert features["events_extracted"] == 85
    assert features["value_bets_from_provider"] == 8
