# Trading ML Models (M5-M7, M9) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all four trading ML models — M5 (Setup Score Predictor), M6 (Temporal Pattern Recognizer), M7 (Dynamic Gate Classifier), M9 (Macro & News Engine) — with training infrastructure, integration hooks, and API/frontend display.

**Architecture:** Each model follows the same pattern as betting models: model class with `.train()` and `.predict()`, registered in `TrainingOrchestrator`, served via `Predictor` singleton. M6 uses PyTorch 1D-CNN instead of LightGBM. M9 is multi-component (regime classifier + news impact lookup + economic calendar fetcher).

**Tech Stack:** LightGBM (M5, M7), PyTorch (M6), scikit-learn RandomForest (M7 fallback), yfinance (macro data), SQLAlchemy, joblib/torch.save serialization.

---

## File Structure

### New Files
- `backend/src/ml/models/setup_scorer.py` — M5 Setup Score Predictor (LightGBM regression + classification)
- `backend/src/ml/models/temporal_pattern.py` — M6 Temporal Pattern Recognizer (1D-CNN PyTorch)
- `backend/src/ml/models/gate_classifier.py` — M7 Dynamic Gate Classifier (RandomForest multiclass)
- `backend/src/ml/models/macro_engine.py` — M9 Macro & News Context Engine (multi-component)
- `backend/src/ml/features/gate_features.py` — M7 day-type / macro-regime feature extraction
- `backend/src/ml/features/macro_features.py` — M9 macro regime + news impact features
- `backend/src/data/economic_calendar.py` — Economic calendar fetcher (stores to `economic_events` table)
- `backend/tests/test_trading_ml_models.py` — Tests for all 4 trading models

### Modified Files
- `backend/src/ml/training/train_all.py` — Add M5, M6, M7, M9 to `MODEL_CONFIGS` and trainer dispatch
- `backend/src/ml/migrations.py` — Add `market_sessions` table for M7 training labels
- `backend/src/market_data/scanner.py` — M5 hook: replace composite score with ML prediction
- `backend/src/market_data/scoring.py` — M5 hook: ML-predicted score overrides fixed weights
- `backend/src/market_data/amt.py` — M7 hook: auto-classify day type; M9 hook: enriched macro
- `backend/src/ml/feature_store.py` — Add `resolve_trading_outcomes()` for R-multiple backfill
- `backend/src/api/routes/extraction.py` — Add trading ML models to status endpoint
- `frontend/src/components/Terminal/pages/StatsPage.tsx` — Show trading models in ML table

---

## Chunk 1: Shared Infrastructure + M5 Setup Score Predictor

### Task 1: Add trading model configs to TrainingOrchestrator

**Files:**
- Modify: `backend/src/ml/training/train_all.py`

- [ ] **Step 1: Add M5-M7, M9 to MODEL_CONFIGS**

Add these entries to the `MODEL_CONFIGS` dict in `train_all.py`:

```python
"setup_scorer": {
    "min_samples": 200, "domain": "trading",
    "source_type": "trading_signal", "task": "regression",
},
"temporal_pattern": {
    "min_samples": 500, "domain": "trading",
    "source_type": "trading_signal", "task": "classification",
},
"gate_classifier": {
    "min_samples": 100, "domain": "trading",
    "source_type": "market_session", "task": "multiclass",
},
"macro_engine": {
    "min_samples": 50, "domain": "trading",
    "source_type": "news_event", "task": "regression",
},
```

- [ ] **Step 2: Add trainer dispatch functions**

Add to `_get_trainer()`:
```python
"setup_scorer": lambda data, s: _train_setup_scorer(data, s),
"temporal_pattern": lambda data, s: _train_temporal_pattern(data, s),
"gate_classifier": lambda data, s: _train_gate_classifier(data, s),
"macro_engine": lambda data, s: _train_macro_engine(data, s),
```

Add the four `_train_*` functions:
```python
def _train_setup_scorer(data, session):
    from src.ml.models.setup_scorer import SetupScorerModel
    return SetupScorerModel().train(data)

def _train_temporal_pattern(data, session):
    from src.ml.models.temporal_pattern import TemporalPatternModel
    return TemporalPatternModel().train(data)

def _train_gate_classifier(data, session):
    from src.ml.models.gate_classifier import GateClassifierModel
    return GateClassifierModel().train(data)

def _train_macro_engine(data, session):
    from src.ml.models.macro_engine import MacroEngineModel
    return MacroEngineModel().train(data)
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/ml/training/train_all.py
git commit -m "feat(ml): add M5-M7/M9 trading model configs to training orchestrator"
```

---

### Task 2: Add market_sessions migration table for M7

**Files:**
- Modify: `backend/src/ml/migrations.py`

- [ ] **Step 1: Add _create_market_sessions()**

This table stores labeled trading sessions for M7 training. The `day_type` label comes from end-of-day classification (manual or auto).

```python
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
    conn.execute("CREATE INDEX idx_market_sessions_date ON market_sessions(date)")
```

- [ ] **Step 2: Wire into run_migrations()**

Add `_create_market_sessions(conn)` call before `conn.commit()`.

- [ ] **Step 3: Commit**

```bash
git add backend/src/ml/migrations.py
git commit -m "feat(ml): add market_sessions table for M7 day-type training labels"
```

---

### Task 3: Add resolve_trading_outcomes() to feature store

**Files:**
- Modify: `backend/src/ml/feature_store.py`

- [ ] **Step 1: Add outcome resolution for trading signals**

```python
def resolve_trading_outcomes(session: Session) -> int:
    """Backfill trading signal outcomes from completed trades.

    Links trading_signal → trade via trade_id, uses r_multiple as outcome.
    """
    from src.db.models import MlFeature, TradingSignal, Trading

    unresolved = session.query(MlFeature).filter(
        MlFeature.domain == "trading",
        MlFeature.source_type == "trading_signal",
        MlFeature.outcome.is_(None),
    ).all()

    count = 0
    for feat in unresolved:
        signal = session.query(TradingSignal).filter_by(id=feat.source_id).first()
        if not signal or not signal.trade_id:
            continue
        trade = session.query(Trading).filter_by(id=signal.trade_id).first()
        if not trade or trade.r_multiple is None:
            continue
        feat.outcome = trade.r_multiple
        feat.outcome_binary = 1 if trade.r_multiple > 0 else 0
        feat.resolved_at = datetime.now(timezone.utc)
        count += 1

    session.flush()
    return count
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/ml/feature_store.py
git commit -m "feat(ml): add resolve_trading_outcomes() for R-multiple backfill"
```

