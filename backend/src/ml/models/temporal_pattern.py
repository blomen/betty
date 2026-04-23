"""M6: Temporal Pattern Recognizer -- flattened GBDT on candle sequences.

Predicts reversal/continuation from last 20 candles of orderflow.
Flattens the sequence into summary statistics (trends, aggregates, last-candle)
and feeds into LightGBM multiclass via walk-forward validation.

Input: 20-candle sequence (via candle_features.snapshot_candles).
Output: {direction, probability, confidence}.
"""

import json
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

MIN_SAMPLES = 500
MODELS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "models"

# Raw per-candle features (must match candle_features.snapshot_candles output)
CANDLE_FEATURE_NAMES = [
    "delta",
    "delta_pct",
    "cvd",
    "volume",
    "volume_ratio",
    "spread_ticks",
    "body_ratio",
    "close_position",
    "tick_count",
    "passive_active_ratio",
    "vwap_distance_ticks",
    "poc_distance_ticks",
    "imbalance_ratio_max",
    "stacked_imbalance_count",
    "big_trades_count",
    "big_trades_net_delta",
]
SEQ_LEN = 20

# Target classes
CLASSES = ["reversal_long", "reversal_short", "continuation_long", "continuation_short", "chop"]
N_CLASSES = len(CLASSES)

# Flattened feature names for the GBDT model
# For each candle feature: slope, mean, std, min, max, last, first_half_mean, second_half_mean
_SUMMARY_SUFFIXES = ["_slope", "_mean", "_std", "_min", "_max", "_last", "_first_half", "_second_half"]
FLATTENED_FEATURE_NAMES = []
for cf in CANDLE_FEATURE_NAMES:
    for suffix in _SUMMARY_SUFFIXES:
        FLATTENED_FEATURE_NAMES.append(cf + suffix)
# Cross-feature interactions
FLATTENED_FEATURE_NAMES += [
    "momentum_consistency",  # How many consecutive same-sign deltas at end
    "volume_acceleration",  # Second derivative of volume
    "cvd_delta_divergence",  # CVD trend vs delta trend disagreement
    "spread_expansion",  # Spread trend (widening = volatility)
]
N_FLATTENED = len(FLATTENED_FEATURE_NAMES)


class TemporalPatternModel:
    def train(self, data) -> dict | None:
        X_list, y_list = [], []
        for row in data:
            features = row.features if isinstance(row.features, dict) else json.loads(row.features)
            candles = features.get("candle_sequence")
            if not candles or len(candles) < SEQ_LEN:
                continue
            flat = _flatten_sequence(candles[-SEQ_LEN:])
            if flat is None:
                continue
            label = _get_label(row.outcome, row.outcome_binary)
            if label is None:
                continue
            X_list.append(flat)
            y_list.append(label)

        if len(X_list) < MIN_SAMPLES:
            logger.info(f"M6: insufficient data ({len(X_list)} < {MIN_SAMPLES})")
            return None

        X = np.array(X_list, dtype=np.float32)
        y = np.array(y_list, dtype=np.int64)

        from src.ml.optimizer.trainer import train_model

        result = train_model(
            X,
            y,
            task="multiclass",
            min_samples=MIN_SAMPLES,
            feature_names=FLATTENED_FEATURE_NAMES,
            num_class=N_CLASSES,
        )
        if result is None:
            return None

        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        path = MODELS_DIR / "temporal_pattern_latest.joblib"
        try:
            import joblib

            joblib.dump(
                {
                    "model": result["model"],
                    "feature_names": FLATTENED_FEATURE_NAMES,
                    "task": "multiclass",
                },
                path,
            )
        except ImportError:
            return None

        return {
            "file_path": str(path),
            "training_data_count": len(X_list),
            "validation_score": result.get("validation_score"),
            "feature_importance": result.get("feature_importance"),
            "baseline_metric": 1.0 / N_CLASSES,
        }

    def predict(self, candle_sequence: list[dict]) -> dict | None:
        """Flatten candle sequence for prediction by central Predictor."""
        if not candle_sequence or len(candle_sequence) < SEQ_LEN:
            return None
        flat = _flatten_sequence(candle_sequence[-SEQ_LEN:])
        if flat is None:
            return None
        return {"flattened_features": dict(zip(FLATTENED_FEATURE_NAMES, flat.tolist(), strict=False))}


def _flatten_sequence(candles: list[dict]) -> np.ndarray | None:
    """Flatten a 20-candle sequence into summary statistics for GBDT."""
    seq = _encode_candle_sequence(candles)
    if seq is None:
        return None

    features = []
    half = SEQ_LEN // 2
    x_axis = np.arange(SEQ_LEN, dtype=np.float32)

    for col_idx in range(seq.shape[1]):
        col = seq[:, col_idx]
        # Linear slope via polyfit
        slope = np.polyfit(x_axis, col, 1)[0] if np.std(col) > 1e-8 else 0.0
        features.append(slope)
        features.append(np.mean(col))
        features.append(np.std(col))
        features.append(np.min(col))
        features.append(np.max(col))
        features.append(col[-1])  # last candle value
        features.append(np.mean(col[:half]))  # first half mean
        features.append(np.mean(col[half:]))  # second half mean

    # Cross-feature interactions
    delta_col = seq[:, 0]  # delta
    cvd_col = seq[:, 2]  # cvd
    volume_col = seq[:, 3]  # volume
    spread_col = seq[:, 5]  # spread_ticks

    # Momentum consistency: consecutive same-sign deltas at end of window
    momentum = 0
    if len(delta_col) > 1:
        sign = np.sign(delta_col[-1])
        for i in range(len(delta_col) - 1, -1, -1):
            if np.sign(delta_col[i]) == sign and sign != 0:
                momentum += 1
            else:
                break
    features.append(float(momentum))

    # Volume acceleration (second derivative)
    if len(volume_col) >= 3:
        vol_diff2 = np.diff(volume_col, n=2)
        features.append(float(np.mean(vol_diff2)))
    else:
        features.append(0.0)

    # CVD vs delta divergence (slope disagreement)
    delta_slope = np.polyfit(x_axis, delta_col, 1)[0] if np.std(delta_col) > 1e-8 else 0.0
    cvd_slope = np.polyfit(x_axis, cvd_col, 1)[0] if np.std(cvd_col) > 1e-8 else 0.0
    features.append(float(np.sign(delta_slope) != np.sign(cvd_slope)))

    # Spread expansion (slope of spread)
    spread_slope = np.polyfit(x_axis, spread_col, 1)[0] if np.std(spread_col) > 1e-8 else 0.0
    features.append(float(spread_slope))

    return np.array(features, dtype=np.float32)


def _encode_candle_sequence(candles: list[dict]) -> np.ndarray | None:
    """Encode list of candle dicts to (seq_len, n_features) array."""
    rows = []
    for c in candles:
        row = []
        for name in CANDLE_FEATURE_NAMES:
            val = c.get(name)
            row.append(float(val) if val is not None else 0.0)
        rows.append(row)
    return np.array(rows, dtype=np.float32)


def _get_label(outcome, outcome_binary) -> int | None:
    """Map R-multiple outcome to class label."""
    if outcome is None:
        return None
    if outcome > 0.5:
        return 0  # reversal_long
    elif outcome < -0.5:
        return 1  # reversal_short
    elif outcome > 0:
        return 2  # continuation_long (mild positive)
    elif outcome < 0:
        return 3  # continuation_short (mild negative)
    else:
        return 4  # chop
