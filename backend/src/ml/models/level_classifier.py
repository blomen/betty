"""M7: Level Touch Classifier -- 5-class (or 3-class fallback) multiclass model.

Classifies the outcome of a level touch as one of:
  strong_reversal, weak_reversal, chop, weak_continuation, strong_continuation

Falls back to 3 classes (reversal / chop / continuation) when individual class
counts are below MIN_SAMPLES_PER_CLASS.
"""
import json
import logging
import numpy as np
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_SAMPLES = 300
MIN_SAMPLES_PER_CLASS = 50
MODELS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "models"

# 5-class labels (canonical order from outcomes.py)
from src.ml.level_touch.outcomes import OUTCOMES, OUTCOME_TO_INDEX  # noqa: E402

# 3-class fallback: collapse strong/weak into single direction class
FALLBACK_CLASSES = ["reversal", "chop", "continuation"]
FALLBACK_MAP: dict[str, str] = {
    "strong_reversal": "reversal",
    "weak_reversal": "reversal",
    "chop": "chop",
    "weak_continuation": "continuation",
    "strong_continuation": "continuation",
}
_FALLBACK_TO_INDEX = {c: i for i, c in enumerate(FALLBACK_CLASSES)}

from src.ml.features.level_touch_features import (  # noqa: E402
    FEATURE_NAMES,
    CATEGORICAL_MAPS,
    BOOLEAN_FEATURES,
)


def _encode_features(features: dict) -> np.ndarray:
    """Encode a feature dict to a fixed-length float32 array.

    Encoding rules:
    - Categoricals (in CATEGORICAL_MAPS): look up int; np.nan if key unknown or value None.
    - Booleans (in BOOLEAN_FEATURES): cast True→1.0, False→0.0; np.nan if None.
    - Numerics: float(); np.nan on None or conversion error.
    """
    vec = []
    for name in FEATURE_NAMES:
        val = features.get(name)
        if val is None:
            vec.append(np.nan)
            continue

        if name in CATEGORICAL_MAPS:
            encoded = CATEGORICAL_MAPS[name].get(val)
            vec.append(float(encoded) if encoded is not None else np.nan)
        elif name in BOOLEAN_FEATURES:
            vec.append(1.0 if val else 0.0)
        else:
            try:
                vec.append(float(val))
            except (TypeError, ValueError):
                vec.append(np.nan)

    return np.array(vec, dtype=np.float32)


class LevelClassifierModel:
    """Train and save a LightGBM multiclass classifier for level touch outcomes."""

    def train(self, data: list[dict]) -> dict | None:
        """Train on rows of {features: dict|str, outcome: str}.

        Steps:
        1. Encode features with _encode_features.
        2. Check total sample count >= MIN_SAMPLES.
        3. Check per-class counts; collapse to 3 classes if any < MIN_SAMPLES_PER_CLASS.
        4. Train via train_model(task="multiclass").
        5. Save artifact with joblib including 'classes' and 'use_fallback'.

        Returns dict with file_path, training_data_count, validation_score,
        baseline_metric or None if insufficient data.
        """
        try:
            import lightgbm as lgb  # noqa: F401
        except ImportError:
            logger.warning("lightgbm not installed — level classifier disabled")
            return None

        X_list, y_raw = [], []
        for row in data:
            # Support both dict rows and ORM-style objects
            if isinstance(row, dict):
                features_raw = row.get("features")
                outcome = row.get("outcome")
            else:
                features_raw = getattr(row, "features", None)
                outcome = getattr(row, "outcome", None)

            if features_raw is None or outcome is None:
                continue

            features = (
                json.loads(features_raw)
                if isinstance(features_raw, str)
                else features_raw
            )

            vec = _encode_features(features)
            X_list.append(vec)
            y_raw.append(outcome)

        if len(X_list) < MIN_SAMPLES:
            logger.info(
                "LevelClassifier: insufficient data (%d < %d)", len(X_list), MIN_SAMPLES
            )
            return None

        # Determine whether we can use 5 classes or must fall back to 3
        from collections import Counter
        class_counts = Counter(y_raw)
        use_fallback = any(
            class_counts.get(cls, 0) < MIN_SAMPLES_PER_CLASS for cls in OUTCOMES
        )

        if use_fallback:
            logger.info(
                "LevelClassifier: sparse class distribution %s — collapsing to 3 classes",
                dict(class_counts),
            )
            classes = FALLBACK_CLASSES
            label_to_idx = _FALLBACK_TO_INDEX
            y_mapped = [FALLBACK_MAP.get(lbl, "chop") for lbl in y_raw]
        else:
            classes = list(OUTCOMES)
            label_to_idx = dict(OUTCOME_TO_INDEX)
            y_mapped = list(y_raw)

        # Encode labels to int indices, dropping rows with unknown outcomes
        X_final, y_final = [], []
        for vec, label in zip(X_list, y_mapped):
            idx = label_to_idx.get(label)
            if idx is None:
                continue
            X_final.append(vec)
            y_final.append(idx)

        if len(X_final) < MIN_SAMPLES:
            logger.info(
                "LevelClassifier: after label filtering insufficient data (%d < %d)",
                len(X_final), MIN_SAMPLES,
            )
            return None

        X = np.array(X_final, dtype=np.float32)
        y = np.array(y_final, dtype=np.int64)
        num_class = len(classes)

        from src.ml.optimizer.trainer import train_model
        result = train_model(
            X, y,
            task="multiclass",
            min_samples=MIN_SAMPLES,
            feature_names=FEATURE_NAMES,
            num_class=num_class,
        )
        if result is None:
            return None

        import joblib
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        path = MODELS_DIR / "level_classifier_latest.joblib"
        joblib.dump(
            {
                "model": result["model"],
                "feature_names": FEATURE_NAMES,
                "task": "multiclass",
                "classes": classes,
                "use_fallback": use_fallback,
            },
            path,
        )

        baseline = 1.0 / num_class
        return {
            "file_path": str(path),
            "training_data_count": len(X_final),
            "validation_score": result.get("validation_score") or 0.0,
            "baseline_metric": baseline,
        }

    def predict(self, features: dict) -> dict | None:
        """Return None — actual prediction is done by the central Predictor singleton.

        To get predictions use: Predictor.predict("level_classifier", features)
        """
        return None
