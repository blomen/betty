"""Narrative GBT — slow-layer macro context (Phase 3b refactor).

The Narrative GBT's job is to paint the big picture: what day type are we in
and where is the macro bias/risk regime. It deliberately does NOT identify
setups anymore — setup identification is done by the trigger layer from
orderflow + level alignment, which operates on fast features.

Current outputs:
  - day_type probabilities (num_day_types-dim, via day_type_model)

Future Phase 3c extensions (not yet wired):
  - bias_score          — signed macro bias strength
  - risk_on_off         — risk-on vs risk-off scalar

These outputs feed into the *risk* heads (size, add, early-exit) rather than
into setup identification.
"""

from __future__ import annotations

import logging
from pathlib import Path

import joblib
import numpy as np
from sklearn.preprocessing import StandardScaler

try:
    from lightgbm import LGBMClassifier as _Classifier

    _ENGINE = "lightgbm"
except ImportError:
    from sklearn.ensemble import GradientBoostingClassifier as _Classifier  # type: ignore[assignment]

    _ENGINE = "sklearn"

log = logging.getLogger(__name__)


class NarrativeGBT:
    """Slow-layer GBT: day type classifier from structure/TPO/AMT/macro features."""

    engine: str = _ENGINE

    def __init__(self) -> None:
        self.day_type_model: _Classifier | None = None
        self.scaler: StandardScaler | None = None
        self._alive_mask: np.ndarray | None = None

    def train(
        self,
        X: np.ndarray,
        day_type_labels: np.ndarray,
        n_estimators: int = 500,
        max_depth: int = 5,
        learning_rate: float = 0.05,
        subsample: float = 0.8,
    ) -> dict:
        """Train day-type classifier with chronological 80/20 split + early stopping."""
        n = len(X)
        val_split = int(n * 0.80)

        stds = np.std(X[:val_split], axis=0)
        self._alive_mask = stds > 1e-8
        alive_count = int(self._alive_mask.sum())

        self.scaler = StandardScaler()
        X_train = self.scaler.fit_transform(X[:val_split, self._alive_mask])
        X_val = self.scaler.transform(X[val_split:, self._alive_mask])
        y_dt_train, y_dt_val = day_type_labels[:val_split], day_type_labels[val_split:]

        if _ENGINE == "lightgbm":
            base_params = dict(
                n_estimators=n_estimators,
                max_depth=min(max_depth, 4),
                num_leaves=15,
                learning_rate=learning_rate,
                subsample=subsample,
                min_child_samples=100,
                colsample_bytree=0.5,
                min_split_gain=0.01,
                reg_alpha=0.1,
                reg_lambda=1.0,
                n_jobs=2,
                verbose=-1,
            )
        else:
            base_params = dict(
                n_estimators=n_estimators,
                max_depth=min(max_depth, 4),
                learning_rate=learning_rate,
                subsample=subsample,
                min_samples_leaf=100,
                max_features="sqrt",
                validation_fraction=0.1,
                n_iter_no_change=20,
                tol=1e-4,
            )

        metrics: dict = {
            "alive_features": alive_count,
            "total_features": int(X.shape[1]),
            "engine": _ENGINE,
            "train_size": val_split,
            "val_size": n - val_split,
        }

        log.info(
            "Training day_type head: %d train / %d val, %d features, %d classes",
            val_split,
            n - val_split,
            alive_count,
            len(np.unique(day_type_labels)),
        )
        self.day_type_model = _Classifier(**base_params)
        if _ENGINE == "lightgbm":
            self.day_type_model.fit(
                X_train,
                y_dt_train,
                eval_set=[(X_val, y_dt_val)],
                callbacks=[
                    __import__("lightgbm").early_stopping(50, verbose=False),
                    __import__("lightgbm").log_evaluation(0),
                ],
            )
        else:
            self.day_type_model.fit(X_train, y_dt_train)

        train_acc = round(self.day_type_model.score(X_train, y_dt_train) * 100, 1)
        val_acc = round(self.day_type_model.score(X_val, y_dt_val) * 100, 1)
        metrics["day_type_accuracy_train"] = train_acc
        metrics["day_type_accuracy"] = val_acc
        metrics["day_type_classes"] = int(len(np.unique(day_type_labels)))
        log.info("Day type: train=%.1f%% val=%.1f%%", train_acc, val_acc)

        return metrics

    def _scale_single(self, obs: np.ndarray) -> np.ndarray:
        return self.scaler.transform(obs[self._alive_mask].reshape(1, -1))

    def predict_day_type(self, obs: np.ndarray) -> np.ndarray:
        """Predict day-type probabilities for a single observation."""
        x = self._scale_single(obs)
        return self.day_type_model.predict_proba(x)[0].astype(np.float32)

    def predict_day_type_batch(self, obs: np.ndarray) -> np.ndarray:
        """Predict day-type probabilities for a batch of observations."""
        X = self.scaler.transform(obs[:, self._alive_mask])
        return self.day_type_model.predict_proba(X).astype(np.float32)

    def feature_importance(self, top_n: int = 20) -> list[tuple[int, float]]:
        imp = self.day_type_model.feature_importances_
        alive_indices = np.where(self._alive_mask)[0]
        top = np.argsort(-imp)[:top_n]
        return [(int(alive_indices[i]), float(imp[i])) for i in top]

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "day_type_model": self.day_type_model,
                "scaler": self.scaler,
                "alive_mask": self._alive_mask,
                "version": 2,  # v2: setup_models removed (Phase 3b)
            },
            path,
        )
        log.info("NarrativeGBT saved to %s", path)

    @classmethod
    def load(cls, path: Path) -> NarrativeGBT:
        """Load a saved NarrativeGBT. Older checkpoints with setup_models are tolerated."""
        data = joblib.load(path)
        model = cls()
        model.day_type_model = data["day_type_model"]
        model.scaler = data["scaler"]
        model._alive_mask = data["alive_mask"]
        log.info("NarrativeGBT loaded from %s (version=%s)", path, data.get("version", "legacy"))
        return model
