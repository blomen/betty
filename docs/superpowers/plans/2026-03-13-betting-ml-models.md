# Betting ML Models Implementation Plan (M1-M4 + M8)

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build training + inference infrastructure for 5 betting ML models (Edge Quality, Limit Predictor, Devig Selector, Boost Calibrator, Adaptive Kelly) that activate at data thresholds and gracefully fall back to existing rules-based logic.

**Architecture:** Each model follows the M10 optimizer pattern: `check_and_train()` returns `None` below data threshold, returns predictions above it. Feature logging happens from Day 1 (already wired for M1 via `betting_features.py`). A central `Predictor` loads active models from `ml_model_registry` at startup. A `train_all.py` orchestrator retrains weekly.

**Tech Stack:** LightGBM/XGBoost (via existing `trainer.py`), scikit-learn (isotonic regression for M4), joblib (serialization), SQLite (feature store + model registry)

**Spec:** `docs/superpowers/specs/2026-03-12-ml-system-design.md`

---

## File Structure

**Create:**
- `backend/src/ml/models/__init__.py` — Package marker
- `backend/src/ml/models/edge_quality.py` — M1: Edge Quality Classifier
- `backend/src/ml/models/limit_predictor.py` — M2: Provider Limit Predictor
- `backend/src/ml/models/devig_selector.py` — M3: Devig Method Selector
- `backend/src/ml/models/boost_calibrator.py` — M4: LLM Boost Calibrator
- `backend/src/ml/models/adaptive_kelly.py` — M8: Adaptive Kelly Sizing
- `backend/src/ml/features/limit_features.py` — M2 feature extraction
- `backend/src/ml/features/devig_features.py` — M3 feature extraction
- `backend/src/ml/features/boost_features.py` — M4 feature extraction
- `backend/src/ml/features/kelly_features.py` — M8 feature extraction
- `backend/src/ml/training/__init__.py` — Package marker
- `backend/src/ml/training/train_all.py` — Weekly training orchestrator
- `backend/src/ml/serving/__init__.py` — Package marker
- `backend/src/ml/serving/predictor.py` — Model loading + inference
- `backend/data/models/` — Directory for serialized model files
- `backend/tests/test_betting_models.py` — Tests for all 5 models
- `backend/tests/test_model_serving.py` — Tests for predictor + training

**Modify:**
- `backend/src/ml/migrations.py` — Add `devig_method_log` table, `betting_outcome_log` table
- `backend/src/ml/feature_store.py` — Add helper for bulk feature retrieval
- `backend/src/analysis/scanner.py` — Wire M1 predictions, log features
- `backend/src/analysis/devig.py` — Wire M3 predictions
- `backend/src/analysis/ev_enrichment.py` — Wire M4 calibration
- `backend/src/risk/calculator.py` — Wire M2 predictions
- `backend/src/bankroll/stake_calculator.py` — Wire M8 predictions
- `backend/src/pipeline/orchestrator.py` — Add training trigger after extraction

---

## Chunk 1: Shared Infrastructure

### Task 1: Model Serving Infrastructure

**Files:**
- Create: `backend/src/ml/serving/__init__.py`
- Create: `backend/src/ml/serving/predictor.py`
- Test: `backend/tests/test_model_serving.py`

- [ ] **Step 1: Create serving package**

```python
# backend/src/ml/serving/__init__.py
```

- [ ] **Step 2: Write the failing test for Predictor**

```python
# backend/tests/test_model_serving.py
"""Tests for ML model serving infrastructure."""
import pytest
import numpy as np
from unittest.mock import MagicMock, patch
from src.ml.serving.predictor import Predictor


def test_predictor_init_no_models():
    """Predictor initializes with empty model cache when no models registered."""
    predictor = Predictor()
    assert predictor.models == {}


def test_predictor_predict_no_model():
    """Predict returns None when model not loaded."""
    predictor = Predictor()
    result = predictor.predict("edge_quality", {"edge_pct": 5.0})
    assert result is None


def test_predictor_predict_with_model():
    """Predict returns prediction when model loaded."""
    predictor = Predictor()
    mock_model = MagicMock()
    mock_model.predict_proba = MagicMock(return_value=np.array([[0.3, 0.7]]))
    predictor.models["edge_quality"] = {
        "model": mock_model,
        "feature_names": ["edge_pct", "prob_sum"],
        "task": "classification",
    }
    result = predictor.predict("edge_quality", {"edge_pct": 5.0, "prob_sum": 0.98})
    assert result is not None
    assert abs(result - 0.7) < 0.01


def test_predictor_predict_regression():
    """Predict returns raw value for regression models."""
    predictor = Predictor()
    mock_model = MagicMock()
    mock_model.predict = MagicMock(return_value=np.array([0.35]))
    predictor.models["adaptive_kelly"] = {
        "model": mock_model,
        "feature_names": ["edge_pct"],
        "task": "regression",
    }
    result = predictor.predict("adaptive_kelly", {"edge_pct": 5.0})
    assert abs(result - 0.35) < 0.01


def test_predictor_load_model():
    """Load model from registry entry."""
    predictor = Predictor()
    with patch("joblib.load") as mock_load:
        mock_load.return_value = {
            "model": MagicMock(),
            "feature_names": ["f1"],
            "task": "classification",
        }
        predictor.load_model("edge_quality", "/fake/path.joblib")
        assert "edge_quality" in predictor.models
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_model_serving.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.ml.serving'`

- [ ] **Step 4: Implement Predictor**

```python
# backend/src/ml/serving/predictor.py
"""Model serving — loads trained models from registry and serves predictions.

Thread-safe singleton. Models loaded lazily from ml_model_registry.
Falls back to None (rules-based) when model unavailable.
"""
import logging
import numpy as np
from pathlib import Path

logger = logging.getLogger(__name__)


class Predictor:
    """Central prediction server for all ML models."""

    def __init__(self):
        self.models: dict[str, dict] = {}

    def load_model(self, model_name: str, file_path: str) -> bool:
        """Load a serialized model from disk."""
        try:
            import joblib
            data = joblib.load(file_path)
            self.models[model_name] = data
            logger.info(f"Loaded model {model_name} from {file_path}")
            return True
        except Exception as e:
            logger.warning(f"Failed to load model {model_name}: {e}")
            return False

    def load_from_registry(self, session) -> int:
        """Load all active models from ml_model_registry table."""
        from src.db.models import MlModelRegistry
        active = session.query(MlModelRegistry).filter_by(is_active=1).all()
        loaded = 0
        for entry in active:
            if self.load_model(entry.model_name, entry.file_path):
                loaded += 1
        return loaded

    def predict(self, model_name: str, features: dict) -> float | dict | None:
        """Get prediction for a model. Returns None if model not loaded.

        Returns:
            - float for classification (P(positive class)) and regression
            - dict for multiclass ({"class": index, "probabilities": [...]})
            - None if model not loaded or prediction fails
        """
        if model_name not in self.models:
            return None

        model_data = self.models[model_name]
        model = model_data["model"]
        feature_names = model_data["feature_names"]
        task = model_data.get("task", "classification")

        try:
            X = np.array([[features.get(f, 0.0) for f in feature_names]])
            if task == "multiclass":
                proba = model.predict_proba(X)[0]
                return {
                    "class": int(np.argmax(proba)),
                    "probabilities": proba.tolist(),
                }
            elif task == "classification":
                proba = model.predict_proba(X)
                return float(proba[0][1])  # P(positive class)
            else:
                pred = model.predict(X)
                return float(pred[0])
        except Exception as e:
            logger.warning(f"Prediction failed for {model_name}: {e}")
            return None

    def is_loaded(self, model_name: str) -> bool:
        """Check if a model is loaded and ready."""
        return model_name in self.models


# Module-level singleton
_predictor: Predictor | None = None


def get_predictor() -> Predictor:
    """Get or create the global predictor singleton."""
    global _predictor
    if _predictor is None:
        _predictor = Predictor()
    return _predictor
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_model_serving.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/ml/serving/__init__.py backend/src/ml/serving/predictor.py backend/tests/test_model_serving.py
git commit -m "feat(ml): add model serving infrastructure with Predictor singleton"
```

---

### Task 2: Training Orchestrator

**Files:**
- Create: `backend/src/ml/training/__init__.py`
- Create: `backend/src/ml/training/train_all.py`
- Create: `backend/src/ml/models/__init__.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_model_serving.py`:

```python
from src.ml.training.train_all import TrainingOrchestrator


def test_training_orchestrator_init():
    """Orchestrator initializes with model registry."""
    orch = TrainingOrchestrator()
    assert orch.model_configs is not None
    assert "edge_quality" in orch.model_configs


def test_training_orchestrator_check_thresholds(db_session):
    """Returns empty dict when no data available."""
    orch = TrainingOrchestrator()
    ready = orch.check_thresholds(db_session)
    assert isinstance(ready, dict)
    # No data = nothing ready
    assert all(v is False for v in ready.values())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_model_serving.py::test_training_orchestrator_init -v`
Expected: FAIL

- [ ] **Step 3: Implement TrainingOrchestrator**

```python
# backend/src/ml/training/__init__.py
```

