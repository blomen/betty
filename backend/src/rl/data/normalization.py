"""
Running normalizer for RL observation vectors.

Two modes:
- EWMNormalizer (default): exponentially-weighted mean/variance with halflife.
  Adapts to regime shifts within 1-2 trading days. Recent data matters more.
- RunningNormalizer (legacy): Welford's global mean/variance.
  Treats 2011 data same as 2026 — bad for non-stationary markets.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


class EWMNormalizer:
    """Exponentially-weighted moving normalizer.

    Uses EWM mean and variance with configurable halflife. Recent observations
    have more weight than old ones, so the normalizer tracks regime shifts
    instead of being diluted by 15 years of history.

    halflife=500 means an observation from 500 samples ago has half the weight
    of the current one. For ~100 episodes/day, that's ~5 trading days.
    """

    def __init__(self, dim: int, halflife: int = 500):
        self.dim = dim
        self.halflife = halflife
        self.alpha = 1.0 - np.exp(-np.log(2) / halflife)
        self.count = 0
        self.ewm_mean = np.zeros(dim, dtype=np.float64)
        self.ewm_var = np.ones(dim, dtype=np.float64)  # start at 1 to avoid div-by-zero

    def update(self, x: np.ndarray) -> None:
        """Update EWM statistics with one observation."""
        x = np.asarray(x, dtype=np.float64)
        self.count += 1
        if self.count == 1:
            self.ewm_mean = x.copy()
            self.ewm_var = np.ones(self.dim, dtype=np.float64)
            return
        delta = x - self.ewm_mean
        self.ewm_mean += self.alpha * delta
        self.ewm_var = (1 - self.alpha) * (self.ewm_var + self.alpha * delta * delta)

    def normalize(self, x: np.ndarray, context_start: int | None = None) -> np.ndarray:
        """Normalize using EWM mean/std. Returns float32."""
        x = np.asarray(x, dtype=np.float64)
        if self.count < 2:
            return x.astype(np.float32)
        std = np.sqrt(np.maximum(self.ewm_var, 1e-8))
        result = x.copy()
        if context_start is not None:
            result[context_start:] = (x[context_start:] - self.ewm_mean[context_start:]) / std[context_start:]
        else:
            result = (x - self.ewm_mean) / std
        return result.astype(np.float32)

    @property
    def variance(self) -> np.ndarray:
        return self.ewm_var.copy()

    @property
    def std(self) -> np.ndarray:
        return np.sqrt(np.maximum(self.ewm_var, 1e-8))

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "type": "ewm",
            "dim": self.dim,
            "halflife": self.halflife,
            "count": self.count,
            "ewm_mean": self.ewm_mean.tolist(),
            "ewm_var": self.ewm_var.tolist(),
        }
        path.write_text(json.dumps(data, indent=2))

    def load(self, path: Path) -> None:
        path = Path(path)
        data = json.loads(path.read_text())
        if data.get("type") == "ewm":
            if data["dim"] != self.dim:
                raise ValueError(f"Saved dim {data['dim']} != {self.dim}")
            self.halflife = data.get("halflife", 500)
            self.alpha = 1.0 - np.exp(-np.log(2) / self.halflife)
            self.count = data["count"]
            self.ewm_mean = np.array(data["ewm_mean"], dtype=np.float64)
            self.ewm_var = np.array(data["ewm_var"], dtype=np.float64)
        else:
            # Backward compat: load old Welford format, convert to EWM
            if data["dim"] != self.dim:
                raise ValueError(f"Saved dim {data['dim']} != {self.dim}")
            self.count = data["count"]
            self.ewm_mean = np.array(data["mean"], dtype=np.float64)
            m2 = np.array(data["M2"], dtype=np.float64)
            self.ewm_var = m2 / max(self.count - 1, 1)


# Keep RunningNormalizer as alias for backward compatibility
class RunningNormalizer(EWMNormalizer):
    """Backward-compatible alias. Now uses EWM instead of Welford."""

    def __init__(self, dim: int, halflife: int = 500):
        super().__init__(dim, halflife)
