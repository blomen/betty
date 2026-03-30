"""Market structure and session context feature extraction."""
from __future__ import annotations

import math
import numpy as np

from ...market_data.levels import VWAPBands, VolumeProfile, SessionLevels, SwingStructure
from ..config import TICK_SIZE

_N_FEATURES = 32


def _extract_swing_features(
    price: float,
    swing: SwingStructure | None,
) -> np.ndarray:
    """Extract 9 swing structure features (indices 23-31)."""
    feats = np.zeros(9, dtype=np.float32)
    if swing is None:
        return feats

    trend_map = {"uptrend": 1.0, "downtrend": -1.0, "ranging": 0.0}

    for i, tf_swings in enumerate([swing.daily, swing.weekly, swing.monthly]):
        # Trend direction (feats 0-2 → indices 23-25)
        feats[i] = trend_map.get(tf_swings.structure, 0.0)

        # Distance to nearest swing level (feats 3-5 → indices 26-28)
        all_prices = [s.price for s in tf_swings.swing_highs + tf_swings.swing_lows]
        if all_prices:
            nearest = min(all_prices, key=lambda p: abs(p - price))
            dist_ticks = (price - nearest) / TICK_SIZE
            feats[3 + i] = float(np.clip(dist_ticks / 200.0, -1.0, 1.0))

        # Position in swing range (feats 6-8 → indices 29-31)
        if all_prices:
            range_high = max(all_prices)
            range_low = min(all_prices)
            span = range_high - range_low
            if span > 0:
                feats[6 + i] = float(np.clip((price - range_low) / span, 0.0, 1.0))
            else:
                feats[6 + i] = 0.5

    return feats


def extract_structure_features(
    price: float,
    vwap_bands: VWAPBands | None,
    volume_profile: VolumeProfile | None,
    session_levels: SessionLevels | None,
    session_context: dict | None,
    swing_structure: SwingStructure | None = None,
) -> np.ndarray:
    """Extract 32 market structure and session context features.

    Feature layout (indices 0-31):
    --- VWAP (0) ---
      0  price_vs_vwap_sd
    --- Volume Profile (1-5) ---
      1-5  price_in_va, dist_to_poc/vah/val, va_width
    --- IB Range (6-8) ---
      6-8  ib_range, poor_high, poor_low
    --- Market Type one-hot (9-11) ---
      9-11  trend_day, range_day, neutral_day
    --- Session Context (12-22) ---
      12-22  timing, session type, IB break
    --- Swing Structure (23-31) ---
      23-25  swing_trend_d/w/m
      26-28  swing_dist_d/w/m
      29-31  swing_pos_d/w/m
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
        feats[2] = float(np.clip(abs(price - poc) / TICK_SIZE / 200.0, 0.0, 1.0))
        feats[3] = float(np.clip((price - vah) / TICK_SIZE / 200.0, -1.0, 1.0))
        feats[4] = float(np.clip((price - val) / TICK_SIZE / 200.0, -1.0, 1.0))
        va_width = max(vah - val, 0.0)
        feats[5] = float(np.clip(va_width / TICK_SIZE / 400.0, 0.0, 1.0))

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
    price_in_va_bool = feats[1] > 0.5
    if daily_range_pct > 0.02:
        feats[9] = 1.0
    elif daily_range_pct < 0.008 and price_in_va_bool:
        feats[10] = 1.0
    else:
        feats[11] = 1.0

    # --- Session Context (feats 12-22) ---
    minutes_since_rth = float(ctx.get("minutes_since_rth", 0))
    feats[12] = min(minutes_since_rth / 390.0, 1.0)
    session_volume_pct = float(ctx.get("session_volume_pct", 0.5))
    feats[13] = min(max(session_volume_pct, 0.0), 1.0)
    feats[14] = float(np.clip(daily_range_pct / 0.03, 0.0, 1.0))
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

    # --- Swing Structure (feats 23-31) ---
    feats[23:32] = _extract_swing_features(price, swing_structure)

    return feats
