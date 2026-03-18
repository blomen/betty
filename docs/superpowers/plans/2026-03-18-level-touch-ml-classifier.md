# Level Touch ML Classifier — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Train a gradient-boosted tree model that predicts price continuation vs reversal (5 classes) when NQ futures price touches a structural level, using ~60 orderflow/session/temporal features.

**Architecture:** Dedicated `level_touch_outcomes` + `level_touch_features` tables for specialized outcome tracking. Feature extractor follows existing flat-function pattern (`ml/features/`). Model follows existing class pattern (`ml/models/`). Outcome tracker extends `LevelMonitor` with 30-min delayed labeling via asyncio. Backfill pipeline replays historical 1m candles. Uses existing `train_model()` utility (LightGBM, already in codebase — equivalent to XGBoost for tabular data, handles missing values natively). Predictions served via existing `Predictor` singleton and emitted as SSE events on the market stream.

**Tech Stack:** LightGBM (via existing `optimizer/trainer.py`), SHAP, scikit-learn, Optuna (hyperparameter tuning), SQLAlchemy, asyncio

**Spec:** `docs/superpowers/specs/2026-03-18-level-touch-ml-classifier-design.md`

---

## File Structure

```
backend/src/ml/
├── migrations.py                          # MODIFY: add level_touch tables
├── features/
│   └── level_touch_features.py            # CREATE: ~60-feature extractor
├── models/
│   └── level_classifier.py                # CREATE: LevelClassifierModel (train + encode)
├── serving/
│   └── predictor.py                       # MODIFY: handle level_classifier multiclass
├── training/
│   └── train_all.py                       # MODIFY: register level_classifier
└── level_touch/
    ├── __init__.py                        # CREATE
    ├── outcomes.py                        # CREATE: OutcomeTracker (30-min delayed labeling)
    ├── backfill.py                        # CREATE: historical replay pipeline
    └── shap_explainer.py                  # CREATE: per-prediction SHAP explanations

backend/src/market_data/
└── level_monitor.py                       # MODIFY: approach_direction + ML integration

backend/src/api/routes/
└── market.py                              # MODIFY: add ML prediction endpoint + SSE event

backend/src/app.py                         # MODIFY: add ml backfill + ml train CLI commands

backend/tests/
├── test_level_touch_features.py           # CREATE
├── test_level_touch_outcomes.py           # CREATE
├── test_level_classifier.py               # CREATE
└── test_level_touch_backfill.py           # CREATE

frontend/src/
├── types/market.ts                        # MODIFY: add MlPrediction type
└── components/Terminal/pages/L2Page.tsx   # MODIFY: add prediction panel (or ContextSidebar)
```

---

### Task 1: DB Migrations — Level Touch Tables

**Files:**
- Modify: `backend/src/ml/migrations.py`
- Modify: `backend/src/db/models.py`
- Test: `backend/tests/test_migrations.py`

- [ ] **Step 1: Read existing migration patterns**

Read `backend/src/ml/migrations.py` to understand the idempotent create pattern. Read `backend/src/db/models.py` to see SQLAlchemy model conventions (column naming, types, indexes).

- [ ] **Step 2: Add SQLAlchemy models to `db/models.py`**

Add after the existing `MarketLevel` model:

```python
class LevelTouchOutcome(Base):
    __tablename__ = "level_touch_outcomes"

    id = Column(Integer, primary_key=True)
    symbol = Column(Text, nullable=False)
    touch_ts = Column(Float, nullable=False)          # epoch timestamp
    level_name = Column(Text, nullable=False)
    level_type = Column(Text, nullable=False)
    level_price = Column(Float, nullable=False)
    approach_direction = Column(Text, nullable=False)  # "from_above" | "from_below"
    outcome = Column(Text)                             # NULL until 30 min later
    max_continuation_ticks = Column(Float)
    max_reversal_ticks = Column(Float)
    outcome_measured_at = Column(Float)
    session_date = Column(Text, nullable=False)
    is_backfill = Column(Integer, default=0)
    prediction = Column(Text)                          # what model predicted (for accuracy tracking)
    prediction_confidence = Column(Float)


class LevelTouchFeature(Base):
    __tablename__ = "level_touch_features"

    id = Column(Integer, primary_key=True)
    touch_outcome_id = Column(Integer, ForeignKey("level_touch_outcomes.id"), nullable=False)
    features = Column(Text, nullable=False)            # JSON-serialized feature dict
    feature_version = Column(Integer, default=1)
    created_at = Column(Float)                         # epoch timestamp
```

- [ ] **Step 3: Add migration function to `migrations.py`**

Add `_create_level_touch_tables(conn)` following existing pattern (check table existence first):

```python
def _create_level_touch_tables(conn):
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
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_lto_session ON level_touch_outcomes(session_date)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_lto_outcome ON level_touch_outcomes(outcome)
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS level_touch_features (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            touch_outcome_id INTEGER NOT NULL REFERENCES level_touch_outcomes(id),
            features TEXT NOT NULL,
            feature_version INTEGER DEFAULT 1,
            created_at REAL
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_ltf_outcome ON level_touch_features(touch_outcome_id)
    """)
```

Call it from `run_migrations()`.

- [ ] **Step 4: Write migration test**

In `backend/tests/test_migrations.py`, add:

```python
def test_level_touch_tables_created(db_session):
    """Verify level_touch_outcomes and level_touch_features tables exist after migration."""
    from src.ml.migrations import run_migrations
    import sqlite3
    conn = sqlite3.connect(":memory:")
    run_migrations(conn)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='level_touch_outcomes'")
    assert cursor.fetchone() is not None
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='level_touch_features'")
    assert cursor.fetchone() is not None
    conn.close()
```

- [ ] **Step 5: Run tests**

Run: `cd backend && python -m pytest tests/test_migrations.py -v -k level_touch`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/db/models.py backend/src/ml/migrations.py backend/tests/test_migrations.py
git commit -m "feat(ml): add level_touch_outcomes and level_touch_features tables"
```

---

### Task 2: Feature Extractor — ~60 Level Touch Features

**Files:**
- Create: `backend/src/ml/features/level_touch_features.py`
- Test: `backend/tests/test_level_touch_features.py`

**Context:** Follow the pattern in `backend/src/ml/features/trading_features.py` — flat function with many optional params returning a dict. Import `OrderflowSignals` from `backend/src/market_data/orderflow.py`.

- [ ] **Step 1: Write test for feature extractor with full inputs**

```python
# backend/tests/test_level_touch_features.py
from src.ml.features.level_touch_features import extract_level_touch_features

def test_extract_all_features_present():
    """All ~60 feature keys are present in output dict."""
    features = extract_level_touch_features(
        level_type="vah", level_category="session",
        approach_direction="from_below",
    )
    assert isinstance(features, dict)
    # Level metadata
    assert "level_type" in features
    assert "level_category" in features
    assert "approach_direction" in features
    assert "level_strength" in features
    assert "level_confluence" in features
    assert "distance_from_poc" in features
    assert "distance_from_vwap" in features
    # Orderflow
    assert "delta" in features
    assert "delta_aligned" in features
    assert "cvd" in features
    assert "vsa_absorption" in features
    # Temporal
    assert "delta_slope_5m" in features
    assert "cvd_acceleration" in features
    assert "volume_roc_5m" in features
    # Session
    assert "market_type" in features
    assert "opening_type" in features
    assert "session_elapsed_pct" in features
    # Macro
    assert "vix_level" in features
    assert "regime_score" in features
    # Candle pattern
    assert "last_3_candles_direction" in features
    assert "range_expansion" in features
    # Total count
    assert len(features) >= 58

def test_extract_with_orderflow_values():
    """Orderflow values are correctly passed through."""
    features = extract_level_touch_features(
        level_type="poc", level_category="session",
        approach_direction="from_above",
        delta=-500, delta_aligned=True, delta_divergence=False,
        cvd=-2000, cvd_trend="falling",
        vsa_absorption=True, trapped_traders=False,
    )
    assert features["delta"] == -500
    assert features["delta_aligned"] is True
    assert features["cvd"] == -2000
    assert features["cvd_trend"] == "falling"
    assert features["vsa_absorption"] is True

def test_extract_with_none_defaults():
    """Unset features default to None."""
    features = extract_level_touch_features(
        level_type="pdh", level_category="prior",
        approach_direction="from_below",
    )
    assert features["delta"] is None
    assert features["vix_level"] is None
    assert features["level_strength"] is None

def test_extract_temporal_derivatives():
    """Temporal derivative features are included."""
    features = extract_level_touch_features(
        level_type="vwap_2sd", level_category="band",
        approach_direction="from_below",
        delta_slope_5m=12.5, delta_slope_10m=8.3,
        cvd_acceleration=450, volume_roc_5m=1.8,
        time_to_level_seconds=45.0, price_velocity=2.1,
        absorption_building=3, imbalance_trend=2,
    )
    assert features["delta_slope_5m"] == 12.5
    assert features["cvd_acceleration"] == 450
    assert features["time_to_level_seconds"] == 45.0
    assert features["absorption_building"] == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_level_touch_features.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement feature extractor**

Create `backend/src/ml/features/level_touch_features.py`:

```python
"""Feature extractor for level touch ML classifier.

Extracts ~60 features at the moment price touches a structural level.
Follows the flat-function pattern from trading_features.py.
All parameters optional (None = missing/unavailable, e.g., during backfill).
"""

def extract_level_touch_features(
    # --- Level metadata (7) ---
    level_type: str | None = None,
    level_category: str | None = None,
    level_strength: float | None = None,
    level_confluence: int | None = None,
    approach_direction: str | None = None,
    distance_from_poc: float | None = None,
    distance_from_vwap: float | None = None,
    # --- Orderflow snapshot (18) ---
    delta: int | None = None,
    delta_aligned: bool | None = None,
    delta_divergence: bool | None = None,
    delta_unwind: bool | None = None,
    cvd: int | None = None,
    cvd_trend: str | None = None,
    vsa_absorption: bool | None = None,
    tick_vol_accelerating: bool | None = None,
    trapped_traders: bool | None = None,
    passive_active_ratio: float | None = None,
    big_trades_count: int | None = None,
    big_trades_net_delta: int | None = None,
    stop_run_detected: bool | None = None,
    imbalance_ratio_max: float | None = None,
    stacked_imbalance_count: int | None = None,
    stacked_imbalance_direction: str | None = None,
    last_candle_delta: int | None = None,
    last_candle_body_ratio: float | None = None,
    # --- Temporal derivatives (10) ---
    delta_slope_5m: float | None = None,
    delta_slope_10m: float | None = None,
    cvd_acceleration: float | None = None,
    volume_roc_5m: float | None = None,
    tick_rate_roc: float | None = None,
    spread_compression: float | None = None,
    time_to_level_seconds: float | None = None,
    price_velocity: float | None = None,
    absorption_building: int | None = None,
    imbalance_trend: float | None = None,
    # --- Session context (15) ---
    market_type: str | None = None,
    opening_type: str | None = None,
    ib_range: float | None = None,
    ib_range_vs_aspr: float | None = None,
    aspr_percentile: float | None = None,
    rotation_factor: float | None = None,
    value_migration: str | None = None,
    price_vs_vah: float | None = None,
    price_vs_val: float | None = None,
    price_vs_poc: float | None = None,
    price_in_value_area: bool | None = None,
    session_elapsed_pct: float | None = None,
    minutes_since_open: float | None = None,
    developing_poc_direction: str | None = None,
    prior_touch_count: int | None = None,
    # --- Macro context (5) ---
    vix_level: float | None = None,
    vix_change: float | None = None,
    regime: str | None = None,
    regime_score: float | None = None,
    macro_bias: str | None = None,
    # --- Candle pattern (5) ---
    last_3_candles_direction: int | None = None,
    last_candle_is_doji: bool | None = None,
    consecutive_same_direction: int | None = None,
    highest_volume_candle_position: int | None = None,
    range_expansion: float | None = None,
) -> dict:
    return {
        # Level metadata
        "level_type": level_type,
        "level_category": level_category,
        "level_strength": level_strength,
        "level_confluence": level_confluence,
        "approach_direction": approach_direction,
        "distance_from_poc": distance_from_poc,
        "distance_from_vwap": distance_from_vwap,
        # Orderflow snapshot
        "delta": delta,
        "delta_aligned": delta_aligned,
        "delta_divergence": delta_divergence,
        "delta_unwind": delta_unwind,
        "cvd": cvd,
        "cvd_trend": cvd_trend,
        "vsa_absorption": vsa_absorption,
        "tick_vol_accelerating": tick_vol_accelerating,
        "trapped_traders": trapped_traders,
        "passive_active_ratio": passive_active_ratio,
        "big_trades_count": big_trades_count,
        "big_trades_net_delta": big_trades_net_delta,
        "stop_run_detected": stop_run_detected,
        "imbalance_ratio_max": imbalance_ratio_max,
        "stacked_imbalance_count": stacked_imbalance_count,
        "stacked_imbalance_direction": stacked_imbalance_direction,
        "last_candle_delta": last_candle_delta,
        "last_candle_body_ratio": last_candle_body_ratio,
        # Temporal derivatives
        "delta_slope_5m": delta_slope_5m,
        "delta_slope_10m": delta_slope_10m,
        "cvd_acceleration": cvd_acceleration,
        "volume_roc_5m": volume_roc_5m,
        "tick_rate_roc": tick_rate_roc,
        "spread_compression": spread_compression,
        "time_to_level_seconds": time_to_level_seconds,
        "price_velocity": price_velocity,
        "absorption_building": absorption_building,
        "imbalance_trend": imbalance_trend,
        # Session context
        "market_type": market_type,
        "opening_type": opening_type,
        "ib_range": ib_range,
        "ib_range_vs_aspr": ib_range_vs_aspr,
        "aspr_percentile": aspr_percentile,
        "rotation_factor": rotation_factor,
        "value_migration": value_migration,
        "price_vs_vah": price_vs_vah,
        "price_vs_val": price_vs_val,
        "price_vs_poc": price_vs_poc,
        "price_in_value_area": price_in_value_area,
        "session_elapsed_pct": session_elapsed_pct,
        "minutes_since_open": minutes_since_open,
        "developing_poc_direction": developing_poc_direction,
        "prior_touch_count": prior_touch_count,
        # Macro context
        "vix_level": vix_level,
        "vix_change": vix_change,
        "regime": regime,
        "regime_score": regime_score,
        "macro_bias": macro_bias,
        # Candle pattern
        "last_3_candles_direction": last_3_candles_direction,
        "last_candle_is_doji": last_candle_is_doji,
        "consecutive_same_direction": consecutive_same_direction,
        "highest_volume_candle_position": highest_volume_candle_position,
        "range_expansion": range_expansion,
    }


# Feature names for model encoding (order matters — must match training)
FEATURE_NAMES = [
    # Numeric features (passed directly)
    "level_strength", "level_confluence",
    "distance_from_poc", "distance_from_vwap",
    "delta", "cvd", "passive_active_ratio",
    "big_trades_count", "big_trades_net_delta",
    "imbalance_ratio_max", "stacked_imbalance_count",
    "last_candle_delta", "last_candle_body_ratio",
    "delta_slope_5m", "delta_slope_10m", "cvd_acceleration",
    "volume_roc_5m", "tick_rate_roc", "spread_compression",
    "time_to_level_seconds", "price_velocity",
    "absorption_building", "imbalance_trend",
    "ib_range", "ib_range_vs_aspr", "aspr_percentile",
    "rotation_factor",
    "price_vs_vah", "price_vs_val", "price_vs_poc",
    "session_elapsed_pct", "minutes_since_open",
    "prior_touch_count",
    "vix_level", "vix_change", "regime_score",
    "last_3_candles_direction", "consecutive_same_direction",
    "highest_volume_candle_position", "range_expansion",
    # Boolean features (cast to 0/1)
    "delta_aligned", "delta_divergence", "delta_unwind",
    "vsa_absorption", "tick_vol_accelerating", "trapped_traders",
    "stop_run_detected", "price_in_value_area", "last_candle_is_doji",
    # Categorical features (encoded to int via maps)
    "level_type", "level_category", "approach_direction",
    "cvd_trend", "stacked_imbalance_direction",
    "market_type", "opening_type", "value_migration",
    "developing_poc_direction", "regime", "macro_bias",
]

# Categorical encoding maps
LEVEL_TYPE_MAP = {
    "poc": 0, "vah": 1, "val": 2, "vwap": 3,
    "vwap_1sd": 4, "vwap_2sd": 5, "vwap_3sd": 6,
    "pdh": 7, "pdl": 8, "ib_high": 9, "ib_low": 10,
    "order_block": 11, "fvg": 12, "naked_poc": 13,
    "tokyo": 14, "london": 15, "weekly": 16, "monthly": 17,
}
LEVEL_CATEGORY_MAP = {"band": 0, "prior": 1, "overnight": 2, "structure": 3, "session": 4}
APPROACH_MAP = {"from_below": 0, "from_above": 1}
CVD_TREND_MAP = {"rising": 0, "falling": 1, "flat": 2}
STACKED_DIR_MAP = {"buy": 0, "sell": 1, "neutral": 2}
MARKET_TYPE_MAP = {"balanced": 0, "trending_up": 1, "trending_down": 2}
OPENING_TYPE_MAP = {"OD": 0, "OTD": 1, "ORR": 2, "OA": 3}
VALUE_MIGRATION_MAP = {"up": 0, "down": 1, "overlapping": 2}
DEV_POC_MAP = {"up": 0, "down": 1, "flat": 2}
REGIME_MAP = {"risk_on": 0, "risk_off": 1, "mixed": 2}
MACRO_BIAS_MAP = {"bull": 0, "bear": 1, "neutral": 2}

CATEGORICAL_MAPS = {
    "level_type": LEVEL_TYPE_MAP,
    "level_category": LEVEL_CATEGORY_MAP,
    "approach_direction": APPROACH_MAP,
    "cvd_trend": CVD_TREND_MAP,
    "stacked_imbalance_direction": STACKED_DIR_MAP,
    "market_type": MARKET_TYPE_MAP,
    "opening_type": OPENING_TYPE_MAP,
    "value_migration": VALUE_MIGRATION_MAP,
    "developing_poc_direction": DEV_POC_MAP,
    "regime": REGIME_MAP,
    "macro_bias": MACRO_BIAS_MAP,
}

BOOLEAN_FEATURES = {
    "delta_aligned", "delta_divergence", "delta_unwind",
    "vsa_absorption", "tick_vol_accelerating", "trapped_traders",
    "stop_run_detected", "price_in_value_area", "last_candle_is_doji",
}
```

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/test_level_touch_features.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/features/level_touch_features.py backend/tests/test_level_touch_features.py
git commit -m "feat(ml): add level touch feature extractor with ~60 features"
```

---

### Task 3: Temporal Derivative Computation

**Files:**
- Create: `backend/src/ml/level_touch/__init__.py`
- Create: `backend/src/ml/level_touch/compute.py`
- Test: `backend/tests/test_level_touch_features.py` (extend)

**Context:** The temporal derivative features (delta_slope, cvd_acceleration, volume_roc, etc.) need to be computed from recent candles. This function takes a list of `CandleFlow` objects and returns the derivative values to feed into the feature extractor. Import `CandleFlow` from `backend/src/market_data/orderflow.py`.

- [ ] **Step 1: Write test for temporal derivative computation**

```python
# Add to backend/tests/test_level_touch_features.py
import numpy as np
from src.ml.level_touch.compute import compute_temporal_derivatives

def test_compute_temporal_derivatives_basic():
    """Temporal derivatives computed from candle sequence."""
    # Create simple candle-like dicts (avoid CandleFlow dependency in test)
    candles = []
    for i in range(10):
        candles.append({
            "delta": 100 + i * 20,  # increasing delta
            "volume": 500 + i * 50,
            "tick_count": 80 + i * 5,
            "spread": 2.0 - i * 0.1,  # decreasing spread
            "body_ratio": 0.5 - i * 0.02,
            "stacked_imbalance_count": i // 3,
        })

    result = compute_temporal_derivatives(candles)
    assert result["delta_slope_5m"] is not None
    assert result["delta_slope_10m"] is not None
    assert result["cvd_acceleration"] is not None
    assert result["volume_roc_5m"] is not None
    assert result["spread_compression"] is not None
    assert result["absorption_building"] is not None
    # Delta is increasing, so slope should be positive
    assert result["delta_slope_5m"] > 0
    # Spread is decreasing, so compression < 1
    assert result["spread_compression"] < 1.0