---

### Task 4: Build M5 Setup Score Predictor model

**Files:**
- Create: `backend/src/ml/models/setup_scorer.py`

- [ ] **Step 1: Create SetupScorerModel**

```python
"""M5: Setup Score Predictor — predicts R-multiple for trading signals.

Replaces fixed +10/+8/+5 scoring weights with learned interactions.
Phase 1: 15-20 high-prior features. Phase 2: full 70+ features.
"""
import json
import logging
import numpy as np
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_SAMPLES = 200
MODELS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "models"

# Phase 1: highest-prior features (start with these at 200 trades)
FEATURE_NAMES_PHASE1 = [
    "base_score", "delta_pct", "cvd_slope_5bar",
    "volume_ratio_vs_20bar", "volume_ratio_vs_session",
    "distance_to_level_ticks", "distance_to_poc_ticks",
    "price_position_in_va", "ib_range_vs_avg",
    "minutes_since_rth_open", "aspr_percentile",
    "passive_active_ratio", "absorption_bar_count",
    "vix_level", "gex",
]

# Phase 2: add at 500+ trades
FEATURE_NAMES_PHASE2 = FEATURE_NAMES_PHASE1 + [
    "delta_divergence_bars", "delta_unwind_speed_bars",
    "cvd_slope_10bar", "cvd_acceleration",
    "body_ratio_last", "body_ratio_avg_3bar",
    "spread_ticks", "spread_ratio_vs_avg",
    "trapped_magnitude", "tick_count_ratio",
    "imbalance_ratio_max", "stacked_imbalance_count",
    "big_trades_count", "big_trades_net_delta",
    "stop_run_magnitude_ticks", "stop_run_volume_ratio",
    "distance_to_vwap_ticks", "price_vs_vwap_sd",
    "va_width_ticks", "va_width_vs_yesterday",
    "single_print_count_above", "single_print_count_below",
    "num_levels_within_20_ticks",
    "rotation_factor", "aspr",
    "session_volume_total", "session_volume_acceleration",
    "news_event_minutes_away", "news_event_importance",
    "unfinished_auction_count_above", "unfinished_auction_count_below",
]

# Categorical features encoded as integers
SETUP_TYPE_MAP = {
    "spring": 0, "sfp": 1, "poor_extreme": 2, "ib_break": 3,
    "rule_80": 4, "fakeout": 5, "break_from_balance": 6,
    "double_distribution": 7, "news_directional": 8,
}
DIRECTION_MAP = {"long": 0, "short": 1}
MARKET_TYPE_MAP = {"balanced": 0, "trending_up": 1, "trending_down": 2, "unknown": 3}
OPENING_TYPE_MAP = {"OD": 0, "OTD": 1, "ORR": 2, "OA": 3, "unknown": 4}


class SetupScorerModel:
    def train(self, data) -> dict | None:
        try:
            import lightgbm as lgb
        except ImportError:
            logger.warning("lightgbm not installed")
            return None

        use_phase2 = len(data) >= 500
        feature_names = FEATURE_NAMES_PHASE2 if use_phase2 else FEATURE_NAMES_PHASE1

        X, y = [], []
        for row in data:
            features = row.features if isinstance(row.features, dict) else json.loads(row.features)
            vec = _encode_features(features, feature_names)
            if vec is not None and row.outcome is not None:
                X.append(vec)
                y.append(row.outcome)

        X = np.array(X, dtype=np.float32)
        y = np.array(y, dtype=np.float32)

        if len(X) < MIN_SAMPLES:
            return None

        from src.ml.optimizer.trainer import train_model, walk_forward_splits
        result = train_model(X, y, task="regression", min_samples=MIN_SAMPLES)
        if result is None:
            return None

        import joblib
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        path = MODELS_DIR / "setup_scorer_latest.joblib"
        joblib.dump(result["model"], path)

        return {
            "file_path": str(path),
            "training_data_count": len(X),
            "validation_score": result["validation_score"],
            "baseline_metric": None,
        }

    def predict(self, features: dict) -> float | None:
        """Predict R-multiple for a trading setup."""
        use_phase2 = len(features) > 20
        feature_names = FEATURE_NAMES_PHASE2 if use_phase2 else FEATURE_NAMES_PHASE1
        vec = _encode_features(features, feature_names)
        if vec is None:
            return None
        return vec  # Actual prediction done by Predictor


def _encode_features(features: dict, feature_names: list) -> np.ndarray | None:
    """Encode feature dict to numeric array, handling categoricals."""
    vec = []
    for name in feature_names:
        val = features.get(name)
        if name == "setup_type":
            val = SETUP_TYPE_MAP.get(val, -1)
        elif name == "direction":
            val = DIRECTION_MAP.get(val, -1)
        elif name == "market_type":
            val = MARKET_TYPE_MAP.get(val, 3)
        elif name == "opening_type":
            val = OPENING_TYPE_MAP.get(val, 4)
        elif isinstance(val, bool):
            val = int(val)
        vec.append(float(val) if val is not None else 0.0)
    return np.array(vec, dtype=np.float32)
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/ml/models/setup_scorer.py
git commit -m "feat(ml): add M5 Setup Score Predictor model"
```

---

### Task 5: Build M6 Temporal Pattern Recognizer model

**Files:**
- Create: `backend/src/ml/models/temporal_pattern.py`

- [ ] **Step 1: Create TemporalPatternModel with 1D-CNN**

