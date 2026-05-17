"""Per-class isotonic regression calibration for ModelProtocol outputs.

Sklearn's IsotonicRegression fits a monotone non-parametric mapping
raw_prob → calibrated_prob. Fit on holdout (raw model probability,
realized outcome). Transform any new model output to get calibrated
probabilities.

For 3-class (CONTINUATION/REVERSAL/SKIP), fit one calibrator per class on
the one-vs-rest binarization, then renormalize so calibrated probs sum to 1.
"""

from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
from sklearn.isotonic import IsotonicRegression


class IsotonicCalibrator:
    def __init__(self) -> None:
        self._fitted: dict[int, IsotonicRegression] = {}

    def fit_per_class(
        self,
        class_idx: int,
        raw_probs: np.ndarray,
        true_outcomes: np.ndarray,
    ) -> None:
        """Fit calibrator for one class. true_outcomes is binary
        (1 if this class won, 0 otherwise)."""
        ir = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        ir.fit(np.asarray(raw_probs).astype(float), np.asarray(true_outcomes).astype(float))
        self._fitted[class_idx] = ir

    def transform(self, raw_logits: list[float]) -> list[float]:
        """Apply per-class calibration, then renormalize to sum to 1.

        Renormalization only applies when every input position has a fitted
        calibrator.  If any class is unfitted (passes through raw), the
        outputs are returned as-is so that a partially-fitted calibrator does
        not collapse meaningful probability mass to 1.0.
        """
        if not self._fitted:
            return list(raw_logits)
        calibrated = []
        all_fitted = True
        for ci, p in enumerate(raw_logits):
            ir = self._fitted.get(ci)
            if ir is None:
                calibrated.append(float(p))
                all_fitted = False
            else:
                calibrated.append(float(ir.predict([float(p)])[0]))
        if not all_fitted:
            return calibrated
        # Renormalize only when all classes are calibrated
        total = sum(calibrated)
        if total <= 0:
            return [1.0 / len(calibrated)] * len(calibrated)
        return [c / total for c in calibrated]

    def save(self, path: Path | str) -> None:
        joblib.dump(self._fitted, path)

    @classmethod
    def load(cls, path: Path | str) -> IsotonicCalibrator:
        inst = cls()
        inst._fitted = joblib.load(path)
        return inst
