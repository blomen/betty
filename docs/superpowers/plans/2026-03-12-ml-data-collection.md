# ML Data Collection Foundation — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the database tables, migrations, feature store, and feature extraction hooks so every scan, trading signal, and extraction run logs its full continuous feature vector from day one.

**Architecture:** New `backend/src/ml/` module with feature extraction running inline after each scan/signal/extraction. Features stored as JSON blobs in SQLite `ml_features` table. Extraction metrics linked to downstream value outcomes via `extraction_features` and `provider_value_log` tables. Macro data fetched on schedule into dedicated tables. Model registry table prepared for Phase 2.

**Tech Stack:** Python 3.10+, SQLAlchemy (ORM models), SQLite, existing BankrollBBQ analysis/trading pipeline

**Spec:** `docs/superpowers/specs/2026-03-12-ml-system-design.md`

**Scope:** This plan covers Phase 1 only — data collection foundation. Model training (Phase 2) and individual models (Phase 3) are separate plans once data accumulates.

---

## File Structure

### New Files

```
backend/src/ml/
├── __init__.py                    # Package init
├── migrations.py                  # Idempotent schema migrations (new tables + ALTER columns)
├── feature_store.py               # Read/write ml_features + candle_snapshots tables
├── features/
│   ├── __init__.py
│   ├── betting_features.py        # Extract feature vector for sports betting opportunities
│   ├── trading_features.py        # Extract feature vector for trading signals
│   ├── candle_features.py         # Snapshot last 20 candles at signal time
│   └── extraction_features.py     # Extract features for extraction pipeline (M10)
├── macro/
│   ├── __init__.py
│   ├── economic_calendar.py       # Fetch economic events from API
│   └── options_flow.py            # Fetch VIX, DXY, yields, GEX daily
backend/src/db/models.py           # Add ORM models: MlFeature, CandleSnapshot, EconomicEvent,
                                   #   NewsImpact, OptionsFlow, CotData, MlModelRegistry,
                                   #   ExtractionFeature, ProviderValueLog
                                   # Add columns to Opportunity
backend/tests/
├── __init__.py
├── conftest.py                    # Shared fixtures (in-memory SQLite session)
├── test_migrations.py
├── test_feature_store.py
├── test_betting_features.py
├── test_trading_features.py
├── test_candle_features.py
├── test_extraction_features.py
└── test_macro_fetchers.py
```

### Modified Files

```
backend/src/db/models.py           # New ORM models + Opportunity columns
backend/src/analysis/scanner.py    # Hook: call betting_features.extract() after scan
backend/src/market_data/scoring.py # Hook: enrich conditions with continuous values
backend/src/market_data/scanner.py # Hook: call trading_features.extract() + candle snapshot after signal
backend/src/pipeline/orchestrator.py  # Hook: call extraction_features.extract() after run completes
backend/src/pipeline/metrics.py       # Hook: attribute value bets to providers after scan
```

---

## Chunk 1: Database Foundation

### Task 1: Test Infrastructure

**Files:**
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/conftest.py`

- [ ] **Step 1: Create test directory and conftest**

```bash
mkdir -p backend/tests
```

```python
# backend/tests/__init__.py
# (empty)
```

```python
# backend/tests/conftest.py
"""Shared test fixtures for BankrollBBQ tests."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.db.models import Base


@pytest.fixture
def db_session():
    """In-memory SQLite session with all tables created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()
```

- [ ] **Step 2: Verify pytest discovers the fixture**

Run: `cd backend && python -m pytest tests/ --collect-only`
Expected: `<Module tests/conftest.py>` collected, 0 tests (no test files yet)

- [ ] **Step 3: Commit**

```bash
git add backend/tests/__init__.py backend/tests/conftest.py
git commit -m "test: add pytest infrastructure with in-memory SQLite fixture"
```

---

### Task 2: New ORM Models

**Files:**
- Modify: `backend/src/db/models.py`
- Create: `backend/tests/test_models.py`

These are the 7 new tables from the spec: `ml_features`, `candle_snapshots`, `economic_events`, `news_impact`, `options_flow`, `cot_data`, `ml_model_registry`.

- [ ] **Step 1: Write test that new models create tables**

```python
# backend/tests/test_models.py
"""Test that new ML-related ORM models create valid tables."""
from sqlalchemy import inspect


def test_ml_feature_table_exists(db_session):
    """ml_features table should exist after Base.metadata.create_all."""
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
    """Can insert and read back an ml_features row."""
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
    assert result.outcome is None  # Not resolved yet


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_models.py -v`
Expected: FAIL — `MlFeature` not defined

- [ ] **Step 3: Add ORM models to models.py**

Add the following models at the end of `backend/src/db/models.py` (before the `init_db` function if one exists, otherwise at EOF):

```python
class MlFeature(Base):
    """ML feature vector logged at decision time. Resolved with outcome later."""
    __tablename__ = "ml_features"

    id = Column(Integer, primary_key=True)
    domain = Column(String, nullable=False)           # 'betting' or 'trading'
    source_id = Column(String, nullable=False)        # opportunity.id or trading_signal.id
    source_type = Column(String, nullable=False)      # 'opportunity', 'signal', 'boost'
    features = Column(JSON, nullable=False)
    feature_version = Column(Integer, nullable=False, default=1)
    outcome = Column(Float, nullable=True)            # CLV for betting, R-multiple for trading
    outcome_binary = Column(Integer, nullable=True)   # 1=win, 0=loss
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("idx_ml_features_domain", "domain"),
        Index("idx_ml_features_source", "source_type", "source_id"),
    )


class CandleSnapshot(Base):
    """Last 20 candles of orderflow data at signal time for temporal pattern training."""
    __tablename__ = "candle_snapshots"

    id = Column(Integer, primary_key=True)
    signal_id = Column(Integer, ForeignKey("trading_signals.id"), nullable=False)
    candles = Column(JSON, nullable=False)            # Array of 20 candle objects
    timeframe = Column(String, default="1m")
    created_at = Column(DateTime, default=_utcnow)

    signal = relationship("TradingSignal")


class EconomicEvent(Base):
    """Scheduled economic releases with forecast/actual/surprise."""
    __tablename__ = "economic_events"

    id = Column(Integer, primary_key=True)
    event_name = Column(String, nullable=False)       # 'CPI', 'NFP', 'FOMC', etc.
    event_datetime = Column(DateTime, nullable=False)
    importance = Column(Integer, nullable=False)       # 1=low, 2=medium, 3=high
    forecast = Column(Float, nullable=True)
    actual = Column(Float, nullable=True)
    previous = Column(Float, nullable=True)
    surprise = Column(Float, nullable=True)           # actual - forecast
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("idx_econ_events_datetime", "event_datetime"),
    )


class NewsImpact(Base):
    """NQ price response to economic events at various time horizons."""
    __tablename__ = "news_impact"

    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("economic_events.id"), nullable=False)
    symbol = Column(String, nullable=False, default="NQ")
    price_before = Column(Float, nullable=False)
    price_1m = Column(Float, nullable=True)
    price_5m = Column(Float, nullable=True)
    price_15m = Column(Float, nullable=True)
    price_30m = Column(Float, nullable=True)
    price_60m = Column(Float, nullable=True)
    immediate_impact_pct = Column(Float, nullable=True)
    sustained_impact_pct = Column(Float, nullable=True)
    reversal_pct = Column(Float, nullable=True)
    vix_at_event = Column(Float, nullable=True)
    delta_1m_after = Column(Float, nullable=True)
    volume_1m_after = Column(Float, nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    event = relationship("EconomicEvent")


class OptionsFlow(Base):
    """Daily options/gamma/macro market data."""
    __tablename__ = "options_flow"

    id = Column(Integer, primary_key=True)
    date = Column(String, nullable=False)             # YYYY-MM-DD
    symbol = Column(String, nullable=False, default="NQ")
    gex = Column(Float, nullable=True)
    gex_flip_level = Column(Float, nullable=True)
    net_options_delta = Column(Float, nullable=True)
    put_call_ratio = Column(Float, nullable=True)
    total_options_volume = Column(Float, nullable=True)
    vix_level = Column(Float, nullable=True)
    vix_1d_change = Column(Float, nullable=True)
    vix_term_structure = Column(String, nullable=True)  # 'contango' or 'backwardation'
    dxy_level = Column(Float, nullable=True)
    dxy_1d_change = Column(Float, nullable=True)
    us10y_level = Column(Float, nullable=True)
    us10y_1d_change = Column(Float, nullable=True)
    us02y_level = Column(Float, nullable=True)
    yield_curve_spread = Column(Float, nullable=True)  # 10Y - 2Y
    es_nq_ratio = Column(Float, nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("date", "symbol", name="uq_options_flow_date_symbol"),
    )


class CotData(Base):
    """Weekly Commitment of Traders data."""
    __tablename__ = "cot_data"

    id = Column(Integer, primary_key=True)
    report_date = Column(String, nullable=False)      # YYYY-MM-DD (Tuesday)
    symbol = Column(String, nullable=False, default="NQ")
    net_position = Column(Integer, nullable=True)
    net_change = Column(Integer, nullable=True)
    long_pct = Column(Float, nullable=True)
    short_pct = Column(Float, nullable=True)
    open_interest = Column(Integer, nullable=True)
    open_interest_change = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("report_date", "symbol", name="uq_cot_date_symbol"),
    )


class MlModelRegistry(Base):
    """Tracks trained model versions and which is currently active."""
    __tablename__ = "ml_model_registry"

    id = Column(Integer, primary_key=True)
    model_name = Column(String, nullable=False)       # 'edge_quality', 'setup_scorer', etc.
    version = Column(Integer, nullable=False)
    file_path = Column(String, nullable=False)        # Relative path to serialized model
    training_data_count = Column(Integer, nullable=True)
    validation_metric = Column(Float, nullable=True)
    baseline_metric = Column(Float, nullable=True)    # Rules-based baseline on same data
    is_active = Column(Integer, default=0)            # 1 = currently serving
    created_at = Column(DateTime, default=_utcnow)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_models.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/db/models.py backend/tests/test_models.py
