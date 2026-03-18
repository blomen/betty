# Level Touch ML Classifier — Design Spec

**Date:** 2026-03-18
**Instrument:** NQ futures only
**Model:** XGBoost multi-class classifier
**Goal:** Predict price continuation vs reversal when price touches a key structural level

---

## Overview

When price hits a structural level (VAH, VAL, POC, VWAP bands, PDH/PDL, IB, order blocks, etc.), the ML model predicts what happens next within a 30-minute window. The system collects features at the moment of touch, observes the outcome after 30 min, and trains on the labeled dataset.

## Target Variable

**5-class categorical**, measured within 30 minutes of level touch:

| Class | Definition |
|-------|-----------|
| `strong_reversal` | Price reverses ≥20 ticks from level |
| `weak_reversal` | Price reverses 8-19 ticks |
| `chop` | Price stays within ±7 ticks of the level |
| `weak_continuation` | Price pushes through 8-19 ticks |
| `strong_continuation` | Price pushes through ≥20 ticks |

"Reversal" = price moves back from the level in the direction it came from.
"Continuation" = price pushes through the level in the direction of approach.

---

## Section 1: Data Collection Layer

### 1a. Outcome Tracker

Extends `LevelMonitor`. On each `level_touched` event:

1. Records the touch: level name, price, timestamp, approach direction (from above/below)
2. Starts a 30-minute observation window (background asyncio task)
3. After 30 min, reads candles/ticks from that window and classifies:
   - Measures max excursion through the level (continuation) and max excursion away (reversal)
   - Applies the tick thresholds above
4. Writes the labeled outcome to `level_touch_outcomes` table

**Deduplication:** Same level touched multiple times within 30 min — only first touch counts. Subsequent touches within the observation window are ignored.

### 1b. Feature Extractor

At the moment of `level_touched`, snapshots all ~60 features into a `level_touch_features` table. Features and outcome are written independently — features at touch time, outcome 30 min later.

### 1c. New DB Tables

```sql
level_touch_outcomes:
    id                      INTEGER PRIMARY KEY,
    symbol                  TEXT NOT NULL,
    touch_ts                REAL NOT NULL,          -- epoch timestamp
    level_name              TEXT NOT NULL,
    level_type              TEXT NOT NULL,
    level_price             REAL NOT NULL,
    approach_direction      TEXT NOT NULL,           -- "from_above" or "from_below"
    outcome                 TEXT,                    -- NULL until 30 min later
    max_continuation_ticks  REAL,
    max_reversal_ticks      REAL,
    outcome_measured_at     REAL,
    session_date            TEXT NOT NULL,
    is_backfill             BOOLEAN DEFAULT FALSE

level_touch_features:
    id                      INTEGER PRIMARY KEY,
    touch_outcome_id        INTEGER REFERENCES level_touch_outcomes(id),
    -- ~60 feature columns (see Section 2)
```

---

## Section 2: Feature Set (~60 features)

### Level Metadata (7 features)

| Feature | Source | Description |
|---------|--------|-------------|
| `level_type` | MonitoredLevel | Categorical: poc, vah, val, vwap, vwap_1sd, vwap_2sd, vwap_3sd, pdh, pdl, ib_high, ib_low, order_block, fvg, naked_poc, tokyo, london, weekly, monthly |
| `level_category` | MonitoredLevel | band / prior / overnight / structure / session |
| `level_strength` | VP hierarchy | Weighted strength score from `compute_vp_hierarchy()` |
| `level_confluence` | VP hierarchy | Number of overlapping levels in cluster |
| `approach_direction` | Tick data | from_above (0) or from_below (1) |
| `distance_from_poc` | Session | Ticks between this level and current session POC |
| `distance_from_vwap` | Session | Ticks between this level and VWAP |

### Orderflow Snapshot (18 features)

Directly from `compute_signals()` at touch time:

| Feature | Type | Description |
|---------|------|-------------|
| `delta` | int | Net buy-sell over last 10 candles |
| `delta_aligned` | bool | Delta matches approach direction |
| `delta_divergence` | bool | Price vs delta disagree |
| `delta_unwind` | bool | Sign flip at extreme |
| `cvd` | int | Cumulative volume delta |
| `cvd_trend` | cat | rising / falling / flat |
| `vsa_absorption` | bool | High vol + narrow body |
| `tick_vol_accelerating` | bool | Tick count accelerating |
| `trapped_traders` | bool | Delta unwind + high vol |
| `passive_active_ratio` | float | Limit vs market order ratio |
| `big_trades_count` | int | Ticks >= 3x median vol |
| `big_trades_net_delta` | int | Net direction of big trades |
| `stop_run_detected` | bool | Spike + reversal pattern |
| `imbalance_ratio_max` | float | Strongest diagonal imbalance |
| `stacked_imbalance_count` | int | Consecutive imbalanced levels |
| `stacked_imbalance_direction` | cat | buy / sell / neutral |
| `last_candle_delta` | int | Delta of most recent candle only |
| `last_candle_body_ratio` | float | Body/spread of last candle |

