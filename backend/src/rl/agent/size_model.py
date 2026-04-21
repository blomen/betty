"""SizeModel — trained head for position sizing (Phase 3c).

Replaces the rule-based `size_multiplier(composite)` tier map in confidence.py
with a learned classifier that predicts the best size tier for the current
observation. Input: the same 318-dim augmented observation the DQN sees
(base 302 + GBT forecast 8 + position_state 8) plus narrative context.

Label derivation — we do NOT have counterfactual rewards (what would have
happened at each size), so we use realized_R buckets as the training target:
the predicted tier == the post-hoc optimal size for that trade under a
monotonic risk-reward assumption (bigger winners → bigger size, losers → skip).

5 tiers aligned with the legacy heuristic:
    0 → 0.0x  (skip)    realized_R < -0.3
    1 → 0.3x  (C)       realized_R ∈ [-0.3, 0)
    2 → 0.6x  (B)       realized_R ∈ [0, 0.5)
    3 → 1.0x  (A)       realized_R ∈ [0.5, 1.0)
    4 → 1.5x  (A+)      realized_R ≥ 1.0

At inference: argmax of predicted class probs maps to the tier multiplier.
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

# Size tier multipliers — keep in sync with confidence.size_multiplier fallback
SIZE_TIERS: tuple[float, ...] = (0.0, 0.3, 0.6, 1.0, 1.5)
NUM_SIZE_TIERS: int = len(SIZE_TIERS)


# Small tolerance so float32 noise at boundaries doesn't swap tiers.
_EPS = 1e-6


def realized_R_to_tier(r: float) -> int:
    """Map a realized R outcome to the best-fit size tier index."""
    r = float(r)
    if r >= 1.0 - _EPS:
        return 4
    if r >= 0.5 - _EPS:
        return 3
    if r >= 0.0 - _EPS:
        return 2
    if r >= -0.3 - _EPS:
        return 1
    return 0


def realized_R_to_tier_batch(r: np.ndarray) -> np.ndarray:
    """Vectorized realized_R → tier index."""
    r = r.astype(np.float64, copy=False)
    out = np.zeros(len(r), dtype=np.int32)
    out[r >= 1.0 - _EPS] = 4
    out[(r >= 0.5 - _EPS) & (r < 1.0 - _EPS)] = 3
    out[(r >= 0.0 - _EPS) & (r < 0.5 - _EPS)] = 2
    out[(r >= -0.3 - _EPS) & (r < 0.0 - _EPS)] = 1
    # r < -0.3 - _EPS stays 0
    return out


class SizeModel:
    """LightGBM 5-class classifier predicting the best size tier for an obs."""

    engine: str = _ENGINE

    def __init__(self) -> None:
        self.model: _Classifier | None = None
        self.scaler: StandardScaler | None = None
        self._alive_mask: np.ndarray | None = None

    def train(
        self,
        X: np.ndarray,
        realized_R: np.ndarray,
        n_estimators: int = 400,
        max_depth: int = 4,
        learning_rate: float = 0.05,
        subsample: float = 0.8,
    ) -> dict:
        """Train the size tier classifier with chronological 80/20 split."""
        n = len(X)
        val_split = int(n * 0.80)

        stds = np.std(X[:val_split], axis=0)
        self._alive_mask = stds > 1e-8
        alive_count = int(self._alive_mask.sum())

        self.scaler = StandardScaler()
        X_train = self.scaler.fit_transform(X[:val_split, self._alive_mask])
        X_val = self.scaler.transform(X[val_split:, self._alive_mask])

        y = realized_R_to_tier_batch(realized_R)
        y_train, y_val = y[:val_split], y[val_split:]

        # class weights — rare A+ / skip classes get upweighted
        unique, counts = np.unique(y_train, return_counts=True)
        weights = {int(c): float(len(y_train) / (len(unique) * n)) for c, n in zip(unique, counts)}
        sample_weight = np.array([weights[int(c)] for c in y_train], dtype=np.float64)

        if _ENGINE == "lightgbm":
            params = dict(
                n_estimators=n_estimators,
                max_depth=max_depth,
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
            params = dict(
                n_estimators=n_estimators,
                max_depth=max_depth,
                learning_rate=learning_rate,
                subsample=subsample,
                min_samples_leaf=100,
                max_features="sqrt",
                validation_fraction=0.1,
                n_iter_no_change=20,
                tol=1e-4,
            )

        log.info(
            "Training SizeModel: %d train / %d val, %d features, %d classes observed",
            val_split,
            n - val_split,
            alive_count,
            len(unique),
        )

        self.model = _Classifier(**params)
        fit_kwargs = {"sample_weight": sample_weight}
        if _ENGINE == "lightgbm":
            fit_kwargs["eval_set"] = [(X_val, y_val)]
            fit_kwargs["callbacks"] = [
                __import__("lightgbm").early_stopping(50, verbose=False),
                __import__("lightgbm").log_evaluation(0),
            ]
        self.model.fit(X_train, y_train, **fit_kwargs)

        train_acc = round(self.model.score(X_train, y_train) * 100, 1)
        val_acc = round(self.model.score(X_val, y_val) * 100, 1)
        metrics = {
            "engine": _ENGINE,
            "alive_features": alive_count,
            "total_features": int(X.shape[1]),
            "train_size": val_split,
            "val_size": n - val_split,
            "train_accuracy": train_acc,
            "val_accuracy": val_acc,
            "class_distribution": {int(c): int(n) for c, n in zip(unique, counts)},
        }
        log.info("SizeModel: train=%.1f%% val=%.1f%%", train_acc, val_acc)

        # Expected-R diagnostic: weighted tier multiplier × realized_R on val
        val_tiers = self.predict_tier_batch(X[val_split:])
        val_mult = np.array([SIZE_TIERS[t] for t in val_tiers], dtype=np.float64)
        metrics["val_mean_size_multiplier"] = round(float(val_mult.mean()), 3)
        metrics["val_mean_weighted_R"] = round(float((val_mult * realized_R[val_split:]).mean()), 4)
        log.info(
            "SizeModel val diagnostics: mean_mult=%.2f, mean_weighted_R=%.4f",
            metrics["val_mean_size_multiplier"],
            metrics["val_mean_weighted_R"],
        )
        return metrics

    def _scale_single(self, obs: np.ndarray) -> np.ndarray:
        return self.scaler.transform(obs[self._alive_mask].reshape(1, -1))

    def predict_tier(self, obs: np.ndarray) -> int:
        """Predicted tier index ∈ [0, NUM_SIZE_TIERS-1] for a single observation."""
        x = self._scale_single(obs)
        return int(np.argmax(self.model.predict_proba(x)[0]))

    def predict_size(self, obs: np.ndarray) -> float:
        """Predicted position-size multiplier for a single observation."""
        return SIZE_TIERS[self.predict_tier(obs)]

    def predict_tier_batch(self, obs: np.ndarray) -> np.ndarray:
        X = self.scaler.transform(obs[:, self._alive_mask])
        return np.argmax(self.model.predict_proba(X), axis=1).astype(np.int32)

    def predict_size_batch(self, obs: np.ndarray) -> np.ndarray:
        tiers = self.predict_tier_batch(obs)
        return np.array([SIZE_TIERS[t] for t in tiers], dtype=np.float32)

    def feature_importance(self, top_n: int = 20) -> list[tuple[int, float]]:
        imp = self.model.feature_importances_
        alive_indices = np.where(self._alive_mask)[0]
        top = np.argsort(-imp)[:top_n]
        return [(int(alive_indices[i]), float(imp[i])) for i in top]

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "model": self.model,
                "scaler": self.scaler,
                "alive_mask": self._alive_mask,
                "tiers": SIZE_TIERS,
                "version": 1,
            },
            path,
        )
        log.info("SizeModel saved to %s", path)

    @classmethod
    def load(cls, path: Path) -> SizeModel:
        data = joblib.load(path)
        m = cls()
        m.model = data["model"]
        m.scaler = data["scaler"]
        m._alive_mask = data["alive_mask"]
        log.info("SizeModel loaded from %s (version=%s)", path, data.get("version", "unknown"))
        return m
