"""TPO (Time Price Opportunity) profile feature extraction."""
from __future__ import annotations

import numpy as np

from ..config import TICK_SIZE

_N_FEATURES = 13


def extract_tpo_features(
    tpo_profile: dict | None,
    current_price: float,
    bars_30m: list[dict] | None = None,
) -> np.ndarray:
    """Extract 13 TPO features from a TPO profile dict.

    Expected tpo_profile keys (all optional with sensible defaults):
        poc: float            — Point of Control price
        vah: float            — Value Area High
        val: float            — Value Area Low
        va_width: float       — Value Area width in points (vah - val)
        time_at_price: float  — Time letters at current price (0-26 TPO periods)
        excess_high: bool     — Upper tail (single prints at session high)
        excess_low: bool      — Lower tail (single prints at session low)
        rotation_factor: float — Rotation factor (-26 to +26)
        rotation_count: int   — Number of rotations completed
        shape: str            — "p" | "b" | "d" | "balanced"

    Feature layout (indices 0-12):
      0  price_vs_tpo_poc_ticks — (price - poc) / tick_size, normalised ±50
      1  va_width_norm          — va_width / 100 (bounded 0-1)
      2  price_in_va            — 1 if val <= price <= vah else 0
      3  time_at_price_norm     — time_at_price / 26
      4  excess_high            — 0/1
      5  excess_low             — 0/1
      6  rotation_factor_norm   — rotation_factor / 26
      7  rotation_count_norm    — rotation_count / 20 (capped)
      8  shape_p                — one-hot: P-shape (bullish, long tail below VA)
      9  shape_b                — one-hot: b-shape (bearish, long tail above VA)
     10  shape_d                — one-hot: d-shape (distribution, fat middle)
     11  shape_balanced         — one-hot: balanced / neutral

    Returns zeros(13) if tpo_profile is None.
    """
    if tpo_profile is None:
        return np.zeros(_N_FEATURES, dtype=np.float32)

    poc = tpo_profile.get("poc", current_price)
    vah = tpo_profile.get("vah", current_price)
    val = tpo_profile.get("val", current_price)
    va_width = tpo_profile.get("va_width") or max(vah - val, 0.0)
    time_at_price = tpo_profile.get("time_at_price") or tpo_profile.get("ib_tpo_count", 0.0)
    excess_high = float(bool(tpo_profile.get("excess_high", False)))
    excess_low = float(bool(tpo_profile.get("excess_low", False)))
    rotation_factor = tpo_profile.get("rotation_factor", 0.0)
    rotation_count = tpo_profile.get("rotation_count", 0)
    raw_shape = tpo_profile.get("shape", "balanced").lower().replace("-shape", "")
    shape = raw_shape if raw_shape in ("p", "b", "d", "balanced") else "balanced"

    # 0: price distance from POC in ticks (normalised to ±1 at 200 ticks)
    poc_dist_ticks = (current_price - poc) / TICK_SIZE
    poc_dist_norm = np.clip(poc_dist_ticks / 200.0, -1.0, 1.0)

    # 1: value area width normalised (NQ VA can be 200+ pts = 800+ ticks)
    va_width_norm = float(np.clip(va_width / TICK_SIZE / 400.0, 0.0, 1.0))

    # 2: price inside value area
    price_in_va = 1.0 if val <= current_price <= vah else 0.0

    # 3: TPO count normalised (ib_tpo_count can reach 500+)
    time_norm = float(np.clip(float(time_at_price) / 500.0, 0.0, 1.0))

    # 6: rotation factor normalised
    rf_norm = np.clip(float(rotation_factor) / 26.0, -1.0, 1.0)

    # 7: rotation count normalised
    rc_norm = min(float(rotation_count) / 20.0, 1.0)

    # 8-11: shape one-hot
    shape_map = {"p": (1.0, 0.0, 0.0, 0.0),
                 "b": (0.0, 1.0, 0.0, 0.0),
                 "d": (0.0, 0.0, 1.0, 0.0),
                 "balanced": (0.0, 0.0, 0.0, 1.0)}
    shape_vec = shape_map.get(shape, (0.0, 0.0, 0.0, 1.0))

    feats = np.array([
        poc_dist_norm,
        va_width_norm,
        price_in_va,
        time_norm,
        excess_high,
        excess_low,
        rf_norm,
        rc_norm,
        shape_vec[0],
        shape_vec[1],
        shape_vec[2],
        shape_vec[3],
        # 12: poor high/low signal (directional: +1 poor high, -1 poor low, 0 neither)
        float(bool(tpo_profile.get("poor_high", False))) - float(bool(tpo_profile.get("poor_low", False))),
    ], dtype=np.float32)

    return feats
