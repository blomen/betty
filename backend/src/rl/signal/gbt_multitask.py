"""Three independent LightGBM heads trained on the same obs pool:
  - magnitude_R: regression on realized R (continuous)
  - win_probability: binary classifier (win=1, loss=0)
  - duration_bars: regression on bars-to-exit

These complement the direction head from TriggerGBT. Together they
populate the MultiTaskOutputs contract from a single obs vector.

Training data source: existing /app/data/rl/episodes/ pool:
  - observations.npy: (N, 313) obs vectors
  - rewards_cont.npy / rewards_rev.npy: realized R per episode (use the
    one matching the GBT's predicted action)
  - duration_bars: derive from episode metadata (TODO: where?)
"""

from __future__ import annotations

from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np


class MultiTaskGBT:
    def __init__(self) -> None:
        self._magnitude_model: lgb.LGBMRegressor | None = None
        self._winprob_model: lgb.LGBMClassifier | None = None
        self._duration_model: lgb.LGBMRegressor | None = None

    def train(
        self,
        X: np.ndarray,
        *,
        magnitudes: np.ndarray,
        win_outcomes: np.ndarray,
        durations: np.ndarray,
    ) -> None:
        """Train all three heads. Each is independent; the GBT trees
        on the same obs find different patterns relevant to each target."""
        common_params = dict(
            n_estimators=200,
            num_leaves=31,
            learning_rate=0.05,
            min_child_samples=20,
            verbose=-1,
        )

        self._magnitude_model = lgb.LGBMRegressor(**common_params)
        self._magnitude_model.fit(X, magnitudes)

        self._winprob_model = lgb.LGBMClassifier(**common_params)
        self._winprob_model.fit(X, win_outcomes)

        self._duration_model = lgb.LGBMRegressor(**common_params)
        self._duration_model.fit(X, durations)

    def predict(self, obs: np.ndarray) -> dict:
        if self._magnitude_model is None:
            return {"magnitude_R": 0.0, "win_probability": 0.5, "duration_bars": 5.0}

        x = obs.reshape(1, -1)
        return {
            "magnitude_R": float(self._magnitude_model.predict(x)[0]),
            "win_probability": float(self._winprob_model.predict_proba(x)[0][1]),
            "duration_bars": float(self._duration_model.predict(x)[0]),
        }

    def save(self, path: Path | str) -> None:
        joblib.dump(
            {
                "magnitude": self._magnitude_model,
                "winprob": self._winprob_model,
                "duration": self._duration_model,
            },
            path,
        )

    @classmethod
    def load(cls, path: Path | str) -> MultiTaskGBT:
        d = joblib.load(path)
        inst = cls()
        inst._magnitude_model = d["magnitude"]
        inst._winprob_model = d["winprob"]
        inst._duration_model = d["duration"]
        return inst
