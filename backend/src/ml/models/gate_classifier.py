"""M7: Dynamic Gate Classifier -- classifies day type and macro regime.

Day types: trend, normal, normal_variation, neutral, composite
Macro regimes: bull, bear, neutral

Uses LightGBM multiclass with walk-forward validation via trainer.
"""

import json
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

MIN_SAMPLES = 100
MODELS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "models"

DAY_TYPE_FEATURE_NAMES = [
    "rf_after_ib",
    "ib_range",
    "ib_range_vs_avg",
    "opening_type_encoded",
    "first_hour_delta_total",
    "first_hour_volume_vs_avg",
    "overnight_range_pct",
    "gap_filled_pct",
    "yesterday_market_type_encoded",
    "poor_high_or_low_in_ib",
    "first_hour_big_trades_count",
    "session_volume_first_hour",
    "vix_level",
    "gex",
    "value_migration_encoded",
    "ib_tpo_count",
]

DAY_TYPE_MAP = {
    "trend": 0,
    "normal": 1,
    "normal_variation": 2,
    "neutral": 3,
    "composite": 4,
}
DAY_TYPE_LABELS = {v: k for k, v in DAY_TYPE_MAP.items()}


class GateClassifierModel:
    def train(self, data) -> dict | None:
        X, y = [], []
        for row in data:
            features = row.features if isinstance(row.features, dict) else json.loads(row.features)
            vec = [float(features.get(f, 0) or 0) for f in DAY_TYPE_FEATURE_NAMES]
            label = features.get("day_type_label")
            if label is None:
                label = row.outcome
            if label is None:
                continue
            X.append(vec)
            y.append(int(label))

        X = np.array(X, dtype=np.float32)
        y = np.array(y, dtype=np.int32)

        if len(X) < MIN_SAMPLES:
            return None

        from src.ml.optimizer.trainer import train_model

        result = train_model(
            X,
            y,
            task="multiclass",
            min_samples=MIN_SAMPLES,
            feature_names=DAY_TYPE_FEATURE_NAMES,
            num_class=len(DAY_TYPE_MAP),
        )
        if result is None:
            return None

        import joblib

        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        path = MODELS_DIR / "gate_classifier_latest.joblib"
        joblib.dump(
            {
                "model": result["model"],
                "feature_names": DAY_TYPE_FEATURE_NAMES,
                "task": "multiclass",
            },
            path,
        )

        return {
            "file_path": str(path),
            "training_data_count": len(X),
            "validation_score": result.get("validation_score"),
            "feature_importance": result.get("feature_importance"),
            "baseline_metric": 1.0 / len(DAY_TYPE_MAP),
        }

    def predict(self, features: dict) -> dict | None:
        """Predict day type from first-hour features.

        Returns None — actual prediction is done by the central Predictor
        which loads the trained model from disk.
        """
        return None