```python
# backend/src/ml/training/train_all.py
"""Weekly training orchestrator for all ML models.

Checks data thresholds, trains models that have sufficient data,
evaluates against baseline, and registers to ml_model_registry.
"""
import logging
from pathlib import Path
from datetime import datetime, timezone

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Model configs: name → {min_samples, domain, source_type, task}
MODEL_CONFIGS = {
    "edge_quality": {
        "min_samples": 200,
        "domain": "betting",
        "source_type": "opportunity",
        "task": "classification",
    },
    "limit_predictor": {
        "min_samples": 20,
        "domain": "betting",
        "source_type": "limit_event",
        "task": "classification",
    },
    "devig_selector": {
        "min_samples": 500,
        "domain": "betting",
        "source_type": "devig_comparison",
        "task": "multiclass",
    },
    "boost_calibrator": {
        "min_samples": 100,
        "domain": "betting",
        "source_type": "boost",
        "task": "calibration",
    },
    "adaptive_kelly": {
        "min_samples": 300,
        "domain": "betting",
        "source_type": "bet_outcome",
        "task": "regression",
    },
}

MODELS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "models"


class TrainingOrchestrator:
    """Orchestrates training for all ML models."""

    def __init__(self):
        self.model_configs = MODEL_CONFIGS

    def check_thresholds(self, session: Session) -> dict[str, bool]:
        """Check which models have enough data to train."""
        from src.ml.feature_store import get_training_data

        ready = {}
        for name, config in self.model_configs.items():
            data = get_training_data(
                session, config["domain"], config["source_type"]
            )
            ready[name] = len(data) >= config["min_samples"]
        return ready

    def train_model(self, session: Session, model_name: str) -> dict | None:
        """Train a single model if data threshold met. Returns model info or None."""
        config = self.model_configs.get(model_name)
        if not config:
            logger.warning(f"Unknown model: {model_name}")
            return None

        from src.ml.feature_store import get_training_data

        data = get_training_data(session, config["domain"], config["source_type"])
        if len(data) < config["min_samples"]:
            logger.info(
                f"{model_name}: {len(data)}/{config['min_samples']} samples — skipping"
            )
            return None

        # Delegate to model-specific trainer
        trainer_fn = _get_trainer(model_name)
        if trainer_fn is None:
            return None

        return trainer_fn(data, session)

    def train_all(self, session: Session) -> dict[str, str]:
        """Train all models that have sufficient data. Returns status per model."""
        results = {}
        ready = self.check_thresholds(session)

        for name, is_ready in ready.items():
            if not is_ready:
                results[name] = "insufficient_data"
                continue
            try:
                result = self.train_model(session, name)
                if result:
                    self._register_model(session, name, result)
                    results[name] = "trained"
                else:
                    results[name] = "train_failed"
            except Exception as e:
                logger.error(f"Training {name} failed: {e}")
                results[name] = f"error: {e}"

        return results

    def _register_model(self, session: Session, model_name: str, result: dict) -> None:
        """Register trained model in ml_model_registry."""
        from src.db.models import MlModelRegistry

        # Deactivate previous versions
        session.query(MlModelRegistry).filter_by(
            model_name=model_name, is_active=1
        ).update({"is_active": 0})

        # Get next version
        last = (
            session.query(MlModelRegistry)
            .filter_by(model_name=model_name)
            .order_by(MlModelRegistry.version.desc())
            .first()
        )
        version = (last.version + 1) if last else 1

        entry = MlModelRegistry(
            model_name=model_name,
            version=version,
            file_path=result.get("file_path", ""),
            training_data_count=result.get("training_data_count", 0),
            validation_metric=result.get("validation_score"),
            baseline_metric=result.get("baseline_metric"),
            is_active=1,
        )
        session.add(entry)
        session.flush()


def _get_trainer(model_name: str):
    """Get trainer function for a model. Lazy import to avoid circular deps."""
    trainers = {
        "edge_quality": lambda data, s: _train_edge_quality(data, s),
        "limit_predictor": lambda data, s: _train_limit_predictor(data, s),
        "devig_selector": lambda data, s: _train_devig_selector(data, s),
        "boost_calibrator": lambda data, s: _train_boost_calibrator(data, s),
        "adaptive_kelly": lambda data, s: _train_adaptive_kelly(data, s),
    }
    return trainers.get(model_name)


def _train_edge_quality(data, session):
    from src.ml.models.edge_quality import EdgeQualityModel
    return EdgeQualityModel().train(data)


def _train_limit_predictor(data, session):
    from src.ml.models.limit_predictor import LimitPredictorModel
    return LimitPredictorModel().train(data)


def _train_devig_selector(data, session):
    from src.ml.models.devig_selector import DevigSelectorModel
    return DevigSelectorModel().train(data)


def _train_boost_calibrator(data, session):
    from src.ml.models.boost_calibrator import BoostCalibratorModel
    return BoostCalibratorModel().train(data)


def _train_adaptive_kelly(data, session):
    from src.ml.models.adaptive_kelly import AdaptiveKellyModel
    return AdaptiveKellyModel().train(data)
```

```python
# backend/src/ml/models/__init__.py
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_model_serving.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/training/ backend/src/ml/models/__init__.py
git commit -m "feat(ml): add training orchestrator with threshold checks and model registry"
```

---

### Task 3: New Migration Tables

**Files:**
- Modify: `backend/src/ml/migrations.py`
- Modify: `backend/src/db/models.py` (add MlModelRegistry ORM if missing)

**Note:** The `opportunities` table column additions (`prob_sum`, `odds_ratio`, `odds_age_minutes`, etc.) already exist in `_add_opportunity_columns()` in migrations.py. No need to add them again.

- [ ] **Step 1: Add `devig_method_log` and `betting_outcome_log` migrations**

Add to `backend/src/ml/migrations.py` before `run_migrations()`:

```python
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
```

And add calls in `run_migrations()`:

```python
def run_migrations(conn: sqlite3.Connection) -> None:
    # ... existing calls ...
    _create_devig_method_log(conn)
    _create_betting_outcome_log(conn)
    conn.commit()
```

- [ ] **Step 2: Add MlModelRegistry ORM model to db/models.py** (if not present)

Check if `MlModelRegistry` class exists in `db/models.py`. If not, add before `init_db()`:

```python
class MlModelRegistry(Base):
    """Registry for trained ML model artifacts."""
    __tablename__ = "ml_model_registry"

    id = Column(Integer, primary_key=True)
    model_name = Column(String, nullable=False)
    version = Column(Integer)
    file_path = Column(String)
    training_data_count = Column(Integer)
    validation_metric = Column(Float)
    baseline_metric = Column(Float)
    is_active = Column(Integer, default=0)
    created_at = Column(DateTime, default=_utcnow)
```

- [ ] **Step 3: Run migrations test**

Run: `cd backend && python -m pytest tests/test_analytics.py -v -k "migration or init"`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/src/ml/migrations.py backend/src/db/models.py
git commit -m "feat(ml): add devig_method_log and betting_outcome_log tables + MlModelRegistry ORM"
```

---

### Task 4: Create models directory for serialized models

- [ ] **Step 1: Create the data/models directory**

```bash
mkdir -p backend/data/models
echo "*\n!.gitkeep" > backend/data/models/.gitignore
```

- [ ] **Step 2: Commit**

```bash
git add backend/data/models/.gitignore
git commit -m "chore: add data/models directory for ML model artifacts"
```

---

## Chunk 2: M1 Edge Quality Classifier

### Task 5: M1 Model Class

**Files:**
- Create: `backend/src/ml/models/edge_quality.py`
- Test: `backend/tests/test_betting_models.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_betting_models.py
"""Tests for betting ML models (M1-M4, M8)."""
import pytest
import numpy as np
from unittest.mock import MagicMock


# ===== M1: Edge Quality =====

def test_edge_quality_feature_names():
    """M1 has correct feature list."""
    from src.ml.models.edge_quality import EdgeQualityModel
    model = EdgeQualityModel()
    assert "edge_pct" in model.feature_names
    assert "prob_sum" in model.feature_names
    assert "odds_ratio" in model.feature_names
    assert "sport" not in model.feature_names  # categorical, encoded separately


def test_edge_quality_train_insufficient_data():
    """Returns None with insufficient data."""
    from src.ml.models.edge_quality import EdgeQualityModel
    model = EdgeQualityModel()
    # Create mock data with only 10 samples
    data = [_mock_ml_feature({"edge_pct": 5.0, "prob_sum": 0.98}, outcome_binary=1) for _ in range(10)]
    result = model.train(data)
    assert result is None


def test_edge_quality_train_sufficient_data():
    """Trains successfully with enough data."""
    from src.ml.models.edge_quality import EdgeQualityModel
    model = EdgeQualityModel()
    # 200+ samples with mixed outcomes
    data = []
    for i in range(250):
        features = {
            "edge_pct": np.random.uniform(1, 30),
            "prob_sum": np.random.uniform(0.85, 1.1),
            "odds_ratio": np.random.uniform(0.8, 1.4),
            "odds_age_minutes": np.random.uniform(0, 120),
            "sharp_age_minutes": np.random.uniform(0, 60),
            "time_to_start_minutes": np.random.uniform(30, 2880),
            "pinnacle_overround": np.random.uniform(0.02, 0.08),
            "num_providers_with_odds": np.random.randint(1, 10),
            "provider_odds_rank": np.random.randint(1, 5),
            "market_consensus_spread": np.random.uniform(0, 0.5),
            "hour_of_day": np.random.randint(0, 24),
            "day_of_week": np.random.randint(0, 7),
        }
        outcome = 1 if np.random.random() > 0.4 else 0
        data.append(_mock_ml_feature(features, outcome_binary=outcome))
    result = model.train(data)
    assert result is not None
    assert "model" in result
    assert "file_path" in result
    assert result["training_data_count"] == 250


def test_edge_quality_predict():
    """Predict returns probability between 0 and 1."""
    from src.ml.models.edge_quality import EdgeQualityModel
    model = EdgeQualityModel()
    # Train first
    data = _generate_training_data(250)
    result = model.train(data)
    assert result is not None
    # Predict
    features = {
        "edge_pct": 8.0, "prob_sum": 0.98, "odds_ratio": 1.08,
        "odds_age_minutes": 15, "sharp_age_minutes": 10,
        "time_to_start_minutes": 120, "pinnacle_overround": 0.03,
        "num_providers_with_odds": 5, "provider_odds_rank": 1,
        "market_consensus_spread": 0.1, "hour_of_day": 14, "day_of_week": 2,
    }
    prob = model.predict(features)
    assert prob is not None
    assert 0 <= prob <= 1


# ===== Helpers =====

def _mock_ml_feature(features: dict, outcome_binary: int = 1, outcome: float = 0.05):
    """Create a mock MlFeature-like object."""
    mock = MagicMock()
    mock.features = features
    mock.outcome = outcome
    mock.outcome_binary = outcome_binary
    return mock


def _generate_training_data(n: int, task: str = "classification"):
    """Generate n mock training samples."""
    data = []
    for i in range(n):
        features = {
            "edge_pct": np.random.uniform(1, 30),
            "prob_sum": np.random.uniform(0.85, 1.1),
            "odds_ratio": np.random.uniform(0.8, 1.4),
            "odds_age_minutes": np.random.uniform(0, 120),
            "sharp_age_minutes": np.random.uniform(0, 60),
            "time_to_start_minutes": np.random.uniform(30, 2880),
            "pinnacle_overround": np.random.uniform(0.02, 0.08),
            "num_providers_with_odds": np.random.randint(1, 10),
            "provider_odds_rank": np.random.randint(1, 5),
            "market_consensus_spread": np.random.uniform(0, 0.5),
            "hour_of_day": np.random.randint(0, 24),
            "day_of_week": np.random.randint(0, 7),
        }
        outcome = 1 if np.random.random() > 0.4 else 0
        data.append(_mock_ml_feature(features, outcome_binary=outcome, outcome=np.random.uniform(-0.1, 0.15)))
    return data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_betting_models.py::test_edge_quality_feature_names -v`
Expected: FAIL

- [ ] **Step 3: Implement EdgeQualityModel**

```python
# backend/src/ml/models/edge_quality.py
"""M1: Edge Quality Classifier.

Predicts whether a detected edge is real (CLV > 0) or noise.
Replaces hardcoded MIN_VALID_PROB_SUM, MAX_ODDS_RATIO, MAX_EDGE_PCT thresholds.

Min training data: 200 bets with CLV tracking.
"""
import logging
import json
import numpy as np
from pathlib import Path