git commit -m "feat(ml): add ORM models for ML feature store and supporting tables"
```

---

### Task 3: Add Columns to Opportunity Model

**Files:**
- Modify: `backend/src/db/models.py` (Opportunity class, lines 456-498)
- Modify: `backend/tests/test_models.py`

The spec adds 10 ML columns to the `opportunities` table.

- [ ] **Step 1: Write test for new Opportunity columns**

Add to `backend/tests/test_models.py`:

```python
def test_opportunity_ml_columns(db_session):
    """Opportunity should have ML feature columns."""
    from src.db.models import Opportunity
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
    assert result.closing_line_value is None  # Post-event field
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_models.py::test_opportunity_ml_columns -v`
Expected: FAIL — `prob_sum` not a valid column

- [ ] **Step 3: Add columns to Opportunity model**

Add these columns to the `Opportunity` class in `backend/src/db/models.py` after the `expires_at` column (line ~492):

```python
    # ML feature columns (populated at scan time, used for training)
    prob_sum = Column(Float, nullable=True)               # Sum of devigged probabilities
    odds_ratio = Column(Float, nullable=True)             # provider odds / sharp odds
    odds_age_minutes = Column(Float, nullable=True)       # Time since provider last updated
    sharp_age_minutes = Column(Float, nullable=True)      # Time since Pinnacle last moved
    time_to_start_minutes = Column(Float, nullable=True)  # Minutes until event
    provider_count = Column(Integer, nullable=True)       # Number of providers with odds
    provider_odds_rank = Column(Integer, nullable=True)   # Rank among providers (1=best)
    market_consensus_spread = Column(Float, nullable=True)  # Std dev of odds across providers
    pinnacle_overround = Column(Float, nullable=True)     # Pinnacle margin on this market
    closing_line_value = Column(Float, nullable=True)     # Post-event: actual edge vs closing line
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_models.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/db/models.py backend/tests/test_models.py
git commit -m "feat(ml): add ML feature columns to Opportunity model"
```

---

### Task 4: Database Migration Script

**Files:**
- Create: `backend/src/ml/__init__.py`
- Create: `backend/src/ml/migrations.py`
- Create: `backend/tests/test_migrations.py`

This script applies schema changes to an existing SQLite database (the ORM models handle new installs, but existing DBs need ALTER TABLE).

- [ ] **Step 1: Write test for migrations**

```python
# backend/tests/test_migrations.py
"""Test idempotent database migrations for ML tables."""
import sqlite3
import pytest


@pytest.fixture
def raw_db(tmp_path):
    """Create a minimal SQLite DB mimicking existing schema (no ML tables)."""
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
    """Running migrations twice should not raise errors."""
    from src.ml.migrations import run_migrations
    run_migrations(raw_db)
    run_migrations(raw_db)  # Second run should be a no-op
    cursor = raw_db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ml_features'")
    assert cursor.fetchone() is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_migrations.py -v`
Expected: FAIL — `src.ml.migrations` not found

- [ ] **Step 3: Create the ml package and migrations module**

```python
# backend/src/ml/__init__.py
# (empty)
```

```python
# backend/src/ml/migrations.py
"""Idempotent schema migrations for ML tables.

Run against an existing SQLite database to add ML-related tables and columns.
Safe to re-run — checks for existence before applying each change.
"""
import sqlite3
import logging

logger = logging.getLogger(__name__)


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return cursor.fetchone() is not None


def run_migrations(conn: sqlite3.Connection) -> None:
    """Apply all ML schema migrations. Idempotent."""
    _create_ml_features(conn)
    _create_candle_snapshots(conn)
    _create_economic_events(conn)
    _create_news_impact(conn)
    _create_options_flow(conn)
    _create_cot_data(conn)
    _create_ml_model_registry(conn)
    _add_opportunity_columns(conn)
    conn.commit()
    logger.info("ML migrations complete")


