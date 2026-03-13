"""Model serving — loads trained models from registry and serves predictions.

Models loaded lazily from ml_model_registry.
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
                return float(proba[0][1])
            else:
                pred = model.predict(X)
                return float(pred[0])
        except Exception as e:
            logger.warning(f"Prediction failed for {model_name}: {e}")
            return None

    def is_loaded(self, model_name: str) -> bool:
        """Check if a model is loaded and ready."""
        return model_name in self.models


_predictor: Predictor | None = None


def get_predictor() -> Predictor:
    """Get or create the global predictor singleton."""
    global _predictor
    if _predictor is None:
        _predictor = Predictor()
    return _predictor
