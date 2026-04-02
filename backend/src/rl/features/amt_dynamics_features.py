"""20-dim AMT dynamics feature extractor for RL observation vector.

Converts an AMTDynamicsTracker snapshot dict into a normalized float32 array
suitable for concatenation into the observation vector.
"""
from __future__ import annotations

import numpy as np

_N_FEATURES = 20

# Ordered keys matching the snapshot dict from AMTDynamicsTracker
_KEYS = (
    "ib_ext_up_count",
    "ib_ext_down_count",
    "ib_max_extension",
    "ib_ext_net_direction",
    "developing_day_type",
    "day_type_confidence",
    "responsive_ratio",
    "initiative_ratio",
    "va_acceptance_high",
    "va_rejection_high",
    "va_acceptance_low",
    "va_rejection_low",
    "poc_migration_speed",
    "va_width_expansion_rate",
    "balance_duration",
    "balance_width",
    "single_print_proximity",
    "excess_high",
    "excess_low",
    "otf_activity",
)

# Per-index: (divisor, clip_lo, clip_hi)
_NORM: tuple[tuple[float, float, float], ...] = (
    (5.0,   0.0, 1.0),   # 0  ib_ext_up_count
    (5.0,   0.0, 1.0),   # 1  ib_ext_down_count
    (300.0, 0.0, 1.0),   # 2  ib_max_extension
    (1.0,  -1.0, 1.0),   # 3  ib_ext_net_direction
    (1.0,   0.0, 1.0),   # 4  developing_day_type
    (1.0,   0.0, 1.0),   # 5  day_type_confidence
    (1.0,   0.0, 1.0),   # 6  responsive_ratio
    (1.0,   0.0, 1.0),   # 7  initiative_ratio
    (6.0,   0.0, 1.0),   # 8  va_acceptance_high
    (1.0,   0.0, 1.0),   # 9  va_rejection_high
    (6.0,   0.0, 1.0),   # 10 va_acceptance_low
    (1.0,   0.0, 1.0),   # 11 va_rejection_low
    (1.0,   0.0, 1.0),   # 12 poc_migration_speed
    (1.0,  -1.0, 1.0),   # 13 va_width_expansion_rate
    (12.0,  0.0, 1.0),   # 14 balance_duration
    (200.0, 0.0, 1.0),   # 15 balance_width
    (200.0,-1.0, 1.0),   # 16 single_print_proximity
    (10.0,  0.0, 1.0),   # 17 excess_high
    (10.0,  0.0, 1.0),   # 18 excess_low
    (1.0,   0.0, 1.0),   # 19 otf_activity
)


def extract_amt_dynamics_features(snapshot: dict | None) -> np.ndarray:
    """Convert an AMTDynamicsTracker snapshot to a 20-dim float32 vector.

    Args:
        snapshot: Dict with 20 keys from ``AMTDynamicsTracker.snapshot()``,
                  or ``None`` if the tracker is unavailable.

    Returns:
        numpy array of shape ``(20,)`` with dtype ``float32``, all values finite.
    """
    if snapshot is None:
        return np.zeros(_N_FEATURES, dtype=np.float32)

    out = np.zeros(_N_FEATURES, dtype=np.float32)

    for i, key in enumerate(_KEYS):
        divisor, lo, hi = _NORM[i]
        out[i] = snapshot.get(key, 0.0) / divisor

    # Vectorised clip with per-element bounds
    lo_arr = np.array([n[1] for n in _NORM], dtype=np.float32)
    hi_arr = np.array([n[2] for n in _NORM], dtype=np.float32)
    np.clip(out, lo_arr, hi_arr, out=out)

    # Ensure all values are finite
    out = np.where(np.isfinite(out), out, np.float32(0.0))

    return out