def _create_ml_features(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "ml_features"):
        return
    conn.execute("""
        CREATE TABLE ml_features (
            id INTEGER PRIMARY KEY,
            domain TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            features JSON NOT NULL,
            feature_version INTEGER NOT NULL DEFAULT 1,
            outcome REAL,
            outcome_binary INTEGER,
            resolved_at DATETIME,
            created_at DATETIME DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX idx_ml_features_domain ON ml_features(domain)")
    conn.execute("CREATE INDEX idx_ml_features_source ON ml_features(source_type, source_id)")


def _create_candle_snapshots(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "candle_snapshots"):
        return
    conn.execute("""
        CREATE TABLE candle_snapshots (
            id INTEGER PRIMARY KEY,
            signal_id INTEGER NOT NULL REFERENCES trading_signals(id),
            candles JSON NOT NULL,
            timeframe TEXT DEFAULT '1m',
            created_at DATETIME DEFAULT (datetime('now'))
        )
    """)


def _create_economic_events(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "economic_events"):
        return
    conn.execute("""
        CREATE TABLE economic_events (
            id INTEGER PRIMARY KEY,
            event_name TEXT NOT NULL,
            event_datetime DATETIME NOT NULL,
            importance INTEGER NOT NULL,
            forecast REAL,
            actual REAL,
            previous REAL,
            surprise REAL,
            created_at DATETIME DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX idx_econ_events_datetime ON economic_events(event_datetime)")


def _create_news_impact(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "news_impact"):
        return
    conn.execute("""
        CREATE TABLE news_impact (
            id INTEGER PRIMARY KEY,
            event_id INTEGER NOT NULL REFERENCES economic_events(id),
            symbol TEXT NOT NULL DEFAULT 'NQ',
            price_before REAL NOT NULL,
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
            created_at DATETIME DEFAULT (datetime('now'))
        )
    """)


def _create_options_flow(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "options_flow"):
        return
    conn.execute("""
        CREATE TABLE options_flow (
            id INTEGER PRIMARY KEY,
            date TEXT NOT NULL,
            symbol TEXT NOT NULL DEFAULT 'NQ',
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
            created_at DATETIME DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE UNIQUE INDEX idx_options_flow_date ON options_flow(date, symbol)")


def _create_cot_data(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "cot_data"):
        return
    conn.execute("""
        CREATE TABLE cot_data (
            id INTEGER PRIMARY KEY,
            report_date TEXT NOT NULL,
            symbol TEXT NOT NULL DEFAULT 'NQ',
            net_position INTEGER,
            net_change INTEGER,
            long_pct REAL,
            short_pct REAL,
            open_interest INTEGER,
            open_interest_change INTEGER,
            created_at DATETIME DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE UNIQUE INDEX idx_cot_date ON cot_data(report_date, symbol)")


def _create_ml_model_registry(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "ml_model_registry"):
        return
    conn.execute("""
        CREATE TABLE ml_model_registry (
            id INTEGER PRIMARY KEY,
            model_name TEXT NOT NULL,
            version INTEGER NOT NULL,
            file_path TEXT NOT NULL,
            training_data_count INTEGER,
            validation_metric REAL,
            baseline_metric REAL,
            is_active INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT (datetime('now'))
        )
    """)


def _add_opportunity_columns(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "opportunities"):
        return
    columns = [
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
    for col_name, col_type in columns:
        if not _column_exists(conn, "opportunities", col_name):
            conn.execute(f"ALTER TABLE opportunities ADD COLUMN {col_name} {col_type}")
            logger.info(f"Added column opportunities.{col_name}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_migrations.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/__init__.py backend/src/ml/migrations.py backend/tests/test_migrations.py
git commit -m "feat(ml): add idempotent migration script for ML tables and columns"
```

---

### Task 5: Feature Store (Read/Write)

**Files:**
- Create: `backend/src/ml/feature_store.py`
- Create: `backend/tests/test_feature_store.py`

The feature store provides `log_features()` (write at decision time) and `resolve_outcome()` (update when bet/trade completes) and `get_training_data()` (read for model training).

- [ ] **Step 1: Write tests for feature store**

```python
# backend/tests/test_feature_store.py
"""Test ML feature store read/write operations."""
from datetime import datetime, timezone


def test_log_betting_features(db_session):
    from src.ml.feature_store import log_features
    log_features(
        session=db_session,
        domain="betting",
        source_id="opp-42",
        source_type="opportunity",
        features={"edge_pct": 7.5, "prob_sum": 1.02, "odds_ratio": 1.05},
        feature_version=1,
    )
    from src.db.models import MlFeature
    row = db_session.query(MlFeature).first()
    assert row is not None
    assert row.domain == "betting"
    assert row.features["edge_pct"] == 7.5
    assert row.outcome is None


def test_resolve_outcome(db_session):
    from src.ml.feature_store import log_features, resolve_outcome
    log_features(
        session=db_session,
        domain="betting",
        source_id="opp-42",
        source_type="opportunity",
        features={"edge_pct": 7.5},
        feature_version=1,
    )
    resolve_outcome(
        session=db_session,
        source_type="opportunity",
        source_id="opp-42",
        outcome=0.03,       # 3% CLV
        outcome_binary=1,   # Win
    )
    from src.db.models import MlFeature
    row = db_session.query(MlFeature).first()
    assert row.outcome == 0.03
    assert row.outcome_binary == 1
    assert row.resolved_at is not None


def test_get_training_data(db_session):
    from src.ml.feature_store import log_features, resolve_outcome, get_training_data
    # Log 3 features, resolve 2
    for i in range(3):
        log_features(db_session, "betting", f"opp-{i}", "opportunity",
                     {"edge_pct": 5.0 + i}, feature_version=1)
    resolve_outcome(db_session, "opportunity", "opp-0", outcome=0.02, outcome_binary=1)
    resolve_outcome(db_session, "opportunity", "opp-1", outcome=-0.01, outcome_binary=0)

    data = get_training_data(db_session, domain="betting", source_type="opportunity")
    assert len(data) == 2  # Only resolved rows
    assert all(row.outcome is not None for row in data)


def test_log_candle_snapshot(db_session):
    from src.db.models import TradingSignal, MarketSession
    # Create prerequisite session and signal
    ms = MarketSession(symbol="NQ", date="2026-03-12")
    db_session.add(ms)
    db_session.flush()
    sig = TradingSignal(session_id=ms.id, setup_type="spring", score=75.0)
    db_session.add(sig)
    db_session.flush()

    from src.ml.feature_store import log_candle_snapshot
    candles = [{"ts": f"2026-03-12T15:{i:02d}:00Z", "delta": 100 + i, "volume": 4000}
               for i in range(20)]
    log_candle_snapshot(db_session, signal_id=sig.id, candles=candles, timeframe="1m")

    from src.db.models import CandleSnapshot
    row = db_session.query(CandleSnapshot).first()
    assert row is not None
    assert len(row.candles) == 20
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_feature_store.py -v`
Expected: FAIL — `src.ml.feature_store` not found

- [ ] **Step 3: Implement feature store**

```python
# backend/src/ml/feature_store.py
"""ML Feature Store — read/write feature vectors and outcomes.

Write features at decision time (scan/signal), resolve with outcomes later.
Training reads only resolved rows.
"""
import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from src.db.models import MlFeature, CandleSnapshot

logger = logging.getLogger(__name__)

CURRENT_FEATURE_VERSION = 1


def log_features(
    session: Session,
    domain: str,
    source_id: str,
    source_type: str,
    features: dict,
    feature_version: int = CURRENT_FEATURE_VERSION,
) -> MlFeature:
    """Log a feature vector at decision time. Outcome filled later."""
    row = MlFeature(
        domain=domain,
        source_id=source_id,
        source_type=source_type,
        features=features,
        feature_version=feature_version,
    )
    session.add(row)
    session.flush()
    logger.debug(f"Logged {domain}/{source_type} features for {source_id}")
    return row


def resolve_outcome(
    session: Session,
    source_type: str,
    source_id: str,
    outcome: float,
    outcome_binary: int,
) -> bool:
    """Update a feature row with its resolved outcome. Returns True if found."""
    row = (
        session.query(MlFeature)
        .filter_by(source_type=source_type, source_id=source_id)
        .first()
    )
    if row is None:
        logger.warning(f"No feature row for {source_type}/{source_id}")
        return False
    row.outcome = outcome
    row.outcome_binary = outcome_binary
    row.resolved_at = datetime.now(timezone.utc)
    session.flush()
    return True


def get_training_data(
    session: Session,
    domain: str,
    source_type: str,
    feature_version: int | None = None,
) -> list[MlFeature]:
    """Get resolved feature rows for model training."""
    query = (
        session.query(MlFeature)
        .filter(
            MlFeature.domain == domain,
            MlFeature.source_type == source_type,
            MlFeature.outcome.isnot(None),
        )
    )
    if feature_version is not None:
        query = query.filter(MlFeature.feature_version == feature_version)
    return query.order_by(MlFeature.created_at).all()


def log_candle_snapshot(
    session: Session,
    signal_id: int,
    candles: list[dict],
    timeframe: str = "1m",
) -> CandleSnapshot:
    """Log a candle snapshot for temporal pattern training (M6)."""
    row = CandleSnapshot(
        signal_id=signal_id,
        candles=candles,
        timeframe=timeframe,
    )
    session.add(row)
    session.flush()
    logger.debug(f"Logged {len(candles)}-candle snapshot for signal {signal_id}")
    return row
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_feature_store.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/feature_store.py backend/tests/test_feature_store.py
git commit -m "feat(ml): implement feature store with log/resolve/query operations"
```

---

## Chunk 2: Sports Betting Feature Extraction

### Task 6: Betting Feature Extractor

**Files:**
- Create: `backend/src/ml/features/__init__.py`
- Create: `backend/src/ml/features/betting_features.py`
- Create: `backend/tests/test_betting_features.py`

Extracts the full M1 (Edge Quality) feature vector from scanner context at scan time.

- [ ] **Step 1: Write tests**

```python
# backend/tests/test_betting_features.py
"""Test betting feature extraction for M1 Edge Quality model."""
from datetime import datetime, timezone, timedelta


def test_extract_basic_features():
    """Should extract edge, prob_sum, odds_ratio from scan context."""
    from src.ml.features.betting_features import extract_betting_features

    features = extract_betting_features(
        edge_pct=7.5,
        provider_odds=2.10,
        fair_odds=1.95,
        fair_probability=0.513,
        provider="betsson",
        sport="football",
        market="1x2",
        event_id="evt-1",
        # Market context
        prob_sum=1.02,
        odds_by_outcome={"home": [
            {"provider": "pinnacle", "odds": 1.95, "updated_at": datetime.now(timezone.utc)},
            {"provider": "betsson", "odds": 2.10, "updated_at": datetime.now(timezone.utc)},
            {"provider": "unibet", "odds": 2.05, "updated_at": datetime.now(timezone.utc)},
        ]},
        pinnacle_overround=0.025,
        event_start_time=datetime.now(timezone.utc) + timedelta(hours=2),
    )

    assert isinstance(features, dict)
    assert features["edge_pct"] == 7.5
    assert features["prob_sum"] == 1.02
    assert abs(features["odds_ratio"] - 2.10 / 1.95) < 0.01
    assert features["sport"] == "football"
    assert features["market_type"] == "1x2"
    assert features["provider_platform"] == "betsson"
    assert features["num_providers_with_odds"] >= 2  # Excluding pinnacle
    assert "time_to_start_minutes" in features
    assert "hour_of_day" in features


def test_extract_provider_odds_rank():
    """Provider with best odds should rank 1."""
    from src.ml.features.betting_features import extract_betting_features

    features = extract_betting_features(
        edge_pct=5.0,
        provider_odds=2.20,  # Best soft price
        fair_odds=2.00,
        fair_probability=0.50,
        provider="betsson",
        sport="football",
        market="1x2",
        event_id="evt-1",
        prob_sum=1.01,
        odds_by_outcome={"home": [
            {"provider": "pinnacle", "odds": 2.00, "updated_at": datetime.now(timezone.utc)},
            {"provider": "betsson", "odds": 2.20, "updated_at": datetime.now(timezone.utc)},
            {"provider": "unibet", "odds": 2.10, "updated_at": datetime.now(timezone.utc)},
            {"provider": "10bet", "odds": 2.05, "updated_at": datetime.now(timezone.utc)},
        ]},
        pinnacle_overround=0.03,
        event_start_time=datetime.now(timezone.utc) + timedelta(hours=3),
    )

    assert features["provider_odds_rank"] == 1
    assert features["num_providers_with_odds"] == 3  # Soft providers only


def test_extract_handles_missing_start_time():
    """Should handle None event_start_time gracefully."""
    from src.ml.features.betting_features import extract_betting_features

    features = extract_betting_features(
        edge_pct=6.0,
        provider_odds=2.10,
        fair_odds=1.98,
        fair_probability=0.505,
        provider="unibet",
        sport="tennis",
        market="moneyline",
        event_id="evt-2",
        prob_sum=1.01,
        odds_by_outcome={"home": [
            {"provider": "pinnacle", "odds": 1.98, "updated_at": datetime.now(timezone.utc)},
            {"provider": "unibet", "odds": 2.10, "updated_at": datetime.now(timezone.utc)},
        ]},
        pinnacle_overround=0.02,
        event_start_time=None,
    )

    assert features["time_to_start_minutes"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_betting_features.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement betting feature extractor**

```python
# backend/src/ml/features/__init__.py
# (empty)
```

```python
# backend/src/ml/features/betting_features.py
"""Extract feature vectors for sports betting opportunities (M1 Edge Quality).

Called inline after value scanning. All features are point-in-time —
no future information leakage.
"""
import statistics
from datetime import datetime, timezone

from src.constants import SHARP_PROVIDERS, PLATFORM_MAP


def extract_betting_features(
    edge_pct: float,
    provider_odds: float,
    fair_odds: float,
    fair_probability: float,
    provider: str,
    sport: str,
    market: str,
    event_id: str,
    prob_sum: float,
    odds_by_outcome: dict[str, list[dict]],
    pinnacle_overround: float,
    event_start_time: datetime | None,
    point: float | None = None,
) -> dict:
    """Extract full feature vector for a single value bet opportunity.

    Args:
        edge_pct: Raw edge vs Pinnacle (already computed by scanner)
        provider_odds: The soft provider's odds
        fair_odds: De-vigged Pinnacle fair odds
        fair_probability: 1 / fair_odds
        provider: Provider ID (e.g., 'betsson')
        sport: Sport name
        market: Market type (1x2, moneyline, spread, total)
        event_id: Event identifier
        prob_sum: Sum of devigged probabilities for this market
        odds_by_outcome: Dict of outcome → list of {provider, odds, updated_at}
        pinnacle_overround: Pinnacle's margin on this market
        event_start_time: Event start time (UTC) or None
        point: Spread/total line value (None for 1x2/moneyline)

    Returns:
        Feature dict ready for ml_features.features JSON column.
    """
    now = datetime.now(timezone.utc)

    # Collect soft provider odds for this outcome
    all_outcome_odds = odds_by_outcome.get(
        _find_outcome_for_provider(odds_by_outcome, provider, provider_odds), []
    )
    soft_odds = [p for p in all_outcome_odds if p["provider"] not in SHARP_PROVIDERS]
    soft_odds_values = [p["odds"] for p in soft_odds]

    # Provider odds rank (1 = best price among soft books)
    sorted_odds = sorted(soft_odds_values, reverse=True)
    provider_odds_rank = sorted_odds.index(provider_odds) + 1 if provider_odds in sorted_odds else len(sorted_odds)

    # Market consensus spread (std dev of soft odds)
    consensus_spread = statistics.stdev(soft_odds_values) if len(soft_odds_values) >= 2 else 0.0

    # Odds age (minutes since provider last updated)
    provider_entry = next((p for p in all_outcome_odds if p["provider"] == provider), None)
    odds_age_minutes = None
    if provider_entry and provider_entry.get("updated_at"):
        updated = provider_entry["updated_at"]
        if isinstance(updated, str):
            updated = datetime.fromisoformat(updated)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        odds_age_minutes = (now - updated).total_seconds() / 60

    # Sharp age
    sharp_entry = next((p for p in all_outcome_odds if p["provider"] in SHARP_PROVIDERS), None)
    sharp_age_minutes = None
    if sharp_entry and sharp_entry.get("updated_at"):
        updated = sharp_entry["updated_at"]
        if isinstance(updated, str):
            updated = datetime.fromisoformat(updated)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        sharp_age_minutes = (now - updated).total_seconds() / 60

    # Time to start
    time_to_start = None
    if event_start_time:
        if event_start_time.tzinfo is None:
            event_start_time = event_start_time.replace(tzinfo=timezone.utc)
        time_to_start = (event_start_time - now).total_seconds() / 60

    # Provider platform
    platform = PLATFORM_MAP.get(provider, provider)

    return {
        # Market context
        "edge_pct": edge_pct,
        "prob_sum": prob_sum,
        "odds_ratio": provider_odds / fair_odds if fair_odds > 0 else None,
        "odds_age_minutes": odds_age_minutes,
        "sharp_age_minutes": sharp_age_minutes,
        "time_to_start_minutes": time_to_start,
        "pinnacle_overround": pinnacle_overround,
        "num_providers_with_odds": len(soft_odds),
        "provider_odds_rank": provider_odds_rank,
        "market_consensus_spread": round(consensus_spread, 4),
        # Provider context
        "provider_platform": platform,
        # Event context
        "sport": sport,
        "market_type": market,
        "point": point,
        # Temporal
        "hour_of_day": now.hour,
        "day_of_week": now.weekday(),
    }


def _find_outcome_for_provider(
    odds_by_outcome: dict[str, list[dict]], provider: str, odds: float
) -> str | None:
    """Find which outcome key contains this provider's odds."""
    for outcome, providers in odds_by_outcome.items():
        for p in providers:
            if p["provider"] == provider and abs(p["odds"] - odds) < 0.001:
                return outcome
    # Fallback: return first outcome that has this provider
    for outcome, providers in odds_by_outcome.items():
        for p in providers:
            if p["provider"] == provider:
                return outcome
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_betting_features.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/features/__init__.py backend/src/ml/features/betting_features.py backend/tests/test_betting_features.py
git commit -m "feat(ml): implement betting feature extractor for M1 edge quality"
```

---

### Task 7: Hook Betting Features Into Scanner

**Files:**
- Modify: `backend/src/analysis/scanner.py` (after `scan_value()` returns, ~line 175)

This is the integration point: after the scanner finds value bets, we log their features. This is a lightweight hook — if feature logging fails, it should not break scanning.

- [ ] **Step 1: Add feature logging to scan_value_with_stakes**

In `backend/src/analysis/scanner.py`, add the feature logging call inside `scan_value_with_stakes()` after each `ValueBet` is enriched (around line 247, inside the `for vb in raw_bets:` loop, after `enriched_bets.append(enriched)`):

```python
            # Log ML features (best-effort, never blocks scanning)
            try:
                from src.ml.features.betting_features import extract_betting_features
                from src.ml.feature_store import log_features
                ml_features = extract_betting_features(
                    edge_pct=vb.edge_pct,
                    provider_odds=vb.provider_odds,
                    fair_odds=vb.fair_odds,
                    fair_probability=vb.fair_probability,
                    provider=vb.provider,
                    sport=event.sport if event else "unknown",
                    market=vb.market,
                    event_id=vb.event_id,
                    prob_sum=soft_prob_sums.get(vb.provider, 0),
                    odds_by_outcome=odds_by_outcome_for_event.get(vb.event_id, {}),
                    pinnacle_overround=pinnacle_overround_for_event.get(vb.event_id, 0),
                    event_start_time=event.start_time if event else None,
                    point=vb.point if hasattr(vb, 'point') else None,
                )
                log_features(
                    session=self.session,
                    domain="betting",
                    source_id=str(vb.event_id) + "_" + vb.provider + "_" + vb.market + "_" + vb.outcome,
                    source_type="opportunity",
                    features=ml_features,
                )
            except Exception as e:
                logger.debug(f"ML feature logging skipped: {e}")
```

**Important context:** The scanner currently doesn't expose `soft_prob_sums`, `odds_by_outcome`, or `pinnacle_overround` at the `scan_value_with_stakes` level — these are computed inside `find_value_in_market`. The integration requires either:

(a) Storing these values on the `ValueBet` dataclass (preferred — add `prob_sum`, `pinnacle_overround`, `odds_by_outcome_snapshot` fields), or
(b) Re-computing them in the hook (wasteful).

**Approach (a):** Add optional fields to `ValueBet` in `backend/src/analysis/value.py`:

```python
# Add to ValueBet dataclass (after existing fields):
    prob_sum: float | None = None
    pinnacle_overround: float | None = None
    odds_snapshot: list[dict] | None = None   # [{provider, odds, updated_at}] for this outcome
```

Then populate these in `find_value_in_market()` when creating each `ValueBet`. The ML hook in `scan_value_with_stakes` reads them directly from `vb.prob_sum`, etc.

- [ ] **Step 2: Add fields to ValueBet dataclass**

In `backend/src/analysis/value.py`, add to the `ValueBet` dataclass (after existing fields):

```python
    # ML feature data (populated by scanner, consumed by feature extractor)
    prob_sum: float | None = None
    pinnacle_overround: float | None = None
    odds_snapshot: list[dict] | None = None
```

- [ ] **Step 3: Propagate ML fields in scan_value_with_stakes enriched copy**

In `backend/src/analysis/scanner.py`, inside `scan_value_with_stakes()`, the enriched `ValueBet` constructor (~line 229) copies fields from `vb`. Add the new ML fields to this constructor:

```python
            enriched = ValueBet(
                # ... existing fields ...
                prob_sum=vb.prob_sum,
                pinnacle_overround=vb.pinnacle_overround,
                odds_snapshot=vb.odds_snapshot,
            )
```

- [ ] **Step 4: Populate ML fields in find_value_in_market**

In `backend/src/analysis/scanner.py`, inside `find_value_in_market()`, when constructing each `ValueBet`, add:

```python
    prob_sum=soft_prob_sums.get(provider, 0),
    pinnacle_overround=pinnacle_overround,
    odds_snapshot=provider_odds_list,
```

Where `pinnacle_overround` is already computed as part of devigging (it's the margin from `calculate_margin()`). If not available as a local variable, compute it:

```python
pinnacle_overround = sum(1.0 / o["odds"] for o in pinnacle_market.values()) - 1.0 if pinnacle_market else 0
```

- [ ] **Step 4: Simplify the ML hook in scan_value_with_stakes**

With ML fields on ValueBet, the hook becomes:

```python
            try:
                from src.ml.features.betting_features import extract_betting_features
                from src.ml.feature_store import log_features
                # Build odds_by_outcome from the snapshot
                outcome_odds = {vb.outcome: vb.odds_snapshot or []}
                ml_features = extract_betting_features(
                    edge_pct=vb.edge_pct,
                    provider_odds=vb.provider_odds,
                    fair_odds=vb.fair_odds,
                    fair_probability=vb.fair_probability,
                    provider=vb.provider,
                    sport=event.sport if event else "unknown",
                    market=vb.market,
                    event_id=vb.event_id,
                    prob_sum=vb.prob_sum or 0,
                    odds_by_outcome=outcome_odds,
                    pinnacle_overround=vb.pinnacle_overround or 0,
                    event_start_time=event.start_time if event else None,
                    point=vb.point,
                )
                log_features(
                    session=self.session,
                    domain="betting",
                    source_id=f"{vb.event_id}_{vb.provider}_{vb.market}_{vb.outcome}",
                    source_type="opportunity",
                    features=ml_features,
                )
            except Exception as e:
                logger.debug(f"ML feature logging skipped: {e}")
```

- [ ] **Step 5: Verify scanning still works**

Run: `cd backend && python -m src.app extract pinnacle` (or a quick scan test)
Expected: Scanning completes without errors. ML feature logging may log debug messages.

- [ ] **Step 6: Commit**

```bash
git add backend/src/analysis/value.py backend/src/analysis/scanner.py
git commit -m "feat(ml): hook betting feature extraction into value scanner"
```

---

## Chunk 3: Trading Feature Extraction

### Task 8: Trading Feature Extractor

**Files:**
- Create: `backend/src/ml/features/trading_features.py`
- Create: `backend/tests/test_trading_features.py`

Extracts the M5 (Setup Score Predictor) feature vector from trading signal context.

- [ ] **Step 1: Write tests**

```python
# backend/tests/test_trading_features.py
"""Test trading feature extraction for M5 Setup Score model."""


def test_extract_basic_trading_features():
    from src.ml.features.trading_features import extract_trading_features

    features = extract_trading_features(
        setup_type="spring",
        direction="long",
        level_touched="val",
        base_score=65,
        # Orderflow (continuous values)
        delta=380,
        delta_pct=0.089,
        cvd=12500,
        cvd_slope_5bar=45.2,
        volume=4250,
        volume_ratio_vs_20bar=1.45,
        body_ratio_last=0.42,
        spread_ticks=30,
        passive_active_ratio=1.8,
        trapped_magnitude=0.35,
        # Market structure
        distance_to_level_ticks=3,
        distance_to_poc_ticks=15,
        distance_to_vwap_ticks=-8,
        price_position_in_va=0.72,
        ib_range_ticks=120,
        ib_range_vs_avg=0.85,
        # Session context
        minutes_since_rth_open=45,
        market_type="normal",
        opening_type="OTD",
    )

    assert isinstance(features, dict)
    assert features["setup_type"] == "spring"
    assert features["direction"] == "long"
    assert features["delta"] == 380
    assert features["delta_pct"] == 0.089
    assert features["distance_to_level_ticks"] == 3
    assert features["minutes_since_rth_open"] == 45


def test_extract_with_defaults():
    """Missing optional values should default to None."""
    from src.ml.features.trading_features import extract_trading_features

    features = extract_trading_features(
        setup_type="ib_break",
        direction="short",
    )

    assert features["setup_type"] == "ib_break"
    assert features["delta"] is None
    assert features["distance_to_poc_ticks"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_trading_features.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement trading feature extractor**

```python
# backend/src/ml/features/trading_features.py
"""Extract feature vectors for trading signals (M5 Setup Score Predictor).

Captures continuous orderflow, market structure, and session context
at signal generation time. All values are continuous (not boolean thresholds).
"""


def extract_trading_features(
    setup_type: str,
    direction: str,
    level_touched: str | None = None,
    base_score: float | None = None,
    # Orderflow — continuous
    delta: int | None = None,
    delta_pct: float | None = None,
    cvd: int | None = None,
    cvd_slope_5bar: float | None = None,
    cvd_slope_10bar: float | None = None,
    cvd_acceleration: float | None = None,
    volume: int | None = None,
    volume_ratio_vs_20bar: float | None = None,
    volume_ratio_vs_session: float | None = None,
    body_ratio_last: float | None = None,
    body_ratio_avg_3bar: float | None = None,
    spread_ticks: float | None = None,
    spread_ratio_vs_avg: float | None = None,
    passive_active_ratio: float | None = None,
    trapped_magnitude: float | None = None,
    tick_count_ratio: float | None = None,
    absorption_bar_count: int | None = None,
    delta_divergence_bars: int | None = None,
    delta_unwind_speed_bars: int | None = None,
    # Footprint / institutional flow
    imbalance_ratio_max: float | None = None,
    stacked_imbalance_count: int | None = None,
    stacked_imbalance_direction: str | None = None,
    big_trades_count: int | None = None,
    big_trades_net_delta: int | None = None,
    stop_run_magnitude_ticks: float | None = None,
    stop_run_volume_ratio: float | None = None,
    unfinished_auction_count_above: int | None = None,
    unfinished_auction_count_below: int | None = None,
    session_volume_total: int | None = None,
    session_volume_acceleration: float | None = None,
    # Market structure
    distance_to_level_ticks: float | None = None,
    distance_to_poc_ticks: float | None = None,
    distance_to_vwap_ticks: float | None = None,
    price_position_in_va: float | None = None,
    price_vs_vwap_sd: float | None = None,
    ib_range_ticks: float | None = None,
    ib_range_vs_avg: float | None = None,
    va_width_ticks: float | None = None,
    va_width_vs_yesterday: float | None = None,
    single_print_count_above: int | None = None,
    single_print_count_below: int | None = None,
    num_levels_within_20_ticks: int | None = None,
    # Session context
    rotation_factor: int | None = None,
    aspr: float | None = None,
    aspr_percentile: float | None = None,
    market_type: str | None = None,
    opening_type: str | None = None,
    poor_high: bool | None = None,
    poor_low: bool | None = None,
    minutes_since_rth_open: float | None = None,
    minutes_since_ib_close: float | None = None,
    # Macro (from options_flow table, if available)
    vix_level: float | None = None,
    gex: float | None = None,
    news_event_minutes_away: float | None = None,
    news_event_importance: int | None = None,
) -> dict:
    """Extract full feature vector for a trading signal.

    Returns dict ready for ml_features.features JSON column.
    All values are continuous where possible — no boolean thresholds.
    """
    return {
        # Setup identity
        "setup_type": setup_type,
        "direction": direction,
        "level_touched": level_touched,
        "base_score": base_score,
        # Orderflow — continuous
        "delta": delta,
        "delta_pct": delta_pct,
        "cvd": cvd,
        "cvd_slope_5bar": cvd_slope_5bar,
        "cvd_slope_10bar": cvd_slope_10bar,
        "cvd_acceleration": cvd_acceleration,
        "volume": volume,
        "volume_ratio_vs_20bar": volume_ratio_vs_20bar,
        "volume_ratio_vs_session": volume_ratio_vs_session,
        "body_ratio_last": body_ratio_last,
        "body_ratio_avg_3bar": body_ratio_avg_3bar,
        "spread_ticks": spread_ticks,
        "spread_ratio_vs_avg": spread_ratio_vs_avg,
        "passive_active_ratio": passive_active_ratio,
        "trapped_magnitude": trapped_magnitude,
        "tick_count_ratio": tick_count_ratio,
        "absorption_bar_count": absorption_bar_count,
        "delta_divergence_bars": delta_divergence_bars,
        "delta_unwind_speed_bars": delta_unwind_speed_bars,
        # Footprint / institutional
        "imbalance_ratio_max": imbalance_ratio_max,
        "stacked_imbalance_count": stacked_imbalance_count,
        "stacked_imbalance_direction": stacked_imbalance_direction,
        "big_trades_count": big_trades_count,
        "big_trades_net_delta": big_trades_net_delta,
        "stop_run_magnitude_ticks": stop_run_magnitude_ticks,
        "stop_run_volume_ratio": stop_run_volume_ratio,
        "unfinished_auction_count_above": unfinished_auction_count_above,
        "unfinished_auction_count_below": unfinished_auction_count_below,
        "session_volume_total": session_volume_total,
        "session_volume_acceleration": session_volume_acceleration,
        # Market structure
        "distance_to_level_ticks": distance_to_level_ticks,
        "distance_to_poc_ticks": distance_to_poc_ticks,
        "distance_to_vwap_ticks": distance_to_vwap_ticks,
        "price_position_in_va": price_position_in_va,
        "price_vs_vwap_sd": price_vs_vwap_sd,
        "ib_range_ticks": ib_range_ticks,
        "ib_range_vs_avg": ib_range_vs_avg,
        "va_width_ticks": va_width_ticks,
        "va_width_vs_yesterday": va_width_vs_yesterday,
        "single_print_count_above": single_print_count_above,
        "single_print_count_below": single_print_count_below,
        "num_levels_within_20_ticks": num_levels_within_20_ticks,
        # Session context
        "rotation_factor": rotation_factor,
        "aspr": aspr,
        "aspr_percentile": aspr_percentile,
        "market_type": market_type,
        "opening_type": opening_type,
        "poor_high": poor_high,
        "poor_low": poor_low,
        "minutes_since_rth_open": minutes_since_rth_open,
        "minutes_since_ib_close": minutes_since_ib_close,
        # Macro context
        "vix_level": vix_level,
        "gex": gex,
        "news_event_minutes_away": news_event_minutes_away,
        "news_event_importance": news_event_importance,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_trading_features.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/features/trading_features.py backend/tests/test_trading_features.py
git commit -m "feat(ml): implement trading feature extractor for M5 setup scorer"
```

---

### Task 9: Candle Snapshot Extractor

**Files:**
- Create: `backend/src/ml/features/candle_features.py`
- Create: `backend/tests/test_candle_features.py`

Snapshots the last 20 candles of orderflow data at signal time for M6 temporal pattern training.

- [ ] **Step 1: Write tests**

```python
# backend/tests/test_candle_features.py
"""Test candle snapshot extraction for M6 temporal patterns."""
from datetime import datetime, timezone
from src.market_data.orderflow import CandleFlow


def _make_candle(minute: int, delta: int = 100, volume: int = 4000) -> CandleFlow:
    return CandleFlow(
        ts=datetime(2026, 3, 12, 15, minute, tzinfo=timezone.utc),
        open=21500.0, high=21505.0, low=21498.0, close=21503.0,
        volume=volume, buy_volume=volume // 2 + delta // 2,
        sell_volume=volume // 2 - delta // 2,
        delta=delta, tick_count=1800, spread=28,
    )


def test_snapshot_extracts_last_20():
    from src.ml.features.candle_features import snapshot_candles
    candles = [_make_candle(i) for i in range(30)]
    result = snapshot_candles(candles, vwap=21501.0, poc=21504.0)
    assert len(result) == 20  # Last 20 only


def test_snapshot_with_fewer_than_20():
    from src.ml.features.candle_features import snapshot_candles
    candles = [_make_candle(i) for i in range(5)]
    result = snapshot_candles(candles, vwap=21501.0, poc=21504.0)
    assert len(result) == 5  # Use what's available


def test_snapshot_fields():
    from src.ml.features.candle_features import snapshot_candles
    candles = [_make_candle(i, delta=200 + i * 10, volume=4000 + i * 100) for i in range(20)]
    result = snapshot_candles(candles, vwap=21501.0, poc=21504.0)
    c = result[0]
    assert "delta" in c
    assert "delta_pct" in c
    assert "volume" in c
    assert "body_ratio" in c
    assert "vwap_distance_ticks" in c
    assert "poc_distance_ticks" in c
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_candle_features.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement candle snapshot extractor**

```python
# backend/src/ml/features/candle_features.py
"""Snapshot last 20 candles of orderflow data at signal time.

Produces the input tensor shape (N, 16) for M6 temporal pattern recognition.
Each candle becomes a dict with 16 continuous features.
"""
from src.market_data.orderflow import CandleFlow

# NQ tick size = 0.25 points
TICK_SIZE = 0.25


def snapshot_candles(
    candles: list[CandleFlow],
    vwap: float | None = None,
    poc: float | None = None,
    max_candles: int = 20,
) -> list[dict]:
    """Extract last N candles as feature dicts for ML storage.

    Args:
        candles: List of CandleFlow objects (chronological order)
        vwap: Current VWAP price (for distance calculation)
        poc: Current POC price (for distance calculation)
        max_candles: How many candles to keep (default 20, last N)

    Returns:
        List of dicts, each with 16 features per candle.
    """
    recent = candles[-max_candles:] if len(candles) > max_candles else candles
    if not recent:
        return []

    # Session average volume for ratio calculation
    avg_volume = sum(c.volume for c in recent) / len(recent) if recent else 1

    result = []
    cumulative_delta = 0
    for c in recent:
        cumulative_delta += c.delta
        vol = c.volume if c.volume > 0 else 1

        result.append({
            "ts": c.ts.isoformat() if c.ts else None,
            "delta": c.delta,
            "delta_pct": round(c.delta / vol, 4),
            "cvd": cumulative_delta,
            "volume": c.volume,
            "volume_ratio": round(c.volume / avg_volume, 3) if avg_volume > 0 else 1.0,
            "spread_ticks": round(c.spread / TICK_SIZE, 1) if c.spread else 0,
            "body_ratio": round(c.body_ratio, 3),
            "close_position": round(
                (c.close - c.low) / (c.high - c.low), 3
            ) if c.high != c.low else 0.5,
            "tick_count": c.tick_count,
            "passive_active_ratio": round(
                (vol - abs(c.delta)) / abs(c.delta), 2
            ) if c.delta != 0 else 0.0,
            "vwap_distance_ticks": round(
                (c.close - vwap) / TICK_SIZE, 1
            ) if vwap else None,
            "poc_distance_ticks": round(
                (c.close - poc) / TICK_SIZE, 1
            ) if poc else None,
            # Placeholders for footprint data (populated when available)
            "imbalance_ratio_max": None,
            "stacked_imbalance_count": None,
            "big_trades_count": None,
            "big_trades_net_delta": None,
        })

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_candle_features.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/features/candle_features.py backend/tests/test_candle_features.py
git commit -m "feat(ml): implement candle snapshot extractor for M6 temporal patterns"
```

---

### Task 10: Hook Trading Features Into Scoring

**Files:**
- Modify: `backend/src/market_data/scoring.py` (enrich conditions with continuous values)
- Modify: `backend/src/market_data/scanner.py` (log features + candle snapshot after signal)

The spec says to enrich `trading_signals.conditions` JSON with a `continuous` dict inside each condition. The simplest approach: after `score_candidate()` returns, attach continuous values as a separate step.

- [ ] **Step 1: Add continuous value enrichment to scoring.py**

Add a new function to `backend/src/market_data/scoring.py`:

```python
def enrich_conditions_with_continuous(
    conditions: list[dict],
    orderflow: OrderflowSignals,
    candles: list | None = None,
) -> list[dict]:
    """Add continuous ML feature values to each condition dict.

    This is additive — existing condition fields are preserved.
    The `continuous` sub-dict is ignored by the current scoring system
    but consumed by the ML feature extractor.
    """
    # Build continuous snapshot from all available OrderflowSignals fields.
    # Fields not yet available from compute_signals() are set to None
    # and will be populated as orderflow.py is enriched in Phase 2/3.
    continuous = {
        "delta_magnitude": orderflow.delta,
        "delta_pct_of_volume": None,       # TODO: needs volume context from candles
        "cvd": orderflow.cvd,
        "cvd_slope_5bar": None,            # TODO: Phase 2 — needs candle history
        "cvd_slope_10bar": None,
        "passive_active_ratio": orderflow.passive_active_ratio,
        "delta_aligned": orderflow.delta_aligned,
        "delta_divergence": orderflow.delta_divergence,
        "delta_unwind": orderflow.delta_unwind,
        "cvd_trend": orderflow.cvd_trend,
        "vsa_absorption": orderflow.vsa_absorption,
        "tick_vol_accelerating": orderflow.tick_vol_accelerating,
        "trapped_traders": orderflow.trapped_traders,
        # Footprint features — TODO: Phase 2
        "imbalance_ratio_max": None,
        "stacked_imbalance_count": None,
        "big_trades_count": None,
        "big_trades_net_delta": None,
    }

    for cond in conditions:
        cond["continuous"] = continuous

    return conditions
```

- [ ] **Step 2: Hook feature logging into MarketScanner.scan()**

In `backend/src/market_data/scanner.py`, after a signal is created and stored, add:

```python
            # Log ML features (best-effort)
            try:
                from src.ml.features.trading_features import extract_trading_features
                from src.ml.features.candle_features import snapshot_candles
                from src.ml.feature_store import log_features, log_candle_snapshot

                ml_features = extract_trading_features(
                    setup_type=signal.setup_type,
                    direction=signal.direction,
                    level_touched=signal.level_touched,
                    base_score=candidate.base_score,
                    delta=orderflow.delta if orderflow else None,
                    passive_active_ratio=orderflow.passive_active_ratio if orderflow else None,
                    # ... populate from available context
                    minutes_since_rth_open=session_analysis.minutes_since_open if session_analysis else None,
                    market_type=session_analysis.day_type if session_analysis else None,
                )
                log_features(
                    session=self.db_session,
                    domain="trading",
                    source_id=str(signal.id),
                    source_type="signal",
                    features=ml_features,
                )

                # Candle snapshot for M6
                if candles:
                    snapshot = snapshot_candles(
                        candles,
                        vwap=session_analysis.vwap if session_analysis else None,
                        poc=session_analysis.poc if session_analysis else None,
                    )
                    log_candle_snapshot(self.db_session, signal_id=signal.id, candles=snapshot)
            except Exception as e:
                logger.debug(f"ML feature logging skipped: {e}")
```

**Important implementation note:** The variable names above (`orderflow`, `session_analysis`, `candles`, `candidate`) are placeholders. The implementer MUST:
1. Read `backend/src/market_data/scanner.py` `MarketScanner.scan()` method
2. Find the exact point where `TradingSignal` is committed to DB (after `db_session.add(signal)` + `flush()`)
3. Map to the actual variable names in that scope (e.g., `signals_obj` for orderflow, `analysis` for session data)
4. If candle data is not directly available, query it from `MarketTrade` table for the session

If the variable names cannot be determined from the scan method's scope, restructure the hook as a standalone function:
```python
# In ml/features/trading_features.py:
def log_signal_features(db_session, signal: TradingSignal, session: MarketSession):
    """Call after signal creation. Queries needed context internally."""
    ...
```

- [ ] **Step 3: Verify trading signal generation still works**

Run the trading signal scanner (if a test mode exists) or verify the backend starts without errors:
`cd backend && python -c "from src.market_data.scanner import MarketScanner; print('OK')"`

- [ ] **Step 4: Commit**

```bash
git add backend/src/market_data/scoring.py backend/src/market_data/scanner.py
git commit -m "feat(ml): hook trading feature extraction into signal generation"
```

---

## Chunk 4: Macro Data Collection

### Task 11: Economic Calendar Fetcher

**Files:**
- Create: `backend/src/ml/macro/__init__.py`
- Create: `backend/src/ml/macro/economic_calendar.py`
- Create: `backend/tests/test_macro_fetchers.py`

Fetches scheduled economic events from a free API and stores in `economic_events` table.

- [ ] **Step 1: Write tests**

```python
# backend/tests/test_macro_fetchers.py
"""Test macro data fetchers (economic calendar, options flow)."""
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone


def test_parse_economic_event():
    """Should parse API response into EconomicEvent-compatible dict."""
    from src.ml.macro.economic_calendar import parse_event

    raw = {
        "title": "CPI m/m",
        "date": "2026-03-12T08:30:00-04:00",
        "impact": "High",
        "forecast": "0.3%",
        "actual": "0.4%",
        "previous": "0.3%",
    }
    parsed = parse_event(raw)
    assert parsed["event_name"] == "CPI m/m"
    assert parsed["importance"] == 3  # High = 3
    assert parsed["forecast"] == 0.3
    assert parsed["actual"] == 0.4
    assert parsed["surprise"] == 0.1  # actual - forecast


def test_parse_importance_mapping():
    from src.ml.macro.economic_calendar import _parse_importance
    assert _parse_importance("High") == 3
    assert _parse_importance("Medium") == 2
    assert _parse_importance("Low") == 1
    assert _parse_importance("Holiday") == 0


def test_parse_percentage_value():
    from src.ml.macro.economic_calendar import _parse_numeric
    assert _parse_numeric("0.3%") == 0.3
    assert _parse_numeric("-1.2%") == -1.2
    # We store display values (250, not 250000) because economic calendar
    # sources use consistent units within each event type (e.g., jobless
    # claims always in K). Surprise = actual - forecast works correctly
    # when both are in the same unit.
    assert _parse_numeric("250K") == 250.0
    assert _parse_numeric("1.5M") == 1.5
    assert _parse_numeric("") is None
    assert _parse_numeric(None) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_macro_fetchers.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement economic calendar parser**

```python
# backend/src/ml/macro/__init__.py
# (empty)
```

```python
# backend/src/ml/macro/economic_calendar.py
"""Fetch and parse economic calendar events.

Data source: ForexFactory calendar API (free, no auth required).
Stores scheduled events (CPI, NFP, FOMC, etc.) with forecast/actual/surprise.

Run daily to populate economic_events table.
"""
import logging
import re
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

IMPORTANCE_MAP = {
    "high": 3,
    "medium": 2,
    "low": 1,
    "holiday": 0,
}


def fetch_events() -> list[dict]:
    """Fetch this week's economic events from ForexFactory feed.

    Returns list of parsed event dicts ready for DB insertion.
    """
    try:
        resp = httpx.get(CALENDAR_URL, timeout=15)
        resp.raise_for_status()
        raw_events = resp.json()
        return [parse_event(e) for e in raw_events if e.get("title")]
    except Exception as e:
        logger.error(f"Failed to fetch economic calendar: {e}")
        return []


def parse_event(raw: dict[str, Any]) -> dict:
    """Parse a single raw event into DB-ready dict."""
    forecast = _parse_numeric(raw.get("forecast"))
    actual = _parse_numeric(raw.get("actual"))
    surprise = round(actual - forecast, 4) if actual is not None and forecast is not None else None

    return {
        "event_name": raw.get("title", ""),
        "event_datetime": raw.get("date"),
        "importance": _parse_importance(raw.get("impact", "")),
        "forecast": forecast,
        "actual": actual,
        "previous": _parse_numeric(raw.get("previous")),
        "surprise": surprise,
    }


def _parse_importance(impact: str) -> int:
    return IMPORTANCE_MAP.get(impact.lower().strip(), 0)


def _parse_numeric(value: str | None) -> float | None:
    """Parse values like '0.3%', '-1.2%', '250K', '1.5M'.

    Strips unit suffixes (%, K, M, B) but does NOT multiply.
    Economic calendar sources use consistent units within each event type
    (e.g., jobless claims always reported in K), so surprise = actual - forecast
    works correctly when both values have the same suffix stripped.
    """
    if not value or not value.strip():
        return None
    cleaned = value.strip().rstrip("%")
    # Strip K/M/B suffixes (store display value, not absolute)
    for suffix in ("K", "M", "B", "k", "m", "b"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[:-1]
            break

    try:
        return round(float(cleaned), 4)
    except ValueError:
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_macro_fetchers.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/macro/__init__.py backend/src/ml/macro/economic_calendar.py backend/tests/test_macro_fetchers.py
git commit -m "feat(ml): add economic calendar fetcher and parser"
```

---

### Task 12: Options Flow / Macro Data Fetcher

**Files:**
- Create: `backend/src/ml/macro/options_flow.py`
- Modify: `backend/tests/test_macro_fetchers.py`

Fetches daily VIX, DXY, yields, and stores in `options_flow` table. Uses free Yahoo Finance or FRED data.

- [ ] **Step 1: Add tests**

Add to `backend/tests/test_macro_fetchers.py`:

```python
def test_build_options_flow_row():
    """Should build a valid options_flow dict from fetched data."""
    from src.ml.macro.options_flow import build_options_flow_row

    row = build_options_flow_row(
        date="2026-03-12",
        vix_level=18.5,
        vix_1d_change=-0.3,
        dxy_level=103.2,
        dxy_1d_change=0.15,
        us10y_level=4.25,
        us10y_1d_change=-0.02,
        us02y_level=4.50,
    )
    assert row["date"] == "2026-03-12"
    assert row["vix_level"] == 18.5
    assert row["yield_curve_spread"] == -0.25  # 10Y - 2Y
    assert row["symbol"] == "NQ"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_macro_fetchers.py::test_build_options_flow_row -v`
Expected: FAIL

- [ ] **Step 3: Implement options flow builder**

```python
# backend/src/ml/macro/options_flow.py
"""Fetch and store daily macro market data (VIX, DXY, yields, GEX).

Data sources:
- VIX, DXY, yields: Yahoo Finance (yfinance) or FRED API
- GEX: Manual input or derived (no free real-time source)

Run daily to populate options_flow table.
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_options_flow_row(
    date: str,
    vix_level: float | None = None,
    vix_1d_change: float | None = None,
    vix_term_structure: str | None = None,
    dxy_level: float | None = None,
    dxy_1d_change: float | None = None,
    us10y_level: float | None = None,
    us10y_1d_change: float | None = None,
    us02y_level: float | None = None,
    gex: float | None = None,
    gex_flip_level: float | None = None,
    net_options_delta: float | None = None,
    put_call_ratio: float | None = None,
    total_options_volume: float | None = None,
    es_nq_ratio: float | None = None,
) -> dict[str, Any]:
    """Build an options_flow row dict from fetched macro data."""
    yield_curve_spread = None
    if us10y_level is not None and us02y_level is not None:
        yield_curve_spread = round(us10y_level - us02y_level, 4)

    return {
        "date": date,
        "symbol": "NQ",
        "vix_level": vix_level,
        "vix_1d_change": vix_1d_change,
        "vix_term_structure": vix_term_structure,
        "dxy_level": dxy_level,
        "dxy_1d_change": dxy_1d_change,
        "us10y_level": us10y_level,
        "us10y_1d_change": us10y_1d_change,
        "us02y_level": us02y_level,
        "yield_curve_spread": yield_curve_spread,
        "gex": gex,
        "gex_flip_level": gex_flip_level,
        "net_options_delta": net_options_delta,
        "put_call_ratio": put_call_ratio,
        "total_options_volume": total_options_volume,
        "es_nq_ratio": es_nq_ratio,
    }


async def fetch_and_store_daily(session) -> bool:
    """Fetch today's macro data and upsert into options_flow table.

    Uses yfinance for VIX (^VIX), DXY (DX-Y.NYB), yields (^TNX, ^IRX).
    GEX must be provided manually or from a paid source.
    """
    try:
        import yfinance as yf
        from datetime import date, timedelta
        from src.db.models import OptionsFlow

        today = date.today().isoformat()

        # Fetch tickers
        tickers = yf.download(
            "^VIX DX-Y.NYB ^TNX ^IRX",
            period="5d",
            auto_adjust=True,
            progress=False,
        )

        if tickers.empty:
            logger.warning("No macro data returned from yfinance")
            return False

        latest = tickers.iloc[-1]
        prev = tickers.iloc[-2] if len(tickers) > 1 else latest

        row_data = build_options_flow_row(
            date=today,
            vix_level=float(latest.get(("Close", "^VIX"), 0)) or None,
            vix_1d_change=round(
                float(latest.get(("Close", "^VIX"), 0)) - float(prev.get(("Close", "^VIX"), 0)), 3
            ) if prev is not None else None,
            dxy_level=float(latest.get(("Close", "DX-Y.NYB"), 0)) or None,
            dxy_1d_change=round(
                float(latest.get(("Close", "DX-Y.NYB"), 0)) - float(prev.get(("Close", "DX-Y.NYB"), 0)), 3
            ) if prev is not None else None,
            us10y_level=float(latest.get(("Close", "^TNX"), 0)) or None,
            us02y_level=float(latest.get(("Close", "^IRX"), 0)) or None,
        )

        # Upsert
        existing = session.query(OptionsFlow).filter_by(date=today, symbol="NQ").first()
        if existing:
            for k, v in row_data.items():
                if v is not None:
                    setattr(existing, k, v)
        else:
            session.add(OptionsFlow(**row_data))
        session.commit()
        logger.info(f"Stored options_flow for {today}")
        return True

    except ImportError:
        logger.warning("yfinance not installed — skip macro data fetch")
        return False
    except Exception as e:
        logger.error(f"Failed to fetch macro data: {e}")
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_macro_fetchers.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/macro/options_flow.py backend/tests/test_macro_fetchers.py
git commit -m "feat(ml): add options flow / macro data fetcher"
```

---

### Task 13: Wire COT Fetcher to cot_data Table

**Files:**
- Modify: `backend/src/market_data/cot.py` (add store function)

The COT fetcher already exists. We need to add a function that stores its output to the new `cot_data` table.

- [ ] **Step 1: Read existing cot.py to understand its output format**

Read `backend/src/market_data/cot.py` to see the `COTReport` dataclass fields and `fetch_cot()` return type.

- [ ] **Step 2: Add store function**

Add to `backend/src/market_data/cot.py`:

```python
def store_cot_data(session, reports: list) -> int:
    """Store COT reports to cot_data table. Returns count stored."""
    from src.db.models import CotData

    stored = 0
    for report in reports:
        existing = session.query(CotData).filter_by(
            report_date=report.report_date, symbol="NQ"
        ).first()
        if existing:
            continue  # Already stored
        row = CotData(
            report_date=report.report_date,
            symbol="NQ",
            net_position=report.net_non_commercial,
            open_interest=report.open_interest,
        )
        session.add(row)
        stored += 1

    if stored:
        session.commit()
    return stored
```

**Note:** Adapt field names based on what the actual `COTReport` dataclass provides. The implementer should read the existing `fetch_cot()` output to get exact field names.

- [ ] **Step 3: Commit**

```bash
git add backend/src/market_data/cot.py
git commit -m "feat(ml): wire COT fetcher output to cot_data table"
```

---

## Chunk 4b: Extraction Pipeline Feature Logging

### Task 14: Extraction Feature ORM Models

**Files:**
- Modify: `backend/src/db/models.py`
- Modify: `backend/src/ml/migrations.py`
- Create: `backend/tests/test_extraction_features.py`

Add `ExtractionFeature` and `ProviderValueLog` ORM models + migration DDL for M10.

- [ ] **Step 1: Write tests**

```python
# backend/tests/test_extraction_features.py
"""Test extraction feature logging for M10 extraction optimizer."""
from sqlalchemy import inspect


def test_extraction_features_table_exists(db_session):
    inspector = inspect(db_session.bind)
    assert "extraction_features" in inspector.get_table_names()


def test_provider_value_log_table_exists(db_session):
    inspector = inspect(db_session.bind)
    assert "provider_value_log" in inspector.get_table_names()


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
    assert result.value_bets_found is None  # Not yet filled


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
    assert result.value_bets_from_provider is None  # Filled after scan


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

    # Simulate outcome resolution after scan
    result = db_session.query(ExtractionFeature).filter_by(run_id="run-xyz").first()
    result.value_bets_found = 47
    result.avg_edge_pct = 8.2
    result.dutch_opportunities_found = 12
    db_session.commit()

    updated = db_session.query(ExtractionFeature).filter_by(run_id="run-xyz").first()
    assert updated.value_bets_found == 47
    assert updated.avg_edge_pct == 8.2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_extraction_features.py -v`
Expected: FAIL — models not defined

- [ ] **Step 3: Add ORM models to models.py**

Add to `backend/src/db/models.py` after the other ML models:

```python
class ExtractionFeature(Base):
    """Per-extraction-run feature snapshot for M10 optimization."""
    __tablename__ = "extraction_features"

    id = Column(Integer, primary_key=True)
    run_id = Column(String, nullable=False)          # FK to extraction_runs.id
    trigger = Column(String, nullable=False)          # 'sharp', 'api_soft', 'browser_soft'
    # Timing context
    hour_of_day = Column(Integer, nullable=True)
    day_of_week = Column(Integer, nullable=True)
    minutes_since_last_sharp = Column(Float, nullable=True)
    minutes_since_last_soft = Column(Float, nullable=True)
    events_starting_next_2h = Column(Integer, nullable=True)
    events_starting_next_6h = Column(Integer, nullable=True)
    # Health snapshot
    providers_attempted = Column(Integer, nullable=True)
    providers_succeeded = Column(Integer, nullable=True)
    providers_failed = Column(Integer, nullable=True)
    circuit_breakers_open = Column(Integer, nullable=True)
    # Volume snapshot
    total_events = Column(Integer, nullable=True)
    total_odds = Column(Integer, nullable=True)
    avg_match_rate = Column(Float, nullable=True)
    # Outcome (filled after scan completes)
    value_bets_found = Column(Integer, nullable=True)
    avg_edge_pct = Column(Float, nullable=True)
    dutch_opportunities_found = Column(Integer, nullable=True)
    reverse_opportunities_found = Column(Integer, nullable=True)
    total_opportunity_value = Column(Float, nullable=True)
    # Resolved later
    bets_placed_from_run = Column(Integer, nullable=True)
    avg_clv_from_run = Column(Float, nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("idx_extraction_features_run", "run_id"),
    )


class ProviderValueLog(Base):
    """Per-provider-per-run attribution — connects extraction to value outcomes."""
    __tablename__ = "provider_value_log"

    id = Column(Integer, primary_key=True)
    run_id = Column(String, nullable=False)
    provider_id = Column(String, nullable=False)
    # Extraction metrics (from provider_run_metrics)
    events_extracted = Column(Integer, nullable=True)
    odds_extracted = Column(Integer, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    match_rate = Column(Float, nullable=True)
    spread_count = Column(Integer, nullable=True)
    total_count = Column(Integer, nullable=True)
    # Value attribution (filled after scan)
    value_bets_from_provider = Column(Integer, nullable=True)
    avg_edge_from_provider = Column(Float, nullable=True)
    exclusive_events = Column(Integer, nullable=True)
    # Resolved later
    clv_avg_from_provider = Column(Float, nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("idx_provider_value_run", "run_id", "provider_id"),
    )
```

- [ ] **Step 4: Add migrations for new tables**

Add to `backend/src/ml/migrations.py` — two new `_create_*` functions:

```python
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
            dutch_opportunities_found INTEGER,
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
```

And add both calls to `run_migrations()`:
```python
def run_migrations(conn: sqlite3.Connection) -> None:
    # ... existing calls ...
    _create_extraction_features(conn)
    _create_provider_value_log(conn)
    conn.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_extraction_features.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/db/models.py backend/src/ml/migrations.py backend/tests/test_extraction_features.py
git commit -m "feat(ml): add extraction feature and provider value log models for M10"
```

---

### Task 15: Extraction Feature Extractor

**Files:**
- Create: `backend/src/ml/features/extraction_features.py`
- Modify: `backend/tests/test_extraction_features.py`

Extracts M10 features from the orchestrator context after each extraction run completes.

- [ ] **Step 1: Add tests**

Add to `backend/tests/test_extraction_features.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_extraction_features.py::test_extract_extraction_features -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement extraction feature extractor**

```python
# backend/src/ml/features/extraction_features.py
"""Extract features for extraction pipeline optimization (M10).

Logs per-run context (timing, health, volume) and per-provider attribution
(events, odds, value bet yield). Connects extraction decisions to downstream
value outcomes.

Integration points:
- After orchestrator completes a run → extract_extraction_features()
- After value scan completes → extract_provider_value() per provider
- After bets resolve → update clv columns
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def extract_extraction_features(
    run_id: str,
    trigger: str,
    providers_attempted: int,
    providers_succeeded: int,
    providers_failed: int,
    total_events: int,
    total_odds: int,
    avg_match_rate: float,
    circuit_breakers_open: int = 0,
    last_sharp_run_time: datetime | None = None,
    last_soft_run_time: datetime | None = None,
    events_starting_next_2h: int | None = None,
    events_starting_next_6h: int | None = None,
) -> dict:
    """Extract features for a completed extraction run.

    Called by orchestrator after extraction completes.
    Value bet outcomes filled in separately after scan.
    """
    now = datetime.now(timezone.utc)

    minutes_since_sharp = None
    if last_sharp_run_time:
        if last_sharp_run_time.tzinfo is None:
            last_sharp_run_time = last_sharp_run_time.replace(tzinfo=timezone.utc)
        minutes_since_sharp = (now - last_sharp_run_time).total_seconds() / 60

    minutes_since_soft = None
    if last_soft_run_time:
        if last_soft_run_time.tzinfo is None:
            last_soft_run_time = last_soft_run_time.replace(tzinfo=timezone.utc)
        minutes_since_soft = (now - last_soft_run_time).total_seconds() / 60

    return {
        "run_id": run_id,
        "trigger": trigger,
        "hour_of_day": now.hour,
        "day_of_week": now.weekday(),
        "minutes_since_last_sharp": minutes_since_sharp,
        "minutes_since_last_soft": minutes_since_soft,
        "events_starting_next_2h": events_starting_next_2h,
        "events_starting_next_6h": events_starting_next_6h,
        "providers_attempted": providers_attempted,
        "providers_succeeded": providers_succeeded,
        "providers_failed": providers_failed,
        "circuit_breakers_open": circuit_breakers_open,
        "total_events": total_events,
        "total_odds": total_odds,
        "avg_match_rate": avg_match_rate,
    }


def extract_provider_value(
    run_id: str,
    provider_id: str,
    events_extracted: int,
    odds_extracted: int,
    duration_seconds: float,
    match_rate: float,
    spread_count: int = 0,
    total_count: int = 0,
    value_bets_from_provider: int | None = None,
    avg_edge_from_provider: float | None = None,
    exclusive_events: int | None = None,
) -> dict:
    """Extract per-provider value attribution features.

    Called after value scan to connect provider extraction to value outcomes.
    """
    return {
        "run_id": run_id,
        "provider_id": provider_id,
        "events_extracted": events_extracted,
        "odds_extracted": odds_extracted,
        "duration_seconds": duration_seconds,
        "match_rate": match_rate,
        "spread_count": spread_count,
        "total_count": total_count,
        "value_bets_from_provider": value_bets_from_provider,
        "avg_edge_from_provider": avg_edge_from_provider,
        "exclusive_events": exclusive_events,
    }


def log_extraction_run(session, features: dict) -> None:
    """Store extraction features to DB."""
    from src.db.models import ExtractionFeature
    row = ExtractionFeature(**features)
    session.add(row)
    session.flush()
    logger.debug(f"Logged extraction features for run {features.get('run_id')}")


def log_provider_value(session, features: dict) -> None:
    """Store provider value attribution to DB."""
    from src.db.models import ProviderValueLog
    row = ProviderValueLog(**features)
    session.add(row)
    session.flush()


def update_extraction_outcomes(
    session,
    run_id: str,
    value_bets_found: int,
    avg_edge_pct: float | None,
    dutch_opportunities_found: int = 0,
    reverse_opportunities_found: int = 0,
) -> None:
    """Update extraction features with scan outcomes. Called after value scan."""
    from src.db.models import ExtractionFeature
    row = session.query(ExtractionFeature).filter_by(run_id=run_id).first()
    if row:
        row.value_bets_found = value_bets_found
        row.avg_edge_pct = avg_edge_pct
        row.dutch_opportunities_found = dutch_opportunities_found
        row.reverse_opportunities_found = reverse_opportunities_found
        session.flush()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_extraction_features.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/features/extraction_features.py backend/tests/test_extraction_features.py
git commit -m "feat(ml): implement extraction feature extractor for M10 pipeline optimizer"
```

---

### Task 16: Hook Extraction Features Into Orchestrator

**Files:**
- Modify: `backend/src/pipeline/orchestrator.py`
- Modify: `backend/src/pipeline/metrics.py`

After each extraction run completes and metrics are persisted, log extraction features.
After value scan completes, attribute value bets back to providers.

- [ ] **Step 1: Add extraction feature logging to orchestrator**

In `backend/src/pipeline/orchestrator.py`, find where `metrics.persist_to_db()` is called (after extraction completes). Add after it:

```python
        # Log ML extraction features (best-effort)
        try:
            from src.ml.features.extraction_features import (
                extract_extraction_features, log_extraction_run,
                extract_provider_value, log_provider_value,
            )

            # Run-level features
            run_features = extract_extraction_features(
                run_id=run_id,
                trigger=trigger,
                providers_attempted=metrics.providers_attempted,
                providers_succeeded=metrics.providers_succeeded,
                providers_failed=metrics.providers_failed,
                total_events=metrics.total_events,
                total_odds=metrics.total_odds,
                avg_match_rate=metrics.avg_match_rate,
                circuit_breakers_open=sum(
                    1 for s in self.circuit_breaker.get_all_statuses().values()
                    if s.state == "open"
                ),
                last_sharp_run_time=self.scheduler.get_last_run_time("sharp") if hasattr(self, 'scheduler') else None,
                last_soft_run_time=self.scheduler.get_last_run_time("api_soft") if hasattr(self, 'scheduler') else None,
            )
            log_extraction_run(session, run_features)

            # Per-provider features
            for provider_id, pm in metrics.providers.items():
                pv_features = extract_provider_value(
                    run_id=run_id,
                    provider_id=provider_id,
                    events_extracted=pm.events_processed,
                    odds_extracted=pm.odds_processed,
                    duration_seconds=pm.duration_seconds,
                    match_rate=pm.events_matched / max(pm.events_processed, 1),
                    spread_count=pm.spread_count,
                    total_count=pm.total_count,
                )
                log_provider_value(session, pv_features)

            session.commit()
        except Exception as e:
            logger.debug(f"ML extraction feature logging skipped: {e}")
```

**Important:** The exact variable names (`run_id`, `trigger`, `metrics`, `session`) depend on orchestrator scope. The implementer MUST:
1. Read `orchestrator.py` to find where `persist_to_db()` is called
2. Map to actual variable names in that scope
3. Access `self.circuit_breaker` and `self.scheduler` as available

- [ ] **Step 2: Add value attribution hook after scan**

After value scanning completes (in the route or service that triggers scanning after extraction), add:

```python
        # Attribute value bets to providers (for M10 provider priority scoring)
        try:
            from src.ml.features.extraction_features import update_extraction_outcomes
            from collections import Counter

            provider_counts = Counter(vb.provider for vb in value_bets)
            update_extraction_outcomes(
                session=session,
                run_id=current_run_id,
                value_bets_found=len(value_bets),
                avg_edge_pct=sum(vb.edge_pct for vb in value_bets) / len(value_bets) if value_bets else None,
                dutch_opportunities_found=len(dutch_opps),
                reverse_opportunities_found=len(reverse_opps),
            )

            # Update per-provider value counts
            from src.db.models import ProviderValueLog
            for provider_id, count in provider_counts.items():
                pvl = session.query(ProviderValueLog).filter_by(
                    run_id=current_run_id, provider_id=provider_id
                ).first()
                if pvl:
                    pvl.value_bets_from_provider = count
                    pvl.avg_edge_from_provider = (
                        sum(vb.edge_pct for vb in value_bets if vb.provider == provider_id) / count
                    )
            session.commit()
        except Exception as e:
            logger.debug(f"ML value attribution skipped: {e}")
```

- [ ] **Step 3: Verify extraction still works**

Run an extraction and confirm it completes without errors:
`cd backend && python -m src.app extract pinnacle`
Expected: Extraction completes. Check DB for new rows in `extraction_features`.

- [ ] **Step 4: Commit**

```bash
git add backend/src/pipeline/orchestrator.py
git commit -m "feat(ml): hook extraction feature logging into orchestrator for M10"
```

---

### Task 17: Run Migrations on Production DB (updated for extraction tables)

**Files:** None new — runs existing migration script against the real SQLite DB.

- [ ] **Step 1: Back up the database**

```bash
cp backend/data/bankrollbbq.db backend/data/bankrollbbq.db.backup-pre-ml
```

- [ ] **Step 2: Run migrations**

```python
# Run from backend directory:
cd backend && python -c "
import sqlite3
from src.ml.migrations import run_migrations
conn = sqlite3.connect('data/bankrollbbq.db')
run_migrations(conn)
conn.close()
print('Migrations applied successfully')
"
```

- [ ] **Step 3: Verify tables exist**

```bash
cd backend && python -c "
import sqlite3
conn = sqlite3.connect('data/bankrollbbq.db')
cursor = conn.execute(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\")
for row in cursor:
    print(row[0])
conn.close()
"
```

Expected: All 7 new tables visible (`ml_features`, `candle_snapshots`, `economic_events`, `news_impact`, `options_flow`, `cot_data`, `ml_model_registry`) plus the existing tables.

- [ ] **Step 4: Verify opportunity columns added**

```bash
cd backend && python -c "
import sqlite3
conn = sqlite3.connect('data/bankrollbbq.db')
cursor = conn.execute('PRAGMA table_info(opportunities)')
for row in cursor:
    print(f'{row[1]:30s} {row[2]}')
conn.close()
"
```

Expected: New columns (`prob_sum`, `odds_ratio`, etc.) visible at end of list.

- [ ] **Step 5: Commit**

No code changes — this step just applies the migration. But commit a note or update if needed.

---

## Chunk 5: Integration Verification

### Task 18: End-to-End Smoke Test

**Files:**
- Create: `backend/tests/test_ml_integration.py`

Verify the full flow: extract → feature extraction → store → query.

- [ ] **Step 1: Write integration test**

```python
# backend/tests/test_ml_integration.py
"""End-to-end smoke test: feature extraction → store → query."""
from datetime import datetime, timezone, timedelta


def test_betting_feature_e2e(db_session):
    """Full flow: extract features → store → resolve → query training data."""
    from src.ml.features.betting_features import extract_betting_features
    from src.ml.feature_store import log_features, resolve_outcome, get_training_data

    # 1. Extract features (as scanner would)
    features = extract_betting_features(
        edge_pct=8.0,
        provider_odds=2.15,
        fair_odds=1.99,
        fair_probability=0.503,
        provider="betsson",
        sport="football",
        market="1x2",
        event_id="evt-100",
        prob_sum=1.02,
        odds_by_outcome={"home": [
            {"provider": "pinnacle", "odds": 1.99, "updated_at": datetime.now(timezone.utc).isoformat()},
            {"provider": "betsson", "odds": 2.15, "updated_at": datetime.now(timezone.utc).isoformat()},
        ]},
        pinnacle_overround=0.025,
        event_start_time=datetime.now(timezone.utc) + timedelta(hours=2),
    )

    # 2. Store
    log_features(db_session, "betting", "opp-100", "opportunity", features)

    # 3. Resolve outcome (simulate: bet won, CLV was positive)
    resolve_outcome(db_session, "opportunity", "opp-100", outcome=0.05, outcome_binary=1)

    # 4. Query training data
    data = get_training_data(db_session, "betting", "opportunity")
    assert len(data) == 1
    assert data[0].features["edge_pct"] == 8.0
    assert data[0].outcome == 0.05


def test_trading_feature_e2e(db_session):
    """Full flow: extract trading features → store → resolve."""
    from src.ml.features.trading_features import extract_trading_features
    from src.ml.feature_store import log_features, resolve_outcome, get_training_data

    features = extract_trading_features(
        setup_type="spring",
        direction="long",
        delta=380,
        delta_pct=0.089,
        volume_ratio_vs_20bar=1.45,
        distance_to_level_ticks=3,
        minutes_since_rth_open=45,
    )

    log_features(db_session, "trading", "sig-50", "signal", features)
    resolve_outcome(db_session, "signal", "sig-50", outcome=2.5, outcome_binary=1)

    data = get_training_data(db_session, "trading", "signal")
    assert len(data) == 1
    assert data[0].features["setup_type"] == "spring"
    assert data[0].outcome == 2.5
```

- [ ] **Step 2: Run integration tests**

Run: `cd backend && python -m pytest tests/test_ml_integration.py -v`
Expected: All PASS

- [ ] **Step 3: Run full test suite**

Run: `cd backend && python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_ml_integration.py
git commit -m "test(ml): add end-to-end integration tests for feature pipeline"
```

---

## Summary

**What this plan delivers:**
- 9 new SQLite tables (ml_features, candle_snapshots, economic_events, news_impact, options_flow, cot_data, ml_model_registry, extraction_features, provider_value_log)
- 10 new columns on the opportunities table
- Idempotent migration script safe to run on existing DB
- Feature store with log/resolve/query operations
- Betting feature extractor (M1 Edge Quality vector)
- Trading feature extractor (M5 Setup Score vector)
- Candle snapshot extractor (M6 temporal pattern data)
- Extraction feature extractor (M10 pipeline optimization — per-run context + per-provider value attribution)
- Economic calendar fetcher
- Options flow / macro data fetcher
- COT data wiring
- Full test suite (20+ tests)
- Scanner and orchestrator integration hooks (best-effort, never blocks extraction)

**Deferred to Phase 2/3 (needs historical data or additional infrastructure):**
- M1 features: `odds_movement_direction/magnitude`, `sharp_line_stability`, `provider_historical_clv_avg`, `is_platform_outlier`, `league_liquidity_proxy` — require historical tracking tables
- M5 features: footprint-level data (`imbalance_ratio_max`, `stacked_imbalance_count`, `big_trades_count`) — require L2 orderflow processing pipeline
- Conditions enrichment: 4 of ~40 continuous fields populated in Phase 1; rest added as `orderflow.py` is extended
- Scheduler wiring for daily macro fetch and weekly COT fetch
- `httpx` dependency check (verify in `requirements.txt` before running economic calendar)

**What comes next (separate plans):**
- **Plan 2: Model Infrastructure + Sports Models** — training pipeline, model serving, M1 Edge Quality, M2 Limit Predictor, M3 Devig Selector, M4 Boost Calibrator
- **Plan 3: Trading Models** — M5 Setup Scorer, M6 Temporal Patterns, M7 Gate Classifier, M8 Adaptive Kelly, M9 Macro Engine

These plans should be written after data has accumulated for 2-4 weeks, when the feature store has enough rows to validate model training assumptions.