logger = logging.getLogger(__name__)

# Phase 1 features (available immediately from scanner data)
FEATURE_NAMES_PHASE1 = [
    "edge_pct", "prob_sum", "odds_ratio",
    "odds_age_minutes", "sharp_age_minutes", "time_to_start_minutes",
    "pinnacle_overround", "num_providers_with_odds", "provider_odds_rank",
    "market_consensus_spread", "hour_of_day", "day_of_week",
    "sport", "market_type", "point",
]

# Phase 2 features (require historical data accumulation)
FEATURE_NAMES_PHASE2 = FEATURE_NAMES_PHASE1 + [
    "odds_movement_direction", "odds_movement_magnitude",
    "sharp_line_stability", "provider_platform",
    "is_platform_outlier", "provider_historical_clv_avg",
    "provider_update_frequency", "provider_match_rate",
    "league_liquidity_proxy", "home_team_popularity_proxy",
    "minutes_since_extraction",
]

# Start with phase 1, graduate to phase 2 as data accumulates
FEATURE_NAMES = FEATURE_NAMES_PHASE1

MIN_SAMPLES = 200
MODELS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "models"


class EdgeQualityModel:
    """XGBoost/LightGBM binary classifier for edge quality."""

    def __init__(self):
        self.feature_names = FEATURE_NAMES
        self.model = None

    def train(self, data: list) -> dict | None:
        """Train on resolved ml_features rows. Returns model info or None."""
        if len(data) < MIN_SAMPLES:
            logger.info(f"Edge quality: {len(data)}/{MIN_SAMPLES} samples — skipping")
            return None

        X, y = self._prepare_data(data)
        if X is None:
            return None

        from src.ml.optimizer.trainer import train_model
        result = train_model(X, y, task="classification", min_samples=MIN_SAMPLES)
        if result is None:
            return None

        # Save model
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        file_path = str(MODELS_DIR / "edge_quality_latest.joblib")
        try:
            import joblib
            joblib.dump({
                "model": result["model"],
                "feature_names": self.feature_names,
                "task": "classification",
            }, file_path)
        except ImportError:
            logger.warning("joblib not installed — cannot save model")
            return None

        self.model = result["model"]
        return {
            "model": result["model"],
            "file_path": file_path,
            "training_data_count": len(data),
            "validation_score": result.get("validation_score"),
            "feature_importance": result.get("feature_importance"),
        }

    def predict(self, features: dict) -> float | None:
        """Predict probability that edge is real (CLV > 0)."""
        if self.model is None:
            return None
        X = np.array([[features.get(f, 0.0) for f in self.feature_names]])
        try:
            proba = self.model.predict_proba(X)
            return float(proba[0][1])
        except Exception as e:
            logger.warning(f"Edge quality prediction failed: {e}")
            return None

    def _prepare_data(self, data: list) -> tuple:
        """Extract X, y arrays from ml_features rows."""
        X_list, y_list = [], []
        for row in data:
            features = row.features if isinstance(row.features, dict) else json.loads(row.features)
            x = [features.get(f, 0.0) for f in self.feature_names]
            # Replace None with 0
            x = [0.0 if v is None else float(v) for v in x]
            X_list.append(x)
            y_list.append(int(row.outcome_binary))
        return np.array(X_list), np.array(y_list)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_betting_models.py -v -k "edge_quality"`
Expected: PASS (all 4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/models/edge_quality.py backend/tests/test_betting_models.py
git commit -m "feat(ml): add M1 Edge Quality Classifier model"
```

---

### Task 6: M1 Scanner Integration

**Files:**
- Modify: `backend/src/analysis/scanner.py`

- [ ] **Step 1: Add ML prediction to value scanning**

In `scanner.py`, find the `scan_value()` method. After the existing quality filters (prob_sum, odds_ratio, max_edge), add M1 prediction hook:

```python
# After existing quality filter checks, before appending to results:
# ML edge quality check (M1) — replaces fixed thresholds when model available
try:
    from src.ml.serving.predictor import get_predictor
    predictor = get_predictor()
    if predictor.is_loaded("edge_quality"):
        ml_features = {
            "edge_pct": edge_pct, "prob_sum": prob_sum,
            "odds_ratio": odds_ratio,
            "odds_age_minutes": odds_age_minutes,
            "sharp_age_minutes": sharp_age_minutes,
            "time_to_start_minutes": time_to_start_minutes,
            "pinnacle_overround": pinnacle_overround,
            "num_providers_with_odds": num_providers,
            "provider_odds_rank": provider_rank,
            "market_consensus_spread": consensus_spread,
            "hour_of_day": now.hour, "day_of_week": now.weekday(),
        }
        prob = predictor.predict("edge_quality", ml_features)
        if prob is not None and prob < 0.5:
            continue  # ML says edge is likely noise
except Exception:
    pass  # Best-effort — never block scanning
```

**Important:** This is a best-effort hook. The existing hardcoded filters remain as the primary gate. The ML check is an additional filter that only activates when a model is loaded. The `try/except` ensures extraction never breaks.

- [ ] **Step 2: Add feature logging to value scanning**

After computing a value bet (just before yielding/appending), log features to ml_features:

```python
# Log features for M1 training (best-effort)
try:
    from src.ml.feature_store import log_features
    from src.ml.features.betting_features import extract_betting_features
    features = extract_betting_features(
        edge_pct=edge_pct, provider_odds=provider_odds,
        fair_odds=fair_odds, fair_probability=fair_probability,
        provider=provider, sport=sport, market=market,
        event_id=event_id, prob_sum=prob_sum,
        odds_by_outcome=odds_by_outcome,
        pinnacle_overround=pinnacle_overround,
        event_start_time=event_start_time, point=point,
    )
    log_features(self.session, "betting", str(opportunity_id), "opportunity", features)
except Exception:
    pass  # Best-effort
```

**Note:** The scanner subagent will need to read the current `scan_value()` code to find the exact insertion points. The above is the logic — placement depends on the actual code structure.

- [ ] **Step 3: Run existing scanner tests**

Run: `cd backend && python -m pytest tests/ -v -k "scanner or value"` (if any exist)
Expected: PASS (no regressions)

- [ ] **Step 4: Commit**

```bash
git add backend/src/analysis/scanner.py
git commit -m "feat(ml): wire M1 edge quality prediction + feature logging into scanner"
```

---

## Chunk 3: M2 Limit Predictor

### Task 7: M2 Feature Extraction

**Files:**
- Create: `backend/src/ml/features/limit_features.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_betting_models.py`:

```python
def test_limit_features_extraction():
    """M2 limit features extract from behavioral data."""
    from src.ml.features.limit_features import extract_limit_features
    features = extract_limit_features(
        stake_entropy=0.3, market_diversity=0.4,
        timing_regularity=0.5, outcome_correlation=0.2,
        bonus_usage_ratio=0.1, clv_score=0.6, win_rate_deviation=0.3,
        total_bets=50, account_age_days=90,
        total_turnover=5000, provider_id="betsson",
        similar_platform_limits=0,
    )
    assert "clv_score" in features
    assert "total_bets" in features
    assert "provider_platform" in features
    assert features["total_bets"] == 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_betting_models.py::test_limit_features_extraction -v`
Expected: FAIL

- [ ] **Step 3: Implement limit feature extraction**

```python
# backend/src/ml/features/limit_features.py
"""Extract features for M2 Provider Limit Predictor.

Uses existing BehavioralFeatures from risk/features.py plus new features
for predicting how many bets remain before a provider limits the account.
"""
from src.constants import PLATFORM_MAP


def extract_limit_features(
    stake_entropy: float, market_diversity: float,
    timing_regularity: float, outcome_correlation: float,
    bonus_usage_ratio: float, clv_score: float, win_rate_deviation: float,
    total_bets: int, account_age_days: int,
    total_turnover: float, provider_id: str,
    similar_platform_limits: int,
    max_single_bet_edge: float = 0.0,
    bet_frequency_trend: float = 0.0,
    sport_concentration_top3: float = 0.0,
    has_used_freebet: bool = False,
) -> dict:
    """Extract feature vector for limit prediction."""
    return {
        "stake_entropy": stake_entropy,
        "market_diversity": market_diversity,
        "timing_regularity": timing_regularity,
        "outcome_correlation": outcome_correlation,
        "bonus_usage_ratio": bonus_usage_ratio,
        "clv_score": clv_score,
        "win_rate_deviation": win_rate_deviation,
        "total_bets": total_bets,
        "account_age_days": account_age_days,
        "total_turnover": total_turnover,
        "provider_platform": PLATFORM_MAP.get(provider_id, provider_id),
        "similar_platform_limits": similar_platform_limits,
        "max_single_bet_edge": max_single_bet_edge,
        "bet_frequency_trend": bet_frequency_trend,
        "sport_concentration_top3": sport_concentration_top3,
        "has_used_freebet": int(has_used_freebet),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_betting_models.py::test_limit_features_extraction -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/features/limit_features.py
git commit -m "feat(ml): add M2 limit predictor feature extraction"
```

---

### Task 8: M2 Model Class

**Files:**
- Create: `backend/src/ml/models/limit_predictor.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_betting_models.py`:

```python
def test_limit_predictor_low_data_logistic():
    """M2 uses logistic regression with <50 samples."""
    from src.ml.models.limit_predictor import LimitPredictorModel
    model = LimitPredictorModel()
    # 25 samples — should use logistic regression
    data = []
    for i in range(25):
        features = {
            "clv_score": np.random.uniform(0, 1),
            "total_bets": np.random.randint(10, 200),
            "max_single_bet_edge": np.random.uniform(0, 30),
            "stake_entropy": np.random.uniform(0, 1),
            "similar_platform_limits": np.random.randint(0, 3),
        }
        data.append(_mock_ml_feature(features, outcome_binary=1 if np.random.random() > 0.7 else 0))
    result = model.train(data)
    assert result is not None  # Should train with logistic regression at 20+ samples
    assert result.get("algorithm") == "logistic_regression"


def test_limit_predictor_high_data_lgbm():
    """M2 graduates to LightGBM with 50+ samples."""
    from src.ml.models.limit_predictor import LimitPredictorModel
    model = LimitPredictorModel()
    data = []
    for i in range(60):
        features = {
            "clv_score": np.random.uniform(0, 1),
            "total_bets": np.random.randint(10, 200),
            "max_single_bet_edge": np.random.uniform(0, 30),
            "stake_entropy": np.random.uniform(0, 1),
            "market_diversity": np.random.uniform(0, 1),
            "timing_regularity": np.random.uniform(0, 1),
            "outcome_correlation": np.random.uniform(0, 1),
            "bonus_usage_ratio": np.random.uniform(0, 1),
            "win_rate_deviation": np.random.uniform(0, 1),
            "account_age_days": np.random.randint(1, 365),
            "total_turnover": np.random.uniform(100, 50000),
            "similar_platform_limits": np.random.randint(0, 5),
            "bet_frequency_trend": np.random.uniform(-1, 1),
            "sport_concentration_top3": np.random.uniform(0.3, 1.0),
            "has_used_freebet": np.random.randint(0, 2),
        }
        data.append(_mock_ml_feature(features, outcome_binary=1 if np.random.random() > 0.7 else 0))
    result = model.train(data)
    assert result is not None
    assert result.get("algorithm") == "lightgbm"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_betting_models.py::test_limit_predictor_low_data_logistic -v`
Expected: FAIL

- [ ] **Step 3: Implement LimitPredictorModel**

```python
# backend/src/ml/models/limit_predictor.py
"""M2: Provider Limit Predictor.

Predicts how many bets remain before a provider limits the account.
Uses logistic regression at low data (<50 events), graduates to LightGBM at 50+.

Min training data: 20 limit events.
"""
import logging
import json
import numpy as np
from pathlib import Path

logger = logging.getLogger(__name__)

# Low-data features (logistic regression with 20-50 samples)
LOW_DATA_FEATURES = [
    "clv_score", "total_bets", "max_single_bet_edge",
    "stake_entropy", "similar_platform_limits",
]

# Full features (LightGBM at 50+ samples)
FULL_FEATURES = [
    "clv_score", "total_bets", "max_single_bet_edge",
    "stake_entropy", "market_diversity", "timing_regularity",
    "outcome_correlation", "bonus_usage_ratio", "win_rate_deviation",
    "account_age_days", "total_turnover", "similar_platform_limits",
    "bet_frequency_trend", "sport_concentration_top3", "has_used_freebet",
    "avg_stake_vs_provider_median", "time_between_bets_cv",
    "time_from_odds_change_to_bet", "same_side_as_sharp_movement_pct",
    "deposit_withdrawal_ratio",
]

MIN_SAMPLES = 20
LGBM_THRESHOLD = 50
MODELS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "models"


class LimitPredictorModel:
    """Predicts provider limiting risk."""

    def __init__(self):
        self.model = None
        self.feature_names = LOW_DATA_FEATURES
        self.algorithm = None

    def train(self, data: list) -> dict | None:
        """Train on limit event data."""
        if len(data) < MIN_SAMPLES:
            logger.info(f"Limit predictor: {len(data)}/{MIN_SAMPLES} samples — skipping")
            return None

        use_lgbm = len(data) >= LGBM_THRESHOLD
        self.feature_names = FULL_FEATURES if use_lgbm else LOW_DATA_FEATURES
        X, y = self._prepare_data(data)

        if use_lgbm:
            from src.ml.optimizer.trainer import train_model
            result = train_model(X, y, task="classification", min_samples=MIN_SAMPLES)
            if result is None:
                return None
            self.model = result["model"]
            self.algorithm = "lightgbm"
        else:
            # Logistic regression for low data
            from sklearn.linear_model import LogisticRegression
            model = LogisticRegression(C=0.1, max_iter=1000)
            model.fit(X, y)
            self.model = model
            self.algorithm = "logistic_regression"

        # Save
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        file_path = str(MODELS_DIR / "limit_predictor_latest.joblib")
        try:
            import joblib
            joblib.dump({
                "model": self.model,
                "feature_names": self.feature_names,
                "task": "classification",
                "algorithm": self.algorithm,
            }, file_path)
        except ImportError:
            logger.warning("joblib not installed")
            return None

        return {
            "model": self.model,
            "file_path": file_path,
            "training_data_count": len(data),
            "algorithm": self.algorithm,
            "validation_score": result.get("validation_score") if use_lgbm else None,
        }

    def predict(self, features: dict) -> float | None:
        """Predict limit probability (0=safe, 1=about to be limited)."""
        if self.model is None:
            return None
        X = np.array([[features.get(f, 0.0) for f in self.feature_names]])
        try:
            proba = self.model.predict_proba(X)
            return float(proba[0][1])
        except Exception as e:
            logger.warning(f"Limit prediction failed: {e}")
            return None

    def _prepare_data(self, data: list) -> tuple:
        X_list, y_list = [], []
        for row in data:
            features = row.features if isinstance(row.features, dict) else json.loads(row.features)
            x = [features.get(f, 0.0) for f in self.feature_names]
            x = [0.0 if v is None else float(v) for v in x]
            X_list.append(x)
            y_list.append(int(row.outcome_binary))
        return np.array(X_list), np.array(y_list)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_betting_models.py -v -k "limit_predictor"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/models/limit_predictor.py
git commit -m "feat(ml): add M2 Limit Predictor with logistic→LightGBM graduation"
```

---

## Chunk 4: M3 Devig Method Selector

### Task 9: M3 Feature Extraction

**Files:**
- Create: `backend/src/ml/features/devig_features.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_betting_models.py`:

```python
def test_devig_features_extraction():
    """M3 devig features extract market-level context."""
    from src.ml.features.devig_features import extract_devig_features
    features = extract_devig_features(
        sport="football", market="1x2", num_outcomes=3,
        pinnacle_overround=0.03, favourite_odds=1.5,
        odds_range=5.0, league="premier_league",
    )
    assert features["sport"] == "football"
    assert features["num_outcomes"] == 3
    assert features["has_draw_option"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_betting_models.py::test_devig_features_extraction -v`
Expected: FAIL

- [ ] **Step 3: Implement devig feature extraction**

```python
# backend/src/ml/features/devig_features.py
"""Extract features for M3 Devig Method Selector.

Determines which devigging method (multiplicative, additive, power)
produces the most accurate fair odds for a given market context.
"""

SPORT_ENCODING = {
    "football": 0, "basketball": 1, "tennis": 2, "ice_hockey": 3,
    "american_football": 4, "baseball": 5, "mma": 6, "esports": 7,
    "handball": 8, "volleyball": 9, "boxing": 10, "rugby": 11,
    "cricket": 12, "darts": 13, "table_tennis": 14, "curling": 15,
}

MARKET_ENCODING = {"1x2": 0, "moneyline": 1, "spread": 2, "total": 3}


def extract_devig_features(
    sport: str, market: str, num_outcomes: int,
    pinnacle_overround: float, favourite_odds: float,
    odds_range: float, league: str = "",
    market_age_hours: float = 0.0,
) -> dict:
    """Extract feature vector for devig method selection."""
    # League tier: top leagues = 1, others = 0
    is_top_league = 1 if league and any(
        t in league.lower() for t in [
            "premier", "la_liga", "bundesliga", "serie_a", "ligue_1",
            "nba", "nfl", "mlb", "nhl", "champions", "europa",
        ]
    ) else 0

    return {
        "sport": SPORT_ENCODING.get(sport, len(SPORT_ENCODING)),
        "market_type": MARKET_ENCODING.get(market, len(MARKET_ENCODING)),
        "num_outcomes": num_outcomes,
        "pinnacle_overround": pinnacle_overround,
        "favourite_odds": favourite_odds,
        "odds_range": odds_range,
        "league_tier": is_top_league,
        "market_age_hours": market_age_hours,
        "has_draw_option": 1 if num_outcomes == 3 else 0,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_betting_models.py::test_devig_features_extraction -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/features/devig_features.py
git commit -m "feat(ml): add M3 devig method selector feature extraction"
```

---

### Task 10: M3 Model Class

**Files:**
- Create: `backend/src/ml/models/devig_selector.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_betting_models.py`:

```python
def test_devig_selector_train():
    """M3 trains multiclass classifier."""
    from src.ml.models.devig_selector import DevigSelectorModel
    model = DevigSelectorModel()
    data = []
    methods = ["multiplicative", "additive", "power"]
    for i in range(550):
        features = {
            "sport": np.random.randint(0, 10),
            "market_type": np.random.randint(0, 4),
            "num_outcomes": np.random.choice([2, 3]),
            "pinnacle_overround": np.random.uniform(0.02, 0.08),
            "favourite_odds": np.random.uniform(1.1, 5.0),
            "odds_range": np.random.uniform(0.5, 10.0),
            "league_tier": np.random.randint(0, 2),
            "market_age_hours": np.random.uniform(0, 48),
            "has_draw_option": np.random.randint(0, 2),
        }
        method = np.random.choice(methods)
        mock = _mock_ml_feature(features, outcome_binary=methods.index(method))
        mock.outcome = float(methods.index(method))
        data.append(mock)
    result = model.train(data)
    assert result is not None


def test_devig_selector_predict():
    """M3 returns method name and confidence."""
    from src.ml.models.devig_selector import DevigSelectorModel
    model = DevigSelectorModel()
    data = []
    methods = ["multiplicative", "additive", "power"]
    for i in range(550):
        features = {
            "sport": np.random.randint(0, 10),
            "market_type": np.random.randint(0, 4),
            "num_outcomes": np.random.choice([2, 3]),
            "pinnacle_overround": np.random.uniform(0.02, 0.08),
            "favourite_odds": np.random.uniform(1.1, 5.0),
            "odds_range": np.random.uniform(0.5, 10.0),
            "league_tier": np.random.randint(0, 2),
            "market_age_hours": np.random.uniform(0, 48),
            "has_draw_option": np.random.randint(0, 2),
        }
        mock = _mock_ml_feature(features, outcome_binary=methods.index(method))
        data.append(mock)
    model.train(data)
    prediction = model.predict({
        "sport": 0, "market_type": 0, "num_outcomes": 3,
        "pinnacle_overround": 0.03, "favourite_odds": 1.5,
        "odds_range": 3.0, "league_tier": 1, "market_age_hours": 2.0,
        "has_draw_option": 1,
    })
    assert prediction is not None
    assert "method" in prediction
    assert prediction["method"] in methods
    assert 0 <= prediction["confidence"] <= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_betting_models.py::test_devig_selector_train -v`