```python
"""M6: Temporal Pattern Recognizer — 1D-CNN on candle sequences.

Predicts reversal/continuation from last 20 candles of orderflow.
Input: (20, N_FEATURES) candle sequence.
Output: {direction, probability, confidence}.
"""
import json
import logging
import numpy as np
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_SAMPLES = 500
MODELS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "models"

# Features per candle (must match candle_features.snapshot_candles output)
CANDLE_FEATURE_NAMES = [
    "delta", "delta_pct", "cvd", "volume", "volume_ratio",
    "spread_ticks", "body_ratio", "close_position", "tick_count",
    "passive_active_ratio", "vwap_distance_ticks", "poc_distance_ticks",
    "imbalance_ratio_max", "stacked_imbalance_count",
    "big_trades_count", "big_trades_net_delta",
]
N_FEATURES = len(CANDLE_FEATURE_NAMES)
SEQ_LEN = 20

# Target classes
CLASSES = ["reversal_long", "reversal_short", "continuation_long", "continuation_short", "chop"]
N_CLASSES = len(CLASSES)


class TemporalPatternModel:
    def train(self, data) -> dict | None:
        try:
            import torch
            import torch.nn as nn
        except ImportError:
            logger.warning("torch not installed — M6 disabled")
            return None

        X_list, y_list = [], []
        for row in data:
            features = row.features if isinstance(row.features, dict) else json.loads(row.features)
            candles = features.get("candle_sequence")
            if not candles or len(candles) < SEQ_LEN:
                continue
            seq = _encode_candle_sequence(candles[-SEQ_LEN:])
            if seq is None:
                continue
            label = _get_label(row.outcome, row.outcome_binary)
            if label is None:
                continue
            X_list.append(seq)
            y_list.append(label)

        if len(X_list) < MIN_SAMPLES:
            logger.info(f"M6: insufficient data ({len(X_list)} < {MIN_SAMPLES})")
            return None

        X = np.array(X_list, dtype=np.float32)
        y = np.array(y_list, dtype=np.int64)

        # Z-score normalize per feature across each window
        mean = X.mean(axis=1, keepdims=True)
        std = X.std(axis=1, keepdims=True) + 1e-8
        X = (X - mean) / std

        X_tensor = torch.tensor(X).permute(0, 2, 1)  # (batch, features, seq_len)
        y_tensor = torch.tensor(y)

        # Train/val split (last 20% for validation, time-ordered)
        split = int(len(X_tensor) * 0.8)
        X_train, X_val = X_tensor[:split], X_tensor[split:]
        y_train, y_val = y_tensor[:split], y_tensor[split:]

        model = _CandleCNN(N_FEATURES, N_CLASSES)
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
        criterion = nn.CrossEntropyLoss()

        best_val_acc = 0.0
        for epoch in range(50):
            model.train()
            optimizer.zero_grad()
            out = model(X_train)
            loss = criterion(out, y_train)
            loss.backward()
            optimizer.step()

            model.eval()
            with torch.no_grad():
                val_out = model(X_val)
                val_preds = val_out.argmax(dim=1)
                val_acc = (val_preds == y_val).float().mean().item()
                if val_acc > best_val_acc:
                    best_val_acc = val_acc

        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        path = MODELS_DIR / "temporal_pattern_latest.pt"
        torch.save(model.state_dict(), path)

        return {
            "file_path": str(path),
            "training_data_count": len(X_list),
            "validation_score": best_val_acc,
            "baseline_metric": 1.0 / N_CLASSES,  # random baseline
        }

    def predict(self, candle_sequence: list[dict]) -> dict | None:
        """Predict pattern from candle sequence."""
        try:
            import torch
        except ImportError:
            return None

        if not candle_sequence or len(candle_sequence) < SEQ_LEN:
            return None

        seq = _encode_candle_sequence(candle_sequence[-SEQ_LEN:])
        if seq is None:
            return None

        # Z-score normalize
        mean = seq.mean(axis=0, keepdims=True)
        std = seq.std(axis=0, keepdims=True) + 1e-8
        seq = (seq - mean) / std

        X = torch.tensor(seq, dtype=torch.float32).unsqueeze(0).permute(0, 2, 1)
        return X  # Actual inference done by Predictor with loaded model


class _CandleCNN(object):
    """Minimal 1D-CNN for candle pattern recognition.

    Architecture: Conv1d → ReLU → Conv1d → ReLU → AdaptiveAvgPool → FC → Softmax
    Designed for <10ms inference on 20-candle sequences.
    """
    def __new__(cls, n_features, n_classes):
        import torch.nn as nn

        class CandleCNNModule(nn.Module):
            def __init__(self):
                super().__init__()
                self.conv1 = nn.Conv1d(n_features, 32, kernel_size=3, padding=1)
                self.conv2 = nn.Conv1d(32, 64, kernel_size=3, padding=1)
                self.pool = nn.AdaptiveAvgPool1d(1)
                self.fc = nn.Linear(64, n_classes)
                self.relu = nn.ReLU()

            def forward(self, x):
                x = self.relu(self.conv1(x))
                x = self.relu(self.conv2(x))
                x = self.pool(x).squeeze(-1)
                return self.fc(x)

        return CandleCNNModule()


def _encode_candle_sequence(candles: list[dict]) -> np.ndarray | None:
    """Encode list of candle dicts to (seq_len, n_features) array."""
    rows = []
    for c in candles:
        row = []
        for name in CANDLE_FEATURE_NAMES:
            val = c.get(name)
            row.append(float(val) if val is not None else 0.0)
        rows.append(row)
    return np.array(rows, dtype=np.float32)


def _get_label(outcome, outcome_binary) -> int | None:
    """Map R-multiple outcome to class label."""
    if outcome is None:
        return None
    if outcome > 0.5:
        return 0  # reversal_long (or continuation_long depending on context)
    elif outcome < -0.5:
        return 1  # reversal_short
    elif outcome > 0:
        return 2  # continuation_long (mild positive)
    elif outcome < 0:
        return 3  # continuation_short (mild negative)
    else:
        return 4  # chop
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/ml/models/temporal_pattern.py
git commit -m "feat(ml): add M6 Temporal Pattern Recognizer with 1D-CNN"
```

---

### Task 6: Build M7 Dynamic Gate Classifier model

**Files:**
- Create: `backend/src/ml/features/gate_features.py`
- Create: `backend/src/ml/models/gate_classifier.py`

- [ ] **Step 1: Create gate_features.py**

