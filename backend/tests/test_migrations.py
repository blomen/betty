"""Test idempotent database migrations for ML tables."""
import sqlite3
import pytest


@pytest.fixture
def raw_db(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE opportunities (
            id INTEGER PRIMARY KEY,
            type TEXT,
            event_id TEXT,
            market TEXT,
            provider1_id TEXT,
            odds1 REAL,
            outcome1 TEXT,
            edge_pct REAL,
            is_active INTEGER DEFAULT 1,
            detected_at TEXT,
            expires_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE trading_signals (
            id INTEGER PRIMARY KEY,
            setup_type TEXT,
            direction TEXT,
            score REAL,
            conditions TEXT
        )
    """)
    conn.commit()
    yield conn
    conn.close()


def test_migrate_creates_ml_features_table(raw_db):
    from src.ml.migrations import run_migrations
    run_migrations(raw_db)
    cursor = raw_db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ml_features'")
    assert cursor.fetchone() is not None


def test_migrate_creates_all_new_tables(raw_db):
    from src.ml.migrations import run_migrations
    run_migrations(raw_db)
    cursor = raw_db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    expected = {"ml_features", "candle_snapshots", "economic_events", "news_impact",
                "options_flow", "cot_data", "ml_model_registry"}
    assert expected.issubset(tables)


def test_migrate_adds_opportunity_columns(raw_db):
    from src.ml.migrations import run_migrations
    run_migrations(raw_db)
    cursor = raw_db.execute("PRAGMA table_info(opportunities)")
    columns = {row[1] for row in cursor.fetchall()}
    expected = {"prob_sum", "odds_ratio", "odds_age_minutes", "sharp_age_minutes",
                "time_to_start_minutes", "provider_count", "provider_odds_rank",
                "market_consensus_spread", "pinnacle_overround", "closing_line_value"}
    assert expected.issubset(columns)


def test_migrate_is_idempotent(raw_db):
    from src.ml.migrations import run_migrations
    run_migrations(raw_db)
    run_migrations(raw_db)
    cursor = raw_db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ml_features'")
    assert cursor.fetchone() is not None
