"""Abstract base for any predictor in the Arnold signal layer.

Implementations:
  - GBTPredictor (current production)
  - FTTransformerPredictor (shadow, eventually promoted)
  - Future: TabNet, Decision Transformer for execution side

All implementations return the same Signal dataclass via predict().
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from .types import MultiTaskOutputs, Signal


class ModelProtocol(ABC):
    """Abstract base. Subclasses override predict_raw() — the rest is
    handled by the default predict() which optionally applies calibration
    then packages into a Signal."""

    def __init__(self) -> None:
        self._calibrator = None  # set via attach_calibrator()

    def attach_calibrator(self, calibrator) -> None:
        """Attach an IsotonicCalibrator (or None to clear). Calibrator
        must have transform(logits: list[float]) -> list[float]."""
        self._calibrator = calibrator

    @abstractmethod
    def predict_raw(self, obs: np.ndarray) -> MultiTaskOutputs:
        """Return raw multi-task outputs. Subclass responsibility."""
        ...

    def predict(self, obs: np.ndarray, *, zone_id: int, timestamp: float) -> Signal:
        """Full pipeline: predict_raw → calibrate → package into Signal."""
        raw = self.predict_raw(obs)

        logits = raw.direction_logits
        if self._calibrator is not None:
            logits = self._calibrator.transform(logits)

        return Signal(
            p_cont=float(logits[0]),
            p_rev=float(logits[1]),
            p_skip=float(logits[2]),
            expected_R=float(raw.magnitude_R),
            win_probability=float(raw.win_probability),
            duration_bars=float(raw.duration_bars),
            uncertainty=float(raw.uncertainty),
            timestamp=float(timestamp),
            zone_id=int(zone_id),
        )