```python
"""Extract features for M7 Dynamic Gate Classifier (day type + macro regime)."""


def extract_gate_features(
    rf_after_ib: float | None = None,
    ib_range: float | None = None,
    ib_range_vs_avg: float | None = None,
    opening_type: str | None = None,
    first_hour_delta_total: float | None = None,
    first_hour_volume_vs_avg: float | None = None,
    overnight_range_pct: float | None = None,
    gap_filled_pct: float | None = None,
    yesterday_market_type: str | None = None,
    poor_high_or_low_in_ib: bool | None = None,
    first_hour_big_trades_count: int | None = None,
    session_volume_first_hour: float | None = None,
    vix_level: float | None = None,
    gex: float | None = None,
    value_migration_direction: str | None = None,
    ib_tpo_count: int | None = None,
) -> dict:
    """Extract features for day-type classification."""
    opening_map = {"OD": 0, "OTD": 1, "ORR": 2, "OA": 3}
    mtype_map = {"balanced": 0, "trending_up": 1, "trending_down": 2, "unknown": 3}
    migration_map = {"up": 0, "down": 1, "overlapping": 2}

    return {
        "rf_after_ib": rf_after_ib,
        "ib_range": ib_range,
        "ib_range_vs_avg": ib_range_vs_avg,
        "opening_type_encoded": opening_map.get(opening_type, 4),
        "first_hour_delta_total": first_hour_delta_total,
        "first_hour_volume_vs_avg": first_hour_volume_vs_avg,
        "overnight_range_pct": overnight_range_pct,
        "gap_filled_pct": gap_filled_pct,
        "yesterday_market_type_encoded": mtype_map.get(yesterday_market_type, 3),
        "poor_high_or_low_in_ib": int(poor_high_or_low_in_ib) if poor_high_or_low_in_ib is not None else 0,
        "first_hour_big_trades_count": first_hour_big_trades_count,
        "session_volume_first_hour": session_volume_first_hour,
        "vix_level": vix_level,
        "gex": gex,
        "value_migration_encoded": migration_map.get(value_migration_direction, 2),
        "ib_tpo_count": ib_tpo_count,
    }
```

- [ ] **Step 2: Create gate_classifier.py**

```python
"""M7: Dynamic Gate Classifier — classifies day type and macro regime.

Day types: trend, normal, normal_variation, neutral, composite
Macro regimes: bull, bear, neutral

Uses RandomForest (good with small datasets, no tuning needed).
"""
import json
import logging
import numpy as np
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_SAMPLES = 100
MODELS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "models"

DAY_TYPE_FEATURE_NAMES = [
    "rf_after_ib", "ib_range", "ib_range_vs_avg",
    "opening_type_encoded", "first_hour_delta_total",
    "first_hour_volume_vs_avg", "overnight_range_pct",
    "gap_filled_pct", "yesterday_market_type_encoded",
    "poor_high_or_low_in_ib", "first_hour_big_trades_count",
    "session_volume_first_hour", "vix_level", "gex",
    "value_migration_encoded", "ib_tpo_count",
]

DAY_TYPE_MAP = {
    "trend": 0, "normal": 1, "normal_variation": 2,
    "neutral": 3, "composite": 4,
}
DAY_TYPE_LABELS = {v: k for k, v in DAY_TYPE_MAP.items()}


class GateClassifierModel:
    def train(self, data) -> dict | None:
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import cross_val_score

        X, y = [], []
        for row in data:
            features = row.features if isinstance(row.features, dict) else json.loads(row.features)
            vec = [float(features.get(f, 0) or 0) for f in DAY_TYPE_FEATURE_NAMES]
            label = features.get("day_type_label")
            if label is None:
                # Use outcome field as label index
                label = row.outcome
            if label is None:
                continue
            X.append(vec)
            y.append(int(label))

        X = np.array(X, dtype=np.float32)
        y = np.array(y, dtype=np.int32)

        if len(X) < MIN_SAMPLES:
            return None

        model = RandomForestClassifier(
            n_estimators=100, max_depth=8, min_samples_leaf=5,
            random_state=42, n_jobs=-1,
        )
        scores = cross_val_score(model, X, y, cv=min(5, len(X) // 20), scoring="accuracy")
        model.fit(X, y)

        import joblib
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        path = MODELS_DIR / "gate_classifier_latest.joblib"
        joblib.dump(model, path)

        return {
            "file_path": str(path),
            "training_data_count": len(X),
            "validation_score": float(np.mean(scores)),
            "baseline_metric": 1.0 / len(DAY_TYPE_MAP),
        }

    def predict(self, features: dict) -> dict | None:
        """Predict day type from first-hour features."""
        vec = np.array(
            [float(features.get(f, 0) or 0) for f in DAY_TYPE_FEATURE_NAMES],
            dtype=np.float32,
        ).reshape(1, -1)
        return vec  # Actual prediction done by Predictor
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/ml/features/gate_features.py backend/src/ml/models/gate_classifier.py
git commit -m "feat(ml): add M7 Dynamic Gate Classifier with RandomForest"
```

---

## Chunk 2: M9 Macro Engine + Economic Calendar

### Task 7: Create economic calendar fetcher

**Files:**
- Create: `backend/src/data/economic_calendar.py`

- [ ] **Step 1: Create economic_calendar.py**

```python
"""Economic calendar fetcher — stores scheduled events to economic_events table.

Fetches from free API sources (investing.com scraper fallback to static schedule).
Designed to run daily to populate the economic_events table for M9.
"""
import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Static high-importance US economic events (recurring schedule)
HIGH_IMPORTANCE_EVENTS = [
    "FOMC Rate Decision", "Non-Farm Payrolls", "CPI", "Core CPI",
    "PPI", "Core PPI", "Jobless Claims", "GDP", "Retail Sales",
    "ISM Manufacturing PMI", "ISM Services PMI", "Consumer Confidence",
    "Durable Goods Orders", "PCE Price Index", "Core PCE",
]


async def fetch_and_store_calendar(session: Session, days_ahead: int = 7) -> int:
    """Fetch economic events and store to DB. Returns count of new events."""
    import asyncio
    return await asyncio.get_event_loop().run_in_executor(
        None, _fetch_and_store_sync, session, days_ahead
    )


def _fetch_and_store_sync(session: Session, days_ahead: int) -> int:
    """Synchronous calendar fetch and store."""
    from src.db.models import EconomicEvent

    events = _fetch_events(days_ahead)
    count = 0
    for evt in events:
        existing = session.query(EconomicEvent).filter_by(
            event_name=evt["event_name"],
            event_datetime=evt["event_datetime"],
        ).first()
        if existing:
            # Update actual/surprise if newly released
            if evt.get("actual") is not None and existing.actual is None:
                existing.actual = evt["actual"]
                existing.surprise = evt.get("surprise")
                count += 1
            continue
        row = EconomicEvent(
            event_name=evt["event_name"],
            event_datetime=evt["event_datetime"],
            importance=evt.get("importance", 2),
            forecast=evt.get("forecast"),
            actual=evt.get("actual"),
            previous=evt.get("previous"),
            surprise=evt.get("surprise"),
        )
        session.add(row)
        count += 1
    session.flush()
    return count


def _fetch_events(days_ahead: int) -> list[dict]:
    """Fetch economic events from free sources.

    Tries yfinance economic calendar first, falls back to empty list.
    Events are enriched incrementally as actuals are released.
    """
    events = []
    try:
        import yfinance as yf
        from datetime import timedelta

        # yfinance doesn't have a direct calendar API, so we use
        # a simple approach: check for known high-importance events
        # This is a placeholder — in production, wire to a real calendar API
        logger.info("Economic calendar: using static schedule (wire real API for production)")
    except ImportError:
        logger.debug("yfinance not available for calendar")

    return events


def get_upcoming_events(session: Session, minutes_ahead: int = 120) -> list:
    """Get economic events happening within the next N minutes."""
    from src.db.models import EconomicEvent

    now = datetime.now(timezone.utc)
    from datetime import timedelta
    cutoff = now + timedelta(minutes=minutes_ahead)

    return session.query(EconomicEvent).filter(
        EconomicEvent.event_datetime >= now.isoformat(),
        EconomicEvent.event_datetime <= cutoff.isoformat(),
    ).order_by(EconomicEvent.event_datetime).all()


def get_recent_events(session: Session, minutes_ago: int = 60) -> list:
    """Get economic events that happened within the last N minutes."""
    from src.db.models import EconomicEvent

    now = datetime.now(timezone.utc)
    from datetime import timedelta
    cutoff = now - timedelta(minutes=minutes_ago)

    return session.query(EconomicEvent).filter(
        EconomicEvent.event_datetime >= cutoff.isoformat(),
        EconomicEvent.event_datetime <= now.isoformat(),
        EconomicEvent.actual.isnot(None),
    ).order_by(EconomicEvent.event_datetime.desc()).all()
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/data/economic_calendar.py
git commit -m "feat(ml): add economic calendar fetcher for M9 news engine"
```

