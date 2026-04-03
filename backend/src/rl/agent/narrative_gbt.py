"""Narrative GBT model for the hierarchical observation architecture.

The Narrative GBT is the slow-layer model that processes structure/TPO/AMT
features to produce:
  - Day type classification (multi-class)
  - Setup probabilities (8-dim, one per SetupType)

These outputs feed into the Trigger GBT as contextual priors.
"""
from __future__ import annotations

import logging
from pathlib import Path

import joblib
import numpy as np
from sklearn.preprocessing import StandardScaler

# Prefer LightGBM (10-50x faster, multi-threaded) with sklearn fallback
try:
    from lightgbm import LGBMClassifier as _Classifier
    _ENGINE = "lightgbm"
except ImportError:
    from sklearn.ensemble import GradientBoostingClassifier as _Classifier  # type: ignore[assignment]
    _ENGINE = "sklearn"

from backend.src.rl.labeling.setup_types import NUM_SETUP_TYPES, SetupType

log = logging.getLogger(__name__)

# Minimum positive samples required to train a setup head
_MIN_POSITIVE = 50


class NarrativeGBT:
    """Slow-layer GBT: day type + setup probabilities from structure/TPO/AMT features.

    Produces:
      - day_type probabilities (num_day_types-dim, via day_type_model)
      - setup probabilities (8-dim, one binary model per SetupType)

    The 8-dim setup probability vector is the primary output consumed by the
    Trigger GBT and DQN decision layer.
    """

    engine: str = _ENGINE

    def __init__(self) -> None:
        self.day_type_model: _Classifier | None = None
        # One binary classifier per setup type; None means insufficient training data
        self.setup_models: list[_Classifier | None] = [None] * NUM_SETUP_TYPES
        self.scaler: StandardScaler | None = None
        self._alive_mask: np.ndarray | None = None
        # Records which setup indices were actually trained
        self._trained_setup_indices: list[int] = []

    def train(
        self,
        X: np.ndarray,
        day_type_labels: np.ndarray,
        setup_labels: np.ndarray,
        n_estimators: int = 500,
        max_depth: int = 5,
        learning_rate: float = 0.05,
        subsample: float = 0.8,
    ) -> dict:
        """Train day-type classifier and per-setup binary classifiers.

        Args:
            X: Feature matrix of shape (N, F) — structure/TPO/AMT features.
            day_type_labels: Integer day-type labels of shape (N,).
            setup_labels: Binary setup matrix of shape (N, NUM_SETUP_TYPES).
                          Each column is 1 if that setup was present, 0 otherwise.
            n_estimators: Trees per model.
            max_depth: Max tree depth.
            learning_rate: Boosting learning rate.
            subsample: Row subsampling fraction.

        Returns:
            Dict with training metrics (alive_features, day_type_accuracy,
            trained_setups, skipped_setups, engine).
        """
        # Remove dead features (zero variance)
        stds = np.std(X, axis=0)
        self._alive_mask = stds > 1e-8
        alive_count = int(self._alive_mask.sum())
        X_alive = X[:, self._alive_mask]

        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X_alive)

        if _ENGINE == "lightgbm":
            base_params = dict(
                n_estimators=n_estimators,
                max_depth=max_depth,
                learning_rate=learning_rate,
                subsample=subsample,
                min_child_samples=50,
                colsample_bytree=0.7,
                n_jobs=-1,
                verbose=-1,
            )
        else:
            base_params = dict(
                n_estimators=n_estimators,
                max_depth=max_depth,
                learning_rate=learning_rate,
                subsample=subsample,
                min_samples_leaf=50,
                max_features="sqrt",
                validation_fraction=0.1,
                n_iter_no_change=20,
                tol=1e-4,
            )

        metrics: dict = {
            "alive_features": alive_count,
            "total_features": int(X.shape[1]),
            "engine": _ENGINE,
        }

        # --- Day-type classifier ---
        log.info(
            "Training day_type head: %d samples, %d features, %d classes",
            len(X_scaled), alive_count, len(np.unique(day_type_labels)),
        )
        self.day_type_model = _Classifier(**base_params)
        self.day_type_model.fit(X_scaled, day_type_labels)
        metrics["day_type_accuracy"] = round(
            self.day_type_model.score(X_scaled, day_type_labels) * 100, 1
        )
        metrics["day_type_classes"] = int(len(np.unique(day_type_labels)))

        # --- Per-setup binary classifiers ---
        setup_names = [s.value for s in SetupType if s != SetupType.UNKNOWN]
        trained, skipped = [], []
        self.setup_models = [None] * NUM_SETUP_TYPES

        for i in range(NUM_SETUP_TYPES):
            col = setup_labels[:, i].astype(np.int32)
            pos_count = int(col.sum())

            if pos_count < _MIN_POSITIVE:
                log.debug(
                    "Skipping setup[%d] %s: only %d positive samples (need %d)",
                    i, setup_names[i], pos_count, _MIN_POSITIVE,
                )
                skipped.append(setup_names[i])
                continue

            # Imbalance weight: up-weight positive class
            neg_count = len(col) - pos_count
            scale_pos_weight = neg_count / max(pos_count, 1)

            if _ENGINE == "lightgbm":
                params = {**base_params, "scale_pos_weight": scale_pos_weight}
            else:
                # sklearn GradientBoosting doesn't support scale_pos_weight directly;
                # use sample weights instead
                params = {**base_params}

            clf = _Classifier(**params)

            if _ENGINE == "sklearn":
                sample_weight = np.where(col == 1, scale_pos_weight, 1.0)
                clf.fit(X_scaled, col, sample_weight=sample_weight)
            else:
                clf.fit(X_scaled, col)

            self.setup_models[i] = clf
            self._trained_setup_indices.append(i)
            trained.append(setup_names[i])
            log.info(
                "Trained setup[%d] %s: %d pos / %d neg (scale_pos_weight=%.2f)",
                i, setup_names[i], pos_count, neg_count, scale_pos_weight,
            )

        metrics["trained_setups"] = trained
        metrics["skipped_setups"] = skipped
        log.info(
            "NarrativeGBT training complete: %d setup models trained, %d skipped",
            len(trained), len(skipped),
        )
        return metrics

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _scale_single(self, obs: np.ndarray) -> np.ndarray:
        """Apply alive mask + scaler to a single observation vector."""
        return self.scaler.transform(obs[self._alive_mask].reshape(1, -1))

    # ------------------------------------------------------------------
    # Single-observation prediction
    # ------------------------------------------------------------------

    def predict_setup_probs(self, obs: np.ndarray) -> np.ndarray:
        """Predict setup probabilities for a single observation.

        Returns:
            ndarray of shape (8,) — P(setup active) for each SetupType.
            Untrained setups return 0.0.
        """
        x = self._scale_single(obs)
        probs = np.zeros(NUM_SETUP_TYPES, dtype=np.float32)
        for i, clf in enumerate(self.setup_models):
            if clf is not None:
                probs[i] = float(clf.predict_proba(x)[0, 1])
        return probs

    def predict_day_type(self, obs: np.ndarray) -> np.ndarray:
        """Predict day-type probabilities for a single observation.

        Returns:
            ndarray of shape (num_day_types,) — class probabilities.
        """
        x = self._scale_single(obs)
        return self.day_type_model.predict_proba(x)[0].astype(np.float32)

    # ------------------------------------------------------------------
    # Batch prediction (for replay / evaluation)
    # ------------------------------------------------------------------

    def predict_setup_probs_batch(self, obs: np.ndarray) -> np.ndarray:
        """Predict setup probabilities for a batch of observations.

        Args:
            obs: ndarray of shape (N, F).

        Returns:
            ndarray of shape (N, 8) — P(setup active) per sample per setup.
            Untrained setup columns are 0.0.
        """
        X = self.scaler.transform(obs[:, self._alive_mask])
        n = len(X)
        probs = np.zeros((n, NUM_SETUP_TYPES), dtype=np.float32)
        for i, clf in enumerate(self.setup_models):
            if clf is not None:
                probs[:, i] = clf.predict_proba(X)[:, 1]
        return probs

    def predict_day_type_batch(self, obs: np.ndarray) -> np.ndarray:
        """Predict day-type probabilities for a batch of observations.

        Returns:
            ndarray of shape (N, num_day_types).
        """
        X = self.scaler.transform(obs[:, self._alive_mask])
        return self.day_type_model.predict_proba(X).astype(np.float32)

    # ------------------------------------------------------------------
    # Feature importance
    # ------------------------------------------------------------------

    def feature_importance(self, top_n: int = 20) -> list[tuple[int, float]]:
        """Top feature importances from the day-type model."""
        imp = self.day_type_model.feature_importances_
        alive_indices = np.where(self._alive_mask)[0]
        top = np.argsort(-imp)[:top_n]
        return [(int(alive_indices[i]), float(imp[i])) for i in top]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: Path) -> None:
        """Persist all model components to a single joblib file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "day_type_model": self.day_type_model,
                "setup_models": self.setup_models,
                "scaler": self.scaler,
                "alive_mask": self._alive_mask,
                "trained_setup_indices": self._trained_setup_indices,
                "version": 1,
            },
            path,
        )
        log.info("NarrativeGBT saved to %s", path)

    @classmethod
    def load(cls, path: Path) -> NarrativeGBT:
        """Load a saved NarrativeGBT from disk."""
        data = joblib.load(path)
        model = cls()
        model.day_type_model = data["day_type_model"]
        model.setup_models = data["setup_models"]
        model.scaler = data["scaler"]
        model._alive_mask = data["alive_mask"]
        model._trained_setup_indices = data.get("trained_setup_indices", [])
        log.info("NarrativeGBT loaded from %s", path)
        return model
