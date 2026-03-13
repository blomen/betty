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
    def __init__(self):
        self.feature_names = FEATURE_NAMES
        self.isotonic_model = None
        self.lgbm_model = None

    def train(self, data: list) -> dict | None:
        if len(data) < MIN_SAMPLES:
            return None

        X, y = self._prepare_data(data)

        from sklearn.isotonic import IsotonicRegression
        llm_probs = X[:, 0]  # First feature is llm_raw_probability
        self.isotonic_model = IsotonicRegression(out_of_bounds="clip")
        self.isotonic_model.fit(llm_probs, y)

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
            pass

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
        if self.isotonic_model is None:
            return None
        try:
            if self.lgbm_model is not None:
                X = np.array([[features.get(f, 0.0) for f in self.feature_names]])
                proba = self.lgbm_model.predict_proba(X)
                return float(proba[0][1])
            llm_prob = features.get("llm_raw_probability", 0.5)
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