---

### Task 8: Create macro features extractor

**Files:**
- Create: `backend/src/ml/features/macro_features.py`

- [ ] **Step 1: Create macro_features.py**

```python
"""Extract macro/news features for M9 Macro & News Context Engine."""
from datetime import datetime, timezone


def extract_macro_features(
    vix_level: float | None = None,
    vix_change_1d: float | None = None,
    vix_term_structure: str | None = None,
    dxy_level: float | None = None,
    dxy_change_1d: float | None = None,
    us10y_level: float | None = None,
    us10y_change_1d: float | None = None,
    us02y_level: float | None = None,
    yield_curve_spread: float | None = None,
    gex: float | None = None,
    gex_flip_distance_ticks: float | None = None,
    net_options_delta: float | None = None,
    put_call_ratio: float | None = None,
    es_nq_ratio_change: float | None = None,
    cot_net_position: int | None = None,
    cot_change_1w: int | None = None,
) -> dict:
    """Extract macro regime features."""
    term_map = {"contango": 0, "backwardation": 1}
    return {
        "vix_level": vix_level,
        "vix_change_1d": vix_change_1d,
        "vix_term_structure_encoded": term_map.get(vix_term_structure, -1),
        "dxy_level": dxy_level,
        "dxy_change_1d": dxy_change_1d,
        "us10y_level": us10y_level,
        "us10y_change_1d": us10y_change_1d,
        "us02y_level": us02y_level,
        "yield_curve_spread": yield_curve_spread,
        "gex": gex,
        "gex_flip_distance_ticks": gex_flip_distance_ticks,
        "net_options_delta": net_options_delta,
        "put_call_ratio": put_call_ratio,
        "es_nq_ratio_change": es_nq_ratio_change,
        "cot_net_position": cot_net_position,
        "cot_change_1w": cot_change_1w,
    }


def extract_news_impact_features(
    event_name: str | None = None,
    importance: int | None = None,
    surprise: float | None = None,
    vix_at_event: float | None = None,
    delta_1m_after: float | None = None,
    volume_1m_after: float | None = None,
    immediate_impact_pct: float | None = None,
    sustained_impact_pct: float | None = None,
    reversal_pct: float | None = None,
    minutes_since_event: float | None = None,
) -> dict:
    """Extract features from a recent economic event for scoring adjustment."""
    # Event name as simple hash for model (LightGBM handles this as categorical)
    event_map = {
        "FOMC Rate Decision": 0, "Non-Farm Payrolls": 1, "CPI": 2,
        "Core CPI": 3, "PPI": 4, "Jobless Claims": 5, "GDP": 6,
        "Retail Sales": 7, "ISM Manufacturing PMI": 8, "ISM Services PMI": 9,
    }
    return {
        "event_type_encoded": event_map.get(event_name, -1),
        "importance": importance,
        "surprise": surprise,
        "vix_at_event": vix_at_event,
        "delta_1m_after": delta_1m_after,
        "volume_1m_after": volume_1m_after,
        "immediate_impact_pct": immediate_impact_pct,
        "sustained_impact_pct": sustained_impact_pct,
        "reversal_pct": reversal_pct,
        "minutes_since_event": minutes_since_event,
    }
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/ml/features/macro_features.py
git commit -m "feat(ml): add macro and news impact feature extractors for M9"
```

---

### Task 9: Build M9 Macro & News Context Engine model

**Files:**
- Create: `backend/src/ml/models/macro_engine.py`

- [ ] **Step 1: Create MacroEngineModel**