### Temporal Derivatives (10 features)

Captures how signals evolved on approach, not just their value at touch.

| Feature | Description |
|---------|-------------|
| `delta_slope_5m` | Linear regression slope of per-candle delta over last 5 candles |
| `delta_slope_10m` | Same over last 10 candles |
| `cvd_acceleration` | CVD last 5 candles minus CVD prior 5 candles |
| `volume_roc_5m` | Volume rate of change: (vol last 5) / (vol prior 5) |
| `tick_rate_roc` | Tick count acceleration: recent 3 / prior 3 |
| `spread_compression` | Avg candle spread last 3 / avg spread last 10 (< 1 = narrowing) |
| `time_to_level_seconds` | Time from APPROACHING to AT_LEVEL |
| `price_velocity` | Price change per minute over last 5 min |
| `absorption_building` | Count of candles in last 10 with body_ratio < 0.3 |
| `imbalance_trend` | Stacked imbalance count last 5 vs prior 5 |

### Session Context (15 features)

| Feature | Source | Description |
|---------|--------|-------------|
| `market_type` | SessionAnalysis | balanced / trending_up / trending_down |
| `opening_type` | SessionAnalysis | OD / OTD / ORR / OA |
| `ib_range` | Session | IB high - IB low (ticks) |
| `ib_range_vs_aspr` | Session | IB range / ASPR (expansion indicator) |
| `aspr_percentile` | SessionMetric | Where today's volatility ranks historically |
| `rotation_factor` | SessionMetric | Directional bias of 30-min periods |
| `value_migration` | Session | up / down / overlapping |
| `price_vs_vah` | Computed | Current price - VAH (ticks, signed) |
| `price_vs_val` | Computed | Current price - VAL (ticks) |
| `price_vs_poc` | Computed | Current price - POC (ticks) |
| `price_in_value_area` | Computed | Boolean: price between VAL and VAH |
| `session_elapsed_pct` | Clock | % of RTH session elapsed (0-100) |
| `minutes_since_open` | Clock | Minutes since 09:30 ET |
| `developing_poc_direction` | levels.py | POC migrating up / down / flat |
| `prior_touch_outcome` | DB | Outcome of last touch on this same level today |

### Macro Context (5 features)

| Feature | Source | Description |
|---------|--------|-------------|
| `vix_level` | Macro snapshot | Current VIX |
| `vix_change` | Macro snapshot | VIX % change today |
| `regime` | Macro snapshot | risk_on / risk_off / mixed |
| `regime_score` | Macro snapshot | -1.0 to +1.0 |
| `macro_bias` | MarketContext | bull / bear / neutral |

### Candle Pattern at Touch (5 features)

| Feature | Description |
|---------|-------------|
| `last_3_candles_direction` | Count of up candles in last 3 (0-3) |
| `last_candle_is_doji` | body_ratio < 0.1 |
| `consecutive_same_direction` | How many candles in a row moved same way |
| `highest_volume_candle_position` | Which of last 10 candles had peak volume (0=oldest, 9=newest) |
| `range_expansion` | Last candle spread / avg spread last 10 |

---

## Section 3: Backfill Pipeline

### Process (per historical session date)

```
For each date in historical range:
    1. Fetch 1m candles for that RTH session (09:30-16:00 ET)
    2. Recompute session analysis:
       - Volume profile → POC/VAH/VAL
       - VWAP bands
       - Session levels (PDH/PDL, IB, Tokyo, London)
       - Order blocks, FVGs, swing points
       - Naked POCs from prior sessions
    3. Build level set (same logic as LevelMonitor.load_levels())
    4. Walk through candles chronologically:
       a. For each candle, check if price crossed any level
       b. On cross → "virtual touch" event
       c. Extract features from candles available UP TO that point (no lookahead)
       d. Look ahead 30 min of candles → classify outcome
       e. Write feature row + outcome row to DB
    5. Deduplicate: same level can only be touched once per 30-min window
```

### Backfill vs Live Feature Availability

| Aspect | Live | Backfill |
|--------|------|----------|
| Tick-level orderflow | Full (per-tick delta, footprint) | Approximated from 1m OHLCV |
| Diagonal/stacked imbalances | Exact | Not available (null) |
| Big trades, passive/active ratio | Exact | Not available (null) |
| Time-to-level | Exact (tick timestamps) | Estimated from candle timestamps |
| Book data (bid/ask) | Available | Not available |

