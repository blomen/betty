"""Model serving — loads trained models from registry and serves predictions.

Models loaded lazily from ml_model_registry.
Falls back to None (rules-based) when model unavailable.
"""

import logging
import warnings
from pathlib import Path

import numpy as np

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

    def load_from_disk(self, models_dir: str | Path | None = None) -> int:
        """Fallback: auto-discover *_latest.joblib files from disk.

        Used when ml_model_registry is empty but trained models exist on disk.
        """
        if models_dir is None:
            models_dir = Path(__file__).parent.parent.parent.parent / "data" / "models"
        else:
            models_dir = Path(models_dir)

        if not models_dir.exists():
            return 0

        loaded = 0
        for joblib_path in sorted(models_dir.glob("*_latest.joblib")):
            model_name = joblib_path.stem.replace("_latest", "")
            if model_name not in self.models and self.load_model(model_name, str(joblib_path)):
                loaded += 1
        if loaded:
            logger.info(f"Loaded {loaded} ML models from disk (registry was empty)")
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
        feature_names = model_data["feature_names"]
        task = model_data.get("task", "classification")
        model = model_data.get("model")

        try:
            X = np.array([[features.get(f, 0.0) for f in feature_names]])
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="X does not have valid feature names")
                if task == "calibration":
                    # Boost calibrator: prefer lgbm_model, fall back to isotonic
                    lgbm = model_data.get("lgbm_model")
                    if lgbm is not None:
                        proba = lgbm.predict_proba(X)
                        return float(proba[0][1])
                    isotonic = model_data.get("isotonic_model")
                    if isotonic is not None:
                        llm_prob = features.get("llm_raw_probability", 0.5)
                        return float(isotonic.predict([llm_prob])[0])
                    return None
                if task == "multiclass":
                    proba = model.predict_proba(X)[0]
                    best_idx = int(np.argmax(proba))
                    result: dict = {
                        "class": best_idx,
                        "probabilities": proba.tolist(),
                    }
                    classes = model_data.get("classes")
                    if classes:
                        result["class_name"] = classes[best_idx]
                        result["confidence"] = float(proba[best_idx])
                        result["probabilities"] = {cls: float(p) for cls, p in zip(classes, proba, strict=False)}
                    return result
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