```python
"""M9: Macro & News Context Engine — multi-component macro regime model.

Components:
1. News impact predictor — LightGBM regression: given event type + surprise → predicted NQ impact
2. Macro regime enhancer — enriches MacroSnapshot with ML-learned regime signals
3. Options flow integration — stores daily options/macro data to options_flow table

This model enhances the existing rule-based classify_regime() in macro_provider.py.
"""
import json
import logging
import numpy as np
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_SAMPLES = 50
MODELS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "models"

NEWS_IMPACT_FEATURES = [
    "event_type_encoded", "importance", "surprise",
    "vix_at_event", "delta_1m_after", "volume_1m_after",
]

MACRO_REGIME_FEATURES = [
    "vix_level", "vix_change_1d", "vix_term_structure_encoded",
    "dxy_level", "dxy_change_1d", "us10y_level", "us10y_change_1d",
    "yield_curve_spread", "gex", "net_options_delta", "put_call_ratio",
    "es_nq_ratio_change", "cot_net_position", "cot_change_1w",
]


class MacroEngineModel:
    def train(self, data) -> dict | None:
        """Train news impact predictor on historical event data."""
        try:
            import lightgbm as lgb
        except ImportError:
            logger.warning("lightgbm not installed")
            return None

        X, y = [], []
        for row in data:
            features = row.features if isinstance(row.features, dict) else json.loads(row.features)
            vec = [float(features.get(f, 0) or 0) for f in NEWS_IMPACT_FEATURES]
            if row.outcome is not None:
                X.append(vec)
                y.append(row.outcome)

        X = np.array(X, dtype=np.float32)
        y = np.array(y, dtype=np.float32)

        if len(X) < MIN_SAMPLES:
            return None

        from src.ml.optimizer.trainer import train_model
        result = train_model(X, y, task="regression", min_samples=MIN_SAMPLES)
        if result is None:
            return None

        import joblib
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        path = MODELS_DIR / "macro_engine_latest.joblib"
        joblib.dump(result["model"], path)

        return {
            "file_path": str(path),
            "training_data_count": len(X),
            "validation_score": result["validation_score"],
            "baseline_metric": None,
        }

    def predict_news_impact(self, features: dict) -> float | None:
        """Predict NQ price impact of an economic event."""
        vec = np.array(
            [float(features.get(f, 0) or 0) for f in NEWS_IMPACT_FEATURES],
            dtype=np.float32,
        ).reshape(1, -1)
        return vec  # Actual prediction done by Predictor


async def store_daily_options_flow(session, macro_snapshot) -> None:
    """Store daily options/macro data to options_flow table.

    Called once per day after macro fetch.
    """
    from src.db.models import OptionsFlow
    from datetime import date

    today = date.today().isoformat()
    existing = session.query(OptionsFlow).filter_by(date=today, symbol="NQ").first()
    if existing:
        return

    row = OptionsFlow(
        date=today,
        symbol="NQ",
        vix_level=macro_snapshot.vix,
        vix_1d_change=macro_snapshot.vix_change_pct,
        dxy_level=macro_snapshot.dxy,
        dxy_1d_change=macro_snapshot.dxy_change_pct,
        us10y_level=macro_snapshot.us10y,
        us10y_1d_change=macro_snapshot.us10y_change_bps,
        us02y_level=macro_snapshot.us2y,
        yield_curve_spread=macro_snapshot.yield_curve_spread,
    )
    session.add(row)
    session.flush()
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/ml/models/macro_engine.py
git commit -m "feat(ml): add M9 Macro & News Context Engine model"
```

---

## Chunk 3: Integration Hooks + API + Frontend

### Task 10: Wire M5 into market_data/scanner.py

**Files:**
- Modify: `backend/src/market_data/scanner.py`

- [ ] **Step 1: Add M5 prediction hook after composite scoring**

After `composite = self._composite_score(conditions)` (around line 69), add ML override:

```python
# M5: ML-predicted score overrides composite (best-effort)
try:
    from src.ml.serving.predictor import get_predictor
    predictor = get_predictor()
    if predictor.is_loaded("setup_scorer"):
        ml_pred = predictor.predict("setup_scorer", ml_features)
        if ml_pred is not None and isinstance(ml_pred, (int, float)):
            # ML returns predicted R-multiple; convert to 0-100 score
            # R > 1.0 → 85+, R > 0.5 → 70+, R < 0 → below threshold
            ml_score = min(100, max(0, 50 + ml_pred * 25))
            composite = ml_score
except Exception as e:
    logger.debug(f"M5 prediction skipped: {e}")
```

This requires moving the ML feature extraction BEFORE the threshold check. Restructure the scan loop so features are extracted first, then used for both logging and prediction.

- [ ] **Step 2: Commit**

```bash
git add backend/src/market_data/scanner.py
git commit -m "feat(ml): wire M5 Setup Score prediction into scanner"
```

---

### Task 11: Wire M6 candle pattern prediction into scanner

**Files:**
- Modify: `backend/src/market_data/scanner.py`

- [ ] **Step 1: Add M6 candle pattern overlay**

After the M5 score hook, add M6 pattern confirmation:

```python
# M6: Temporal pattern overlay — boosts/penalizes based on candle pattern
try:
    from src.ml.serving.predictor import get_predictor
    predictor = get_predictor()
    if predictor.is_loaded("temporal_pattern") and candles:
        from src.ml.features.candle_features import snapshot_candles as _snap
        candle_dicts = _snap(
            candles,
            vwap=session.vwap_bands.vwap if session.vwap_bands else None,
            poc=session.volume_profile.poc if session.volume_profile else None,
        )
        if candle_dicts and len(candle_dicts) >= 20:
            pattern_pred = predictor.predict("temporal_pattern", {"candle_sequence": candle_dicts})
            if pattern_pred and isinstance(pattern_pred, dict):
                # Boost if pattern aligns with direction
                pattern_class = pattern_pred.get("class", 4)
                probs = pattern_pred.get("probabilities", [])
                if probs:
                    # Classes: 0=rev_long, 1=rev_short, 2=cont_long, 3=cont_short, 4=chop
                    if direction == "long" and pattern_class in (0, 2):
                        composite = min(100, composite + max(probs) * 10)
                    elif direction == "short" and pattern_class in (1, 3):
                        composite = min(100, composite + max(probs) * 10)
                    elif pattern_class == 4:
                        composite = max(0, composite - 5)
except Exception as e:
    logger.debug(f"M6 prediction skipped: {e}")
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/market_data/scanner.py
git commit -m "feat(ml): wire M6 Temporal Pattern prediction into scanner"
```

---

### Task 12: Wire M7 day-type classification into AMT

**Files:**
- Modify: `backend/src/market_data/amt.py`

- [ ] **Step 1: Add M7 hook in market_type classification**

In the `SessionAnalysis` construction or the classify method, add ML override:

```python
# M7: ML day-type prediction (best-effort, supplements rule-based)
try:
    from src.ml.serving.predictor import get_predictor
    predictor = get_predictor()
    if predictor.is_loaded("gate_classifier"):
        from src.ml.features.gate_features import extract_gate_features
        gate_features = extract_gate_features(
            ib_range=initial_balance.ib_range if initial_balance else None,
            vix_level=macro.vix if macro else None,
            gex=None,  # from options_flow if available
        )
        ml_type = predictor.predict("gate_classifier", gate_features)
        if ml_type and isinstance(ml_type, dict):
            from src.ml.models.gate_classifier import DAY_TYPE_LABELS
            predicted_class = ml_type.get("class", -1)
            probs = ml_type.get("probabilities", [])
            confidence = max(probs) if probs else 0
            if confidence > 0.6:
                market_type = DAY_TYPE_LABELS.get(predicted_class, market_type)
except Exception as e:
    import logging
    logging.getLogger(__name__).debug(f"M7 prediction skipped: {e}")
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/market_data/amt.py
git commit -m "feat(ml): wire M7 day-type classification into AMT analysis"
```