Expected: FAIL

- [ ] **Step 3: Implement DevigSelectorModel**

```python
# backend/src/ml/models/devig_selector.py
"""M3: Devig Method Selector.

Multi-class classifier to pick the best devigging method per market context.
Classes: multiplicative (0), additive (1), power (2).

Min training data: 500 bets across sports/markets.
"""
import logging
import json
import numpy as np
from pathlib import Path

logger = logging.getLogger(__name__)

FEATURE_NAMES = [
    "sport", "market_type", "num_outcomes", "pinnacle_overround",
    "favourite_odds", "odds_range", "league_tier", "market_age_hours",
    "has_draw_option",
]

METHODS = ["multiplicative", "additive", "power"]
MIN_SAMPLES = 500
MODELS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "models"


class DevigSelectorModel:
    """Multi-class classifier for devig method selection."""

    def __init__(self):
        self.feature_names = FEATURE_NAMES
        self.model = None

    def train(self, data: list) -> dict | None:
        if len(data) < MIN_SAMPLES:
            logger.info(f"Devig selector: {len(data)}/{MIN_SAMPLES} — skipping")
            return None

        X, y = self._prepare_data(data)

        try:
            import lightgbm as lgb
        except ImportError:
            logger.warning("lightgbm not installed")
            return None

        params = {
            "objective": "multiclass",
            "num_class": 3,
            "num_leaves": 15,
            "learning_rate": 0.05,
            "n_estimators": 100,
            "verbose": -1,
            "min_child_samples": 5,
        }
        model = lgb.LGBMClassifier(**params)
        model.fit(X, y)
        self.model = model

        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        file_path = str(MODELS_DIR / "devig_selector_latest.joblib")
        try:
            import joblib
            joblib.dump({
                "model": model,
                "feature_names": self.feature_names,
                "task": "multiclass",
            }, file_path)
        except ImportError:
            return None

        return {
            "model": model,
            "file_path": file_path,
            "training_data_count": len(data),
        }

    def predict(self, features: dict) -> dict | None:
        """Returns {"method": "multiplicative", "confidence": 0.82}."""
        if self.model is None:
            return None
        X = np.array([[features.get(f, 0.0) for f in self.feature_names]])
        try:
            proba = self.model.predict_proba(X)[0]
            best_idx = int(np.argmax(proba))
            return {
                "method": METHODS[best_idx],
                "confidence": float(proba[best_idx]),
            }
        except Exception as e:
            logger.warning(f"Devig selector prediction failed: {e}")
            return None

    def _prepare_data(self, data: list) -> tuple:
        X_list, y_list = [], []
        for row in data:
            features = row.features if isinstance(row.features, dict) else json.loads(row.features)
            x = [features.get(f, 0.0) for f in self.feature_names]
            x = [0.0 if v is None else float(v) for v in x]
            X_list.append(x)
            # Use outcome (float: 0=multiplicative, 1=additive, 2=power), NOT outcome_binary
            y_list.append(int(row.outcome))
        return np.array(X_list), np.array(y_list)
```

**Note on M3 training data:** M3 uses `outcome` field (not `outcome_binary`) to store the 3-class label. When logging M3 training data to `ml_features`, set `outcome=method_index` (0/1/2) and `outcome_binary=None`. The `devig_method_log` table stores all three methods' results; a resolution job computes which method was best and updates the `ml_features.outcome` field.

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/test_betting_models.py -v -k "devig_selector"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/models/devig_selector.py
git commit -m "feat(ml): add M3 Devig Method Selector multiclass model"
```

---

### Task 11: M3 Devig Integration + Method Logging

**Files:**
- Modify: `backend/src/analysis/devig.py`

- [ ] **Step 1: Add devig method logging function**

Add to the bottom of `devig.py`:

```python
def compute_all_methods(odds_list: list[float]) -> dict:
    """Compute fair odds using all 3 methods for M3 training data.

    Returns dict with keys: multiplicative, additive, power.
    Each value is a list of fair odds (same length as odds_list).
    """
    return {
        "multiplicative": devig_multiplicative(odds_list),
        "additive": devig_additive(odds_list),
        "power": devig_power(odds_list),
    }
```

- [ ] **Step 2: Add ML-guided devig selection hook**

This is a best-effort hook that will be called from `get_fair_odds_for_outcome()` when M3 is available:

```python
def _ml_devig_method(sport: str, market: str, num_outcomes: int,
                     overround: float, favourite_odds: float,
                     odds_range: float) -> str | None:
    """Ask M3 for best devig method. Returns None if model unavailable."""
    try:
        from src.ml.serving.predictor import get_predictor
        from src.ml.features.devig_features import extract_devig_features
        predictor = get_predictor()
        if not predictor.is_loaded("devig_selector"):
            return None
        features = extract_devig_features(
            sport=sport, market=market, num_outcomes=num_outcomes,
            pinnacle_overround=overround, favourite_odds=favourite_odds,
            odds_range=odds_range,
        )
        result = predictor.predict("devig_selector", features)
        if result and isinstance(result, dict):
            methods = ["multiplicative", "additive", "power"]
            class_idx = result.get("class", 0)
            return methods[class_idx] if class_idx < len(methods) else None
        return None
    except Exception:
        return None
