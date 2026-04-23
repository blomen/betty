"""CME exchange statistics feature extraction (OI, settlement, volume)."""

from __future__ import annotations

import numpy as np

_N_FEATURES = 5

# NQ tick size
_TICK_SIZE = 0.25


def extract_exchange_stats_features(macro: dict | None, price: float = 0.0) -> np.ndarray:
    """Extract 5 exchange-statistics features from the macro/state dict.

    Feature layout (indices 0-4):
      0  oi_norm          — open_interest / 1M, clipped 0-1
      1  oi_change_norm   — daily OI change / 50k, clipped ±1
      2  settlement_dist  — (price - settlement) / (tick × 200), clipped ±1
      3  cleared_vol_norm — cleared_volume / 500k, clipped 0-1
      4  block_vol_ratio  — block_volume / cleared_volume, clipped 0-1

    Returns zeros(5) if macro is None or keys are missing.
    """
    if not macro:
        return np.zeros(_N_FEATURES, dtype=np.float32)

    oi = float(macro.get("oi", 0))
    oi_change = float(macro.get("oi_change", 0))
    settlement = float(macro.get("settlement_price", 0))
    cleared_vol = float(macro.get("cleared_volume", 0))
    block_vol = float(macro.get("block_volume", 0))

    # Settlement distance in ticks, normalised
    settlement_dist = (price - settlement) / (_TICK_SIZE * 200) if settlement > 0 and price > 0 else 0.0

    # Block volume ratio
    block_ratio = block_vol / max(cleared_vol, 1.0) if cleared_vol > 0 else 0.0

    return np.array(
        [
            np.clip(oi / 1_000_000, 0.0, 1.0),
            np.clip(oi_change / 50_000, -1.0, 1.0),
            np.clip(settlement_dist, -1.0, 1.0),
            np.clip(cleared_vol / 500_000, 0.0, 1.0),
            np.clip(block_ratio, 0.0, 1.0),
        ],
        dtype=np.float32,
    )