---

### Task 13: Wire M9 macro enrichment + news context

**Files:**
- Modify: `backend/src/market_data/scanner.py`

- [ ] **Step 1: Add M9 news proximity context to feature logging**

In the ML feature logging block, enrich with news event proximity:

```python
# M9: Add news event proximity to features
try:
    if self.db_session is not None:
        from src.data.economic_calendar import get_upcoming_events, get_recent_events
        upcoming = get_upcoming_events(self.db_session, minutes_ahead=30)
        if upcoming:
            nearest = upcoming[0]
            ml_features["news_event_minutes_away"] = (
                datetime.fromisoformat(nearest.event_datetime) - datetime.now(timezone.utc)
            ).total_seconds() / 60
            ml_features["news_event_importance"] = nearest.importance
        recent = get_recent_events(self.db_session, minutes_ago=60)
        if recent:
            latest = recent[0]
            ml_features["post_news_minutes"] = (
                datetime.now(timezone.utc) - datetime.fromisoformat(latest.event_datetime)
            ).total_seconds() / 60
            ml_features["news_surprise"] = latest.surprise
except Exception as e:
    logger.debug(f"M9 news context skipped: {e}")
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/market_data/scanner.py
git commit -m "feat(ml): wire M9 news context into scanner feature logging"
```

---

### Task 14: Wire M9 options_flow daily storage into orchestrator

**Files:**
- Modify: `backend/src/pipeline/orchestrator.py`

- [ ] **Step 1: Add daily options_flow storage and trading outcome resolution**

In the orchestrator's post-extraction analytics (where training is triggered), add:

```python
# Store daily macro data to options_flow (M9)
try:
    from src.ml.models.macro_engine import store_daily_options_flow
    from src.market_data.macro_provider import fetch_macro_snapshot
    macro = await fetch_macro_snapshot()
    await store_daily_options_flow(session, macro)
except Exception as e:
    logger.debug(f"Daily options_flow storage skipped: {e}")

# Resolve trading signal outcomes
try:
    from src.ml.feature_store import resolve_trading_outcomes
    resolved = resolve_trading_outcomes(session)
    if resolved:
        logger.info(f"Resolved {resolved} trading signal outcomes")
except Exception as e:
    logger.debug(f"Trading outcome resolution skipped: {e}")
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/pipeline/orchestrator.py
git commit -m "feat(ml): wire M9 daily options_flow storage and trading outcome resolution"
```

---

### Task 15: Update API and frontend for trading models

**Files:**
- Modify: `backend/src/api/routes/extraction.py`
- Modify: `frontend/src/components/Terminal/pages/StatsPage.tsx`

- [ ] **Step 1: Add trading models to ML status endpoint**

In the `/extraction/ml/status` endpoint, the `MODEL_CONFIGS` is imported from `train_all.py` which now includes the trading models. No code change needed — the endpoint already iterates `MODEL_CONFIGS` dynamically. Verify this works.

- [ ] **Step 2: Update StatsPage to show all models**

The StatsPage already renders from the API response dynamically. The 4 new trading models will appear automatically once `MODEL_CONFIGS` is updated. No frontend change needed.

- [ ] **Step 3: Commit (if any changes needed)**

```bash
git add backend/src/api/routes/extraction.py frontend/src/components/Terminal/pages/StatsPage.tsx
git commit -m "feat(ml): verify trading models appear in ML status API and frontend"
```

---

### Task 16: Write tests for all trading models

**Files:**
- Create: `backend/tests/test_trading_ml_models.py`

- [ ] **Step 1: Write comprehensive tests**

