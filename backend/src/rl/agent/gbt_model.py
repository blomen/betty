"""Gradient-Boosted Trees model for direction + stop prediction.

Replaces DQN for the GAMMA=0 case where each episode is independent.
GBT naturally handles nonlinear feature interactions that the DQN
architecture struggles with on this feature set.

Outputs match DQN interface so SessionManager works with either.
"""
from __future__ import annotations

import logging
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.preprocessing import StandardScaler

log = logging.getLogger(__name__)


class GBTModel:
    """Direction classifier + stop regressor using gradient-boosted trees.

    Interface mirrors DQNetwork for drop-in replacement in SessionManager:
    - predict_direction(obs) → (action_idx, confidence, prob_cont, prob_rev)
    - predict_stop(obs) → stop_ticks
    """

    def __init__(self) -> None:
        self.direction_model: GradientBoostingClassifier | None = None
        self.stop_model: GradientBoostingRegressor | None = None
        self.scaler: StandardScaler | None = None
        self._alive_mask: np.ndarray | None = None  # mask for non-dead features

    def train(
        self,
        X_train: np.ndarray,
        y_direction: np.ndarray,
        stop_targets: np.ndarray,
        reward_gap: np.ndarray | None = None,
        n_estimators: int = 500,
        max_depth: int = 5,
        learning_rate: float = 0.05,
        subsample: float = 0.8,
    ) -> dict:
        """Train direction classifier and stop regressor.

        Args:
            X_train: observations (N, obs_dim)
            y_direction: 0=CONT, 1=REV (N,)
            stop_targets: optimal stop in ticks (N,)
            reward_gap: rc - rr for sample weighting (N,) — optional
            n_estimators: number of boosting rounds
            max_depth: tree depth
            learning_rate: shrinkage rate
            subsample: row subsampling fraction

        Returns:
            dict with training metrics
        """
        # Remove dead features
        stds = np.std(X_train, axis=0)
        self._alive_mask = stds > 1e-8
        alive_count = self._alive_mask.sum()
        X = X_train[:, self._alive_mask]

        # Scale features
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)

        # Sample weights: weight clear signals higher
        sample_weight = None
        if reward_gap is not None:
            # Higher weight for episodes where direction matters more
            sample_weight = np.clip(np.abs(reward_gap), 0.1, 5.0)

        # Direction classifier
        log.info("Training direction GBT: %d samples, %d features, %d trees",
                 len(X_scaled), alive_count, n_estimators)
        self.direction_model = GradientBoostingClassifier(
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
        self.direction_model.fit(X_scaled, y_direction, sample_weight=sample_weight)
        actual_trees = self.direction_model.n_estimators_
        train_acc = self.direction_model.score(X_scaled, y_direction) * 100

        # Stop regressor
        log.info("Training stop GBT: %d samples", len(X_scaled))
        self.stop_model = GradientBoostingRegressor(
            n_estimators=min(200, n_estimators),
            max_depth=4,
            learning_rate=learning_rate,
            subsample=subsample,
            min_samples_leaf=50,
        )
        self.stop_model.fit(X_scaled, stop_targets)

        return {
            "alive_features": int(alive_count),
            "total_features": int(X_train.shape[1]),
            "direction_trees": int(actual_trees),
            "train_accuracy": round(train_acc, 1),
        }

    def predict_direction(self, obs: np.ndarray) -> tuple[int, float, float, float]:
        """Predict direction for a single observation.

        Returns:
            (action_idx, confidence, prob_cont, prob_rev)
            action_idx: 0=CONT, 1=REV
            confidence: |prob_cont - prob_rev| in [0, 1]
            prob_cont: probability of CONT being better
            prob_rev: probability of REV being better
        """
        x = obs[self._alive_mask].reshape(1, -1)
        x_scaled = self.scaler.transform(x)
        probs = self.direction_model.predict_proba(x_scaled)[0]
        prob_cont, prob_rev = float(probs[0]), float(probs[1])
        action_idx = 0 if prob_cont >= prob_rev else 1
        confidence = abs(prob_cont - prob_rev)
        return action_idx, confidence, prob_cont, prob_rev

    def predict_direction_batch(self, obs: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Batch prediction for evaluation.

        Returns:
            (action_indices, confidences, probabilities)
        """
        X = obs[:, self._alive_mask]
        X_scaled = self.scaler.transform(X)
        probs = self.direction_model.predict_proba(X_scaled)
        actions = np.argmax(probs, axis=1)
        confidences = np.abs(probs[:, 0] - probs[:, 1])
        return actions, confidences, probs

    def predict_stop(self, obs: np.ndarray) -> float:
        """Predict optimal stop distance in ticks for a single observation."""
        x = obs[self._alive_mask].reshape(1, -1)
        x_scaled = self.scaler.transform(x)
        return float(np.clip(self.stop_model.predict(x_scaled)[0], 6.0, 40.0))

    def predict_stop_batch(self, obs: np.ndarray) -> np.ndarray:
        """Batch stop prediction."""
        X = obs[:, self._alive_mask]
        X_scaled = self.scaler.transform(X)
        return np.clip(self.stop_model.predict(X_scaled), 6.0, 40.0)

    def feature_importance(self, feature_names: list[str] | None = None, top_n: int = 20) -> list[tuple[int, float]]:
        """Get top feature importances from direction model.

        Returns list of (original_feature_idx, importance).
        """
        imp = self.direction_model.feature_importances_
        alive_indices = np.where(self._alive_mask)[0]
        top = np.argsort(-imp)[:top_n]
        return [(int(alive_indices[i]), float(imp[i])) for i in top]

    def save(self, path: Path) -> None:
        """Save all model components."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({
            "direction_model": self.direction_model,
            "stop_model": self.stop_model,
            "scaler": self.scaler,
            "alive_mask": self._alive_mask,
        }, path)
        log.info("GBT model saved to %s", path)

    @classmethod
    def load(cls, path: Path) -> GBTModel:
        """Load a saved GBT model."""
        data = joblib.load(path)
        model = cls()
        model.direction_model = data["direction_model"]
        model.stop_model = data["stop_model"]
        model.scaler = data["scaler"]
        model._alive_mask = data["alive_mask"]
        return model