~8 features will be null for backfill data. XGBoost handles missing values natively. Each row flagged with `is_backfill: bool`.

### Optional: Databento Tick Re-fetch

Second pass for higher-quality backfill:
1. Identify dates with most level touches from candle-based backfill
2. Fetch MBP-1 + trades from Databento for those dates
3. Rebuild CandleFlow with full footprint data
4. Re-extract features with complete orderflow signals

### CLI Command

```bash
python -m src.app ml backfill --start 2025-01-01 --end 2026-03-18 --symbol NQ
```

### Expected Volume

~10-20 level touches per session × ~300 trading days = **3,000-6,000 labeled samples** from candle data alone.

---

## Section 4: Model Training, Evaluation & Live Inference

### 4a. Training Pipeline

```
load_features_and_outcomes()     # Join features + outcomes from DB
    ↓
preprocess()                     # One-hot categoricals, handle nulls, scale numerics
    ↓
train_test_split()               # 80/20 temporal split (train on older, test on newer)
    ↓
XGBClassifier.fit()              # 5-class multi-class
    ↓
evaluate()                       # Confusion matrix, per-class precision/recall, SHAP
    ↓
save_model()                     # backend/data/models/level_classifier.joblib
```

**Key decisions:**
- **Temporal split** — always train on older data, validate on newer. No random shuffle.
- **Class weighting** — `sample_weight` to balance (chop will be overrepresented).
- **Hyperparameter tuning** — Optuna with 5-fold time-series cross-validation (expanding window).
- **Feature importance** — SHAP values exported to `backend/data/models/feature_importance.json`.

### 4b. Evaluation Metrics

| Metric | Purpose |
|--------|---------|
| Accuracy per class | Per-category prediction quality |
| Confusion matrix | Where does the model confuse categories? |
| Precision on strong_rev + strong_cont | Actionable signals — false positives cost money |
| "Actionable accuracy" | When model says strong_rev or strong_cont, how often correct? |
| Backfill vs live accuracy | Does backfill-trained model generalize to live? |

**Stored per training run:**

```sql
ml_training_runs:
    id, trained_at, model_version,
    n_samples, n_backfill, n_live,
    accuracy, precision_strong, recall_strong,
    confusion_matrix_json, feature_importance_json,
    hyperparams_json, model_path
```

### 4c. Retraining

- **Manual:** `python -m src.app ml train --symbol NQ`
- **Auto trigger:** When live samples exceed 20% of training set size, log recommendation (no auto-retrain).
- Each run produces a versioned model file. Old models kept for comparison.

### 4d. Live Inference

Hooks into `LevelMonitor` flow:

```
level_touched event
    ↓
Feature extractor snapshots ~60 features
    ↓
Model.predict_proba(features)
    → {strong_rev: 0.05, weak_rev: 0.15, chop: 0.40, weak_cont: 0.30, strong_cont: 0.10}
    ↓
Emit SSE: "ml_prediction" with class, confidence, probabilities, top features
    ↓
Store prediction in level_touch_features row for accuracy tracking
```

**Confidence threshold:** Only surface predictions with confidence > 0.4. Below that, emit "uncertain".

### 4e. API & Frontend

**New endpoint:**
- `GET /api/trading/market/ml/prediction` — latest prediction with probabilities

**SSE event** (on existing `/api/trading/market/stream`):
```json
{
    "type": "ml_prediction",
    "data": {
        "level": "VAH_daily",
        "predicted": "strong_reversal",
        "confidence": 0.62,
        "probabilities": {
            "strong_rev": 0.62, "weak_rev": 0.18,
            "chop": 0.12, "weak_cont": 0.05, "strong_cont": 0.03
        },
        "top_features": [
            {"name": "vsa_absorption", "contribution": 0.15},
            {"name": "delta_divergence", "contribution": 0.12}
        ]
    }
}
```

**Frontend:** Add a Level Prediction panel to the existing L2 page ContextSidebar showing prediction, confidence bar, probability distribution, and top contributing features as a small bar chart.

### 4f. File Structure

```
backend/src/ml/
├── __init__.py
├── features.py            # extract_level_touch_features()
├── outcomes.py            # OutcomeTracker (30-min delayed classification)
├── backfill.py            # Historical replay pipeline
├── level_classifier.py    # Train, evaluate, predict (XGBoost wrapper)
└── shap_explainer.py      # SHAP feature importance per prediction

backend/data/models/
├── level_classifier_v1.joblib
└── feature_importance.json
```

---

## Dependencies

New Python packages:
- `xgboost` — gradient boosted tree classifier
- `shap` — feature importance explanations
- `optuna` — hyperparameter tuning
- `scikit-learn` — preprocessing, metrics, train/test split
- `joblib` — model serialization (comes with scikit-learn)