def test_compute_temporal_derivatives_insufficient_candles():
    """Returns all None with fewer than 5 candles."""
    candles = [{"delta": 100, "volume": 500, "tick_count": 80,
                "spread": 2.0, "body_ratio": 0.5, "stacked_imbalance_count": 0}]
    result = compute_temporal_derivatives(candles)
    assert result["delta_slope_5m"] is None
    assert result["cvd_acceleration"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_level_touch_features.py::test_compute_temporal_derivatives_basic -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement temporal derivative computation**

Create `backend/src/ml/level_touch/__init__.py` (empty).

Create `backend/src/ml/level_touch/compute.py`:

```python
"""Compute temporal derivative features from candle sequences.

These capture HOW signals evolved on approach to a level,
not just their point-in-time value at touch.
"""
import numpy as np


def compute_temporal_derivatives(candles: list[dict], lookback: int = 10) -> dict:
    """Compute temporal derivatives from recent candles.

    Args:
        candles: List of candle dicts with keys: delta, volume, tick_count,
                 spread, body_ratio, stacked_imbalance_count.
                 Can be CandleFlow objects or plain dicts.
        lookback: Max candles to consider (default 10).

    Returns:
        Dict with temporal derivative feature values (None if insufficient data).
    """
    recent = candles[-lookback:] if len(candles) > lookback else candles
    n = len(recent)

    result = {
        "delta_slope_5m": None,
        "delta_slope_10m": None,
        "cvd_acceleration": None,
        "volume_roc_5m": None,
        "tick_rate_roc": None,
        "spread_compression": None,
        "absorption_building": None,
        "imbalance_trend": None,
    }

    if n < 5:
        return result

    # Helper to get attribute or dict key
    def _get(candle, key, default=0):
        if isinstance(candle, dict):
            return candle.get(key, default)
        return getattr(candle, key, default)

    deltas = [_get(c, "delta", 0) for c in recent]
    volumes = [_get(c, "volume", 0) for c in recent]
    tick_counts = [_get(c, "tick_count", 0) for c in recent]
    spreads = [_get(c, "spread", 0) for c in recent]
    body_ratios = [_get(c, "body_ratio", 0) for c in recent]
    stacked_counts = [_get(c, "stacked_imbalance_count", 0) for c in recent]

    # Delta slope (linear regression over last 5 and 10 candles)
    def _slope(values):
        n_v = len(values)
        if n_v < 2:
            return None
        x = np.arange(n_v, dtype=np.float64)
        y = np.array(values, dtype=np.float64)
        if np.all(np.isnan(y)):
            return None
        coeffs = np.polyfit(x, y, 1)
        return float(coeffs[0])

    result["delta_slope_5m"] = _slope(deltas[-5:])
    result["delta_slope_10m"] = _slope(deltas) if n >= 10 else _slope(deltas)

    # CVD acceleration: sum of delta in last 5 minus sum in prior 5
    if n >= 10:
        cvd_recent = sum(deltas[-5:])
        cvd_prior = sum(deltas[-10:-5])
        result["cvd_acceleration"] = float(cvd_recent - cvd_prior)
    elif n >= 5:
        mid = n // 2
        cvd_recent = sum(deltas[mid:])
        cvd_prior = sum(deltas[:mid])
        result["cvd_acceleration"] = float(cvd_recent - cvd_prior)

    # Volume rate of change: avg vol last 5 / avg vol prior 5
    if n >= 10:
        vol_recent = sum(volumes[-5:]) / 5
        vol_prior = sum(volumes[-10:-5]) / 5
        result["volume_roc_5m"] = float(vol_recent / vol_prior) if vol_prior > 0 else None
    elif n >= 5:
        mid = n // 2
        vol_recent = sum(volumes[mid:]) / len(volumes[mid:])
        vol_prior = sum(volumes[:mid]) / len(volumes[:mid])
        result["volume_roc_5m"] = float(vol_recent / vol_prior) if vol_prior > 0 else None

    # Tick rate of change: avg ticks last 3 / avg ticks prior 3
    if n >= 6:
        tick_recent = sum(tick_counts[-3:]) / 3
        tick_prior = sum(tick_counts[-6:-3]) / 3
        result["tick_rate_roc"] = float(tick_recent / tick_prior) if tick_prior > 0 else None

    # Spread compression: avg spread last 3 / avg spread last 10
    avg_spread_all = sum(spreads) / n if n > 0 else 0
    avg_spread_recent = sum(spreads[-3:]) / min(3, n)
    result["spread_compression"] = (
        float(avg_spread_recent / avg_spread_all) if avg_spread_all > 0 else None
    )

    # Absorption building: count candles with body_ratio < 0.3
    result["absorption_building"] = sum(1 for br in body_ratios if br < 0.3)

    # Imbalance trend: stacked count in last 5 vs prior 5
    if n >= 10:
        imb_recent = sum(stacked_counts[-5:])
        imb_prior = sum(stacked_counts[-10:-5])
        result["imbalance_trend"] = float(imb_recent - imb_prior)
    elif n >= 5:
        mid = n // 2
        imb_recent = sum(stacked_counts[mid:])
        imb_prior = sum(stacked_counts[:mid])
        result["imbalance_trend"] = float(imb_recent - imb_prior)

    return result


def compute_candle_pattern_features(candles: list[dict]) -> dict:
    """Compute candle pattern features from recent candles.

    Returns:
        Dict with candle pattern feature values.
    """
    result = {
        "last_3_candles_direction": None,
        "last_candle_is_doji": None,
        "consecutive_same_direction": None,
        "highest_volume_candle_position": None,
        "range_expansion": None,
    }

    if not candles:
        return result

    def _get(candle, key, default=0):
        if isinstance(candle, dict):
            return candle.get(key, default)
        return getattr(candle, key, default)

    n = len(candles)

    # Last 3 candles direction: count of up candles (close > open)
    last_3 = candles[-3:] if n >= 3 else candles
    up_count = sum(
        1 for c in last_3
        if _get(c, "close", 0) > _get(c, "open", 0)
    )
    result["last_3_candles_direction"] = up_count

    # Last candle is doji
    last = candles[-1]
    result["last_candle_is_doji"] = _get(last, "body_ratio", 1.0) < 0.1

    # Consecutive same direction
    if n >= 2:
        last_dir = _get(candles[-1], "close", 0) > _get(candles[-1], "open", 0)
        count = 1
        for c in reversed(candles[:-1]):
            if (_get(c, "close", 0) > _get(c, "open", 0)) == last_dir:
                count += 1
            else:
                break
        result["consecutive_same_direction"] = count
    else:
        result["consecutive_same_direction"] = 1

    # Highest volume candle position in last 10
    recent_10 = candles[-10:] if n >= 10 else candles
    volumes = [_get(c, "volume", 0) for c in recent_10]
    if volumes:
        result["highest_volume_candle_position"] = int(np.argmax(volumes))

    # Range expansion: last candle spread / avg spread of last 10
    spreads = [_get(c, "spread", 0) for c in recent_10]
    avg_spread = sum(spreads) / len(spreads) if spreads else 0
    last_spread = _get(candles[-1], "spread", 0)
    result["range_expansion"] = (
        float(last_spread / avg_spread) if avg_spread > 0 else None
    )

    return result
```

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/test_level_touch_features.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/level_touch/ backend/tests/test_level_touch_features.py
git commit -m "feat(ml): add temporal derivative and candle pattern computation"
```

---

### Task 4: Outcome Classifier — 30-min Outcome Measurement

**Files:**
- Create: `backend/src/ml/level_touch/outcomes.py`
- Test: `backend/tests/test_level_touch_outcomes.py`

**Context:** Given candles from the 30-min window after a level touch, classify the outcome into one of 5 categories. This is a pure function — no DB, no async.

- [ ] **Step 1: Write tests for outcome classification**

```python
# backend/tests/test_level_touch_outcomes.py
from src.ml.level_touch.outcomes import classify_outcome, TICK_SIZE

def test_strong_reversal_from_below():
    """Price approached from below, reversed down 25 ticks."""
    result = classify_outcome(
        level_price=20000.0,
        approach_direction="from_below",
        candle_highs=[20002.0, 20001.0, 19999.0, 19996.0, 19994.0],
        candle_lows=[19999.0, 19998.0, 19996.0, 19993.0, 19993.75],
    )
    assert result["outcome"] == "strong_reversal"
    assert result["max_reversal_ticks"] >= 20

def test_strong_continuation_from_below():
    """Price approached from below, pushed through 25 ticks."""
    result = classify_outcome(
        level_price=20000.0,
        approach_direction="from_below",
        candle_highs=[20002.0, 20003.0, 20005.0, 20006.0, 20006.25],
        candle_lows=[20000.0, 20001.0, 20003.0, 20004.0, 20005.0],
    )
    assert result["outcome"] == "strong_continuation"
    assert result["max_continuation_ticks"] >= 20

def test_chop():
    """Price stays within ±7 ticks of level."""
    result = classify_outcome(
        level_price=20000.0,
        approach_direction="from_below",
        candle_highs=[20001.0, 20001.25, 20000.75, 20001.0, 20000.50],
        candle_lows=[19999.5, 19999.75, 19999.25, 19999.0, 19999.50],
    )
    assert result["outcome"] == "chop"
    assert result["max_continuation_ticks"] < 8
    assert result["max_reversal_ticks"] < 8

def test_weak_reversal_from_above():
    """Price approached from above, bounced up 12 ticks."""
    result = classify_outcome(
        level_price=20000.0,
        approach_direction="from_above",
        candle_highs=[20003.0, 20003.0, 20003.0, 20003.0, 20003.0],
        candle_lows=[20000.0, 20000.25, 20000.50, 20001.0, 20001.0],
    )
    assert result["outcome"] == "weak_reversal"
    assert 8 <= result["max_reversal_ticks"] < 20

def test_weak_continuation_from_above():
    """Price approached from above, pushed through down 10 ticks."""
    result = classify_outcome(
        level_price=20000.0,
        approach_direction="from_above",
        candle_highs=[20000.0, 19999.5, 19999.0, 19998.5, 19998.0],
        candle_lows=[19999.0, 19998.5, 19997.75, 19997.50, 19997.50],
    )
    assert result["outcome"] == "weak_continuation"
    assert 8 <= result["max_continuation_ticks"] < 20
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_level_touch_outcomes.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement outcome classifier**

Create `backend/src/ml/level_touch/outcomes.py`:

```python
"""Outcome classification for level touch events.

Measures price excursion in 30-min window after level touch,
classifies into 5 categories based on tick thresholds.
"""
import asyncio
import json
import logging
import time

logger = logging.getLogger(__name__)

TICK_SIZE = 0.25
STRONG_THRESHOLD = 20   # ticks
WEAK_THRESHOLD = 8      # ticks
OBSERVATION_WINDOW_SEC = 30 * 60  # 30 minutes

# Outcome class labels
OUTCOMES = [
    "strong_reversal",
    "weak_reversal",
    "chop",
    "weak_continuation",
    "strong_continuation",
]
OUTCOME_TO_INDEX = {o: i for i, o in enumerate(OUTCOMES)}


def classify_outcome(
    level_price: float,
    approach_direction: str,
    candle_highs: list[float],
    candle_lows: list[float],
) -> dict:
    """Classify the outcome of a level touch.

    Args:
        level_price: The structural level price.
        approach_direction: "from_below" or "from_above".
        candle_highs: High prices of candles in 30-min window.
        candle_lows: Low prices of candles in 30-min window.

    Returns:
        dict with: outcome, max_continuation_ticks, max_reversal_ticks
    """
    if not candle_highs or not candle_lows:
        return {
            "outcome": None,
            "max_continuation_ticks": None,
            "max_reversal_ticks": None,
        }

    max_high = max(candle_highs)
    min_low = min(candle_lows)

    if approach_direction == "from_below":
        # Continuation = price goes above level, reversal = price drops below
        continuation_ticks = (max_high - level_price) / TICK_SIZE
        reversal_ticks = (level_price - min_low) / TICK_SIZE
    else:
        # from_above: continuation = price drops below level, reversal = price rises above
        continuation_ticks = (level_price - min_low) / TICK_SIZE
        reversal_ticks = (max_high - level_price) / TICK_SIZE

    # Clamp negatives to 0 (price may not cross level at all)
    continuation_ticks = max(0.0, continuation_ticks)
    reversal_ticks = max(0.0, reversal_ticks)

    # Dominant direction determines class
    if continuation_ticks >= STRONG_THRESHOLD and continuation_ticks >= reversal_ticks:
        outcome = "strong_continuation"
    elif reversal_ticks >= STRONG_THRESHOLD and reversal_ticks > continuation_ticks:
        outcome = "strong_reversal"
    elif continuation_ticks >= WEAK_THRESHOLD and continuation_ticks >= reversal_ticks:
        outcome = "weak_continuation"
    elif reversal_ticks >= WEAK_THRESHOLD and reversal_ticks > continuation_ticks:
        outcome = "weak_reversal"
    else:
        outcome = "chop"

    return {
        "outcome": outcome,
        "max_continuation_ticks": round(continuation_ticks, 2),
        "max_reversal_ticks": round(reversal_ticks, 2),
    }
```

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/test_level_touch_outcomes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/level_touch/outcomes.py backend/tests/test_level_touch_outcomes.py
git commit -m "feat(ml): add outcome classification for level touches (5-class)"
```

---

### Task 5: Outcome Tracker — Async 30-min Delayed Labeling

**Files:**
- Modify: `backend/src/ml/level_touch/outcomes.py` (add OutcomeTracker class)
- Test: `backend/tests/test_level_touch_outcomes.py` (extend)

**Context:** The `OutcomeTracker` class manages the lifecycle: receives touch events, schedules 30-min delayed outcome measurement, writes to DB. Needs `db_session_factory` and access to `MarketRepo.get_candles()`. Uses `asyncio.get_event_loop().call_later()` for the delayed callback, same pattern as `LevelMonitor._emit_level_context()`.

- [ ] **Step 1: Write test for OutcomeTracker registration and deduplication**

```python
# Add to backend/tests/test_level_touch_outcomes.py
from src.ml.level_touch.outcomes import OutcomeTracker

def test_outcome_tracker_registers_touch():
    tracker = OutcomeTracker()
    tracker.register_touch(
        symbol="NQ", level_name="VAH", level_type="vah",
        level_price=20000.0, approach_direction="from_below",
        touch_ts=1000.0, session_date="2026-03-18",
        features={"delta": 500},
    )
    assert len(tracker._pending) == 1

def test_outcome_tracker_deduplication():
    """Same level within 30 min is deduplicated."""
    tracker = OutcomeTracker()
    tracker.register_touch(
        symbol="NQ", level_name="VAH", level_type="vah",
        level_price=20000.0, approach_direction="from_below",
        touch_ts=1000.0, session_date="2026-03-18",
        features={"delta": 500},
    )
    tracker.register_touch(
        symbol="NQ", level_name="VAH", level_type="vah",
        level_price=20000.0, approach_direction="from_below",
        touch_ts=1500.0, session_date="2026-03-18",
        features={"delta": 600},
    )
    assert len(tracker._pending) == 1  # Second touch ignored

def test_outcome_tracker_different_level_not_deduplicated():
    """Different levels are tracked independently."""
    tracker = OutcomeTracker()
    tracker.register_touch(
        symbol="NQ", level_name="VAH", level_type="vah",
        level_price=20000.0, approach_direction="from_below",
        touch_ts=1000.0, session_date="2026-03-18",
        features={"delta": 500},
    )
    tracker.register_touch(
        symbol="NQ", level_name="POC", level_type="poc",
        level_price=19950.0, approach_direction="from_above",
        touch_ts=1000.0, session_date="2026-03-18",
        features={"delta": -200},
    )
    assert len(tracker._pending) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_level_touch_outcomes.py -v -k tracker`
Expected: FAIL

- [ ] **Step 3: Implement OutcomeTracker**

Add to `backend/src/ml/level_touch/outcomes.py`:

```python
class OutcomeTracker:
    """Tracks level touches and schedules 30-min delayed outcome measurement.

    Usage:
        tracker = OutcomeTracker()
        tracker.set_context(loop, db_session_factory)
        tracker.register_touch(...)  # called from LevelMonitor
    """

    def __init__(self):
        self._pending: dict[str, dict] = {}  # key: "level_name" -> touch data
        self._loop: asyncio.AbstractEventLoop | None = None
        self._db_session_factory = None

    def set_context(self, loop: asyncio.AbstractEventLoop, db_session_factory):
        self._loop = loop
        self._db_session_factory = db_session_factory

    def register_touch(
        self,
        symbol: str,
        level_name: str,
        level_type: str,
        level_price: float,
        approach_direction: str,
        touch_ts: float,
        session_date: str,
        features: dict,
        prediction: str | None = None,
        prediction_confidence: float | None = None,
    ):
        """Register a level touch. Deduplicates within 30-min window per level.
        Excludes touches within 30 min of RTH close (15:30 ET or later).
        """
        # RTH boundary check: exclude touches after 15:30 ET
        from datetime import datetime
        from zoneinfo import ZoneInfo
        et_now = datetime.now(ZoneInfo("US/Eastern"))
        if et_now.hour >= 15 and et_now.minute >= 30:
            logger.debug(f"Skip {level_name}: within 30 min of RTH close")
            return

        key = level_name
        if key in self._pending:
            prior_ts = self._pending[key]["touch_ts"]
            if touch_ts - prior_ts < OBSERVATION_WINDOW_SEC:
                logger.debug(f"Dedup: {level_name} touched again within 30 min")
                return

        touch_data = {
            "symbol": symbol,
            "level_name": level_name,
            "level_type": level_type,
            "level_price": level_price,
            "approach_direction": approach_direction,
            "touch_ts": touch_ts,
            "session_date": session_date,
            "features": features,
            "prediction": prediction,
            "prediction_confidence": prediction_confidence,
        }
        self._pending[key] = touch_data

        # Write feature row immediately (outcome filled later)
        if self._db_session_factory:
            self._write_touch_to_db(touch_data)

        # Schedule outcome measurement after 30 min
        if self._loop:
            self._loop.call_later(
                OBSERVATION_WINDOW_SEC,
                lambda td=touch_data: asyncio.ensure_future(
                    self._measure_outcome(td)
                ),
            )
            logger.info(
                f"Level touch registered: {level_name} @ {level_price} "
                f"({approach_direction}), outcome in 30 min"
            )

    def _write_touch_to_db(self, touch_data: dict):
        """Write the outcome row (without outcome) and feature row to DB."""
        try:
            session = self._db_session_factory()
            try:
                from src.db.models import LevelTouchOutcome, LevelTouchFeature

                outcome_row = LevelTouchOutcome(
                    symbol=touch_data["symbol"],
                    touch_ts=touch_data["touch_ts"],
                    level_name=touch_data["level_name"],
                    level_type=touch_data["level_type"],
                    level_price=touch_data["level_price"],
                    approach_direction=touch_data["approach_direction"],
                    session_date=touch_data["session_date"],
                    prediction=touch_data.get("prediction"),
                    prediction_confidence=touch_data.get("prediction_confidence"),
                )
                session.add(outcome_row)
                session.flush()

                feature_row = LevelTouchFeature(
                    touch_outcome_id=outcome_row.id,
                    features=json.dumps(touch_data["features"]),
                    created_at=time.time(),
                )
                session.add(feature_row)
                session.commit()

                touch_data["_outcome_id"] = outcome_row.id
            finally:
                session.close()
        except Exception:
            logger.exception("Failed to write level touch to DB")

    async def _measure_outcome(self, touch_data: dict):
        """Measure outcome 30 min after touch and update DB."""
        outcome_id = touch_data.get("_outcome_id")
        if not outcome_id or not self._db_session_factory:
            return

        try:
            session = self._db_session_factory()
            try:
                from src.repositories.market_repo import MarketRepo
                from src.db.models import LevelTouchOutcome
                from datetime import datetime, timezone

                repo = MarketRepo(session)
                touch_dt = datetime.fromtimestamp(touch_data["touch_ts"], tz=timezone.utc)
                end_dt = datetime.fromtimestamp(
                    touch_data["touch_ts"] + OBSERVATION_WINDOW_SEC, tz=timezone.utc
                )

                candles = repo.get_candles(
                    symbol=touch_data["symbol"],
                    interval="1m",
                    start=touch_dt,
                    end=end_dt,
                )

                if candles:
                    highs = [c.h for c in candles]
                    lows = [c.l for c in candles]
                    result = classify_outcome(
                        level_price=touch_data["level_price"],
                        approach_direction=touch_data["approach_direction"],
                        candle_highs=highs,
                        candle_lows=lows,
                    )

                    row = session.get(LevelTouchOutcome, outcome_id)
                    if row:
                        row.outcome = result["outcome"]
                        row.max_continuation_ticks = result["max_continuation_ticks"]
                        row.max_reversal_ticks = result["max_reversal_ticks"]
                        row.outcome_measured_at = time.time()
                        session.commit()
                        logger.info(
                            f"Outcome measured: {touch_data['level_name']} → "
                            f"{result['outcome']} (cont={result['max_continuation_ticks']}, "
                            f"rev={result['max_reversal_ticks']})"
                        )
                else:
                    logger.warning(
                        f"No candles found for outcome measurement: {touch_data['level_name']}"
                    )
            finally:
                session.close()
        except Exception:
            logger.exception("Failed to measure outcome")
        finally:
            # Remove from pending
            self._pending.pop(touch_data["level_name"], None)
```

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/test_level_touch_outcomes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/level_touch/outcomes.py backend/tests/test_level_touch_outcomes.py
git commit -m "feat(ml): add OutcomeTracker with 30-min delayed labeling and deduplication"
```

---

### Task 6: Extend LevelMonitor — Approach Direction + ML Integration

**Files:**
- Modify: `backend/src/market_data/level_monitor.py`

**Context:** The `LevelMonitor` needs: (1) track approach direction via `approach_price` field on `MonitoredLevel`, (2) on `level_touched`, extract features and call `OutcomeTracker.register_touch()`, (3) if model loaded, run prediction and emit `ml_prediction` SSE event.

Read `backend/src/market_data/level_monitor.py` carefully before modifying. Key integration points:
- `MonitoredLevel` dataclass — add `approach_price: float | None = None`
- `on_tick()` — set `approach_price` when transitioning WATCHING→APPROACHING
- `_on_level_touched()` method (or equivalent) — add feature extraction + outcome tracker + prediction

- [ ] **Step 1: Read current level_monitor.py**

Read `backend/src/market_data/level_monitor.py` in full to understand the exact integration points.

- [ ] **Step 2: Add `approach_price` to MonitoredLevel**

Add field to the `MonitoredLevel` dataclass:
```python
approach_price: float | None = None  # price when WATCHING → APPROACHING
```

- [ ] **Step 3: Set approach_price on WATCHING→APPROACHING transition**

In the `on_tick()` method, when a level transitions from WATCHING to APPROACHING:
```python
if old_status == LevelStatus.WATCHING and level.status == LevelStatus.APPROACHING:
    level.approach_price = price
```

- [ ] **Step 4: Derive approach_direction from approach_price**

Add helper to `LevelMonitor`:
```python
def _get_approach_direction(self, level: MonitoredLevel) -> str:
    if level.approach_price is not None and level.approach_price < level.price:
        return "from_below"
    return "from_above"
```

- [ ] **Step 5: Add OutcomeTracker instance and set_context**

Add to `LevelMonitor.__init__()`:
```python
from src.ml.level_touch.outcomes import OutcomeTracker
self._outcome_tracker = OutcomeTracker()
```

In `set_async_context()`, also set the tracker's context:
```python
self._outcome_tracker.set_context(loop, db_session_factory)
```

- [ ] **Step 6: Integrate feature extraction + outcome tracking on level touch**

In the `_on_level_touched()` method (or wherever `level_touched` SSE events are emitted), after existing orderflow snapshot logic, add:

```python
# ML feature extraction + outcome tracking
approach_dir = self._get_approach_direction(level)
direction = "long" if approach_dir == "from_below" else "short"
of_signals = compute_signals(candles, direction, lookback=10)

from src.ml.features.level_touch_features import extract_level_touch_features
from src.ml.level_touch.compute import compute_temporal_derivatives, compute_candle_pattern_features

temporal = compute_temporal_derivatives(candles)
candle_patterns = compute_candle_pattern_features(candles)

features = extract_level_touch_features(
    level_type=level.name.lower().replace(" ", "_"),
    level_category=level.category,
    approach_direction=approach_dir,
    level_confluence=len(level.cluster),
    # Orderflow from of_signals
    delta=of_signals.delta,
    delta_aligned=of_signals.delta_aligned,
    delta_divergence=of_signals.delta_divergence,
    delta_unwind=of_signals.delta_unwind,
    cvd=of_signals.cvd,
    cvd_trend=of_signals.cvd_trend,
    vsa_absorption=of_signals.vsa_absorption,
    tick_vol_accelerating=of_signals.tick_vol_accelerating,
    trapped_traders=of_signals.trapped_traders,
    passive_active_ratio=of_signals.passive_active_ratio,
    big_trades_count=of_signals.big_trades_count,
    big_trades_net_delta=of_signals.big_trades_net_delta,
    stop_run_detected=of_signals.stop_run_detected,
    imbalance_ratio_max=of_signals.imbalance_ratio_max,
    stacked_imbalance_count=of_signals.stacked_imbalance_count,
    stacked_imbalance_direction=of_signals.stacked_imbalance_direction,
    last_candle_delta=candles[-1].delta if candles else None,
    last_candle_body_ratio=candles[-1].body_ratio if candles else None,
    # Temporal derivatives
    **temporal,
    # Candle patterns
    **candle_patterns,
    # Session context — fill from self._session if available
    # Macro — fill from self._macro if available
)

# Run prediction if model loaded
prediction = None
confidence = None
top_features = []
from src.ml.serving.predictor import get_predictor
predictor = get_predictor()
if predictor.is_loaded("level_classifier"):
    pred_result = predictor.predict("level_classifier", features)
    if pred_result and isinstance(pred_result, dict):
        prediction = pred_result.get("class_name")
        confidence = pred_result.get("confidence")

        # SHAP explanation (if explainer initialized)
        from src.ml.level_touch.shap_explainer import explain_prediction
        from src.ml.models.level_classifier import _encode_features
        from src.ml.features.level_touch_features import FEATURE_NAMES
        top_features = explain_prediction(
            _encode_features(features), FEATURE_NAMES,
            pred_result["class"], top_n=5,
        )

        # Confidence gating per spec:
        # Actionable classes (strong_reversal, strong_continuation): > 0.50
        # Informational classes: > 0.35
        # Below threshold: emit "uncertain"
        ACTIONABLE = {"strong_reversal", "strong_continuation"}
        threshold = 0.50 if prediction in ACTIONABLE else 0.35
        surfaced_prediction = prediction if confidence > threshold else "uncertain"

        # Emit ML prediction SSE event
        self._publish("ml_prediction", {
            "level": level.name,
            "predicted": surfaced_prediction,
            "raw_predicted": prediction,
            "confidence": confidence,
            "probabilities": pred_result.get("probabilities", {}),
            "top_features": top_features,
        })

        # Store for API polling
        from src.ml.level_touch import set_last_prediction
        set_last_prediction({
            "level": level.name,
            "predicted": surfaced_prediction,
            "raw_predicted": prediction,
            "confidence": confidence,
            "probabilities": pred_result.get("probabilities", {}),
            "top_features": top_features,
            "timestamp": time.time(),
        })

# Register with outcome tracker
self._outcome_tracker.register_touch(
    symbol="NQ",
    level_name=level.name,
    level_type=level.name.lower().replace(" ", "_"),
    level_price=level.price,
    approach_direction=approach_dir,
    touch_ts=time.time(),
    session_date=datetime.now().strftime("%Y-%m-%d"),
    features=features,
    prediction=prediction,
    prediction_confidence=confidence,
)
```

Note: Adapt this to the exact method signatures and variable names in the current `level_monitor.py`. The above is a template — read the actual file first and integrate accordingly.

- [ ] **Step 7: Test manually**

Start the backend dev server, verify no import errors. If live data is flowing, wait for a level touch and check:
- `level_touch_outcomes` table has a new row
- `level_touch_features` table has a new row with JSON features
- SSE stream shows `ml_prediction` event (only if model is loaded)

- [ ] **Step 8: Commit**

```bash
git add backend/src/market_data/level_monitor.py
git commit -m "feat(ml): integrate level touch feature extraction + outcome tracking into LevelMonitor"
```

---

### Task 7: Level Classifier Model — Train + Predict

**Files:**
- Create: `backend/src/ml/models/level_classifier.py`
- Test: `backend/tests/test_level_classifier.py`

**Context:** Follows the existing model pattern in `backend/src/ml/models/setup_scorer.py`. Uses `train_model()` from `backend/src/ml/optimizer/trainer.py` with `task="multiclass"`. The model encodes features using the maps from `level_touch_features.py`.

- [ ] **Step 1: Write test for feature encoding**

```python
# backend/tests/test_level_classifier.py
import numpy as np
from src.ml.models.level_classifier import LevelClassifierModel, _encode_features

def test_encode_features_basic():
    """Features are encoded to numeric array."""
    features = {
        "level_type": "vah", "level_category": "session",
        "approach_direction": "from_below",
        "delta": 500, "cvd": 2000, "delta_aligned": True,
        "vsa_absorption": False, "regime": "risk_on",
        "market_type": "trending_up",
    }
    vec = _encode_features(features)
    assert isinstance(vec, np.ndarray)
    assert not np.any(np.isnan(vec))  # encoded categoricals shouldn't be NaN

def test_encode_features_with_missing():
    """Missing features get NaN (LightGBM handles natively)."""
    features = {
        "level_type": "poc", "approach_direction": "from_above",
        # Most features missing
    }
    vec = _encode_features(features)
    assert isinstance(vec, np.ndarray)
    # Many values should be NaN
    assert np.any(np.isnan(vec))
```

- [ ] **Step 2: Write test for model training with synthetic data**

```python
def test_train_with_synthetic_data():
    """Model trains on synthetic level touch data."""
    import json
    model = LevelClassifierModel()

    # Create synthetic training rows (mimicking DB rows)
    rows = []
    for i in range(500):
        outcome_idx = i % 5  # cycle through all 5 classes
        features = {
            "level_type": ["poc", "vah", "val", "vwap", "pdh"][i % 5],
            "level_category": "session",
            "approach_direction": "from_below" if i % 2 == 0 else "from_above",
            "delta": float(100 + i * 10 * (-1 if outcome_idx < 2 else 1)),
            "cvd": float(i * 50 * (-1 if outcome_idx < 2 else 1)),
            "delta_aligned": outcome_idx >= 3,
            "vsa_absorption": outcome_idx < 2,
            "session_elapsed_pct": float(i % 100),
            "vix_level": 15.0 + (i % 10),
        }
        rows.append({
            "features": features,
            "outcome": ["strong_reversal", "weak_reversal", "chop",
                       "weak_continuation", "strong_continuation"][outcome_idx],
        })

    result = model.train(rows)
    assert result is not None
    assert "file_path" in result
    assert result["training_data_count"] > 0
    assert result["validation_score"] >= 0.0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_level_classifier.py -v`
Expected: FAIL (module not found)

- [ ] **Step 4: Implement LevelClassifierModel**

Create `backend/src/ml/models/level_classifier.py`:

```python
"""Level touch classifier model.

Predicts 5-class outcome (strong_reversal, weak_reversal, chop,
weak_continuation, strong_continuation) from ~60 features at level touch.

Uses LightGBM via the existing train_model() utility.
"""
import json
import logging
from pathlib import Path

import joblib
import numpy as np

from src.ml.features.level_touch_features import (
    BOOLEAN_FEATURES,
    CATEGORICAL_MAPS,
    FEATURE_NAMES,
)
from src.ml.level_touch.outcomes import OUTCOME_TO_INDEX, OUTCOMES

logger = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "models"
MIN_SAMPLES = 300
MIN_SAMPLES_PER_CLASS = 50  # below this, collapse to 3 classes
FALLBACK_CLASSES = ["reversal", "chop", "continuation"]
FALLBACK_MAP = {
    "strong_reversal": "reversal",
    "weak_reversal": "reversal",
    "chop": "chop",
    "weak_continuation": "continuation",
    "strong_continuation": "continuation",
}


def _encode_features(features: dict) -> np.ndarray:
    """Encode feature dict to numeric array for model input.

    Returns:
        np.ndarray of shape (len(FEATURE_NAMES),). NaN for missing values.
    """
    vec = []
    for name in FEATURE_NAMES:
        val = features.get(name)
        if val is None:
            vec.append(np.nan)
        elif name in BOOLEAN_FEATURES:
            vec.append(1.0 if val else 0.0)
        elif name in CATEGORICAL_MAPS:
            cat_map = CATEGORICAL_MAPS[name]
            encoded = cat_map.get(val, cat_map.get(str(val)))
            vec.append(float(encoded) if encoded is not None else np.nan)
        else:
            try:
                vec.append(float(val))
            except (ValueError, TypeError):
                vec.append(np.nan)
    return np.array(vec, dtype=np.float32)


class LevelClassifierModel:
    """XGBoost/LightGBM classifier for level touch outcomes."""

    def train(self, data: list[dict]) -> dict | None:
        """Train on level touch data.

        Args:
            data: List of dicts with 'features' (dict) and 'outcome' (str) keys.
                  Features can be dict or JSON string.

        Returns:
            dict with file_path, training_data_count, validation_score, baseline_metric
            or None if insufficient data.
        """
        if len(data) < MIN_SAMPLES:
            logger.info(f"level_classifier: {len(data)}/{MIN_SAMPLES} — insufficient")
            return None

        # Encode features and labels
        X, y = [], []
        for row in data:
            features = row["features"]
            if isinstance(features, str):
                features = json.loads(features)
            outcome = row.get("outcome")
            if outcome is None:
                continue

            vec = _encode_features(features)
            X.append(vec)
            y.append(outcome)

        if len(X) < MIN_SAMPLES:
            return None

        X = np.array(X, dtype=np.float32)

        # Check class distribution — collapse to 3 if needed
        from collections import Counter
        class_counts = Counter(y)
        use_fallback = any(
            class_counts.get(cls, 0) < MIN_SAMPLES_PER_CLASS
            for cls in OUTCOMES
        )

        if use_fallback:
            logger.info(
                f"Class counts {dict(class_counts)} — collapsing to 3 classes"
            )
            classes = FALLBACK_CLASSES
            y_encoded = np.array(
                [classes.index(FALLBACK_MAP[label]) for label in y],
                dtype=np.int64,
            )
            num_class = 3
        else:
            classes = OUTCOMES
            y_encoded = np.array(
                [OUTCOME_TO_INDEX[label] for label in y],
                dtype=np.int64,
            )
            num_class = 5

        # Train via centralized trainer
        from src.ml.optimizer.trainer import train_model

        result = train_model(
            X, y_encoded,
            task="multiclass",
            min_samples=MIN_SAMPLES,
            feature_names=FEATURE_NAMES,
            num_class=num_class,
        )
        if result is None:
            logger.warning("level_classifier: train_model returned None")
            return None

        # Save model
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        path = MODELS_DIR / "level_classifier_latest.joblib"
        joblib.dump({
            "model": result["model"],
            "feature_names": FEATURE_NAMES,
            "task": "multiclass",
            "classes": classes,
            "num_class": num_class,
            "use_fallback": use_fallback,
        }, path)

        # Baseline: random accuracy = 1/num_class
        baseline = 1.0 / num_class

        return {
            "file_path": str(path),
            "training_data_count": len(X),
            "validation_score": result.get("validation_score", 0.0),
            "baseline_metric": baseline,
        }

    def predict(self, features: dict) -> dict | None:
        """Predict class from features. Actual prediction via central Predictor."""
        return None
```

- [ ] **Step 5: Run tests**

Run: `cd backend && python -m pytest tests/test_level_classifier.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/ml/models/level_classifier.py backend/tests/test_level_classifier.py
git commit -m "feat(ml): add LevelClassifierModel with 5-class/3-class fallback training"
```

---

### Task 8: Register in Training Orchestrator + Predictor

**Files:**
- Modify: `backend/src/ml/training/train_all.py`
- Modify: `backend/src/ml/serving/predictor.py`

**Context:** Read both files first. Follow existing patterns:
- `train_all.py`: Add to `MODEL_CONFIGS` and `_get_trainer()`
- `predictor.py`: The `predict()` method already handles multiclass (returns `{"class": int, "probabilities": [...]}`). We need to enhance it to also return `class_name` and `confidence` for the level_classifier.

- [ ] **Step 1: Read current files**

Read `backend/src/ml/training/train_all.py` and `backend/src/ml/serving/predictor.py`.

- [ ] **Step 2: Add level_classifier to MODEL_CONFIGS in train_all.py**

```python
"level_classifier": {
    "min_samples": 300,
    "domain": "trading",
    "source_type": "level_touch",
    "task": "multiclass",
},
```

- [ ] **Step 3: Add trainer function in _get_trainer()**

```python
"level_classifier": lambda data, session: _train_level_classifier(data),
```

And add the helper:
```python
def _train_level_classifier(data):
    """Train level classifier from level_touch_outcomes + level_touch_features."""
    from src.ml.models.level_classifier import LevelClassifierModel
    # Data comes from level_touch tables, not ml_features
    # Convert to the format expected by LevelClassifierModel.train()
    rows = []
    for row in data:
        features = row.features if isinstance(row.features, dict) else json.loads(row.features)
        rows.append({"features": features, "outcome": row.outcome})
    return LevelClassifierModel().train(rows)
```

**IMPORTANT:** The existing `TrainingOrchestrator.train_model()` calls `get_training_data(session, config["domain"], config["source_type"])` from `feature_store.py` — there is no `_get_training_data()` method to override. Since the level_classifier uses its own tables (not `ml_features`), the trainer lambda must handle data loading internally, similar to how `_train_schedule_optimizer` calls its own `check_and_train()`.

Update the trainer lambda to be self-contained:

```python
"level_classifier": lambda data, session: _train_level_classifier(session),
```

And the helper fetches data from `level_touch_*` tables directly:

```python
def _train_level_classifier(session):
    """Train level classifier — data from level_touch tables, not ml_features."""
    from src.ml.models.level_classifier import LevelClassifierModel
    from src.db.models import LevelTouchOutcome, LevelTouchFeature
    import json

    rows = (
        session.query(LevelTouchOutcome, LevelTouchFeature)
        .join(LevelTouchFeature, LevelTouchFeature.touch_outcome_id == LevelTouchOutcome.id)
        .filter(LevelTouchOutcome.outcome.isnot(None))
        .order_by(LevelTouchOutcome.touch_ts)
        .all()
    )
    data = []
    for outcome, feature in rows:
        data.append({
            "features": json.loads(feature.features) if isinstance(feature.features, str) else feature.features,
            "outcome": outcome.outcome,
        })
    return LevelClassifierModel().train(data)
```

Also check if `TrainingOrchestrator.train_model()` passes `data` from `get_training_data()` to the trainer lambda. If so, the level_classifier trainer should ignore the `data` param (it will be empty from `ml_features` since we don't log there) and fetch from its own tables via the `session` param.

- [ ] **Step 4: Fix feature encoding for level_classifier in Predictor.predict()**

**CRITICAL:** The existing `Predictor.predict()` encodes features with `features.get(f, 0.0)` — this replaces None with 0.0 and doesn't handle categorical encoding. The level_classifier needs `_encode_features()` which maps categoricals to ints and uses NaN for missing values.

Add a special case at the top of `Predictor.predict()` for level_classifier:

```python
def predict(self, model_name: str, features: dict):
    model_data = self.models.get(model_name)
    if not model_data:
        return None

    model = model_data["model"]
    task = model_data.get("task", "classification")
    feature_names = model_data.get("feature_names", [])
    classes = model_data.get("classes", [])

    # Level classifier has its own encoding (categoricals + NaN for missing)
    if model_name == "level_classifier":
        from src.ml.models.level_classifier import _encode_features
        X = _encode_features(features).reshape(1, -1)
    else:
        # Existing encoding for other models
        X = np.array([[features.get(f, 0.0) for f in feature_names]])

    # ... existing prediction logic ...

    if task == "multiclass":
        probas = model.predict_proba(X)[0]
        class_idx = int(np.argmax(probas))
        confidence = float(probas[class_idx])
        class_name = classes[class_idx] if class_idx < len(classes) else str(class_idx)
        return {
            "class": class_idx,
            "class_name": class_name,
            "confidence": confidence,
            "probabilities": {
                classes[i] if i < len(classes) else str(i): float(p)
                for i, p in enumerate(probas)
            },
        }
```

Note: Read the actual `predictor.py` to determine the exact structure of the predict method before editing. The above shows the key change — using `_encode_features()` for level_classifier instead of the generic encoding.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/training/train_all.py backend/src/ml/serving/predictor.py
git commit -m "feat(ml): register level_classifier in training orchestrator and predictor"
```

---

### Task 9: Backfill Pipeline — Historical Replay

**Files:**
- Create: `backend/src/ml/level_touch/backfill.py`
- Test: `backend/tests/test_level_touch_backfill.py`

**Context:** Replays historical 1m candles to generate labeled training data. For each session date: recompute levels, walk candles chronologically, detect crossings, extract features (no lookahead), classify outcomes (30-min lookahead into candles). Uses `MarketRepo.get_candles()` and level computation functions from `backend/src/market_data/levels.py`.

- [ ] **Step 1: Write test for virtual touch detection**

```python
# backend/tests/test_level_touch_backfill.py
from src.ml.level_touch.backfill import detect_virtual_touches

def test_detect_crossing_from_below():
    """Detect price crossing a level from below."""
    candles = [
        {"o": 19998.0, "h": 19999.0, "l": 19997.0, "c": 19998.5},  # below
        {"o": 19999.0, "h": 20001.0, "l": 19998.0, "c": 20000.5},  # crosses 20000
    ]
    levels = [{"name": "VAH", "price": 20000.0, "type": "vah", "category": "session"}]
    touches = detect_virtual_touches(candles, levels)
    assert len(touches) == 1
    assert touches[0]["level_name"] == "VAH"
    assert touches[0]["approach_direction"] == "from_below"
    assert touches[0]["candle_index"] == 1

def test_detect_crossing_from_above():
    """Detect price crossing a level from above."""
    candles = [
        {"o": 20002.0, "h": 20003.0, "l": 20001.0, "c": 20002.5},  # above
        {"o": 20001.0, "h": 20002.0, "l": 19999.0, "c": 19999.5},  # crosses 20000
    ]
    levels = [{"name": "VAH", "price": 20000.0, "type": "vah", "category": "session"}]
    touches = detect_virtual_touches(candles, levels)
    assert len(touches) == 1
    assert touches[0]["approach_direction"] == "from_above"

def test_deduplication_within_30_candles():
    """Same level not touched twice within 30 1m candles."""
    candles = [
        {"o": 19998.0, "h": 19999.0, "l": 19997.0, "c": 19998.5},
        {"o": 19999.0, "h": 20001.0, "l": 19998.0, "c": 20000.5},  # touch
        # ... 10 candles of chop around level ...
    ]
    for i in range(10):
        candles.append({"o": 20000.0, "h": 20001.0, "l": 19999.0, "c": 20000.25})
    candles.append({"o": 19999.0, "h": 20001.0, "l": 19998.0, "c": 20000.5})  # re-touch

    levels = [{"name": "VAH", "price": 20000.0, "type": "vah", "category": "session"}]
    touches = detect_virtual_touches(candles, levels)
    assert len(touches) == 1  # deduped

def test_no_touch_when_no_crossing():
    """No touch when price doesn't cross level."""
    candles = [
        {"o": 19998.0, "h": 19999.0, "l": 19997.0, "c": 19998.5},
        {"o": 19998.5, "h": 19999.5, "l": 19997.5, "c": 19999.0},
    ]
    levels = [{"name": "VAH", "price": 20000.0, "type": "vah", "category": "session"}]
    touches = detect_virtual_touches(candles, levels)
    assert len(touches) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_level_touch_backfill.py -v`
Expected: FAIL

- [ ] **Step 3: Implement virtual touch detection**

Create `backend/src/ml/level_touch/backfill.py`:

```python
"""Backfill pipeline for level touch ML classifier.

Replays historical 1m candles to generate labeled training data.
"""
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import numpy as np

from src.ml.level_touch.outcomes import (
    OBSERVATION_WINDOW_SEC,
    classify_outcome,
)

logger = logging.getLogger(__name__)

TICK_SIZE = 0.25
DEDUP_WINDOW_CANDLES = 30  # 30 x 1m = 30 min


def detect_virtual_touches(
    candles: list[dict],
    levels: list[dict],
) -> list[dict]:
    """Detect virtual level touches from candle data.

    Args:
        candles: List of 1m candle dicts with o, h, l, c keys.
        levels: List of level dicts with name, price, type, category keys.

    Returns:
        List of touch dicts with: level_name, level_type, level_category,
        level_price, approach_direction, candle_index.
    """
    touches = []
    # Track last touch candle index per level for deduplication
    last_touch_idx: dict[str, int] = {}

    for i in range(1, len(candles)):
        prev_close = candles[i - 1].get("c", 0)
        candle = candles[i]
        high = candle.get("h", 0)
        low = candle.get("l", 0)

        for level in levels:
            name = level["name"]
            price = level["price"]

            # Dedup: skip if touched within last 30 candles
            if name in last_touch_idx:
                if i - last_touch_idx[name] < DEDUP_WINDOW_CANDLES:
                    continue

            # Detect crossing
            if prev_close < price <= high:
                # Crossed from below
                touches.append({
                    "level_name": name,
                    "level_type": level.get("type", ""),
                    "level_category": level.get("category", ""),
                    "level_price": price,
                    "approach_direction": "from_below",
                    "candle_index": i,
                })
                last_touch_idx[name] = i
            elif prev_close > price >= low:
                # Crossed from above
                touches.append({
                    "level_name": name,
                    "level_type": level.get("type", ""),
                    "level_category": level.get("category", ""),
                    "level_price": price,
                    "approach_direction": "from_above",
                    "candle_index": i,
                })
                last_touch_idx[name] = i

    return touches


def backfill_session(
    session_date: str,
    candles_1m: list[dict],
    levels: list[dict],
    session_analysis: dict | None = None,
) -> list[dict]:
    """Backfill level touches for a single session.

    Args:
        session_date: Date string "YYYY-MM-DD".
        candles_1m: 1m candles for the RTH session, ordered by time.
        levels: Structural levels for this session.
        session_analysis: Optional session analysis dict for session context features.

    Returns:
        List of dicts with features + outcome, ready for DB insertion.
    """
    from src.ml.features.level_touch_features import extract_level_touch_features
    from src.ml.level_touch.compute import (
        compute_candle_pattern_features,
        compute_temporal_derivatives,
    )

    # Detect all virtual touches
    touches = detect_virtual_touches(candles_1m, levels)

    # RTH is ~390 minutes (09:30-16:00), exclude last 30 min
    max_touch_idx = len(candles_1m) - 30  # need 30 candles after touch

    results = []
    for touch in touches:
        idx = touch["candle_index"]

        # Skip if too close to end (need 30-min observation window)
        if idx >= max_touch_idx:
            continue

        # Extract features from candles UP TO touch point (no lookahead)
        prior_candles = candles_1m[max(0, idx - 10) : idx + 1]

        temporal = compute_temporal_derivatives(prior_candles)
        candle_patterns = compute_candle_pattern_features(prior_candles)

        # Approximate orderflow from candle data
        # (backfill limitations: no tick-level orderflow)
        last_candle = candles_1m[idx]
        approx_delta = None
        if "v" in last_candle:
            # Rough approximation: if close > open, assume net buying
            body = last_candle.get("c", 0) - last_candle.get("o", 0)
            approx_delta = int(last_candle["v"] * (0.6 if body > 0 else -0.6))

        # Session context
        session_pct = (idx / len(candles_1m)) * 100 if candles_1m else None

        # Count prior touches on this level
        prior_count = sum(
            1 for t in touches
            if t["level_name"] == touch["level_name"] and t["candle_index"] < idx
        )

        features = extract_level_touch_features(
            level_type=touch["level_type"],
            level_category=touch["level_category"],
            approach_direction=touch["approach_direction"],
            level_confluence=None,  # not available in backfill
            delta=approx_delta,
            session_elapsed_pct=session_pct,
            prior_touch_count=prior_count,
            last_candle_body_ratio=(
                abs(last_candle.get("c", 0) - last_candle.get("o", 0))
                / max(last_candle.get("h", 0) - last_candle.get("l", 0), TICK_SIZE)
                if last_candle else None
            ),
            **temporal,
            **candle_patterns,
            # Session analysis features if available
            market_type=session_analysis.get("market_type") if session_analysis else None,
            opening_type=session_analysis.get("opening_type") if session_analysis else None,
            ib_range=session_analysis.get("ib_range") if session_analysis else None,
        )

        # Classify outcome from future candles (30-min lookahead)
        future_candles = candles_1m[idx + 1 : idx + 31]
        outcome_result = classify_outcome(
            level_price=touch["level_price"],
            approach_direction=touch["approach_direction"],
            candle_highs=[c.get("h", 0) for c in future_candles],
            candle_lows=[c.get("l", 0) for c in future_candles],
        )

        results.append({
            "symbol": "NQ",
            # Use candle's actual timestamp if available (from ORM .ts field),
            # otherwise compute from session date + candle index as RTH offset
            "touch_ts": candles_1m[idx].get("ts_epoch",
                datetime.strptime(session_date, "%Y-%m-%d").replace(
                    hour=14, minute=30  # 09:30 ET = 14:30 UTC
                ).timestamp() + idx * 60
            ),
            "level_name": touch["level_name"],
            "level_type": touch["level_type"],
            "level_price": touch["level_price"],
            "approach_direction": touch["approach_direction"],
            "session_date": session_date,
            "is_backfill": True,
            "features": features,
            **outcome_result,
        })

    logger.info(
        f"Backfill {session_date}: {len(candles_1m)} candles, "
        f"{len(levels)} levels, {len(touches)} touches, "
        f"{len(results)} labeled samples"
    )
    return results
```

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/test_level_touch_backfill.py -v`
Expected: PASS

- [ ] **Step 5: Add full backfill orchestrator function**

Add to `backfill.py` the main `run_backfill()` function that iterates over dates, fetches candles from DB, recomputes levels, and stores results:

```python
def run_backfill(
    db_session_factory,
    start_date: str,
    end_date: str,
    symbol: str = "NQ",
):
    """Run full backfill across date range.

    Args:
        db_session_factory: SQLAlchemy session factory.
        start_date: "YYYY-MM-DD" start.
        end_date: "YYYY-MM-DD" end.
        symbol: Instrument symbol.
    """
    from src.repositories.market_repo import MarketRepo
    from src.market_data.levels import (
        compute_session_levels,
        compute_volume_profile,
        compute_vwap_bands,
    )
    from src.db.models import LevelTouchOutcome, LevelTouchFeature

    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    current = start
    total_samples = 0

    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        session = db_session_factory()
        try:
            repo = MarketRepo(session)

            # Fetch 1m candles for this date
            day_start = current.replace(hour=14, minute=30)  # 09:30 ET = 14:30 UTC
            day_end = current.replace(hour=21, minute=0)     # 16:00 ET = 21:00 UTC
            candles_orm = repo.get_candles(symbol, "1m", day_start, day_end)

            if not candles_orm or len(candles_orm) < 60:
                logger.debug(f"Skip {date_str}: {len(candles_orm) if candles_orm else 0} candles")
                current += timedelta(days=1)
                continue

            # Convert ORM rows to dicts (include ts_epoch for proper touch_ts)
            candles = [
                {
                    "o": c.o, "h": c.h, "l": c.l, "c": c.c, "v": c.v,
                    "ts_epoch": c.ts.timestamp() if hasattr(c.ts, 'timestamp') else float(c.ts),
                }
                for c in candles_orm
            ]

            # Recompute levels for this session
            levels = _compute_levels_for_date(candles, repo, symbol, date_str)

            if not levels:
                logger.debug(f"Skip {date_str}: no levels computed")
                current += timedelta(days=1)
                continue

            # Run backfill for this session
            results = backfill_session(date_str, candles, levels)

            # Store to DB
            for row in results:
                outcome_row = LevelTouchOutcome(
                    symbol=row["symbol"],
                    touch_ts=row["touch_ts"],
                    level_name=row["level_name"],
                    level_type=row["level_type"],
                    level_price=row["level_price"],
                    approach_direction=row["approach_direction"],
                    outcome=row["outcome"],
                    max_continuation_ticks=row.get("max_continuation_ticks"),
                    max_reversal_ticks=row.get("max_reversal_ticks"),
                    outcome_measured_at=time.time(),
                    session_date=row["session_date"],
                    is_backfill=1,
                )
                session.add(outcome_row)
                session.flush()

                feature_row = LevelTouchFeature(
                    touch_outcome_id=outcome_row.id,
                    features=json.dumps(row["features"]),
                    created_at=time.time(),
                )
                session.add(feature_row)

            session.commit()
            total_samples += len(results)
            logger.info(f"Backfill {date_str}: {len(results)} samples (total: {total_samples})")

        except Exception:
            logger.exception(f"Backfill failed for {date_str}")
            session.rollback()
        finally:
            session.close()

        current += timedelta(days=1)

    logger.info(f"Backfill complete: {total_samples} total samples")
    return total_samples


def _compute_levels_for_date(candles, repo, symbol, date_str):
    """Recompute structural levels from candle data for backfill.

    IMPORTANT: compute_session_levels() expects bars_1m as list[dict] with keys:
    'ts' (datetime), 'high', 'low', 'open', 'close', 'volume'.
    The ORM candles use 'o', 'h', 'l', 'c', 'v' — we must remap.
    Also, session_date must be a datetime object, not a string.
    """
    from src.market_data.levels import (
        compute_volume_profile,
        compute_vwap_bands,
        compute_session_levels,
    )

    levels = []

    # Convert candles to trades-like format for VP computation
    trades = []
    for c in candles:
        trades.append({"price": c["c"], "volume": c.get("v", 100)})

    if trades:
        vp = compute_volume_profile(trades)
        if vp:
            if vp.poc: levels.append({"name": "POC", "price": vp.poc, "type": "poc", "category": "session"})
            if vp.vah: levels.append({"name": "VAH", "price": vp.vah, "type": "vah", "category": "session"})
            if vp.val: levels.append({"name": "VAL", "price": vp.val, "type": "val", "category": "session"})

    # VWAP bands
    vwap = compute_vwap_bands(trades)
    if vwap:
        levels.append({"name": "VWAP", "price": vwap.vwap, "type": "vwap", "category": "band"})
        levels.append({"name": "VWAP 1SD Upper", "price": vwap.sd1_upper, "type": "vwap_1sd", "category": "band"})
        levels.append({"name": "VWAP 1SD Lower", "price": vwap.sd1_lower, "type": "vwap_1sd", "category": "band"})
        levels.append({"name": "VWAP 2SD Upper", "price": vwap.sd2_upper, "type": "vwap_2sd", "category": "band"})
        levels.append({"name": "VWAP 2SD Lower", "price": vwap.sd2_lower, "type": "vwap_2sd", "category": "band"})

    # Session levels (PDH/PDL, IB, etc.)
    # CRITICAL: Remap candle keys to match compute_session_levels() expected format
    # Read the actual function signature in levels.py before implementing —
    # it expects bars_1m with keys: ts (datetime), high, low, open, close, volume
    # and session_date as a datetime object.
    session_dt = datetime.strptime(date_str, "%Y-%m-%d")
    bars_remapped = []
    for i, c in enumerate(candles):
        bars_remapped.append({
            "ts": session_dt.replace(hour=9, minute=30) + timedelta(minutes=i),
            "open": c["o"], "high": c["h"], "low": c["l"], "close": c["c"],
            "volume": c.get("v", 0),
        })

    try:
        session_levels = compute_session_levels(bars_remapped, session_dt)
        if session_levels:
            if session_levels.pdh: levels.append({"name": "PDH", "price": session_levels.pdh, "type": "pdh", "category": "prior"})
            if session_levels.pdl: levels.append({"name": "PDL", "price": session_levels.pdl, "type": "pdl", "category": "prior"})
            if session_levels.ib_high: levels.append({"name": "IB High", "price": session_levels.ib_high, "type": "ib_high", "category": "session"})
            if session_levels.ib_low: levels.append({"name": "IB Low", "price": session_levels.ib_low, "type": "ib_low", "category": "session"})
    except Exception:
        logger.warning(f"compute_session_levels failed for {date_str}, using VP/VWAP only")

    return levels
```

- [ ] **Step 6: Run tests**

Run: `cd backend && python -m pytest tests/test_level_touch_backfill.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/src/ml/level_touch/backfill.py backend/tests/test_level_touch_backfill.py
git commit -m "feat(ml): add backfill pipeline for historical level touch replay"
```

---

### Task 10: CLI Commands — Backfill + Train

**Files:**
- Modify: `backend/src/app.py`

**Context:** Read `backend/src/app.py` to understand the current CLI structure. Add `ml backfill` and `ml train` commands.

- [ ] **Step 1: Read current app.py**

Read `backend/src/app.py` to find the right place to add ML commands.

- [ ] **Step 2: Add `ml backfill` command**

```python
@app.command()
def ml_backfill(
    start: str = typer.Option("2025-01-01", help="Start date YYYY-MM-DD"),
    end: str = typer.Option(None, help="End date YYYY-MM-DD (default: today)"),
    symbol: str = typer.Option("NQ", help="Symbol"),
):
    """Backfill level touch training data from historical candles."""
    from src.ml.level_touch.backfill import run_backfill
    from src.db.models import get_session_factory

    if end is None:
        end = datetime.now().strftime("%Y-%m-%d")

    session_factory = get_session_factory()
    total = run_backfill(session_factory, start, end, symbol)
    typer.echo(f"Backfill complete: {total} samples generated")
```

- [ ] **Step 3: Add `ml train` command**

```python
@app.command()
def ml_train_level_classifier(
    symbol: str = typer.Option("NQ", help="Symbol"),
):
    """Train the level touch classifier model."""
    from src.ml.models.level_classifier import LevelClassifierModel
    from src.db.models import get_session_factory, LevelTouchOutcome, LevelTouchFeature
    import json

    session_factory = get_session_factory()
    session = session_factory()
    try:
        rows = (
            session.query(LevelTouchOutcome, LevelTouchFeature)
            .join(LevelTouchFeature, LevelTouchFeature.touch_outcome_id == LevelTouchOutcome.id)
            .filter(LevelTouchOutcome.outcome.isnot(None))
            .order_by(LevelTouchOutcome.touch_ts)
            .all()
        )

        data = []
        for outcome, feature in rows:
            data.append({
                "features": json.loads(feature.features) if isinstance(feature.features, str) else feature.features,
                "outcome": outcome.outcome,
            })

        typer.echo(f"Training data: {len(data)} labeled samples")
        model = LevelClassifierModel()
        result = model.train(data)

        if result:
            typer.echo(f"Model saved to: {result['file_path']}")
            typer.echo(f"Validation score: {result['validation_score']:.4f}")
            typer.echo(f"Baseline (random): {result['baseline_metric']:.4f}")
        else:
            typer.echo("Training failed — insufficient data or error")
    finally:
        session.close()
```

- [ ] **Step 4: Test CLI commands manually**

Run: `cd backend && python -m src.app ml-backfill --start 2026-03-01 --end 2026-03-18 --symbol NQ`
Expected: Outputs progress per date and total sample count.

Run: `cd backend && python -m src.app ml-train-level-classifier`
Expected: Either trains successfully or reports insufficient data.

- [ ] **Step 5: Commit**

```bash
git add backend/src/app.py
git commit -m "feat(ml): add CLI commands for level touch backfill and training"
```

---

### Task 11: API Endpoint + SSE Event

**Files:**
- Modify: `backend/src/api/routes/market.py`

**Context:** Read `backend/src/api/routes/market.py` to understand existing endpoint patterns. Add `GET /api/trading/market/ml/prediction` endpoint. The `ml_prediction` SSE event is already emitted from `LevelMonitor` (Task 6), so no stream changes needed — just the REST endpoint for polling.

- [ ] **Step 1: Read market.py route patterns**

Read `backend/src/api/routes/market.py`.

- [ ] **Step 2: Add ML prediction endpoint**

First, create a module-level shared state dict (since `LevelMonitor` is not accessible as a singleton from the API layer). Add to `backend/src/ml/level_touch/__init__.py`:

```python
# Shared state for last ML prediction, written by LevelMonitor, read by API
_last_ml_prediction: dict | None = None

def set_last_prediction(prediction: dict):
    global _last_ml_prediction
    _last_ml_prediction = prediction

def get_last_prediction() -> dict | None:
    return _last_ml_prediction
```

Then the endpoint:

```python
@router.get("/ml/prediction")
async def get_ml_prediction():
    """Get latest ML prediction for level touch."""
    from src.ml.serving.predictor import get_predictor
    predictor = get_predictor()
    if not predictor.is_loaded("level_classifier"):
        return {"status": "model_not_loaded", "prediction": None}

    from src.ml.level_touch import get_last_prediction
    prediction = get_last_prediction()
    if prediction:
        return {"status": "ok", "prediction": prediction}
    return {"status": "no_recent_prediction", "prediction": None}
```

- [ ] **Step 3: Store last prediction from LevelMonitor**

In the ML prediction code added in Task 6, after computing the prediction, call:
```python
from src.ml.level_touch import set_last_prediction
set_last_prediction({
    "level": level.name,
    "predicted": prediction,
    "confidence": confidence,
    "probabilities": pred_result.get("probabilities", {}),
    "timestamp": time.time(),
})
```

- [ ] **Step 4: Test endpoint**

Start backend, hit `curl http://localhost:8000/api/trading/market/ml/prediction`
Expected: JSON response with status and prediction (or "model_not_loaded")

- [ ] **Step 5: Commit**

```bash
git add backend/src/api/routes/market.py backend/src/market_data/level_monitor.py
git commit -m "feat(api): add ML prediction endpoint and store last prediction"
```

---

### Task 12: Frontend — Level Prediction Panel

**Files:**
- Modify: `frontend/src/types/market.ts`
- Modify: `frontend/src/components/Terminal/pages/L2Page.tsx` (or ContextSidebar)

**Context:** Add the `MlPrediction` type and a small panel showing the latest prediction. Read the L2Page and ContextSidebar components to find the right integration point. Follow existing UI patterns (retro terminal style, compact layout).

- [ ] **Step 1: Read L2Page.tsx and identify integration point**

Read `frontend/src/components/Terminal/pages/L2Page.tsx` and any sidebar/context component to find where to add the prediction panel.

- [ ] **Step 2: Add MlPrediction type**

In `frontend/src/types/market.ts`:

```typescript
export interface MlPrediction {
  level: string;
  predicted: string;
  confidence: number;
  probabilities: Record<string, number>;
  timestamp: number;
}
```

- [ ] **Step 3: Add prediction panel component**

Create a small inline component (not a separate file — follow existing patterns). Shows:
- Level name + predicted outcome
- Confidence bar (color-coded)
- 5-class probability distribution as small horizontal bars

The panel listens for `ml_prediction` SSE events on the existing market stream. Use the existing SSE hook pattern from the L2Page.

- [ ] **Step 4: Integrate into L2Page layout**

Add the prediction panel to the appropriate section of the L2 page (likely the context sidebar area).

- [ ] **Step 5: Test in browser**

Start frontend dev server, navigate to L2 tab, verify:
- Panel shows "No prediction" initially
- When a level is touched (or mock the SSE event), prediction appears with bars

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types/market.ts frontend/src/components/Terminal/pages/L2Page.tsx
git commit -m "feat(ui): add ML level prediction panel to L2 page"
```

---

### Task 13: SHAP Explainer — Per-Prediction Feature Importance

**Files:**
- Create: `backend/src/ml/level_touch/shap_explainer.py`

**Context:** After model is trained, use SHAP to explain individual predictions. Returns top N contributing features per prediction. This is called during live inference to populate the `top_features` field in the SSE event.

- [ ] **Step 1: Install shap package**

Run: `cd backend && pip install shap`

- [ ] **Step 2: Implement SHAP explainer**

Create `backend/src/ml/level_touch/shap_explainer.py`:

```python
"""SHAP-based feature importance for level touch predictions."""
import logging
import numpy as np

logger = logging.getLogger(__name__)

_explainer = None
_background_data = None


def init_explainer(model, X_train_sample: np.ndarray):
    """Initialize SHAP TreeExplainer with training data background."""
    global _explainer, _background_data
    try:
        import shap
        _background_data = X_train_sample[:100]  # subsample for speed
        _explainer = shap.TreeExplainer(model, _background_data)
        logger.info("SHAP explainer initialized")
    except Exception:
        logger.exception("Failed to initialize SHAP explainer")


def explain_prediction(
    features_encoded: np.ndarray,
    feature_names: list[str],
    predicted_class: int,
    top_n: int = 5,
) -> list[dict]:
    """Get top N contributing features for a prediction.

    Returns:
        List of {name: str, contribution: float} sorted by abs contribution.
    """
    if _explainer is None:
        return []

    try:
        shap_values = _explainer.shap_values(features_encoded.reshape(1, -1))

        # For multiclass, shap_values is list of arrays per class
        if isinstance(shap_values, list):
            class_shap = shap_values[predicted_class][0]
        else:
            class_shap = shap_values[0]

        # Get top N by absolute SHAP value
        abs_vals = np.abs(class_shap)
        top_indices = np.argsort(abs_vals)[-top_n:][::-1]

        return [
            {
                "name": feature_names[i] if i < len(feature_names) else f"feature_{i}",
                "contribution": round(float(class_shap[i]), 4),
            }
            for i in top_indices
        ]
    except Exception:
        logger.exception("SHAP explanation failed")
        return []
```

- [ ] **Step 3: Integrate into prediction flow**

In the LevelMonitor ML prediction code (Task 6), after getting prediction:
```python
from src.ml.level_touch.shap_explainer import explain_prediction
top_features = explain_prediction(
    features_encoded, FEATURE_NAMES, pred_result["class"], top_n=5
)
# Add to SSE event
```

- [ ] **Step 4: Commit**

```bash
git add backend/src/ml/level_touch/shap_explainer.py
git commit -m "feat(ml): add SHAP explainer for per-prediction feature importance"
```

---

## Execution Notes

- **Tasks 1-5** are foundational and sequential (each depends on prior)
- **Task 6** (LevelMonitor integration) is the critical integration point — test carefully
- **Task 7** (model training) can be developed in parallel with Task 6 once Tasks 1-3 are done
- **Task 9** (backfill) depends on Tasks 1-3 and 7
- **Tasks 10-12** (CLI, API, frontend) depend on core being done
- **Task 13** (SHAP) is independent and can be done any time after Task 7
- **Use LightGBM** (already in codebase) instead of XGBoost — same capabilities, follows existing patterns in `optimizer/trainer.py`
- The `compute_session_levels()` function signature may differ from what's shown — read the actual file before implementing `_compute_levels_for_date()`
