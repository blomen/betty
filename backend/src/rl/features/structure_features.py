"""Market structure and session context feature extraction."""
from __future__ import annotations

import math
import numpy as np

from ...market_data.levels import VWAPBands, VolumeProfile, SessionLevels
from ..config import TICK_SIZE

_N_FEATURES = 23


def extract_structure_features(
    price: float,
    vwap_bands: VWAPBands | None,
    volume_profile: VolumeProfile | None,
    session_levels: SessionLevels | None,
    session_context: dict | None,
) -> np.ndarray:
    """Extract ~23 market structure and session context features.

    Feature layout (indices 0-22):
    --- VWAP (0) ---
      0  price_vs_vwap_sd    — (price - vwap) / sd, clipped ±3

    --- Volume Profile (1-5) ---
      1  price_in_va         — 1 if price inside value area
      2  dist_to_poc_ticks   — |price - poc| / tick_size, normalised (÷50)
      3  dist_to_vah_ticks   — (price - vah) / tick_size, normalised (÷50)
      4  dist_to_val_ticks   — (price - val) / tick_size, normalised (÷50)
      5  single_print_count  — count of single prints (capped ÷20)

    --- IB Range (6-7) ---
      6  ib_range_ticks      — (ib_high - ib_low) / tick_size, normalised (÷80)
      7  poor_high           — 1 if price above ib_high (IB extension up)
      8  poor_low            — 1 if price below ib_low (IB extension down)

    --- Market Type one-hot (9-11) ---
      9  trend_day           — 1 if daily_range > 2× avg
     10  range_day           — 1 if range small + price inside VA
     11  neutral_day         — else

    --- Session Context (12-22) ---
     12  minutes_since_rth_norm — minutes since 09:30 ET / 390
     13  session_volume_pct     — session volume as pct of daily expected (0-1)
     14  daily_range_pct        — (daily_high - daily_low) / reference_range (0-1)
     15  time_of_day_sin        — sin(2π * minute_of_day / 1440)
     16  time_of_day_cos        — cos(2π * minute_of_day / 1440)
     17  session_type_rth       — one-hot RTH
     18  session_type_globex    — one-hot Globex/overnight
     19  session_type_london    — one-hot London
     20  ib_broken_up           — 1 if IB high was broken
     21  ib_broken_down         — 1 if IB low was broken
     22  ib_broken_none         — 1 if IB intact

    Returns zeros(23) on fully missing inputs.
    """
    feats = np.zeros(_N_FEATURES, dtype=np.float32)

    # --- VWAP (feat 0) ---
    if vwap_bands is not None:
        vwap = vwap_bands.vwap
        sd = max(vwap_bands.sd1_upper - vwap, 1e-6)
        feats[0] = float(np.clip((price - vwap) / sd, -3.0, 3.0))

    # --- Volume Profile (feats 1-5) ---
    if volume_profile is not None:
        poc = volume_profile.poc
        vah = volume_profile.vah
        val = volume_profile.val

        feats[1] = 1.0 if val <= price <= vah else 0.0
        feats[2] = float(np.clip(abs(price - poc) / TICK_SIZE / 50.0, 0.0, 1.0))
        feats[3] = float(np.clip((price - vah) / TICK_SIZE / 50.0, -1.0, 1.0))
        feats[4] = float(np.clip((price - val) / TICK_SIZE / 50.0, -1.0, 1.0))
        sp_count = len(volume_profile.single_prints) if volume_profile.single_prints else 0
        feats[5] = min(float(sp_count) / 20.0, 1.0)

    # --- IB Range (feats 6-8) ---
    ib_high: float | None = None
    ib_low: float | None = None
    if session_levels is not None:
        ib_high = session_levels.ib_high
        ib_low = session_levels.ib_low

    if ib_high is not None and ib_low is not None:
        ib_range = ib_high - ib_low
        feats[6] = min(ib_range / TICK_SIZE / 80.0, 1.0)
        feats[7] = 1.0 if price > ib_high else 0.0
        feats[8] = 1.0 if price < ib_low else 0.0

    # --- Market Type one-hot (feats 9-11) ---
    ctx = session_context or {}
    daily_range_pct = float(ctx.get("daily_range_pct", 0.5))
    price_in_va_bool = feats[1] > 0.5  # from above

    if daily_range_pct > 0.75:
        feats[9] = 1.0   # trend day
    elif daily_range_pct < 0.4 and price_in_va_bool:
        feats[10] = 1.0  # range day
    else:
        feats[11] = 1.0  # neutral

    # --- Session Context (feats 12-22) ---
    minutes_since_rth = float(ctx.get("minutes_since_rth", 0))
    feats[12] = min(minutes_since_rth / 390.0, 1.0)

    session_volume_pct = float(ctx.get("session_volume_pct", 0.5))
    feats[13] = min(max(session_volume_pct, 0.0), 1.0)

    feats[14] = min(max(daily_range_pct, 0.0), 1.0)

    minute_of_day = float(ctx.get("minute_of_day", 0))
    angle = 2.0 * math.pi * minute_of_day / 1440.0
    feats[15] = math.sin(angle)
    feats[16] = math.cos(angle)

    session_type = ctx.get("session_type", "rth")
    feats[17] = 1.0 if session_type == "rth" else 0.0
    feats[18] = 1.0 if session_type == "globex" else 0.0
    feats[19] = 1.0 if session_type == "london" else 0.0

    ib_broken = ctx.get("ib_broken", "none")
    feats[20] = 1.0 if ib_broken == "up" else 0.0
    feats[21] = 1.0 if ib_broken == "down" else 0.0
    feats[22] = 1.0 if ib_broken == "none" else 0.0

    return feats
