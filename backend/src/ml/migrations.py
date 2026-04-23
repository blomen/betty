"""
Idempotent SQLite migration script for ML tables and columns.

Run via: run_migrations(conn) where conn is a sqlite3.Connection.
Safe to call multiple times — each step checks existence first.
"""

import sqlite3


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ml_features_source ON ml_features (source_type, source_id)")


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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_econ_events_datetime ON economic_events (event_datetime)")


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
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_options_flow_date ON options_flow (date, symbol)")


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
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cot_date ON cot_data (report_date, symbol)")


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
    if not _table_exists(conn, "opportunities"):
        return
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
            conn.execute(f"ALTER TABLE opportunities ADD COLUMN {col_name} {col_type}")


def _create_extraction_features(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "extraction_features"):
        return
    conn.execute("""
        CREATE TABLE extraction_features (
            id INTEGER PRIMARY KEY,
            run_id TEXT NOT NULL,
            trigger TEXT NOT NULL,
            hour_of_day INTEGER,
            day_of_week INTEGER,
            minutes_since_last_sharp REAL,
            minutes_since_last_soft REAL,
            events_starting_next_2h INTEGER,
            events_starting_next_6h INTEGER,
            providers_attempted INTEGER,
            providers_succeeded INTEGER,
            providers_failed INTEGER,
            circuit_breakers_open INTEGER,
            total_events INTEGER,
            total_odds INTEGER,
            avg_match_rate REAL,
            value_bets_found INTEGER,
            avg_edge_pct REAL,
            arb_opportunities_found INTEGER,
            reverse_opportunities_found INTEGER,
            total_opportunity_value REAL,
            bets_placed_from_run INTEGER,
            avg_clv_from_run REAL,
            created_at DATETIME DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX idx_extraction_features_run ON extraction_features(run_id)")


def _create_provider_value_log(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "provider_value_log"):
        return
    conn.execute("""
        CREATE TABLE provider_value_log (
            id INTEGER PRIMARY KEY,
            run_id TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            events_extracted INTEGER,
            odds_extracted INTEGER,
            duration_seconds REAL,
            match_rate REAL,
            spread_count INTEGER,
            total_count INTEGER,
            value_bets_from_provider INTEGER,
            avg_edge_from_provider REAL,
            exclusive_events INTEGER,
            clv_avg_from_provider REAL,
            created_at DATETIME DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX idx_provider_value_run ON provider_value_log(run_id, provider_id)")


def _create_pinnacle_coverage_log(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "pinnacle_coverage_log"):
        return
    conn.execute("""
        CREATE TABLE pinnacle_coverage_log (
            id INTEGER PRIMARY KEY,
            run_id TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            sport TEXT NOT NULL,
            pinnacle_events INTEGER NOT NULL,
            pinnacle_ml_events INTEGER DEFAULT 0,
            pinnacle_spread_events INTEGER DEFAULT 0,
            pinnacle_total_events INTEGER DEFAULT 0,
            provider_matched_events INTEGER DEFAULT 0,
            provider_ml_events INTEGER DEFAULT 0,
            provider_spread_events INTEGER DEFAULT 0,
            provider_total_events INTEGER DEFAULT 0,
            event_coverage_pct REAL,
            ml_coverage_pct REAL,
            spread_coverage_pct REAL,
            total_coverage_pct REAL,
            missing_events INTEGER,
            missing_spread INTEGER,
            missing_total INTEGER,
            created_at DATETIME DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX idx_pinnacle_coverage_run ON pinnacle_coverage_log(run_id)")
    conn.execute("CREATE INDEX idx_pinnacle_coverage_provider ON pinnacle_coverage_log(provider_id, sport)")


def _create_devig_method_log(conn: sqlite3.Connection) -> None:
    """Stores all 3 devig method results per bet for M3 training."""
    if _table_exists(conn, "devig_method_log"):
        return
    conn.execute("""
        CREATE TABLE devig_method_log (
            id INTEGER PRIMARY KEY,
            bet_id INTEGER,
            event_id TEXT NOT NULL,
            market TEXT NOT NULL,
            outcome TEXT NOT NULL,
            sport TEXT,
            league TEXT,
            num_outcomes INTEGER,
            pinnacle_overround REAL,
            favourite_odds REAL,
            odds_range REAL,
            fair_odds_multiplicative REAL,
            fair_odds_additive REAL,
            fair_odds_power REAL,
            closing_odds REAL,
            clv_multiplicative REAL,
            clv_additive REAL,
            clv_power REAL,
            best_method TEXT,
            created_at DATETIME DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX idx_devig_log_bet ON devig_method_log(bet_id)")
    conn.execute("CREATE INDEX idx_devig_log_sport ON devig_method_log(sport, market)")


def _create_betting_outcome_log(conn: sqlite3.Connection) -> None:
    """Stores bet outcome data for M8 Kelly training."""
    if _table_exists(conn, "betting_outcome_log"):
        return
    conn.execute("""
        CREATE TABLE betting_outcome_log (
            id INTEGER PRIMARY KEY,
            bet_id INTEGER NOT NULL,
            provider_id TEXT NOT NULL,
            edge_pct REAL,
            odds REAL,
            stake REAL,
            kelly_fraction REAL,
            result TEXT,
            pnl REAL,
            clv REAL,
            model_confidence REAL,
            provider_historical_clv REAL,
            provider_win_rate REAL,
            recent_drawdown_pct REAL,
            consecutive_wins INTEGER,
            consecutive_losses INTEGER,
            daily_pnl REAL,
            weekly_pnl REAL,
            account_utilization REAL,
            is_freebet INTEGER DEFAULT 0,
            volatility_regime REAL,
            created_at DATETIME DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX idx_betting_outcome_bet ON betting_outcome_log(bet_id)")
    conn.execute("CREATE INDEX idx_betting_outcome_provider ON betting_outcome_log(provider_id)")


def _create_provider_recommendations(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "provider_recommendations"):
        return
    conn.execute("""
        CREATE TABLE provider_recommendations (
            id INTEGER PRIMARY KEY,
            provider_id TEXT NOT NULL,
            category TEXT NOT NULL,
            severity TEXT NOT NULL,
            message TEXT NOT NULL,
            diagnostic_data JSON,
            status TEXT NOT NULL DEFAULT 'open',
            acted_on_at DATETIME,
            resolved_at DATETIME,
            before_metric REAL,
            after_metric REAL,
            source TEXT DEFAULT 'rules',
            created_at DATETIME DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX idx_recommendations_provider ON provider_recommendations(provider_id)")
    conn.execute("CREATE INDEX idx_recommendations_status ON provider_recommendations(status)")


def _create_market_sessions(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "market_sessions"):
        return
    conn.execute("""
        CREATE TABLE market_sessions (
            id INTEGER PRIMARY KEY,
            date TEXT NOT NULL,
            symbol TEXT DEFAULT 'NQ',
            day_type TEXT,
            opening_type TEXT,
            macro_bias TEXT,
            ib_range REAL,
            rf_after_ib REAL,
            first_hour_delta_total REAL,
            first_hour_volume REAL,
            session_volume_total REAL,
            overnight_range_pct REAL,
            gap_filled_pct REAL,
            vix_level REAL,
            gex REAL,
            features TEXT,
            created_at TEXT,
            UNIQUE (date, symbol)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_sessions_date ON market_sessions(date)")


def _create_level_touch_tables(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS level_touch_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            touch_ts REAL NOT NULL,
            level_name TEXT NOT NULL,
            level_type TEXT NOT NULL,
            level_price REAL NOT NULL,
            approach_direction TEXT NOT NULL,
            outcome TEXT,
            max_continuation_ticks REAL,
            max_reversal_ticks REAL,
            outcome_measured_at REAL,
            session_date TEXT NOT NULL,
            is_backfill INTEGER DEFAULT 0,
            prediction TEXT,
            prediction_confidence REAL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lto_session ON level_touch_outcomes(session_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lto_outcome ON level_touch_outcomes(outcome)")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS level_touch_features (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            touch_outcome_id INTEGER NOT NULL REFERENCES level_touch_outcomes(id),
            features TEXT NOT NULL,
            feature_version INTEGER DEFAULT 1,
            created_at REAL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ltf_outcome ON level_touch_features(touch_outcome_id)")


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
    _create_extraction_features(conn)
    _create_provider_value_log(conn)
    _create_pinnacle_coverage_log(conn)
    _create_provider_recommendations(conn)
    _create_devig_method_log(conn)
    _create_betting_outcome_log(conn)
    _create_market_sessions(conn)
    _create_level_touch_tables(conn)
    conn.commit()
