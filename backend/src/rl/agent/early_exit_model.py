"""EarlyExitModel — trained head that predicts whether to exit early (Phase 3c).

Problem: some trades pump to a partial profit (e.g. +0.5R) and then retrace to
breakeven or worse. Exiting at the peak would have locked those trades as
small winners instead of scratches or losers. This head predicts, at entry,
the probability that THIS trade will be a "pump-and-retrace" — if high, the
session manager can attach an early-exit rule that closes the position when
it first reaches +0.5R.

Label (derived at training time from tick path stats already computed by
episode_builder):

    early_exit_label = 1  if peak_R >= 0.5 AND realized_R < 0.5
                     = 0  otherwise

- peak_R is Maximum Favorable Excursion in R units (added in Phase 3c).
- realized_R is the best-side reward (max of continuation/reversal).

The threshold 0.5 matches the level at which the session manager would
actually take the early exit — a natural breakeven-plus-small-profit level
given the 20-tick stop basis.

Input: same 318-dim augmented observation the DQN and SizeModel use.
Output: P(early_exit is optimal) ∈ [0, 1].
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

# Pump-then-retrace definition. These thresholds match what the live session
# manager would key off: a "+0.5R locked" early-exit rule.
PUMP_R_THRESHOLD: float = 0.5  # peak_R must reach this to consider "pump"
REALIZED_R_MAX: float = 0.5  # realized_R must stay below this to be "retrace"

# Small tolerance for float32 boundary noise (matches size_model convention).
_EPS: float = 1e-6


def derive_early_exit_labels(peak_R: np.ndarray, realized_R: np.ndarray) -> np.ndarray:
    """Binary label: 1 if trade pumped ≥PUMP_R then realized <REALIZED_R_MAX."""
    peak = peak_R.astype(np.float64, copy=False)
    real = realized_R.astype(np.float64, copy=False)
    pumped = peak >= PUMP_R_THRESHOLD - _EPS
    retraced = real < REALIZED_R_MAX - _EPS
    return (pumped & retraced).astype(np.int32)


class EarlyExitModel:
    """LightGBM binary classifier: P(this trade is a pump-and-retrace)."""

    engine: str = _ENGINE

    def __init__(self) -> None:
        self.model: _Classifier | None = None
        self.scaler: StandardScaler | None = None
        self._alive_mask: np.ndarray | None = None

    def train(
        self,
        X: np.ndarray,
        peak_R: np.ndarray,
        realized_R: np.ndarray,
        n_estimators: int = 400,
        max_depth: int = 4,
        learning_rate: float = 0.05,
        subsample: float = 0.8,
        init_model_path: Path | str | None = None,
        confluence_weights: np.ndarray | None = None,
    ) -> dict:
        """Train on the 318-dim observation with pump-and-retrace labels.

        - `init_model_path` warm-starts from a prior EarlyExitModel booster
          (ONLINE1).
        - `confluence_weights` (per-episode) upweights multi-member-zone
          trades so the model isn't drowned out by 1-member zone volume (H8).
        """
        n = len(X)
        val_split = int(n * 0.80)

        stds = np.std(X[:val_split], axis=0)
        self._alive_mask = stds > 1e-8
        alive_count = int(self._alive_mask.sum())

        self.scaler = StandardScaler()
        X_train = self.scaler.fit_transform(X[:val_split, self._alive_mask])
        X_val = self.scaler.transform(X[val_split:, self._alive_mask])

        y = derive_early_exit_labels(peak_R, realized_R)
        y_train, y_val = y[:val_split], y[val_split:]

        pos = int(y_train.sum())
        neg = int(len(y_train) - pos)
        scale_pos_weight = (neg / max(pos, 1)) if pos > 0 else 1.0

        if _ENGINE == "lightgbm":
            # Use AUC as the eval metric for early stopping; default multiclass
            # logloss happily converges to the all-negative prediction on this
            # imbalanced label (~8% positives), producing 0 precision / recall.
            # AUC is threshold-free so early stopping actually rewards learning
            # the positive class.
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
                is_unbalance=True,
                n_jobs=2,
                verbose=-1,
                metric="auc",
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
            "Training EarlyExitModel: %d train / %d val, %d features, %d positive (%.1f%%)",
            val_split,
            n - val_split,
            alive_count,
            pos,
            100.0 * pos / max(len(y_train), 1),
        )

        self.model = _Classifier(**params)
        fit_kwargs: dict = {}

        warm_start_used = False
        if init_model_path is not None and _ENGINE == "lightgbm":
            try:
                prior = joblib.load(Path(init_model_path))
                prior_model = prior.get("model") if isinstance(prior, dict) else None
                prior_mask = prior.get("alive_mask") if isinstance(prior, dict) else None
                if prior_model is not None and prior_mask is not None and int(prior_mask.sum()) == alive_count:
                    fit_kwargs["init_model"] = prior_model.booster_
                    warm_start_used = True
                    log.info("EarlyExitModel: warm-starting from %s", init_model_path)
                else:
                    log.warning(
                        "EarlyExitModel: skipping warm-start — prior alive_count=%s vs current=%d",
                        int(prior_mask.sum()) if prior_mask is not None else "None",
                        alive_count,
                    )
            except Exception:
                log.exception("EarlyExitModel: warm-start failed, falling back to cold start")

        # H8: optional confluence sample weights. Already have is_unbalance for
        # class balance; this adds a multiplicative per-sample factor so
        # multi-member-zone trades count more.
        if confluence_weights is not None:
            assert len(confluence_weights) == len(X), "confluence_weights must align with X"
            fit_kwargs["sample_weight"] = confluence_weights[:val_split].astype(np.float64)

        if _ENGINE == "lightgbm":
            fit_kwargs["eval_set"] = [(X_val, y_val)]
            fit_kwargs["callbacks"] = [
                __import__("lightgbm").early_stopping(50, verbose=False),
                __import__("lightgbm").log_evaluation(0),
            ]
        else:
            sample_weight = np.where(y_train == 1, scale_pos_weight, 1.0)
            fit_kwargs["sample_weight"] = sample_weight
        self.model.fit(X_train, y_train, **fit_kwargs)

        train_acc = round(self.model.score(X_train, y_train) * 100, 1)
        val_acc = round(self.model.score(X_val, y_val) * 100, 1)

        # Report precision/recall at multiple thresholds — with an imbalanced
        # label the default 0.5 cut often yields zero positives. AUC is the
        # real "did it learn?" metric; P/R at tuned thresholds tell downstream
        # callers what threshold to use for actual early-exit decisions.
        val_probs = self.model.predict_proba(X_val)[:, 1]

        def _pr(threshold: float) -> tuple[float, float, int]:
            pred = (val_probs >= threshold).astype(np.int32)
            tp = int(((pred == 1) & (y_val == 1)).sum())
            fp = int(((pred == 1) & (y_val == 0)).sum())
            fn = int(((pred == 0) & (y_val == 1)).sum())
            prec = tp / max(tp + fp, 1)
            rec = tp / max(tp + fn, 1)
            return prec, rec, int(pred.sum())

        try:
            from sklearn.metrics import roc_auc_score

            auc = float(roc_auc_score(y_val, val_probs))
        except Exception:
            auc = float("nan")

        p05, r05, n05 = _pr(0.5)
        p03, r03, n03 = _pr(0.3)
        p07, r07, n07 = _pr(0.7)

        metrics = {
            "engine": _ENGINE,
            "alive_features": alive_count,
            "total_features": int(X.shape[1]),
            "train_size": val_split,
            "val_size": n - val_split,
            "train_positive_pct": round(100.0 * pos / max(len(y_train), 1), 2),
            "val_positive_pct": round(100.0 * float(y_val.sum()) / max(len(y_val), 1), 2),
            "train_accuracy": train_acc,
            "val_accuracy": val_acc,
            "val_auc": round(auc, 4),
            "warm_start": warm_start_used,
            "val_precision@0.5": round(p05, 3),
            "val_recall@0.5": round(r05, 3),
            "val_flagged@0.5": n05,
            "val_precision@0.3": round(p03, 3),
            "val_recall@0.3": round(r03, 3),
            "val_flagged@0.3": n03,
            "val_precision@0.7": round(p07, 3),
            "val_recall@0.7": round(r07, 3),
            "val_flagged@0.7": n07,
        }
        log.info(
            "EarlyExitModel: train=%.1f%% val=%.1f%% AUC=%.3f (P@.5=%.2f R@.5=%.2f)",
            train_acc,
            val_acc,
            auc,
            p05,
            r05,
        )
        return metrics

    def _scale_single(self, obs: np.ndarray) -> np.ndarray:
        return self.scaler.transform(obs[self._alive_mask].reshape(1, -1))

    def predict_proba(self, obs: np.ndarray) -> float:
        """P(pump-and-retrace) for a single observation."""
        x = self._scale_single(obs)
        return float(self.model.predict_proba(x)[0, 1])

    def predict_proba_batch(self, obs: np.ndarray) -> np.ndarray:
        X = self.scaler.transform(obs[:, self._alive_mask])
        return self.model.predict_proba(X)[:, 1].astype(np.float32)

    def should_early_exit(self, obs: np.ndarray, threshold: float = 0.5) -> bool:
        """Convenience: True if P(pump-and-retrace) ≥ threshold."""
        return self.predict_proba(obs) >= threshold

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
                "pump_r_threshold": PUMP_R_THRESHOLD,
                "realized_r_max": REALIZED_R_MAX,
                "version": 1,
            },
            path,
        )
        log.info("EarlyExitModel saved to %s", path)

    @classmethod
    def load(cls, path: Path) -> EarlyExitModel:
        data = joblib.load(path)
        m = cls()
        m.model = data["model"]
        m.scaler = data["scaler"]
        m._alive_mask = data["alive_mask"]
        log.info("EarlyExitModel loaded from %s (version=%s)", path, data.get("version", "unknown"))
        return m
