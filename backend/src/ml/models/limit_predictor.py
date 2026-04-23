"""M2: Provider Limit Predictor.

Predicts probability of being limited at a provider.
Uses logistic regression at low data (<50 events), graduates to LightGBM at 50+.

Min training data: 20 limit events.
"""

import json
import logging
import warnings
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

LOW_DATA_FEATURES = [
    "clv_score",
    "total_bets",
    "max_single_bet_edge",
    "stake_entropy",
    "similar_platform_limits",
]

FULL_FEATURES = [
    "clv_score",
    "total_bets",
    "max_single_bet_edge",
    "stake_entropy",
    "market_diversity",
    "timing_regularity",
    "outcome_correlation",
    "bonus_usage_ratio",
    "win_rate_deviation",
    "account_age_days",
    "total_turnover",
    "similar_platform_limits",
    "bet_frequency_trend",
    "sport_concentration_top3",
    "has_used_freebet",
    "avg_stake_vs_provider_median",
    "time_between_bets_cv",
    "time_from_odds_change_to_bet",
    "same_side_as_sharp_movement_pct",
    "deposit_withdrawal_ratio",
]

MIN_SAMPLES = 20
LGBM_THRESHOLD = 50
MODELS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "models"


class LimitPredictorModel:
    def __init__(self):
        self.model = None
        self.feature_names = LOW_DATA_FEATURES
        self.algorithm = None

    def train(self, data: list) -> dict | None:
        if len(data) < MIN_SAMPLES:
            return None

        use_lgbm = len(data) >= LGBM_THRESHOLD
        self.feature_names = FULL_FEATURES if use_lgbm else LOW_DATA_FEATURES
        X, y = self._prepare_data(data)

        if use_lgbm:
            from src.ml.optimizer.trainer import train_model

            result = train_model(X, y, task="classification", min_samples=MIN_SAMPLES, feature_names=self.feature_names)
            if result is None:
                return None
            self.model = result["model"]
            self.algorithm = "lightgbm"
        else:
            from sklearn.linear_model import LogisticRegression

            model = LogisticRegression(C=0.1, max_iter=1000)
            model.fit(X, y)
            self.model = model
            self.algorithm = "logistic_regression"

        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        file_path = str(MODELS_DIR / "limit_predictor_latest.joblib")
        try:
            import joblib

            joblib.dump(
                {
                    "model": self.model,
                    "feature_names": self.feature_names,
                    "task": "classification",
                    "algorithm": self.algorithm,
                },
                file_path,
            )
        except ImportError:
            return None

        return {
            "model": self.model,
            "file_path": file_path,
            "training_data_count": len(data),
            "algorithm": self.algorithm,
            "validation_score": result.get("validation_score") if use_lgbm else None,
        }

    def predict(self, features: dict) -> float | None:
        if self.model is None:
            return None
        X = np.array([[features.get(f, 0.0) for f in self.feature_names]])
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="X does not have valid feature names")
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
