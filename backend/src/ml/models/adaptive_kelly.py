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
    def __init__(self):
        self.feature_names = FEATURE_NAMES
        self.model = None

    def train(self, data: list) -> dict | None:
        if len(data) < MIN_SAMPLES:
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
            y_list.append(float(row.outcome) if row.outcome is not None else 0.0)
        return np.array(X_list), np.array(y_list)
