"""Test that new ML-related ORM models create valid tables."""
from sqlalchemy import inspect


def test_ml_feature_table_exists(db_session):
    inspector = inspect(db_session.bind)
    tables = inspector.get_table_names()
    assert "ml_features" in tables


def test_candle_snapshots_table_exists(db_session):
    inspector = inspect(db_session.bind)
    assert "candle_snapshots" in inspector.get_table_names()


def test_economic_events_table_exists(db_session):
    inspector = inspect(db_session.bind)
    assert "economic_events" in inspector.get_table_names()


def test_news_impact_table_exists(db_session):
    inspector = inspect(db_session.bind)
    assert "news_impact" in inspector.get_table_names()


def test_options_flow_table_exists(db_session):
    inspector = inspect(db_session.bind)
    assert "options_flow" in inspector.get_table_names()


def test_cot_data_table_exists(db_session):
    inspector = inspect(db_session.bind)
    assert "cot_data" in inspector.get_table_names()


def test_ml_model_registry_table_exists(db_session):
    inspector = inspect(db_session.bind)
    assert "ml_model_registry" in inspector.get_table_names()


def test_ml_feature_insert_and_read(db_session):
    from src.db.models import MlFeature
    row = MlFeature(
        domain="betting",
        source_id="opp-123",
        source_type="opportunity",
        features={"edge_pct": 7.5, "prob_sum": 1.02},
        feature_version=1,
    )
    db_session.add(row)
    db_session.commit()
    result = db_session.query(MlFeature).first()
    assert result.domain == "betting"
    assert result.features["edge_pct"] == 7.5
    assert result.feature_version == 1
    assert result.outcome is None


def test_candle_snapshot_insert(db_session):
    from src.db.models import CandleSnapshot
    row = CandleSnapshot(
        signal_id=1,
        candles=[{"ts": "2026-03-12T15:30:00Z", "delta": 380, "volume": 4250}],
        timeframe="1m",
    )
    db_session.add(row)
    db_session.commit()
    result = db_session.query(CandleSnapshot).first()
    assert len(result.candles) == 1
    assert result.candles[0]["delta"] == 380
