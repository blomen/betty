"""Multi-target GBT forecaster for the hybrid GBT+DQN architecture.

GBT = predictive layer (finds edge in structured tabular features).
DQN = decision layer (acts on GBT forecasts + position/session state).

The GBT produces 8 forecast features that become DQN input dims:
  0: prob_cont           — P(continuation is better)
  1: prob_rev            — P(reversal is better)
  2: direction_confidence — |prob_cont - prob_rev|
  3: expected_best_r     — E[max(reward_cont, reward_rev)]
  4: expected_worst_r    — E[min(reward_cont, reward_rev)]
  5: prob_breakeven      — P(price reaches 1R before stop)
  6: predicted_levels    — E[structural levels captured by best action]
  7: predicted_stop      — optimal stop distance in ticks
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
    from lightgbm import LGBMRegressor as _Regressor

    _ENGINE = "lightgbm"
except ImportError:
    from sklearn.ensemble import _Classifier as _Classifier
    from sklearn.ensemble import _Regressor as _Regressor

    _ENGINE = "sklearn"

log = logging.getLogger(__name__)

GBT_FORECAST_DIM = 8


class GBTModel:
    """Multi-target GBT forecaster: direction + magnitude + TP/SL + levels + stop.

    Produces 8-dim forecast vector consumed by the DQN decision layer.
    """

    engine: str = _ENGINE

    def __init__(self) -> None:
        self.direction_model: _Classifier | None = None
        self.expected_best_r_model: _Regressor | None = None
        self.expected_worst_r_model: _Regressor | None = None
        self.breakeven_model: _Classifier | None = None
        self.levels_model: _Regressor | None = None
        self.stop_model: _Regressor | None = None
        self.scaler: StandardScaler | None = None
        self._alive_mask: np.ndarray | None = None

    def train(
        self,
        X_train: np.ndarray,
        y_direction: np.ndarray,
        stop_targets: np.ndarray,
        rewards_cont: np.ndarray | None = None,
        rewards_rev: np.ndarray | None = None,
        breakeven_reached: np.ndarray | None = None,
        levels_captured: np.ndarray | None = None,
        reward_gap: np.ndarray | None = None,
        n_estimators: int = 500,
        max_depth: int = 5,
        learning_rate: float = 0.05,
        subsample: float = 0.8,
    ) -> dict:
        """Train all GBT heads on base observation vectors.

        Returns dict with training metrics per head.
        """
        # Remove dead features
        stds = np.std(X_train, axis=0)
        self._alive_mask = stds > 1e-8
        alive_count = int(self._alive_mask.sum())
        X = X_train[:, self._alive_mask]

        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)

        # Sample weights for direction
        sample_weight = None
        if reward_gap is not None:
            sample_weight = np.clip(np.abs(reward_gap), 0.1, 5.0)

        if _ENGINE == "lightgbm":
            gbt_params = dict(
                n_estimators=n_estimators,
                max_depth=max_depth,
                learning_rate=learning_rate,
                subsample=subsample,
                min_child_samples=50,
                colsample_bytree=0.7,
                n_jobs=-1,
                verbose=-1,
            )
            reg_params = dict(
                n_estimators=min(300, n_estimators),
                max_depth=min(4, max_depth),
                learning_rate=learning_rate,
                subsample=subsample,
                min_child_samples=50,
                n_jobs=-1,
                verbose=-1,
            )
        else:
            gbt_params = dict(
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
            reg_params = dict(
                n_estimators=min(300, n_estimators),
                max_depth=min(4, max_depth),
                learning_rate=learning_rate,
                subsample=subsample,
                min_samples_leaf=50,
            )

        metrics = {"alive_features": alive_count, "total_features": int(X_train.shape[1]), "engine": _ENGINE}

        # Head 1: Direction classifier (prob_cont, prob_rev, confidence)
        log.info("Training direction head: %d samples, %d features", len(X_scaled), alive_count)
        self.direction_model = _Classifier(**gbt_params)
        self.direction_model.fit(X_scaled, y_direction, sample_weight=sample_weight)
        metrics["direction_trees"] = int(getattr(self.direction_model, "n_estimators_", n_estimators))
        metrics["direction_accuracy"] = round(self.direction_model.score(X_scaled, y_direction) * 100, 1)

        # Head 2: Expected best R (magnitude of best action reward)
        if rewards_cont is not None and rewards_rev is not None:
            best_r = np.maximum(rewards_cont, rewards_rev)
            worst_r = np.minimum(rewards_cont, rewards_rev)

            log.info("Training expected_best_r head")
            self.expected_best_r_model = _Regressor(**reg_params)
            self.expected_best_r_model.fit(X_scaled, best_r)

            log.info("Training expected_worst_r head")
            self.expected_worst_r_model = _Regressor(**reg_params)
            self.expected_worst_r_model.fit(X_scaled, worst_r)

        # Head 3: Breakeven probability
        if breakeven_reached is not None:
            log.info("Training breakeven head")
            self.breakeven_model = _Classifier(**gbt_params)
            self.breakeven_model.fit(X_scaled, breakeven_reached.astype(np.int32))
            metrics["breakeven_accuracy"] = round(
                self.breakeven_model.score(X_scaled, breakeven_reached.astype(np.int32)) * 100, 1
            )

        # Head 4: Predicted levels captured
        if levels_captured is not None:
            log.info("Training levels head")
            self.levels_model = _Regressor(**reg_params)
            self.levels_model.fit(X_scaled, np.clip(levels_captured, 0, 6))

        # Head 5: Stop distance
        log.info("Training stop head")
        self.stop_model = _Regressor(**reg_params)
        self.stop_model.fit(X_scaled, stop_targets)

        return metrics

    # ------------------------------------------------------------------
    # Single-observation prediction
    # ------------------------------------------------------------------

    def _scale_single(self, obs: np.ndarray) -> np.ndarray:
        """Apply alive mask + scaler to a single observation."""
        return self.scaler.transform(obs[self._alive_mask].reshape(1, -1))

    def predict_direction(self, obs: np.ndarray) -> tuple[int, float, float, float]:
        """Predict direction. Returns (action_idx, confidence, prob_cont, prob_rev)."""
        x = self._scale_single(obs)
        probs = self.direction_model.predict_proba(x)[0]
        prob_cont, prob_rev = float(probs[0]), float(probs[1])
        action_idx = 0 if prob_cont >= prob_rev else 1
        confidence = abs(prob_cont - prob_rev)
        return action_idx, confidence, prob_cont, prob_rev

    def predict_stop(self, obs: np.ndarray) -> float:
        """Predict optimal stop distance in ticks."""
        x = self._scale_single(obs)
        return float(np.clip(self.stop_model.predict(x)[0], 6.0, 40.0))

    def predict_full(self, obs: np.ndarray) -> np.ndarray:
        """Produce full 8-dim forecast vector for a single observation.

        Returns ndarray of shape (8,) with:
          [prob_cont, prob_rev, confidence, expected_best_r, expected_worst_r,
           prob_breakeven, predicted_levels, predicted_stop]
        """
        x = self._scale_single(obs)

        # Direction
        probs = self.direction_model.predict_proba(x)[0]
        prob_cont, prob_rev = float(probs[0]), float(probs[1])
        confidence = abs(prob_cont - prob_rev)

        # Expected R
        best_r = float(self.expected_best_r_model.predict(x)[0]) if self.expected_best_r_model else 0.0
        worst_r = float(self.expected_worst_r_model.predict(x)[0]) if self.expected_worst_r_model else 0.0

        # Breakeven
        prob_be = float(self.breakeven_model.predict_proba(x)[0, 1]) if self.breakeven_model else 0.5

        # Levels
        levels = float(self.levels_model.predict(x)[0]) if self.levels_model else 0.0
        levels = np.clip(levels, 0.0, 6.0)

        # Stop
        stop = float(np.clip(self.stop_model.predict(x)[0], 6.0, 40.0))

        return np.array(
            [
                prob_cont,
                prob_rev,
                confidence,
                best_r,
                worst_r,
                prob_be,
                levels,
                stop,
            ],
            dtype=np.float32,
        )

    # ------------------------------------------------------------------
    # Batch prediction (for replay / evaluation)
    # ------------------------------------------------------------------

    def predict_direction_batch(self, obs: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Batch direction prediction. Returns (actions, confidences, probs)."""
        X = self.scaler.transform(obs[:, self._alive_mask])
        probs = self.direction_model.predict_proba(X)
        actions = np.argmax(probs, axis=1)
        confidences = np.abs(probs[:, 0] - probs[:, 1])
        return actions, confidences, probs

    def predict_stop_batch(self, obs: np.ndarray) -> np.ndarray:
        """Batch stop prediction."""
        X = self.scaler.transform(obs[:, self._alive_mask])
        return np.clip(self.stop_model.predict(X), 6.0, 40.0)

    def predict_full_batch(self, obs: np.ndarray) -> np.ndarray:
        """Produce full 8-dim forecast for a batch. Returns (N, 8) array."""
        X = self.scaler.transform(obs[:, self._alive_mask])
        n = len(X)

        # Direction
        probs = self.direction_model.predict_proba(X)
        prob_cont = probs[:, 0]
        prob_rev = probs[:, 1]
        confidence = np.abs(prob_cont - prob_rev)

        # Expected R
        if self.expected_best_r_model:
            best_r = self.expected_best_r_model.predict(X)
            worst_r = self.expected_worst_r_model.predict(X)
        else:
            best_r = np.zeros(n)
            worst_r = np.zeros(n)

        # Breakeven
        prob_be = self.breakeven_model.predict_proba(X)[:, 1] if self.breakeven_model else np.full(n, 0.5)

        # Levels
        levels = np.clip(self.levels_model.predict(X), 0.0, 6.0) if self.levels_model else np.zeros(n)

        # Stop
        stop = np.clip(self.stop_model.predict(X), 6.0, 40.0)

        return np.column_stack(
            [
                prob_cont,
                prob_rev,
                confidence,
                best_r,
                worst_r,
                prob_be,
                levels,
                stop,
            ]
        ).astype(np.float32)

    # ------------------------------------------------------------------
    # Feature importance
    # ------------------------------------------------------------------

    def feature_importance(self, top_n: int = 20) -> list[tuple[int, float]]:
        """Top feature importances from direction model."""
        imp = self.direction_model.feature_importances_
        alive_indices = np.where(self._alive_mask)[0]
        top = np.argsort(-imp)[:top_n]
        return [(int(alive_indices[i]), float(imp[i])) for i in top]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: Path) -> None:
        """Save all model components."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "direction_model": self.direction_model,
                "expected_best_r_model": self.expected_best_r_model,
                "expected_worst_r_model": self.expected_worst_r_model,
                "breakeven_model": self.breakeven_model,
                "levels_model": self.levels_model,
                "stop_model": self.stop_model,
                "scaler": self.scaler,
                "alive_mask": self._alive_mask,
                "version": 2,
            },
            path,
        )
        log.info("GBT model saved to %s", path)

    @classmethod
    def load(cls, path: Path) -> GBTModel:
        """Load a saved GBT model (backward-compatible with v1)."""
        data = joblib.load(path)
        model = cls()
        model.direction_model = data["direction_model"]
        model.stop_model = data["stop_model"]
        model.scaler = data["scaler"]
        model._alive_mask = data["alive_mask"]
        # v2 multi-target heads (optional for backward compat)
        model.expected_best_r_model = data.get("expected_best_r_model")
        model.expected_worst_r_model = data.get("expected_worst_r_model")
        model.breakeven_model = data.get("breakeven_model")
        model.levels_model = data.get("levels_model")
        return model
