"""M1: Edge Quality Classifier.

Predicts whether a detected edge is real (CLV > 0) or noise.
Replaces hardcoded MIN_VALID_PROB_SUM, MAX_ODDS_RATIO, MAX_EDGE_PCT thresholds.

Min training data: 200 bets with CLV tracking.
"""
import logging
import json
import warnings
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

# Start with phase 1
FEATURE_NAMES = FEATURE_NAMES_PHASE1

MIN_SAMPLES = 200
MODELS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "models"


class EdgeQualityModel:
    """LightGBM binary classifier for edge quality."""

    def __init__(self):
        self.feature_names = FEATURE_NAMES
        self.model = None

    def train(self, data: list) -> dict | None:
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
        if self.model is None:
            return None
        X = np.array([[features.get(f, 0.0) for f in self.feature_names]])
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="X does not have valid feature names")
                proba = self.model.predict_proba(X)
            return float(proba[0][1])
        except Exception as e:
            logger.warning(f"Edge quality prediction failed: {e}")
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
