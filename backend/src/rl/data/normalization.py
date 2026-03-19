"""
Running normalizer for RL observation vectors using Welford's online algorithm.

Welford's algorithm computes mean and variance incrementally in a single pass,
avoiding numerical instability from naive sum-of-squares approaches.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


class RunningNormalizer:
    """
    Online normalizer using Welford's one-pass algorithm.

    Maintains running mean and M2 (sum of squared deviations from the mean)
    so that variance can be computed at any point without storing all samples.
    """

    def __init__(self, dim: int):
        self.dim = dim
        self.count = 0
        self.mean = np.zeros(dim, dtype=np.float64)
        self.M2 = np.zeros(dim, dtype=np.float64)

    def update(self, x: np.ndarray) -> None:
        """Update running statistics with one observation (Welford's)."""
        x = np.asarray(x, dtype=np.float64)
        if x.shape != (self.dim,):
            raise ValueError(f"Expected shape ({self.dim},), got {x.shape}")
        self.count += 1
        delta = x - self.mean
        self.mean += delta / self.count
        delta2 = x - self.mean
        self.M2 += delta * delta2

    def normalize(self, x: np.ndarray) -> np.ndarray:
        """Normalize to ~zero mean, unit variance. Returns float32."""
        x = np.asarray(x, dtype=np.float64)
        if self.count < 2:
            return x.astype(np.float32)
        std = np.sqrt(self.M2 / (self.count - 1))
        std = np.maximum(std, 1e-8)
        return ((x - self.mean) / std).astype(np.float32)

    @property
    def variance(self) -> np.ndarray:
        """Sample variance (returns zeros if fewer than 2 samples)."""
        if self.count < 2:
            return np.zeros(self.dim, dtype=np.float64)
        return self.M2 / (self.count - 1)

    @property
    def std(self) -> np.ndarray:
        """Sample standard deviation."""
        return np.sqrt(self.variance)

    def save(self, path: Path) -> None:
        """Persist statistics to a JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "dim": self.dim,
            "count": self.count,
            "mean": self.mean.tolist(),
            "M2": self.M2.tolist(),
        }
        path.write_text(json.dumps(data, indent=2))

    def load(self, path: Path) -> None:
        """Restore statistics from a JSON file produced by :meth:`save`."""
        path = Path(path)
        data = json.loads(path.read_text())
        if data["dim"] != self.dim:
            raise ValueError(
                f"Saved dim {data['dim']} does not match normalizer dim {self.dim}"
            )
        self.count = data["count"]
        self.mean = np.array(data["mean"], dtype=np.float64)
        self.M2 = np.array(data["M2"], dtype=np.float64)
