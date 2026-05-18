"""Passthrough feature selector — picks top 14 raw features from the 276-dim observation.

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

Top 14 features (by GBT importance + R-impact audit):
  Index  52 — struct_0:       price_vs_vwap in SD units
  Index  54 — struct_2:       VWAP position (dist to POC, normalised)
  Index  55 — struct_3:       dist to daily swing high
  Index  56 — struct_4:       dist to daily swing low
  Index  57 — struct_5:       IB distance
  Index 126 — tpo_tokyo_opening_direction:  +0.258R audit (highest in model)
  Index 132 — tpo_london_ib_range:          +0.196R audit
  Index 140 — tpo_ny_price_vs_poc:  NY session price vs POC
  Index 141 — tpo_ny_price_vs_vah:  NY session price vs VAH
  Index 142 — tpo_ny_price_vs_val:  NY session price vs VAL
  Index 144 — tpo_ny_ib_range:              +0.162R audit
  Index 145 — tpo_ny_price_vs_ib_mid:       +0.144R audit
  Index 232 — amtdyn_4:       developing_day_type
  Index 240 — amtdyn_12:      poc_migration_speed
"""

from __future__ import annotations

import numpy as np

PASSTHROUGH_DIM: int = 14

PASSTHROUGH_NAMES: list[str] = [
    "struct_0_price_vs_vwap_sd",
    "struct_2_vwap_position",
    "struct_3_dist_to_swing_high",
    "struct_4_dist_to_swing_low",
    "struct_5_ib_distance",
    "tpo_tokyo_opening_direction",
    "tpo_london_ib_range",
    "tpo_ny_price_vs_poc",
    "tpo_ny_price_vs_vah",
    "tpo_ny_price_vs_val",
    "tpo_ny_ib_range",
    "tpo_ny_price_vs_ib_mid",
    "amtdyn_4_developing_day_type",
    "amtdyn_12_poc_migration_speed",
]

assert len(PASSTHROUGH_NAMES) == PASSTHROUGH_DIM, "PASSTHROUGH_NAMES length mismatch"

# Raw indices into the 276-dim base observation vector.
# TPO segment starts at 116 and runs 12 dims per session (tokyo, london, ny)
# in `_tpo_labels` order: price_vs_poc, price_vs_vah, price_vs_val, shape,
# ib_range, price_vs_ib_mid, poor_signal, price_position_in_va,
# rotation_factor, opening_type, opening_direction, excess_signal.
_PASSTHROUGH_INDICES: tuple[int, ...] = (
    52,  # struct_0: price_vs_vwap in SD units
    54,  # struct_2: VWAP position
    55,  # struct_3: dist to swing high
    56,  # struct_4: dist to swing low
    57,  # struct_5: IB distance
    126,  # tpo_tokyo_opening_direction (tokyo base 116 + slot 10)
    132,  # tpo_london_ib_range (london base 128 + slot 4)
    140,  # tpo_ny_price_vs_poc (ny base 140 + slot 0)
    141,  # tpo_ny_price_vs_vah
    142,  # tpo_ny_price_vs_val
    144,  # tpo_ny_ib_range (ny base 140 + slot 4)
    145,  # tpo_ny_price_vs_ib_mid (ny base 140 + slot 5)
    232,  # amtdyn_4: developing_day_type
    240,  # amtdyn_12: poc_migration_speed
)

assert len(_PASSTHROUGH_INDICES) == PASSTHROUGH_DIM, "_PASSTHROUGH_INDICES length mismatch"

# Pre-compute as numpy index array for fast vectorised selection
_IDX_ARRAY: np.ndarray = np.array(_PASSTHROUGH_INDICES, dtype=np.intp)


def extract_passthrough(base_observation: np.ndarray) -> np.ndarray:
    """Select 14 high-importance features from the full 276-dim observation vector.

    Args:
        base_observation: np.ndarray of shape ``(276,)`` (or broader, if layout
                          expands), dtype float32.  Values should already be
                          normalised by the base observation builder.

    Returns:
        np.ndarray of shape ``(14,)`` with dtype ``float32``.
    """
    return base_observation[_IDX_ARRAY].astype(np.float32, copy=False)
