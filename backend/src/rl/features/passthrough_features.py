"""Passthrough feature selector — picks top 10 raw features from the 276-dim observation.

Based on GBT feature importance analysis.  These are the structural features that
most strongly predict level-touch outcomes, selected for the fast "trigger" layer of
the hierarchical observation.

Observation layout (276-dim, zone mode):
   0:31   — zone composition multi-hot (31)
  31:52   — orderflow (21)
  52:116  — structure (64)
 116:154  — TPO (38)
 154:169  — candles (15)
 169:173  — zone features (4)
 173:178  — zone confluence (5)
 178:189  — macro (11)
 189:194  — exchange stats (5)
 194:208  — setup (14)
 208:228  — AMT (20)
 228:248  — AMT dynamics (20)
 248:268  — micro (20)
 268:269  — approach direction (1)
 269:276  — execution context (7)

Top 10 features (by GBT importance):
  Index  52 — struct_0:       price_vs_vwap in SD units
  Index  54 — struct_2:       VWAP position (dist to POC, normalised)
  Index  55 — struct_3:       dist to daily swing high
  Index  56 — struct_4:       dist to daily swing low
  Index  57 — struct_5:       IB distance
  Index 140 — tpo_ny_price_vs_poc:  NY session price vs POC
  Index 141 — tpo_ny_price_vs_vah:  NY session price vs VAH
  Index 142 — tpo_ny_price_vs_val:  NY session price vs VAL
  Index 232 — amtdyn_4:       developing_day_type
  Index 240 — amtdyn_12:      poc_migration_speed
"""
from __future__ import annotations

import numpy as np

PASSTHROUGH_DIM: int = 10

PASSTHROUGH_NAMES: list[str] = [
    "struct_0_price_vs_vwap_sd",
    "struct_2_vwap_position",
    "struct_3_dist_to_swing_high",
    "struct_4_dist_to_swing_low",
    "struct_5_ib_distance",
    "tpo_ny_price_vs_poc",
    "tpo_ny_price_vs_vah",
    "tpo_ny_price_vs_val",
    "amtdyn_4_developing_day_type",
    "amtdyn_12_poc_migration_speed",
]

assert len(PASSTHROUGH_NAMES) == PASSTHROUGH_DIM, "PASSTHROUGH_NAMES length mismatch"

# Raw indices into the 276-dim base observation vector
_PASSTHROUGH_INDICES: tuple[int, ...] = (
    52,   # struct_0: price_vs_vwap in SD units
    54,   # struct_2: VWAP position
    55,   # struct_3: dist to swing high
    56,   # struct_4: dist to swing low
    57,   # struct_5: IB distance
    140,  # tpo NY price_vs_poc
    141,  # tpo NY price_vs_vah
    142,  # tpo NY price_vs_val
    232,  # amtdyn_4: developing_day_type
    240,  # amtdyn_12: poc_migration_speed
)

assert len(_PASSTHROUGH_INDICES) == PASSTHROUGH_DIM, "_PASSTHROUGH_INDICES length mismatch"

# Pre-compute as numpy index array for fast vectorised selection
_IDX_ARRAY: np.ndarray = np.array(_PASSTHROUGH_INDICES, dtype=np.intp)


def extract_passthrough(base_observation: np.ndarray) -> np.ndarray:
    """Select 10 high-importance features from the full 276-dim observation vector.

    Args:
        base_observation: np.ndarray of shape ``(276,)`` (or broader, if layout
                          expands), dtype float32.  Values should already be
                          normalised by the base observation builder.

    Returns:
        np.ndarray of shape ``(10,)`` with dtype ``float32``.
    """
    return base_observation[_IDX_ARRAY].astype(np.float32, copy=False)