```

**Note:** The subagent should wire `_ml_devig_method()` into `get_fair_odds_for_outcome()` as an optional method override. When M3 returns a method, use that instead of the default `"multiplicative"`. Fall back to multiplicative if M3 returns None.

- [ ] **Step 3: Commit**

```bash
git add backend/src/analysis/devig.py
git commit -m "feat(ml): add M3 devig method selector integration + compute_all_methods"
```

---

## Chunk 5: M4 Boost Calibrator

### Task 12: M4 Feature Extraction

**Files:**
- Create: `backend/src/ml/features/boost_features.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_betting_models.py`:

```python
def test_boost_features_extraction():
    """M4 boost features extract from specials data."""
    from src.ml.features.boost_features import extract_boost_features
    features = extract_boost_features(
        llm_raw_probability=0.45, llm_confidence=3,
        boost_type="single", sport="football", league="premier_league",
        num_legs=1, has_pinnacle_match=True,
        pinnacle_implied_prob=0.42, original_odds=2.20,
        boosted_odds=2.80, provider="betsson",
        hours_to_event=5.0, llm_reasoning_length=500,
    )
    assert features["llm_raw_probability"] == 0.45
    assert features["boost_margin"] == pytest.approx((2.80 - 2.20) / 2.20, rel=0.01)
    assert features["has_pinnacle_match"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_betting_models.py::test_boost_features_extraction -v`
Expected: FAIL

- [ ] **Step 3: Implement boost feature extraction**

```python
# backend/src/ml/features/boost_features.py
"""Extract features for M4 LLM Boost Calibrator.

Calibrates the LLM's probability output based on historical accuracy patterns.
The LLM does all research — this model only adjusts the probability.
"""

SPORT_ENCODING = {
    "football": 0, "basketball": 1, "tennis": 2, "ice_hockey": 3,
    "american_football": 4, "baseball": 5, "mma": 6, "esports": 7,
    "handball": 8, "volleyball": 9,
}


def extract_boost_features(
    llm_raw_probability: float, llm_confidence: int,
    boost_type: str, sport: str, league: str,
    num_legs: int, has_pinnacle_match: bool,
    pinnacle_implied_prob: float | None,
    original_odds: float, boosted_odds: float,
    provider: str, hours_to_event: float = 0.0,
    llm_reasoning_length: int = 0,
    brave_results_count: int = 0,
    legs_matched_ratio: float = 0.0,
    day_of_week: int = 0,
) -> dict:
    """Extract feature vector for boost calibration."""
    boost_margin = (boosted_odds - original_odds) / original_odds if original_odds > 0 else 0

    # Keyword flags for systematic mispricing patterns
    keyword_anytime_scorer = 1 if "anytime" in (boost_type or "").lower() else 0
    keyword_both_teams = 1 if "both teams" in (boost_type or "").lower() else 0
    keyword_over = 1 if "over" in (boost_type or "").lower() else 0

    return {
        "llm_raw_probability": llm_raw_probability,
        "llm_confidence": llm_confidence,
        "boost_type_single": 1 if boost_type == "single" else 0,
        "boost_type_combo": 1 if "combo" in boost_type or "leg" in boost_type else 0,
        "sport": SPORT_ENCODING.get(sport, len(SPORT_ENCODING)),
        "num_legs": num_legs,
        "has_pinnacle_match": int(has_pinnacle_match),
        "pinnacle_implied_prob": pinnacle_implied_prob or 0.0,
        "legs_matched_ratio": legs_matched_ratio,
        "original_odds": original_odds,
        "boosted_odds": boosted_odds,
        "boost_margin": boost_margin,
        "hours_to_event": hours_to_event,
        "llm_reasoning_length": llm_reasoning_length,
        "brave_results_count": brave_results_count,
        "keyword_anytime_scorer": keyword_anytime_scorer,
        "keyword_both_teams": keyword_both_teams,
        "keyword_over": keyword_over,
        "day_of_week": day_of_week,
    }
```

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/features/boost_features.py
git commit -m "feat(ml): add M4 boost calibrator feature extraction"
```

---

### Task 13: M4 Model Class

**Files:**
- Create: `backend/src/ml/models/boost_calibrator.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_betting_models.py`:

```python
def test_boost_calibrator_isotonic():
    """M4 uses isotonic regression for calibration."""
    from src.ml.models.boost_calibrator import BoostCalibratorModel
    model = BoostCalibratorModel()
    data = []
    for i in range(120):
        llm_prob = np.random.uniform(0.1, 0.9)
        features = {
            "llm_raw_probability": llm_prob,
            "llm_confidence": np.random.randint(1, 6),
            "boost_type_single": np.random.randint(0, 2),
            "boost_type_combo": 0,
            "sport": np.random.randint(0, 5),
            "num_legs": 1,
            "has_pinnacle_match": np.random.randint(0, 2),
            "pinnacle_implied_prob": np.random.uniform(0.2, 0.8),
            "legs_matched_ratio": np.random.uniform(0, 1),
            "original_odds": np.random.uniform(1.5, 5.0),
            "boosted_odds": np.random.uniform(2.0, 7.0),
            "boost_margin": np.random.uniform(0.1, 0.5),
            "hours_to_event": np.random.uniform(1, 48),
            "llm_reasoning_length": np.random.randint(100, 2000),
            "brave_results_count": np.random.randint(0, 20),
        }
        outcome = 1 if np.random.random() < llm_prob * 0.8 else 0  # Imperfect calibration
        data.append(_mock_ml_feature(features, outcome_binary=outcome))
    result = model.train(data)
    assert result is not None


def test_boost_calibrator_predict():
    """M4 returns calibrated probability."""
    from src.ml.models.boost_calibrator import BoostCalibratorModel
    model = BoostCalibratorModel()
    data = []
    for i in range(120):
        llm_prob = np.random.uniform(0.1, 0.9)
        features = {"llm_raw_probability": llm_prob, "llm_confidence": 3,
                     "boost_type_single": 1, "boost_type_combo": 0, "sport": 0,
                     "num_legs": 1, "has_pinnacle_match": 1,
                     "pinnacle_implied_prob": 0.4, "legs_matched_ratio": 1.0,
                     "original_odds": 2.5, "boosted_odds": 3.0, "boost_margin": 0.2,
                     "hours_to_event": 5, "llm_reasoning_length": 500,
                     "brave_results_count": 10}
        outcome = 1 if np.random.random() < llm_prob else 0
        data.append(_mock_ml_feature(features, outcome_binary=outcome))
    model.train(data)
    prob = model.predict({"llm_raw_probability": 0.5, "llm_confidence": 3,
                          "boost_type_single": 1, "boost_type_combo": 0, "sport": 0,
                          "num_legs": 1, "has_pinnacle_match": 1,
                          "pinnacle_implied_prob": 0.4, "legs_matched_ratio": 1.0,
                          "original_odds": 2.5, "boosted_odds": 3.0, "boost_margin": 0.2,
                          "hours_to_event": 5, "llm_reasoning_length": 500,
                          "brave_results_count": 10})
    assert prob is not None
    assert 0 <= prob <= 1
```

- [ ] **Step 2: Implement BoostCalibratorModel**

```python
# backend/src/ml/models/boost_calibrator.py
"""M4: LLM Boost Calibrator.

Isotonic regression on top of LLM probability output.
Adjusts LLM's self-reported probability based on historical accuracy.

Min training data: 100 resolved boosts.
"""
import logging
import json
import numpy as np
from pathlib import Path

logger = logging.getLogger(__name__)

FEATURE_NAMES = [
    "llm_raw_probability", "llm_confidence",
    "boost_type_single", "boost_type_combo", "sport",
    "num_legs", "has_pinnacle_match", "pinnacle_implied_prob",
    "legs_matched_ratio", "original_odds", "boosted_odds",
    "boost_margin", "hours_to_event", "llm_reasoning_length",
    "brave_results_count",
    "keyword_anytime_scorer", "keyword_both_teams", "keyword_over",
    "day_of_week",
]

MIN_SAMPLES = 100
MODELS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "models"


class BoostCalibratorModel:
    """Isotonic regression calibrator for LLM boost probabilities."""

    def __init__(self):
        self.feature_names = FEATURE_NAMES
        self.isotonic_model = None
        self.lgbm_model = None

    def train(self, data: list) -> dict | None:
        if len(data) < MIN_SAMPLES:
            logger.info(f"Boost calibrator: {len(data)}/{MIN_SAMPLES} — skipping")
            return None

        X, y = self._prepare_data(data)

        # Primary: isotonic regression on LLM probability
        from sklearn.isotonic import IsotonicRegression
        llm_probs = X[:, 0]  # First feature is llm_raw_probability
        self.isotonic_model = IsotonicRegression(out_of_bounds="clip")
        self.isotonic_model.fit(llm_probs, y)

        # Secondary: LightGBM on all features for richer calibration
        try:
            import lightgbm as lgb
            params = {
                "objective": "binary", "metric": "binary_logloss",
                "num_leaves": 10, "learning_rate": 0.05,
                "n_estimators": 50, "verbose": -1, "min_child_samples": 5,
            }
            self.lgbm_model = lgb.LGBMClassifier(**params)
            self.lgbm_model.fit(X, y)
        except ImportError:
            pass  # Isotonic alone is fine

        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        file_path = str(MODELS_DIR / "boost_calibrator_latest.joblib")
        try:
            import joblib
            joblib.dump({
                "isotonic_model": self.isotonic_model,
                "lgbm_model": self.lgbm_model,
                "feature_names": self.feature_names,
                "task": "calibration",
            }, file_path)
        except ImportError:
            return None

        return {
            "model": self.isotonic_model,
            "file_path": file_path,
            "training_data_count": len(data),
        }

    def predict(self, features: dict) -> float | None:
        """Return calibrated probability."""
        if self.isotonic_model is None:
            return None
        try:
            llm_prob = features.get("llm_raw_probability", 0.5)

            # Use LightGBM if available (richer calibration)
            if self.lgbm_model is not None:
                X = np.array([[features.get(f, 0.0) for f in self.feature_names]])
                proba = self.lgbm_model.predict_proba(X)
                return float(proba[0][1])

            # Fallback to isotonic on LLM probability alone
            calibrated = self.isotonic_model.predict([llm_prob])
            return float(calibrated[0])
        except Exception as e:
            logger.warning(f"Boost calibration failed: {e}")
            return None

    def _prepare_data(self, data: list) -> tuple:
        X_list, y_list = [], []
        for row in data:
            features = row.features if isinstance(row.features, dict) else json.loads(row.features)
            x = [features.get(f, 0.0) for f in self.feature_names]
            x = [0.0 if v is None else float(v) for v in x]
            X_list.append(x)
            y_list.append(int(row.outcome_binary))
        return np.array(X_list), np.array(y_list)
```

- [ ] **Step 3: Run tests**

Run: `cd backend && python -m pytest tests/test_betting_models.py -v -k "boost_calibrator"`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/src/ml/models/boost_calibrator.py
git commit -m "feat(ml): add M4 Boost Calibrator with isotonic + LightGBM calibration"
```

---

### Task 14: M4 EV Enrichment Integration

**Files:**
- Modify: `backend/src/analysis/ev_enrichment.py`

- [ ] **Step 1: Add M4 calibration hook to `enrich_specials_with_ev()`**

After the LLM probability is computed for a boost (after `_parse_llm_response()`), add calibration:

```python
# After computing edge_pct from LLM probability, apply M4 calibration (best-effort)
try:
    from src.ml.serving.predictor import get_predictor
    predictor = get_predictor()
    if predictor.is_loaded("boost_calibrator"):
        from src.ml.features.boost_features import extract_boost_features
        cal_features = extract_boost_features(
            llm_raw_probability=probability,
            llm_confidence=confidence,
            boost_type=special.boost_type or "single",
            sport=special.sport or "",
            league=special.league or "",
            num_legs=num_legs,
            has_pinnacle_match=has_match,
            pinnacle_implied_prob=pinnacle_prob,
            original_odds=special.original_odds or 0,
            boosted_odds=special.boosted_odds,
            provider=special.provider,
            hours_to_event=hours_to_event,
            llm_reasoning_length=len(reasoning or ""),
        )
        calibrated = predictor.predict("boost_calibrator", cal_features)
        if calibrated is not None:
            probability = calibrated  # Replace LLM probability with calibrated
except Exception:
    pass  # Best-effort
```

**Note:** The subagent must read `ev_enrichment.py` to find the exact insertion point. The hook goes after the LLM probability is parsed and before edge_pct is computed.

- [ ] **Step 2: Add feature logging for M4 training**

After computing edge/probability for each boost, log to ml_features:

```python
try:
    from src.ml.feature_store import log_features
    from src.ml.features.boost_features import extract_boost_features
    features = extract_boost_features(
        llm_raw_probability=probability, llm_confidence=confidence,
        boost_type=special.boost_type or "single", sport=special.sport or "",
        league=special.league or "", num_legs=num_legs,
        has_pinnacle_match=has_match, pinnacle_implied_prob=pinnacle_prob,
        original_odds=special.original_odds or 0, boosted_odds=special.boosted_odds,
        provider=special.provider, hours_to_event=hours_to_event,
        llm_reasoning_length=len(reasoning or ""),
    )
    log_features(session, "betting", str(special.id or special.title), "boost", features)
except Exception:
    pass
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/analysis/ev_enrichment.py
git commit -m "feat(ml): wire M4 boost calibration into EV enrichment pipeline"
```

---

## Chunk 6: M8 Adaptive Kelly Sizing

### Task 15: M8 Feature Extraction

**Files:**
- Create: `backend/src/ml/features/kelly_features.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_betting_models.py`:

```python
def test_kelly_features_extraction():
    """M8 kelly features include cross-domain context."""
    from src.ml.features.kelly_features import extract_kelly_features
    features = extract_kelly_features(
        domain="betting",
        model_confidence=0.75,
        predicted_edge=8.0,
        historical_win_rate=0.55,
        historical_avg_return=0.03,
        recent_drawdown_pct=2.5,
        consecutive_wins=3,
        consecutive_losses=0,
        daily_pnl=150.0,
        weekly_pnl=500.0,
        account_utilization=0.4,
        volatility_regime=0.5,
    )
    assert features["model_confidence"] == 0.75
    assert features["predicted_edge"] == 8.0
    assert features["domain_betting"] == 1
    assert features["domain_trading"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement kelly feature extraction**

```python
# backend/src/ml/features/kelly_features.py
"""Extract features for M8 Adaptive Kelly Sizing.

Cross-domain model — serves both sports betting and trading.
Features capture opportunity quality, recent performance, and risk context.
"""


def extract_kelly_features(
    domain: str,
    model_confidence: float,
    predicted_edge: float,
    historical_win_rate: float,
    historical_avg_return: float,
    recent_drawdown_pct: float,
    consecutive_wins: int,
    consecutive_losses: int,
    daily_pnl: float,
    weekly_pnl: float,
    account_utilization: float,
    volatility_regime: float,
    # Sports-specific (optional)
    provider_remaining_lifetime: float | None = None,
    is_freebet: bool = False,
    bonus_wagering_remaining: float = 0.0,
    time_of_day: int = 12,
    # Trading-specific (optional)
    setup_type: str | None = None,
    gex: float | None = None,
    correlation_with_open: float = 0.0,
    session_volume_regime: float = 1.0,
) -> dict:
    """Extract feature vector for adaptive Kelly sizing."""
    return {
        "domain_betting": 1 if domain == "betting" else 0,
        "domain_trading": 1 if domain == "trading" else 0,
        "model_confidence": model_confidence,
        "predicted_edge": predicted_edge,
        "historical_win_rate": historical_win_rate,
        "historical_avg_return": historical_avg_return,
        "recent_drawdown_pct": recent_drawdown_pct,
        "consecutive_wins": consecutive_wins,
        "consecutive_losses": consecutive_losses,
        "daily_pnl": daily_pnl,
        "weekly_pnl": weekly_pnl,
        "account_utilization": account_utilization,
        "volatility_regime": volatility_regime,
        "time_of_day": time_of_day,
        # Sports
        "provider_remaining_lifetime": provider_remaining_lifetime or 0.0,
        "is_freebet": int(is_freebet),
        "bonus_wagering_remaining": bonus_wagering_remaining,
        # Trading
        "gex": gex or 0.0,
        "correlation_with_open": correlation_with_open,
        "session_volume_regime": session_volume_regime,
    }
```

- [ ] **Step 4: Run test**

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/features/kelly_features.py
git commit -m "feat(ml): add M8 adaptive Kelly feature extraction"
```

---

### Task 16: M8 Model Class

**Files:**
- Create: `backend/src/ml/models/adaptive_kelly.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_betting_models.py`:

```python
def test_adaptive_kelly_train():
    """M8 trains regression model for Kelly fraction."""
    from src.ml.models.adaptive_kelly import AdaptiveKellyModel
    model = AdaptiveKellyModel()
    data = []
    for i in range(350):
        features = {
            "domain_betting": 1, "domain_trading": 0,
            "model_confidence": np.random.uniform(0.5, 1.0),
            "predicted_edge": np.random.uniform(1, 20),
            "historical_win_rate": np.random.uniform(0.4, 0.65),
            "historical_avg_return": np.random.uniform(-0.05, 0.1),
            "recent_drawdown_pct": np.random.uniform(0, 15),
            "consecutive_wins": np.random.randint(0, 10),
            "consecutive_losses": np.random.randint(0, 5),
            "daily_pnl": np.random.uniform(-500, 500),
            "weekly_pnl": np.random.uniform(-2000, 2000),
            "account_utilization": np.random.uniform(0, 1),
            "volatility_regime": np.random.uniform(0, 1),
            "provider_remaining_lifetime": np.random.uniform(0, 200),
            "is_freebet": np.random.randint(0, 2),
            "bonus_wagering_remaining": np.random.uniform(0, 5000),
            "gex": 0.0, "correlation_with_open": 0.0,
            "session_volume_regime": 1.0,
        }
        # Outcome is optimal Kelly fraction (backtest-derived)
        outcome = np.clip(np.random.uniform(0.05, 0.5), 0, 1)
        data.append(_mock_ml_feature(features, outcome_binary=1, outcome=outcome))
    result = model.train(data)
    assert result is not None


def test_adaptive_kelly_predict():
    """M8 returns Kelly fraction between 0 and 1."""
    from src.ml.models.adaptive_kelly import AdaptiveKellyModel
    model = AdaptiveKellyModel()
    data = []
    for i in range(350):
        features = {
            "domain_betting": 1, "domain_trading": 0,
            "model_confidence": np.random.uniform(0.5, 1.0),
            "predicted_edge": np.random.uniform(1, 20),
            "historical_win_rate": np.random.uniform(0.4, 0.65),
            "historical_avg_return": np.random.uniform(-0.05, 0.1),
            "recent_drawdown_pct": np.random.uniform(0, 15),
            "consecutive_wins": np.random.randint(0, 10),
            "consecutive_losses": np.random.randint(0, 5),
            "daily_pnl": np.random.uniform(-500, 500),
            "weekly_pnl": np.random.uniform(-2000, 2000),
            "account_utilization": np.random.uniform(0, 1),
            "volatility_regime": np.random.uniform(0, 1),
            "provider_remaining_lifetime": 0.0,
            "is_freebet": 0, "bonus_wagering_remaining": 0.0,
            "gex": 0.0, "correlation_with_open": 0.0,
            "session_volume_regime": 1.0,
        }
        outcome = np.clip(np.random.uniform(0.05, 0.5), 0, 1)
        data.append(_mock_ml_feature(features, outcome_binary=1, outcome=outcome))
    model.train(data)
    kelly = model.predict({
        "domain_betting": 1, "domain_trading": 0,
        "model_confidence": 0.8, "predicted_edge": 10.0,
        "historical_win_rate": 0.55, "historical_avg_return": 0.04,
        "recent_drawdown_pct": 3.0, "consecutive_wins": 2,
        "consecutive_losses": 0, "daily_pnl": 100.0,
        "weekly_pnl": 400.0, "account_utilization": 0.3,
        "volatility_regime": 0.5, "provider_remaining_lifetime": 100.0,
        "is_freebet": 0, "bonus_wagering_remaining": 0.0,
        "gex": 0.0, "correlation_with_open": 0.0,
        "session_volume_regime": 1.0,
    })
    assert kelly is not None
    assert 0 <= kelly <= 1
```

- [ ] **Step 2: Implement AdaptiveKellyModel**

```python
# backend/src/ml/models/adaptive_kelly.py
"""M8: Adaptive Kelly Sizing.

XGBoost regression to predict optimal Kelly fraction for each opportunity.
Cross-domain: serves both sports betting and trading.

Replaces linear Kelly interpolation by edge (sports) / fixed 1% risk (trading).
Min training data: 300 bets/trades.
"""
import logging
import json
import numpy as np
from pathlib import Path

logger = logging.getLogger(__name__)

FEATURE_NAMES = [
    "domain_betting", "domain_trading",
    "model_confidence", "predicted_edge",
    "historical_win_rate", "historical_avg_return",
    "recent_drawdown_pct", "consecutive_wins", "consecutive_losses",
    "daily_pnl", "weekly_pnl", "account_utilization",
    "volatility_regime", "time_of_day",
    "provider_remaining_lifetime", "is_freebet", "bonus_wagering_remaining",
    "gex", "correlation_with_open", "session_volume_regime",
]

MIN_SAMPLES = 300
MODELS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "models"


class AdaptiveKellyModel:
    """Regression model for optimal Kelly fraction."""

    def __init__(self):
        self.feature_names = FEATURE_NAMES
        self.model = None

    def train(self, data: list) -> dict | None:
        if len(data) < MIN_SAMPLES:
            logger.info(f"Adaptive Kelly: {len(data)}/{MIN_SAMPLES} — skipping")
            return None

        X, y = self._prepare_data(data)

        from src.ml.optimizer.trainer import train_model
        result = train_model(X, y, task="regression", min_samples=MIN_SAMPLES)
        if result is None:
            return None

        self.model = result["model"]

        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        file_path = str(MODELS_DIR / "adaptive_kelly_latest.joblib")
        try:
            import joblib
            joblib.dump({
                "model": self.model,
                "feature_names": self.feature_names,
                "task": "regression",
            }, file_path)
        except ImportError:
            return None

        return {
            "model": self.model,
            "file_path": file_path,
            "training_data_count": len(data),
            "validation_score": result.get("validation_score"),
        }

    def predict(self, features: dict) -> float | None:
        """Predict optimal Kelly fraction (0-1)."""
        if self.model is None:
            return None
        X = np.array([[features.get(f, 0.0) for f in self.feature_names]])
        try:
            pred = self.model.predict(X)
            return float(np.clip(pred[0], 0.0, 1.0))
        except Exception as e:
            logger.warning(f"Kelly prediction failed: {e}")
            return None

    def _prepare_data(self, data: list) -> tuple:
        X_list, y_list = [], []
        for row in data:
            features = row.features if isinstance(row.features, dict) else json.loads(row.features)
            x = [features.get(f, 0.0) for f in self.feature_names]
            x = [0.0 if v is None else float(v) for v in x]
            X_list.append(x)
            # Target is the continuous outcome (optimal Kelly fraction)
            y_list.append(float(row.outcome) if row.outcome is not None else 0.0)
        return np.array(X_list), np.array(y_list)
```

- [ ] **Step 3: Run tests**

Run: `cd backend && python -m pytest tests/test_betting_models.py -v -k "adaptive_kelly"`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/src/ml/models/adaptive_kelly.py
git commit -m "feat(ml): add M8 Adaptive Kelly Sizing regression model"
```

---

### Task 17: M8 Stake Calculator Integration

**Files:**
- Modify: `backend/src/bankroll/stake_calculator.py`

- [ ] **Step 1: Add ML Kelly override hook**

In `StakeCalculator.calculate_stake()`, after computing the standard Kelly fraction but before applying caps, add:

```python
# ML adaptive Kelly (M8) — replaces linear Kelly when model available
try:
    from src.ml.serving.predictor import get_predictor
    predictor = get_predictor()
    if predictor.is_loaded("adaptive_kelly"):
        from src.ml.features.kelly_features import extract_kelly_features
        kelly_features = extract_kelly_features(
            domain="betting",
            model_confidence=getattr(self, '_last_ml_confidence', 0.5),
            predicted_edge=edge_pct,
            historical_win_rate=0.55,  # TODO: compute from bet history
            historical_avg_return=0.03,
            recent_drawdown_pct=0.0,
            consecutive_wins=0,
            consecutive_losses=0,
            daily_pnl=0.0,
            weekly_pnl=0.0,
            account_utilization=0.0,
            volatility_regime=0.5,
        )
        ml_kelly = predictor.predict("adaptive_kelly", kelly_features)
        if ml_kelly is not None:
            kelly_fraction = ml_kelly
except Exception:
    pass  # Best-effort — never block stake calculation
```

**Note:** The historical features (win_rate, drawdown, etc.) need proper computation from bet history. For now, use defaults. A future task can wire these from the actual bet history queries. The integration point is what matters — the feature values will improve as data accumulates.

- [ ] **Step 2: Commit**

```bash
git add backend/src/bankroll/stake_calculator.py
git commit -m "feat(ml): wire M8 adaptive Kelly into stake calculator"
```

---

## Chunk 7: Pipeline Wiring

### Task 18: Risk Calculator Integration (M2)

**Files:**
- Modify: `backend/src/risk/calculator.py`

- [ ] **Step 1: Add M2 prediction hook**

In `RiskCalculator.assess()` (or equivalent method), after computing the rule-based risk score, add:

```python
# ML limit prediction (M2) — supplements rule-based score when model available
try:
    from src.ml.serving.predictor import get_predictor
    predictor = get_predictor()
    if predictor.is_loaded("limit_predictor"):
        from src.ml.features.limit_features import extract_limit_features
        limit_features = extract_limit_features(
            stake_entropy=features.stake_entropy,
            market_diversity=features.market_diversity,
            timing_regularity=features.timing_regularity,
            outcome_correlation=features.outcome_correlation,
            bonus_usage_ratio=features.bonus_usage_ratio,
            clv_score=features.clv_score,
            win_rate_deviation=features.win_rate_deviation,
            total_bets=features.total_bets_all_time,
            account_age_days=features.account_age_days,
            total_turnover=0,  # TODO: compute from bet history
            provider_id=provider_id,
            similar_platform_limits=0,  # TODO: count from limit events
        )
        ml_risk = predictor.predict("limit_predictor", limit_features)
        if ml_risk is not None:
            # Blend: 70% ML, 30% rules when model available
            risk_score = 0.7 * ml_risk + 0.3 * risk_score
except Exception:
    pass  # Best-effort
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/risk/calculator.py
git commit -m "feat(ml): wire M2 limit predictor into risk calculator"
```

---

### Task 19: Training Trigger in Orchestrator

**Files:**
- Modify: `backend/src/pipeline/orchestrator.py`

- [ ] **Step 1: Add weekly training check after extraction**

In the orchestrator's post-extraction hook (where analytics already run), add a weekly training trigger:

```python
# Weekly ML model training (best-effort, after analytics)
try:
    import time
    from src.ml.training.train_all import TrainingOrchestrator
    # Only train once per day (check last training time)
    last_train_key = "_ml_last_train_day"
    today = time.strftime("%Y-%m-%d")
    if getattr(self, last_train_key, None) != today:
        orch = TrainingOrchestrator()
        results = orch.train_all(self.session)
        setattr(self, last_train_key, today)
        for model_name, status in results.items():
            if status == "trained":
                logger.info(f"ML model trained: {model_name}")
            elif status != "insufficient_data":
                logger.warning(f"ML training issue for {model_name}: {status}")
except Exception as e:
    logger.debug(f"ML training check skipped: {e}")
```

- [ ] **Step 2: Load models at startup**

In the orchestrator's `__init__()` or startup method:

```python
# Load ML models from registry (best-effort)
try:
    from src.ml.serving.predictor import get_predictor
    predictor = get_predictor()
    loaded = predictor.load_from_registry(self.session)
    if loaded > 0:
        logger.info(f"Loaded {loaded} ML models from registry")
except Exception:
    pass
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/pipeline/orchestrator.py
git commit -m "feat(ml): add daily training trigger and model loading at startup"
```

---

### Task 20: API Endpoints for ML Status

**Files:**
- Modify: `backend/src/api/routes/extraction.py`

- [ ] **Step 1: Add ML model status endpoint**

```python
@router.get("/ml/status")
def get_ml_status(session=Depends(get_session)):
    """Get status of all ML models (loaded, training data count, last trained)."""
    from src.ml.serving.predictor import get_predictor
    from src.ml.training.train_all import TrainingOrchestrator
    from src.db.models import MlModelRegistry

    predictor = get_predictor()
    orch = TrainingOrchestrator()
    thresholds = orch.check_thresholds(session)

    # Get registry info
    models = session.query(MlModelRegistry).order_by(
        MlModelRegistry.model_name, MlModelRegistry.version.desc()
    ).all()

    registry = {}
    for m in models:
        if m.model_name not in registry:
            registry[m.model_name] = {
                "version": m.version,
                "is_active": bool(m.is_active),
                "training_data_count": m.training_data_count,
                "validation_metric": m.validation_metric,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }

    result = {}
    for name, config in orch.model_configs.items():
        result[name] = {
            "loaded": predictor.is_loaded(name),
            "data_ready": thresholds.get(name, False),
            "min_samples": config["min_samples"],
            "registry": registry.get(name),
        }
    return result
```

- [ ] **Step 2: Add ML training trigger endpoint**

```python
@router.post("/ml/train")
def trigger_ml_training(session=Depends(get_session)):
    """Manually trigger ML model training."""
    from src.ml.training.train_all import TrainingOrchestrator
    orch = TrainingOrchestrator()
    results = orch.train_all(session)
    session.commit()
    return results
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/api/routes/extraction.py
git commit -m "feat(ml): add ML status and manual training API endpoints"
```

---

### Task 21: Frontend ML Status Display

**Files:**
- Modify: `frontend/src/services/api.ts`
- Modify: `frontend/src/components/Terminal/pages/StatsPage.tsx`

- [ ] **Step 1: Add API methods**

In `api.ts`, add:

```typescript
async getMlStatus(): Promise<Record<string, any>> {
    const response = await this.get('/extraction/ml/status');
    return response;
},

async triggerMlTraining(): Promise<Record<string, string>> {
    const response = await this.post('/extraction/ml/train');
    return response;
},
```

- [ ] **Step 2: Add ML Models section to StatsPage**

Add a compact table showing ML model status (loaded/not, data progress, last trained):

```
╔══ ML MODELS ════════════════════════════════════════╗
║ Model             │ Status  │ Data      │ Threshold ║
║ edge_quality      │ ○ idle  │ 45/200    │ 200 bets  ║
║ limit_predictor   │ ○ idle  │ 2/20      │ 20 events ║
║ devig_selector    │ ○ idle  │ 45/500    │ 500 bets  ║
║ boost_calibrator  │ ○ idle  │ 12/100    │ 100 boost ║
║ adaptive_kelly    │ ○ idle  │ 45/300    │ 300 bets  ║
╚════════════════════════════════════════════════════╝
```

Where `●` = loaded and active, `○` = idle (below threshold).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/services/api.ts frontend/src/components/Terminal/pages/StatsPage.tsx
git commit -m "feat(ml): add ML model status display to StatsPage"
```

---

### Task 22: CLV Resolution + M2/M3 Feature Logging Pipeline

**Files:**
- Modify: `backend/src/ml/feature_store.py`
- Modify: `backend/src/analysis/scanner.py` (or wherever bet resolution happens)

- [ ] **Step 1: Add CLV resolution function to feature_store.py**

```python
def resolve_clv_outcomes(session: Session) -> int:
    """Backfill outcome fields for betting ml_features rows.

    Joins ml_features (source_type='opportunity') with opportunities table
    to get closing_line_value. Sets outcome=CLV, outcome_binary=1 if CLV>0.
    Returns count of rows updated.
    """
    from src.db.models import MlFeature
    from sqlalchemy import text

    updated = 0
    unresolved = session.query(MlFeature).filter(
        MlFeature.source_type == "opportunity",
        MlFeature.outcome.is_(None),
    ).all()

    for row in unresolved:
        # Look up CLV from opportunities table
        result = session.execute(
            text("SELECT closing_line_value FROM opportunities WHERE id = :oid"),
            {"oid": row.source_id},
        ).fetchone()
        if result and result[0] is not None:
            row.outcome = float(result[0])
            row.outcome_binary = 1 if result[0] > 0 else 0
            row.resolved_at = datetime.now(timezone.utc)
            updated += 1

    session.flush()
    return updated
```

- [ ] **Step 2: Add M3 devig method logging hook**

In `devig.py`, when `compute_all_methods()` is available, wire it to log all 3 methods at bet placement time. Add a helper:

```python
def log_devig_comparison(session, bet_id, event_id, market, outcome, odds_list,
                         sport=None, league=None):
    """Log all 3 devig method results for M3 training. Called at bet placement."""
    try:
        from src.ml.feature_store import log_features
        all_methods = compute_all_methods(odds_list)
        num_outcomes = len(odds_list)
        overround = calculate_margin(odds_list)
        features = {
            "sport": sport or "", "market_type": market,
            "num_outcomes": num_outcomes,
            "pinnacle_overround": overround,
            "favourite_odds": min(odds_list),
            "odds_range": max(odds_list) - min(odds_list),
            "has_draw_option": 1 if num_outcomes == 3 else 0,
        }
        log_features(session, "betting", str(bet_id), "devig_comparison", features)
    except Exception:
        pass  # Best-effort
```

- [ ] **Step 3: Add M2 limit event logging hook**

In `risk/calculator.py`, when a provider is flagged as limited, log features for M2:

```python
def log_limit_event(session, provider_id, features: dict, is_limited: bool):
    """Log a limit event for M2 training. Called when provider limit detected."""
    try:
        from src.ml.feature_store import log_features
        log_features(session, "betting", f"limit_{provider_id}", "limit_event",
                     features, feature_version=1)
        # Immediately resolve outcome since we know the answer
        from src.ml.feature_store import resolve_outcome
        resolve_outcome(session, "limit_event", f"limit_{provider_id}",
                       outcome=1.0 if is_limited else 0.0,
                       outcome_binary=1 if is_limited else 0)
    except Exception:
        pass  # Best-effort
```

- [ ] **Step 4: Wire CLV resolution into orchestrator post-extraction**

Add to the post-extraction hook in `orchestrator.py`:

```python
# Resolve CLV outcomes for ML training (best-effort)
try:
    from src.ml.feature_store import resolve_clv_outcomes
    resolved = resolve_clv_outcomes(self.session)
    if resolved > 0:
        logger.info(f"Resolved CLV for {resolved} ML feature rows")
except Exception:
    pass
```

- [ ] **Step 5: Commit**

```bash
git add backend/src/ml/feature_store.py backend/src/analysis/devig.py backend/src/risk/calculator.py backend/src/pipeline/orchestrator.py
git commit -m "feat(ml): add CLV resolution pipeline + M2/M3 feature logging hooks"
```

---

### Task 23: Run All Tests & Verify

- [ ] **Step 1: Run the full test suite**

```bash
cd backend && python -m pytest tests/ -v
```
Expected: All tests pass (existing + new)

- [ ] **Step 2: Verify ML imports don't break anything**

```bash
cd backend && python -c "from src.ml.models.edge_quality import EdgeQualityModel; from src.ml.models.limit_predictor import LimitPredictorModel; from src.ml.models.devig_selector import DevigSelectorModel; from src.ml.models.boost_calibrator import BoostCalibratorModel; from src.ml.models.adaptive_kelly import AdaptiveKellyModel; from src.ml.serving.predictor import get_predictor; from src.ml.training.train_all import TrainingOrchestrator; print('All ML imports OK')"
```

- [ ] **Step 3: Commit any final fixes**

```bash
git add -A
git commit -m "fix(ml): address test failures from integration"
```
