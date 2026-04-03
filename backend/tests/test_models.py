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
        signal_id=None,
        candles=[{"ts": "2026-03-12T15:30:00Z", "delta": 380, "volume": 4250}],
        timeframe="1m",
    )
    db_session.add(row)
    db_session.commit()
    result = db_session.query(CandleSnapshot).first()
    assert len(result.candles) == 1
    assert result.candles[0]["delta"] == 380


def test_opportunity_ml_columns(db_session):
    from src.db.models import Opportunity, Event, Provider
    db_session.add(Event(id="evt-1", sport="football", league="Test", home_team="X", away_team="Y"))
    db_session.add(Provider(id="betsson", name="Betsson"))
    db_session.flush()
    opp = Opportunity(
        type="value",
        event_id="evt-1",
        market="1x2",
        provider1_id="betsson",
        odds1=2.10,
        outcome1="home",
        edge_pct=7.5,
        prob_sum=1.02,
        odds_ratio=1.05,
        odds_age_minutes=15.0,
        sharp_age_minutes=5.0,
        time_to_start_minutes=120.0,
        provider_count=8,
        provider_odds_rank=2,
        market_consensus_spread=0.03,
        pinnacle_overround=0.025,
    )
    db_session.add(opp)
    db_session.commit()
    result = db_session.query(Opportunity).first()
    assert result.prob_sum == 1.02
    assert result.provider_count == 8
    assert result.closing_line_value is None
