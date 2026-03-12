"""
Idempotent SQLite migration script for ML tables and columns.

Run via: run_migrations(conn) where conn is a sqlite3.Connection.
Safe to call multiple times — each step checks existence first.
"""
import sqlite3


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return cursor.fetchone() is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def _create_ml_features(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "ml_features"):
        return
    conn.execute("""
        CREATE TABLE ml_features (
            id INTEGER PRIMARY KEY,
            domain TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            features TEXT NOT NULL,
            feature_version INTEGER DEFAULT 1,
            outcome REAL,
            outcome_binary INTEGER,
            resolved_at TEXT,
            created_at TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ml_features_domain ON ml_features (domain)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ml_features_source ON ml_features (source_type, source_id)"
    )


def _create_candle_snapshots(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "candle_snapshots"):
        return
    conn.execute("""
        CREATE TABLE candle_snapshots (
            id INTEGER PRIMARY KEY,
            signal_id INTEGER REFERENCES trading_signals(id),
            candles TEXT NOT NULL,
            timeframe TEXT DEFAULT '1m',
            created_at TEXT
        )
    """)


def _create_economic_events(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "economic_events"):
        return
    conn.execute("""
        CREATE TABLE economic_events (
            id INTEGER PRIMARY KEY,
            event_name TEXT NOT NULL,
            event_datetime TEXT NOT NULL,
            importance INTEGER,
            forecast REAL,
            actual REAL,
            previous REAL,
            surprise REAL,
            created_at TEXT
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_econ_events_datetime ON economic_events (event_datetime)"
    )


def _create_news_impact(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "news_impact"):
        return
    conn.execute("""
        CREATE TABLE news_impact (
            id INTEGER PRIMARY KEY,
            event_id INTEGER REFERENCES economic_events(id),
            symbol TEXT DEFAULT 'NQ',
            price_before REAL,
            price_1m REAL,
            price_5m REAL,
            price_15m REAL,
            price_30m REAL,
            price_60m REAL,
            immediate_impact_pct REAL,
            sustained_impact_pct REAL,
            reversal_pct REAL,
            vix_at_event REAL,
            delta_1m_after REAL,
            volume_1m_after REAL,
            created_at TEXT
        )
    """)


def _create_options_flow(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "options_flow"):
        return
    conn.execute("""
        CREATE TABLE options_flow (
            id INTEGER PRIMARY KEY,
            date TEXT NOT NULL,
            symbol TEXT DEFAULT 'NQ',
            gex REAL,
            gex_flip_level REAL,
            net_options_delta REAL,
            put_call_ratio REAL,
            total_options_volume REAL,
            vix_level REAL,
            vix_1d_change REAL,
            vix_term_structure TEXT,
            dxy_level REAL,
            dxy_1d_change REAL,
            us10y_level REAL,
            us10y_1d_change REAL,
            us02y_level REAL,
            yield_curve_spread REAL,
            es_nq_ratio REAL,
            created_at TEXT,
            UNIQUE (date, symbol)
        )
    """)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_options_flow_date ON options_flow (date, symbol)"
    )


def _create_cot_data(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "cot_data"):
        return
    conn.execute("""
        CREATE TABLE cot_data (
            id INTEGER PRIMARY KEY,
            report_date TEXT NOT NULL,
            symbol TEXT DEFAULT 'NQ',
            net_position INTEGER,
            net_change INTEGER,
            long_pct REAL,
            short_pct REAL,
            open_interest INTEGER,
            open_interest_change INTEGER,
            created_at TEXT,
            UNIQUE (report_date, symbol)
        )
    """)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_cot_date ON cot_data (report_date, symbol)"
    )


def _create_ml_model_registry(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "ml_model_registry"):
        return
    conn.execute("""
        CREATE TABLE ml_model_registry (
            id INTEGER PRIMARY KEY,
            model_name TEXT NOT NULL,
            version INTEGER,
            file_path TEXT,
            training_data_count INTEGER,
            validation_metric REAL,
            baseline_metric REAL,
            is_active INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)


def _add_opportunity_columns(conn: sqlite3.Connection) -> None:
    """Add ML feature columns to the opportunities table (idempotent)."""
    new_columns = [
        ("prob_sum", "REAL"),
        ("odds_ratio", "REAL"),
        ("odds_age_minutes", "REAL"),
        ("sharp_age_minutes", "REAL"),
        ("time_to_start_minutes", "REAL"),
        ("provider_count", "INTEGER"),
        ("provider_odds_rank", "INTEGER"),
        ("market_consensus_spread", "REAL"),
        ("pinnacle_overround", "REAL"),
        ("closing_line_value", "REAL"),
    ]
    for col_name, col_type in new_columns:
        if not _column_exists(conn, "opportunities", col_name):
            conn.execute(
                f"ALTER TABLE opportunities ADD COLUMN {col_name} {col_type}"
            )


def run_migrations(conn: sqlite3.Connection) -> None:
    """
    Run all ML-related migrations against the given SQLite connection.

    Safe to call multiple times — all steps are guarded by existence checks.
    """
    _create_ml_features(conn)
    _create_candle_snapshots(conn)
    _create_economic_events(conn)
    _create_news_impact(conn)
    _create_options_flow(conn)
    _create_cot_data(conn)
    _create_ml_model_registry(conn)
    _add_opportunity_columns(conn)
    conn.commit()
