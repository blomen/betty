"""Continuation and Reversal specialist models.

Two GBT classifiers trained on opposite subsets of the data:
- ContinuationSpecialist: trained on episodes where CONT was profitable (reward > 0)
  Answers: "given this level touch, will continuation succeed?"
- ReversalSpecialist: trained on episodes where REV was profitable (reward > 0)
  Answers: "given this level touch, will reversal succeed?"

At inference time both models score the touch. The one with higher
confidence wins. If neither is confident, skip.

This separates the "will price break through?" question from
"will price bounce?" — the features that predict each are different.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from sklearn.preprocessing import StandardScaler

log = logging.getLogger(__name__)


class _Specialist:
    """Base specialist: binary GBT classifier + reward regressor."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.classifier = None       # P(action succeeds)
        self.reward_model = None      # E[R | action succeeds]
        self.scaler: StandardScaler | None = None
        self._alive_mask: np.ndarray | None = None

    def train(
        self,
        X: np.ndarray,
        y_success: np.ndarray,      # 1 = action was profitable, 0 = loss
        rewards: np.ndarray,         # actual R for this action direction
        n_estimators: int = 300,
        max_depth: int = 5,
        learning_rate: float = 0.05,
    ) -> dict:
        try:
            from lightgbm import LGBMClassifier, LGBMRegressor
            _Cls, _Reg = LGBMClassifier, LGBMRegressor
        except ImportError:
            from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
            _Cls, _Reg = GradientBoostingClassifier, GradientBoostingRegressor

        # Remove dead features
        stds = X.std(axis=0)
        self._alive_mask = stds > 1e-8
        X_alive = X[:, self._alive_mask]

        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X_alive)

        params = dict(
            n_estimators=n_estimators, max_depth=max_depth,
            learning_rate=learning_rate, subsample=0.8,
            n_jobs=2, verbose=-1,
        )

        # Binary classifier: will this action succeed?
        self.classifier = _Cls(**params)
        self.classifier.fit(X_scaled, y_success)
        acc = (self.classifier.predict(X_scaled) == y_success).mean()

        # Reward regressor: how much R if it succeeds?
        self.reward_model = _Reg(
            n_estimators=min(200, n_estimators), max_depth=min(4, max_depth),
            learning_rate=learning_rate, subsample=0.8,
            n_jobs=2, verbose=-1,
        )
        self.reward_model.fit(X_scaled, rewards)

        win_rate = y_success.mean()
        avg_r_wins = rewards[y_success == 1].mean() if y_success.sum() > 0 else 0
        avg_r_losses = rewards[y_success == 0].mean() if (y_success == 0).sum() > 0 else 0

        metrics = {
            "name": self.name,
            "samples": len(X),
            "alive_features": int(self._alive_mask.sum()),
            "accuracy": round(acc * 100, 1),
            "win_rate": round(win_rate * 100, 1),
            "avg_r_wins": round(avg_r_wins, 3),
            "avg_r_losses": round(avg_r_losses, 3),
        }
        log.info("%s specialist: %s", self.name, metrics)
        return metrics

    def predict(self, obs: np.ndarray) -> tuple[float, float]:
        """Predict success probability and expected reward.

        Returns:
            (p_success, expected_r)
        """
        if self.classifier is None or self._alive_mask is None:
            return 0.5, 0.0

        x = obs[self._alive_mask].reshape(1, -1)
        x = self.scaler.transform(x)

        proba = self.classifier.predict_proba(x)[0]
        p_success = proba[1] if len(proba) > 1 else 0.5
        expected_r = self.reward_model.predict(x)[0]

        return float(p_success), float(expected_r)

    def predict_batch(self, obs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Batch predict. Returns (p_success, expected_r) arrays."""
        if self.classifier is None or self._alive_mask is None:
            n = len(obs)
            return np.full(n, 0.5), np.zeros(n)

        X = self.scaler.transform(obs[:, self._alive_mask])
        proba = self.classifier.predict_proba(X)
        p_success = proba[:, 1] if proba.shape[1] > 1 else np.full(len(X), 0.5)
        expected_r = self.reward_model.predict(X)

        return p_success.astype(np.float32), expected_r.astype(np.float32)


class ContinuationSpecialist(_Specialist):
    """Expert at predicting when price will break through a level."""

    def __init__(self) -> None:
        super().__init__("continuation")


class ReversalSpecialist(_Specialist):
    """Expert at predicting when price will bounce off a level."""

    def __init__(self) -> None:
        super().__init__("reversal")


class StopSpecialist:
    """Predicts optimal stop distance in ticks for a given setup.

    Trained on MAE-derived optimal stops: the distance that maximizes
    realized P&L — tight enough to limit losses, wide enough to not
    get clipped by noise before the move develops.

    Output: stop distance in ticks (6-40 range, continuous).
    """

    def __init__(self) -> None:
        self.model = None
        self.scaler: StandardScaler | None = None
        self._alive_mask: np.ndarray | None = None

    def train(
        self,
        X: np.ndarray,
        stop_targets: np.ndarray,
        n_estimators: int = 300,
        max_depth: int = 5,
        learning_rate: float = 0.05,
    ) -> dict:
        try:
            from lightgbm import LGBMRegressor
            _Reg = LGBMRegressor
        except ImportError:
            from sklearn.ensemble import GradientBoostingRegressor
            _Reg = GradientBoostingRegressor

        stds = X.std(axis=0)
        self._alive_mask = stds > 1e-8
        X_alive = X[:, self._alive_mask]

        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X_alive)

        self.model = _Reg(
            n_estimators=n_estimators, max_depth=max_depth,
            learning_rate=learning_rate, subsample=0.8,
            n_jobs=2, verbose=-1,
        )
        self.model.fit(X_scaled, stop_targets)

        preds = self.model.predict(X_scaled)
        mae = np.abs(preds - stop_targets).mean()
        return {
            "name": "stop",
            "samples": len(X),
            "alive_features": int(self._alive_mask.sum()),
            "mae_ticks": round(mae, 1),
            "mean_pred": round(preds.mean(), 1),
            "mean_actual": round(stop_targets.mean(), 1),
        }

    def predict(self, obs: np.ndarray) -> float:
        """Predict optimal stop distance in ticks."""
        if self.model is None or self._alive_mask is None:
            return 15.0  # default
        x = obs[self._alive_mask].reshape(1, -1)
        x = self.scaler.transform(x)
        pred = float(self.model.predict(x)[0])
        return max(6.0, min(40.0, pred))  # clamp to valid range

    def predict_batch(self, obs: np.ndarray) -> np.ndarray:
        """Batch predict stop distances."""
        if self.model is None or self._alive_mask is None:
            return np.full(len(obs), 15.0, dtype=np.float32)
        X = self.scaler.transform(obs[:, self._alive_mask])
        preds = self.model.predict(X)
        return np.clip(preds, 6.0, 40.0).astype(np.float32)


class SpecialistEnsemble:
    """Combines CONT and REV specialists for trading decisions.

    Decision logic:
    1. Both specialists score the touch
    2. Compute expected value: EV = p_success * expected_r_win + (1-p_success) * expected_r_loss
    3. Higher EV wins (if above min threshold)
    4. Skip if both EVs are below threshold
    """

    MIN_CONFIDENCE = 0.50   # minimum p_success to consider a trade
    MIN_EV = 0.1            # minimum expected R to take the trade

    def __init__(
        self,
        cont_specialist: ContinuationSpecialist,
        rev_specialist: ReversalSpecialist,
        stop_specialist: StopSpecialist | None = None,
    ) -> None:
        self.cont = cont_specialist
        self.rev = rev_specialist
        self.stop = stop_specialist

    def decide(self, obs: np.ndarray) -> dict:
        """Make a trading decision from a single observation.

        Returns dict with:
            action: "continuation" | "reversal" | "skip"
            cont_p: P(continuation succeeds)
            rev_p: P(reversal succeeds)
            cont_ev: expected R from continuation
            rev_ev: expected R from reversal
            confidence: winner's p_success
            sizing_signal: 0-1 signal for position sizing
        """
        cont_p, cont_r = self.cont.predict(obs)
        rev_p, rev_r = self.rev.predict(obs)

        # Expected values (simplified: p * expected_r)
        cont_ev = cont_p * max(cont_r, 0) - (1 - cont_p) * abs(min(cont_r, 0))
        rev_ev = rev_p * max(rev_r, 0) - (1 - rev_p) * abs(min(rev_r, 0))

        # Decision
        if cont_ev > rev_ev and cont_p >= self.MIN_CONFIDENCE and cont_ev >= self.MIN_EV:
            action = "continuation"
            confidence = cont_p
        elif rev_p >= self.MIN_CONFIDENCE and rev_ev >= self.MIN_EV:
            action = "reversal"
            confidence = rev_p
        else:
            action = "skip"
            confidence = 0.0

        # Sizing signal: how much more confident is the winner over the loser?
        spread = abs(cont_ev - rev_ev)
        winner_p = cont_p if action == "continuation" else rev_p
        sizing_signal = min(1.0, winner_p * spread) if action != "skip" else 0.0

        # Dynamic stop placement
        stop_ticks = self.stop.predict(obs) if self.stop is not None else 15.0

        return {
            "action": action,
            "cont_p": round(cont_p, 3),
            "rev_p": round(rev_p, 3),
            "cont_ev": round(cont_ev, 3),
            "rev_ev": round(rev_ev, 3),
            "confidence": round(confidence, 3),
            "sizing_signal": round(sizing_signal, 3),
            "stop_ticks": round(stop_ticks, 1),
        }

    def decide_batch(self, obs: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Batch decisions. Returns (actions, confidences, sizing_signals).

        actions: 0=CONT, 1=REV, 2=SKIP
        """
        cont_p, cont_r = self.cont.predict_batch(obs)
        rev_p, rev_r = self.rev.predict_batch(obs)

        cont_ev = cont_p * np.maximum(cont_r, 0) - (1 - cont_p) * np.abs(np.minimum(cont_r, 0))
        rev_ev = rev_p * np.maximum(rev_r, 0) - (1 - rev_p) * np.abs(np.minimum(rev_r, 0))

        n = len(obs)
        actions = np.full(n, 2, dtype=np.int32)  # default skip
        confidences = np.zeros(n, dtype=np.float32)

        # Continuation wins
        cont_wins = (cont_ev > rev_ev) & (cont_p >= self.MIN_CONFIDENCE) & (cont_ev >= self.MIN_EV)
        actions[cont_wins] = 0
        confidences[cont_wins] = cont_p[cont_wins]

        # Reversal wins (where cont didn't)
        rev_wins = ~cont_wins & (rev_p >= self.MIN_CONFIDENCE) & (rev_ev >= self.MIN_EV)
        actions[rev_wins] = 1
        confidences[rev_wins] = rev_p[rev_wins]

        spread = np.abs(cont_ev - rev_ev)
        sizing = np.minimum(1.0, confidences * spread)
        sizing[actions == 2] = 0.0

        return actions, confidences, sizing

    def save(self, path: Path) -> None:
        import joblib
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "cont_classifier": self.cont.classifier,
            "cont_reward": self.cont.reward_model,
            "cont_scaler": self.cont.scaler,
            "cont_alive": self.cont._alive_mask,
            "rev_classifier": self.rev.classifier,
            "rev_reward": self.rev.reward_model,
            "rev_scaler": self.rev.scaler,
            "rev_alive": self.rev._alive_mask,
            "version": "v5_specialists",
        }
        if self.stop is not None:
            data["stop_model"] = self.stop.model
            data["stop_scaler"] = self.stop.scaler
            data["stop_alive"] = self.stop._alive_mask
        joblib.dump(data, path)

    @classmethod
    def load(cls, path: Path) -> SpecialistEnsemble:
        import joblib
        data = joblib.load(path)
        cont = ContinuationSpecialist()
        cont.classifier = data["cont_classifier"]
        cont.reward_model = data["cont_reward"]
        cont.scaler = data["cont_scaler"]
        cont._alive_mask = data["cont_alive"]
        rev = ReversalSpecialist()
        rev.classifier = data["rev_classifier"]
        rev.reward_model = data["rev_reward"]
        rev.scaler = data["rev_scaler"]
        rev._alive_mask = data["rev_alive"]
        stop = None
        if "stop_model" in data:
            stop = StopSpecialist()
            stop.model = data["stop_model"]
            stop.scaler = data["stop_scaler"]
            stop._alive_mask = data["stop_alive"]
        return cls(cont, rev, stop)