```python
"""Tests for M5-M7, M9 trading ML models."""
import json
import numpy as np
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone


# --- M5: Setup Scorer ---

def test_setup_scorer_encode_features():
    from src.ml.models.setup_scorer import _encode_features, FEATURE_NAMES_PHASE1
    features = {
        "base_score": 75, "delta_pct": 0.089, "cvd_slope_5bar": 45.2,
        "volume_ratio_vs_20bar": 1.45, "volume_ratio_vs_session": 1.12,
        "distance_to_level_ticks": 3, "distance_to_poc_ticks": 15,
        "price_position_in_va": 0.72, "ib_range_vs_avg": 0.85,
        "minutes_since_rth_open": 45, "aspr_percentile": 0.35,
        "passive_active_ratio": 1.8, "absorption_bar_count": 2,
        "vix_level": 18.5, "gex": -500000,
    }
    vec = _encode_features(features, FEATURE_NAMES_PHASE1)
    assert vec is not None
    assert len(vec) == len(FEATURE_NAMES_PHASE1)
    assert vec[0] == 75.0  # base_score


def test_setup_scorer_train():
    from src.ml.models.setup_scorer import SetupScorerModel
    data = [_mock_trading_feature(i) for i in range(250)]
    result = SetupScorerModel().train(data)
    assert result is not None
    assert result["training_data_count"] == 250
    assert result["file_path"].endswith(".joblib")


def test_setup_scorer_insufficient_data():
    from src.ml.models.setup_scorer import SetupScorerModel
    data = [_mock_trading_feature(i) for i in range(50)]
    result = SetupScorerModel().train(data)
    assert result is None


# --- M6: Temporal Pattern ---

def test_temporal_pattern_encode_sequence():
    from src.ml.models.temporal_pattern import _encode_candle_sequence, CANDLE_FEATURE_NAMES
    candles = [_mock_candle(i) for i in range(20)]
    seq = _encode_candle_sequence(candles)
    assert seq is not None
    assert seq.shape == (20, len(CANDLE_FEATURE_NAMES))


def test_temporal_pattern_get_label():
    from src.ml.models.temporal_pattern import _get_label
    assert _get_label(1.5, 1) == 0  # reversal_long (strong positive)
    assert _get_label(-1.5, 0) == 1  # reversal_short (strong negative)
    assert _get_label(0.3, 1) == 2  # continuation_long (mild positive)
    assert _get_label(-0.3, 0) == 3  # continuation_short (mild negative)
    assert _get_label(0.0, 0) == 4  # chop
    assert _get_label(None, None) is None


def test_temporal_pattern_insufficient_data():
    from src.ml.models.temporal_pattern import TemporalPatternModel
    data = [_mock_trading_feature(i, with_candles=True) for i in range(100)]
    result = TemporalPatternModel().train(data)
    assert result is None  # Need 500


# --- M7: Gate Classifier ---

def test_gate_features_extraction():
    from src.ml.features.gate_features import extract_gate_features
    features = extract_gate_features(
        rf_after_ib=3.0, ib_range=120.0, ib_range_vs_avg=0.85,
        opening_type="OD", first_hour_delta_total=5000.0,
        vix_level=18.5, gex=-500000.0,
    )
    assert features["rf_after_ib"] == 3.0
    assert features["opening_type_encoded"] == 0  # OD=0
    assert features["vix_level"] == 18.5


def test_gate_classifier_train():
    from src.ml.models.gate_classifier import GateClassifierModel
    data = [_mock_session_feature(i) for i in range(120)]
    result = GateClassifierModel().train(data)
    assert result is not None
    assert result["training_data_count"] == 120


# --- M9: Macro Engine ---

def test_macro_features_extraction():
    from src.ml.features.macro_features import extract_macro_features
    features = extract_macro_features(
        vix_level=18.5, vix_change_1d=-2.3,
        dxy_level=104.2, us10y_level=4.52,
        yield_curve_spread=0.15,
    )
    assert features["vix_level"] == 18.5
    assert features["yield_curve_spread"] == 0.15


def test_news_impact_features():
    from src.ml.features.macro_features import extract_news_impact_features
    features = extract_news_impact_features(
        event_name="CPI", importance=3, surprise=0.3,
        vix_at_event=22.0, immediate_impact_pct=-0.5,
    )
    assert features["event_type_encoded"] == 2  # CPI=2
    assert features["surprise"] == 0.3


def test_macro_engine_train():
    from src.ml.models.macro_engine import MacroEngineModel
    data = [_mock_news_feature(i) for i in range(60)]
    result = MacroEngineModel().train(data)
    assert result is not None
    assert result["training_data_count"] == 60


def test_macro_engine_insufficient_data():
    from src.ml.models.macro_engine import MacroEngineModel
    data = [_mock_news_feature(i) for i in range(20)]
    result = MacroEngineModel().train(data)
    assert result is None


# --- Economic Calendar ---

def test_get_upcoming_events():
    from src.data.economic_calendar import get_upcoming_events
    mock_session = MagicMock()
    mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    result = get_upcoming_events(mock_session, minutes_ahead=120)
    assert result == []


# --- Helpers ---

def _mock_trading_feature(idx, with_candles=False):
    """Create mock MlFeature for trading signal."""
    features = {
        "base_score": 65 + (idx % 20), "delta_pct": 0.05 + idx * 0.001,
        "cvd_slope_5bar": 30 + idx * 0.5, "volume_ratio_vs_20bar": 1.0 + idx * 0.01,
        "volume_ratio_vs_session": 1.0, "distance_to_level_ticks": 3 + idx % 10,
        "distance_to_poc_ticks": 10 + idx % 20, "price_position_in_va": 0.5,
        "ib_range_vs_avg": 0.8 + idx * 0.002, "minutes_since_rth_open": 30 + idx,
        "aspr_percentile": 0.4, "passive_active_ratio": 1.5,
        "absorption_bar_count": idx % 4, "vix_level": 18.0, "gex": -500000,
    }
    if with_candles:
        features["candle_sequence"] = [_mock_candle(j) for j in range(20)]
    mock = MagicMock()
    mock.features = features
    mock.outcome = (idx % 5 - 2) * 0.5  # -1.0 to 1.0
    mock.outcome_binary = 1 if mock.outcome > 0 else 0
    return mock


def _mock_candle(idx):
    """Create mock candle dict."""
    return {
        "delta": 100 + idx * 10, "delta_pct": 0.05 + idx * 0.01,
        "cvd": idx * 100, "volume": 5000 + idx * 100,
        "volume_ratio": 1.0 + idx * 0.05, "spread_ticks": 20 + idx,
        "body_ratio": 0.5, "close_position": 0.6,
        "tick_count": 1000 + idx * 50, "passive_active_ratio": 1.5,
        "vwap_distance_ticks": idx - 10, "poc_distance_ticks": idx - 5,
        "imbalance_ratio_max": 2.0, "stacked_imbalance_count": 1,
        "big_trades_count": 5, "big_trades_net_delta": 50,
    }


def _mock_session_feature(idx):
    """Create mock MlFeature for market session (M7)."""
    features = {
        "rf_after_ib": 2 + idx % 5, "ib_range": 100 + idx,
        "ib_range_vs_avg": 0.8 + idx * 0.005, "opening_type_encoded": idx % 4,
        "first_hour_delta_total": 3000 + idx * 100,
        "first_hour_volume_vs_avg": 1.0 + idx * 0.01,
        "overnight_range_pct": 0.5 + idx * 0.01,
        "gap_filled_pct": 0.3 + idx * 0.005,
        "yesterday_market_type_encoded": idx % 3,
        "poor_high_or_low_in_ib": idx % 2,
        "first_hour_big_trades_count": 10 + idx % 20,
        "session_volume_first_hour": 500000 + idx * 10000,
        "vix_level": 18.0, "gex": -500000,
        "value_migration_encoded": idx % 3,
        "ib_tpo_count": 3 + idx % 5,
        "day_type_label": idx % 5,
    }
    mock = MagicMock()
    mock.features = features
    mock.outcome = idx % 5  # day type class
    mock.outcome_binary = None
    return mock


def _mock_news_feature(idx):
    """Create mock MlFeature for news event (M9)."""
    features = {
        "event_type_encoded": idx % 10, "importance": (idx % 3) + 1,
        "surprise": (idx % 10 - 5) * 0.1, "vix_at_event": 18 + idx % 10,
        "delta_1m_after": (idx % 20 - 10) * 100, "volume_1m_after": 5000 + idx * 100,
    }
    mock = MagicMock()
    mock.features = features
    mock.outcome = (idx % 10 - 5) * 0.1  # NQ impact %
    mock.outcome_binary = 1 if mock.outcome > 0 else 0
    return mock
```

- [ ] **Step 2: Run tests**

Run: `pytest backend/tests/test_trading_ml_models.py -v`
Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_trading_ml_models.py
git commit -m "test(ml): add tests for M5-M7/M9 trading ML models"
```

---

### Task 17: Run full test suite

- [ ] **Step 1: Run all tests**

Run: `pytest backend/tests/ -v`
Expected: All tests pass (97 existing + new trading tests).

- [ ] **Step 2: Fix any failures**

If any failures, fix and re-run.
