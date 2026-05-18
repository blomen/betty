"""Trigger GBT model for the hierarchical observation architecture.

The Trigger GBT is the fast-layer model that processes trigger-specific
features (entry context, order flow, level proximity, etc.) to produce
an 8-dim forecast vector consumed by the DQN decision layer.

Forecast dims:
  0: prob_cont           — P(continuation direction wins)
  1: prob_rev            — P(reversal direction wins)
  2: confidence          — |prob_cont - prob_rev|
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
    from sklearn.ensemble import GradientBoostingClassifier as _Classifier  # type: ignore[assignment]
    from sklearn.ensemble import GradientBoostingRegressor as _Regressor  # type: ignore[assignment]

    _ENGINE = "sklearn"

log = logging.getLogger(__name__)

TRIGGER_GBT_FORECAST_DIM = 8


class TriggerGBT:
    """Fast-layer GBT: direction + magnitude + TP/SL + levels + stop from trigger features.

    Follows the same multi-head design as GBTModel but operates on trigger-layer
    features (entry context, order flow, AMT trigger signals) rather than the full
    observation. The Narrative GBT's setup probs are typically concatenated into
    the trigger feature vector before calling this model.

    Produces an 8-dim forecast vector (TRIGGER_GBT_FORECAST_DIM = 8).
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
        X: np.ndarray,
        y_direction: np.ndarray,
        rewards_cont: np.ndarray | None = None,
        rewards_rev: np.ndarray | None = None,
        stop_targets: np.ndarray | None = None,
        breakeven_reached: np.ndarray | None = None,
        levels_captured: np.ndarray | None = None,
        reward_gap: np.ndarray | None = None,
        n_estimators: int = 500,
        max_depth: int = 5,
        learning_rate: float = 0.05,
        subsample: float = 0.8,
    ) -> dict:
        """Train all trigger GBT heads with chronological train/val split.

        Uses the last 20% of data (chronologically) as validation set for
        early stopping and out-of-sample evaluation. This prevents overfitting
        on historical patterns that don't generalize.
        """
        n = len(X)
        # Chronological split: train on first 80%, validate on last 20%
        val_split = int(n * 0.80)
        X_train_raw, X_val_raw = X[:val_split], X[val_split:]
        y_dir_train, y_dir_val = y_direction[:val_split], y_direction[val_split:]

        # Remove dead features (computed on training set only)
        stds = np.std(X_train_raw, axis=0)
        self._alive_mask = stds > 1e-8
        alive_count = int(self._alive_mask.sum())

        self.scaler = StandardScaler()
        X_train = self.scaler.fit_transform(X_train_raw[:, self._alive_mask])
        X_val = self.scaler.transform(X_val_raw[:, self._alive_mask])

        # Sample weights for direction head
        sample_weight = None
        sw_val = None
        if reward_gap is not None:
            sw_all = np.clip(np.abs(reward_gap), 0.1, 5.0)
            sample_weight = sw_all[:val_split]
            sw_val = sw_all[val_split:]

        if _ENGINE == "lightgbm":
            # Regularized params — prevent overfitting with early stopping
            clf_params = dict(
                n_estimators=n_estimators,
                max_depth=min(max_depth, 4),  # cap depth to prevent memorization
                num_leaves=15,  # explicit cap — prevents complex splits at depth 4
                learning_rate=learning_rate,
                subsample=subsample,
                min_child_samples=100,  # high = more regularized
                colsample_bytree=0.5,  # aggressive feature dropout
                min_split_gain=0.01,  # prevent tiny splits on noise
                reg_alpha=0.1,  # L1 regularization
                reg_lambda=1.0,  # L2 regularization
                n_jobs=2,
                verbose=-1,
            )
            reg_params = dict(
                n_estimators=min(300, n_estimators),
                max_depth=min(3, max_depth),
                num_leaves=10,
                learning_rate=learning_rate,
                subsample=subsample,
                min_child_samples=100,
                min_split_gain=0.01,
                reg_alpha=0.1,
                reg_lambda=1.0,
                n_jobs=2,
                verbose=-1,
            )
        else:
            clf_params = dict(
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
            reg_params = dict(
                n_estimators=min(300, n_estimators),
                max_depth=min(3, max_depth),
                learning_rate=learning_rate,
                subsample=subsample,
                min_samples_leaf=100,
            )

        metrics: dict = {
            "alive_features": alive_count,
            "total_features": int(X.shape[1]),
            "engine": _ENGINE,
            "train_size": val_split,
            "val_size": n - val_split,
        }

        # Head 1: Direction classifier with early stopping on validation set
        log.info(
            "Training direction head: %d train / %d val, %d features",
            val_split,
            n - val_split,
            alive_count,
        )
        self.direction_model = _Classifier(**clf_params)
        fit_kwargs = {"sample_weight": sample_weight}
        if _ENGINE == "lightgbm":
            fit_kwargs["eval_set"] = [(X_val, y_dir_val)]
            fit_kwargs["eval_sample_weight"] = [sw_val] if sw_val is not None else None
            fit_kwargs["callbacks"] = [
                __import__("lightgbm").early_stopping(50, verbose=False),
                __import__("lightgbm").log_evaluation(0),
            ]
        self.direction_model.fit(X_train, y_dir_train, **fit_kwargs)

        # Report BOTH in-sample and out-of-sample accuracy
        train_acc = round(self.direction_model.score(X_train, y_dir_train) * 100, 1)
        val_acc = round(self.direction_model.score(X_val, y_dir_val) * 100, 1)
        best_iter = getattr(self.direction_model, "best_iteration_", n_estimators)
        metrics["direction_accuracy_train"] = train_acc
        metrics["direction_accuracy_val"] = val_acc
        metrics["direction_accuracy"] = val_acc  # report val as the real accuracy
        metrics["direction_trees"] = int(best_iter)
        log.info(
            "Direction head: train=%.1f%% val=%.1f%% (trees=%d, early_stop=%s)",
            train_acc,
            val_acc,
            best_iter,
            best_iter < n_estimators,
        )

        # Isotonic calibration — makes P(continuation) match actual probability
        # LightGBM outputs are NOT calibrated: 60% confidence ≠ 60% accuracy.
        # Isotonic regression on validation set fixes this non-parametrically.
        from sklearn.isotonic import IsotonicRegression

        val_probs = self.direction_model.predict_proba(X_val)
        val_p_cont = val_probs[:, 0]  # raw P(continuation)
        val_true_cont = (y_dir_val == 0).astype(np.float64)

        self.calibrator = IsotonicRegression(y_min=0.01, y_max=0.99, out_of_bounds="clip")
        self.calibrator.fit(val_p_cont, val_true_cont)
        cal_probs = self.calibrator.predict(val_p_cont)
        log.info(
            "Isotonic calibration fitted on %d val samples (raw range: %.3f-%.3f → cal range: %.3f-%.3f)",
            len(val_p_cont),
            val_p_cont.min(),
            val_p_cont.max(),
            cal_probs.min(),
            cal_probs.max(),
        )

        # Confidence calibration check on validation set (using calibrated probs)
        val_conf = np.maximum(cal_probs, 1 - cal_probs)
        val_pred_cont = cal_probs > 0.5
        for lo, hi in [(0.50, 0.55), (0.55, 0.60), (0.60, 0.70), (0.70, 1.0)]:
            mask = (val_conf >= lo) & (val_conf < hi)
            if mask.sum() > 0:
                bucket_acc = (val_pred_cont[mask] == val_true_cont[mask].astype(bool)).mean() * 100
                log.info("  conf %.2f-%.2f: %d samples, val_acc=%.1f%% (calibrated)", lo, hi, mask.sum(), bucket_acc)

        # Head 2: Expected best/worst R
        if rewards_cont is not None and rewards_rev is not None:
            best_r = np.maximum(rewards_cont, rewards_rev)
            worst_r = np.minimum(rewards_cont, rewards_rev)

            log.info("Training expected_best_r head")
            self.expected_best_r_model = _Regressor(**reg_params)
            if _ENGINE == "lightgbm":
                self.expected_best_r_model.fit(
                    X_train,
                    best_r[:val_split],
                    eval_set=[(X_val, best_r[val_split:])],
                    callbacks=[
                        __import__("lightgbm").early_stopping(50, verbose=False),
                        __import__("lightgbm").log_evaluation(0),
                    ],
                )
            else:
                self.expected_best_r_model.fit(X_train, best_r[:val_split])

            log.info("Training expected_worst_r head")
            self.expected_worst_r_model = _Regressor(**reg_params)
            if _ENGINE == "lightgbm":
                self.expected_worst_r_model.fit(
                    X_train,
                    worst_r[:val_split],
                    eval_set=[(X_val, worst_r[val_split:])],
                    callbacks=[
                        __import__("lightgbm").early_stopping(50, verbose=False),
                        __import__("lightgbm").log_evaluation(0),
                    ],
                )
            else:
                self.expected_worst_r_model.fit(X_train, worst_r[:val_split])

        # Head 3: Breakeven probability
        if breakeven_reached is not None:
            be_train = breakeven_reached[:val_split].astype(np.int32)
            be_val = breakeven_reached[val_split:].astype(np.int32)
            log.info("Training breakeven head")
            self.breakeven_model = _Classifier(**clf_params)
            if _ENGINE == "lightgbm":
                self.breakeven_model.fit(
                    X_train,
                    be_train,
                    eval_set=[(X_val, be_val)],
                    callbacks=[
                        __import__("lightgbm").early_stopping(50, verbose=False),
                        __import__("lightgbm").log_evaluation(0),
                    ],
                )
            else:
                self.breakeven_model.fit(X_train, be_train)
            metrics["breakeven_accuracy"] = round(self.breakeven_model.score(X_val, be_val) * 100, 1)

        # Head 4: Predicted levels captured
        if levels_captured is not None:
            log.info("Training levels head")
            self.levels_model = _Regressor(**reg_params)
            lc_clipped = np.clip(levels_captured, 0, 6)
            if _ENGINE == "lightgbm":
                self.levels_model.fit(
                    X_train,
                    lc_clipped[:val_split],
                    eval_set=[(X_val, lc_clipped[val_split:])],
                    callbacks=[
                        __import__("lightgbm").early_stopping(50, verbose=False),
                        __import__("lightgbm").log_evaluation(0),
                    ],
                )
            else:
                self.levels_model.fit(X_train, lc_clipped[:val_split])

        # Head 5: Stop distance
        if stop_targets is not None:
            log.info("Training stop head")
            self.stop_model = _Regressor(**reg_params)
            if _ENGINE == "lightgbm":
                self.stop_model.fit(
                    X_train,
                    stop_targets[:val_split],
                    eval_set=[(X_val, stop_targets[val_split:])],
                    callbacks=[
                        __import__("lightgbm").early_stopping(50, verbose=False),
                        __import__("lightgbm").log_evaluation(0),
                    ],
                )
            else:
                self.stop_model.fit(X_train, stop_targets[:val_split])

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

    def _calibrate(self, raw_p_cont: float) -> tuple[float, float]:
        """Apply isotonic calibration to raw P(continuation).

        Returns calibrated (prob_cont, prob_rev).
        """
        cal = getattr(self, "calibrator", None)
        if cal is not None:
            p_cont = float(np.clip(cal.predict([raw_p_cont])[0], 0.01, 0.99))
            return p_cont, 1.0 - p_cont
        return raw_p_cont, 1.0 - raw_p_cont

    def predict_full(self, obs: np.ndarray) -> np.ndarray:
        """Produce full 8-dim forecast vector for a single observation.

        Returns:
            ndarray of shape (8,):
              [prob_cont, prob_rev, confidence, expected_best_r, expected_worst_r,
               prob_breakeven, predicted_levels, predicted_stop]
        """
        x = self._scale_single(obs)

        # Direction (calibrated)
        raw_probs = self.direction_model.predict_proba(x)[0]
        prob_cont, prob_rev = self._calibrate(float(raw_probs[0]))
        confidence = abs(prob_cont - prob_rev)

        # Expected R
        best_r = float(self.expected_best_r_model.predict(x)[0]) if self.expected_best_r_model is not None else 0.0
        worst_r = float(self.expected_worst_r_model.predict(x)[0]) if self.expected_worst_r_model is not None else 0.0

        # Breakeven
        prob_be = float(self.breakeven_model.predict_proba(x)[0, 1]) if self.breakeven_model is not None else 0.5

        # Levels
        levels = float(np.clip(self.levels_model.predict(x)[0], 0.0, 6.0)) if self.levels_model is not None else 0.0

        # Stop
        stop = float(np.clip(self.stop_model.predict(x)[0], 6.0, 40.0)) if self.stop_model is not None else 10.0

        return np.array(
            [prob_cont, prob_rev, confidence, best_r, worst_r, prob_be, levels, stop],
            dtype=np.float32,
        )

    def predict_direction(self, obs: np.ndarray) -> tuple[int, float, float, float]:
        """Predict direction for a single observation (calibrated).

        Returns:
            (action_idx, confidence, prob_cont, prob_rev)
            where action_idx=0 means continuation, 1 means reversal.
        """
        x = self._scale_single(obs)
        raw_probs = self.direction_model.predict_proba(x)[0]
        prob_cont, prob_rev = self._calibrate(float(raw_probs[0]))
        action_idx = 0 if prob_cont >= prob_rev else 1
        confidence = abs(prob_cont - prob_rev)
        return action_idx, confidence, prob_cont, prob_rev

    def predict_stop(self, obs: np.ndarray) -> float:
        """Predict optimal stop distance in ticks for a single observation."""
        x = self._scale_single(obs)
        if self.stop_model is None:
            return 10.0
        return float(np.clip(self.stop_model.predict(x)[0], 6.0, 40.0))

    # ------------------------------------------------------------------
    # Batch prediction (for replay / evaluation)
    # ------------------------------------------------------------------

    def predict_full_batch(self, obs: np.ndarray) -> np.ndarray:
        """Produce full 8-dim forecast for a batch of observations (calibrated).

        Args:
            obs: ndarray of shape (N, F).

        Returns:
            ndarray of shape (N, 8).
        """
        X = self.scaler.transform(obs[:, self._alive_mask])
        n = len(X)

        # Direction (calibrated)
        raw_probs = self.direction_model.predict_proba(X)
        cal = getattr(self, "calibrator", None)
        if cal is not None:
            prob_cont = np.clip(cal.predict(raw_probs[:, 0]), 0.01, 0.99)
            prob_rev = 1.0 - prob_cont
        else:
            prob_cont = raw_probs[:, 0]
            prob_rev = raw_probs[:, 1]
        confidence = np.abs(prob_cont - prob_rev)

        # Expected R
        if self.expected_best_r_model is not None:
            best_r = self.expected_best_r_model.predict(X)
            worst_r = self.expected_worst_r_model.predict(X)
        else:
            best_r = np.zeros(n, dtype=np.float32)
            worst_r = np.zeros(n, dtype=np.float32)

        # Breakeven
        if self.breakeven_model is not None:
            prob_be = self.breakeven_model.predict_proba(X)[:, 1]
        else:
            prob_be = np.full(n, 0.5, dtype=np.float32)

        # Levels
        if self.levels_model is not None:
            levels = np.clip(self.levels_model.predict(X), 0.0, 6.0)
        else:
            levels = np.zeros(n, dtype=np.float32)

        # Stop
        if self.stop_model is not None:
            stop = np.clip(self.stop_model.predict(X), 6.0, 40.0)
        else:
            stop = np.full(n, 10.0, dtype=np.float32)

        return np.column_stack([prob_cont, prob_rev, confidence, best_r, worst_r, prob_be, levels, stop]).astype(
            np.float32
        )

    def predict_direction_batch(self, obs: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Batch direction prediction.

        Returns:
            (actions, confidences, probs) where probs has shape (N, 2).
        """
        X = self.scaler.transform(obs[:, self._alive_mask])
        raw_probs = self.direction_model.predict_proba(X)
        cal = getattr(self, "calibrator", None)
        if cal is not None:
            p_cont = np.clip(cal.predict(raw_probs[:, 0]), 0.01, 0.99)
            probs = np.column_stack([p_cont, 1.0 - p_cont])
        else:
            probs = raw_probs
        actions = np.argmax(probs, axis=1)
        confidences = np.abs(probs[:, 0] - probs[:, 1])
        return actions, confidences, probs

    # ------------------------------------------------------------------
    # Feature importance
    # ------------------------------------------------------------------

    def feature_importance(self, top_n: int = 20) -> list[tuple[int, float]]:
        """Top feature importances from the direction model."""
        imp = self.direction_model.feature_importances_
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
                "direction_model": self.direction_model,
                "expected_best_r_model": self.expected_best_r_model,
                "expected_worst_r_model": self.expected_worst_r_model,
                "breakeven_model": self.breakeven_model,
                "levels_model": self.levels_model,
                "stop_model": self.stop_model,
                "scaler": self.scaler,
                "alive_mask": self._alive_mask,
                "calibrator": getattr(self, "calibrator", None),
                "version": "v5_trigger",
            },
            path,
        )
        log.info("TriggerGBT saved to %s", path)

    @classmethod
    def load(cls, path: Path) -> TriggerGBT:
        """Load a saved TriggerGBT from disk."""
        data = joblib.load(path)
        model = cls()
        model.direction_model = data["direction_model"]
        model.expected_best_r_model = data.get("expected_best_r_model")
        model.expected_worst_r_model = data.get("expected_worst_r_model")
        model.breakeven_model = data.get("breakeven_model")
        model.levels_model = data.get("levels_model")
        model.stop_model = data.get("stop_model")
        model.scaler = data["scaler"]
        model._alive_mask = data["alive_mask"]
        model.calibrator = data.get("calibrator")
        log.info("TriggerGBT loaded from %s (calibrated=%s)", path, model.calibrator is not None)
        return model
