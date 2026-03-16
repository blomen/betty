"""M3: Devig Method Selector.

Multi-class classifier to pick the best devigging method per market context.
Classes: multiplicative (0), additive (1), power (2).

Min training data: 500 bets across sports/markets.
"""
import logging
import json
import warnings
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
    def __init__(self):
        self.feature_names = FEATURE_NAMES
        self.model = None

    def train(self, data: list) -> dict | None:
        if len(data) < MIN_SAMPLES:
            return None

        X, y = self._prepare_data(data)

        try:
            import lightgbm as lgb
        except ImportError:
            logger.warning("lightgbm not installed")
            return None

        params = {
            "objective": "multiclass", "num_class": 3,
            "num_leaves": 15, "learning_rate": 0.05,
            "n_estimators": 100, "verbose": -1, "min_child_samples": 5,
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
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="X does not have valid feature names")
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
