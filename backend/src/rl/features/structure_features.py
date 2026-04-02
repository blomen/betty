"""Market structure (Dow Theory) and session context feature extraction."""
from __future__ import annotations

import math
import numpy as np

from ...market_data.levels import VWAPBands, VolumeProfile, SessionLevels, SwingStructure
from ..config import TICK_SIZE

# 20 session/VWAP/VP/IB features  +  40 Dow Theory swing features  +  4 PDH/PDL
_N_FEATURES = 64

# Normalisation constant: distance in ticks clipped to ±1
_DIST_NORM = 200.0


def _extract_swing_features(
    price: float,
    swing: SwingStructure | None,
) -> np.ndarray:
    """Extract 41 Dow Theory swing structure features.

    Per timeframe (daily=0, weekly=1, monthly=2) — 13 features each = 39:
      [0-2]   trend: uptrend=1.0, reversing_up=0.5, ranging=0.0,
                     reversing_down=-0.5, downtrend=-1.0
      [3-5]   dist_to_sh: signed distance to last swing high (clipped ±1)
      [6-8]   dist_to_sl: signed distance to last swing low (clipped ±1)
      [9-11]  above_sh: 1.0 if price > last SH (breakout territory)
      [12-14] below_sl: 1.0 if price < last SL (breakdown territory)
      [15-17] position: price within SH-SL range (0=at SL, 1=at SH)
      [18-20] hh_lh: 1.0 if last SH > prior SH (HH), -1.0 if LH, 0 if <2 SHs
      [21-23] hl_ll: 1.0 if last SL > prior SL (HL), -1.0 if LL, 0 if <2 SLs
      [24-26] swing_range: (SH - SL) normalised, trend amplitude
      [27-29] bos_active: 1.0 if BOS within recency window
      [30-32] choch_active: 1.0 if CHoCH within recency window
      [33-35] last_event_dir: direction of last structural event
                1.0 bullish (bos/choch_bullish), -1.0 bearish, 0 if none
      [36-38] swing_momentum: acceleration of swings
                (SH1-SH0 vs SH2-SH1) or (SL1-SL0 vs SL2-SL1) normalised

    Global (1):
      [39]    trend_alignment: -1.0 (all down) to +1.0 (all up)
    """
    feats = np.zeros(40, dtype=np.float32)
    if swing is None:
        return feats

    trend_map = {
        "uptrend": 1.0, "reversing_up": 0.5, "ranging": 0.0,
        "reversing_down": -0.5, "downtrend": -1.0,
    }

    for i, tf in enumerate([swing.daily, swing.weekly, swing.monthly]):
        # --- Trend (0-2) ---
        feats[i] = trend_map.get(tf.structure, 0.0)

        sh_prices = [s.price for s in tf.swing_highs]
        sl_prices = [s.price for s in tf.swing_lows]

        last_sh = sh_prices[0] if sh_prices else None
        last_sl = sl_prices[0] if sl_prices else None

        # --- Distance to SH (3-5) ---
        if last_sh is not None:
            feats[3 + i] = float(np.clip((price - last_sh) / TICK_SIZE / _DIST_NORM, -1.0, 1.0))

        # --- Distance to SL (6-8) ---
        if last_sl is not None:
            feats[6 + i] = float(np.clip((price - last_sl) / TICK_SIZE / _DIST_NORM, -1.0, 1.0))

        # --- Above SH / Below SL (9-14) ---
        if last_sh is not None:
            feats[9 + i] = 1.0 if price > last_sh else 0.0
        if last_sl is not None:
            feats[12 + i] = 1.0 if price < last_sl else 0.0

        # --- Position within SH-SL range (15-17) ---
        if last_sh is not None and last_sl is not None:
            span = last_sh - last_sl
            if span > 0:
                feats[15 + i] = float(np.clip((price - last_sl) / span, 0.0, 1.0))
            else:
                feats[15 + i] = 0.5

        # --- HH vs LH (18-20) ---
        if len(sh_prices) >= 2:
            feats[18 + i] = 1.0 if sh_prices[0] > sh_prices[1] else -1.0

        # --- HL vs LL (21-23) ---
        if len(sl_prices) >= 2:
            feats[21 + i] = 1.0 if sl_prices[0] > sl_prices[1] else -1.0

        # --- Swing range amplitude (24-26) ---
        if last_sh is not None and last_sl is not None:
            feats[24 + i] = float(np.clip((last_sh - last_sl) / TICK_SIZE / 400.0, 0.0, 1.0))

        # --- BOS / CHoCH active (27-32) ---
        feats[27 + i] = 1.0 if tf.bos_active else 0.0
        feats[30 + i] = 1.0 if tf.choch_active else 0.0

        # --- Last event direction (33-35) ---
        last_event = tf.last_bos or tf.last_choch
        if tf.last_bos and tf.last_choch:
            # Pick the more recent one by timestamp
            last_event = tf.last_bos if tf.last_bos.timestamp >= tf.last_choch.timestamp else tf.last_choch
        if last_event is not None:
            feats[33 + i] = 1.0 if "bullish" in last_event.event_type else -1.0

        # --- Swing momentum (36-38) ---
        # Compare consecutive swing-to-swing deltas; >0 = accelerating, <0 = decelerating
        if len(sh_prices) >= 3:
            d1 = sh_prices[0] - sh_prices[1]  # most recent delta
            d2 = sh_prices[1] - sh_prices[2]  # prior delta
            if abs(d2) > 0:
                feats[36 + i] = float(np.clip(d1 / max(abs(d2), 1.0), -1.0, 1.0))
        elif len(sl_prices) >= 3:
            d1 = sl_prices[0] - sl_prices[1]
            d2 = sl_prices[1] - sl_prices[2]
            if abs(d2) > 0:
                feats[36 + i] = float(np.clip(d1 / max(abs(d2), 1.0), -1.0, 1.0))

    # --- Trend alignment (39) ---
    feats[39] = swing.trend_alignment

    return feats


def extract_structure_features(
    price: float,
    vwap_bands: VWAPBands | None,
    volume_profile: VolumeProfile | None,
    session_levels: SessionLevels | None,
    session_context: dict | None,
    swing_structure: SwingStructure | None = None,
) -> np.ndarray:
    """Extract 64 market structure and session context features.

    Feature layout (indices 0-63):
    --- VWAP (0) ---
      0  price_vs_vwap_sd
    --- Volume Profile (1-5) ---
      1-5  price_in_va, dist_to_poc/vah/val, va_width
    --- IB Range (6-8) ---
      6-8  ib_range, poor_high, poor_low
    --- Session Context (9-19) ---
      9-19  timing, session type, IB break
    --- Dow Theory Swings (20-59) ---
      20-22  trend_d/w/m
      23-25  dist_to_sh_d/w/m
      26-28  dist_to_sl_d/w/m
      29-31  above_sh_d/w/m
      32-34  below_sl_d/w/m
      35-37  position_d/w/m
      38-40  hh_lh_d/w/m
      41-43  hl_ll_d/w/m
      44-46  swing_range_d/w/m
      47-49  bos_active_d/w/m
      50-52  choch_active_d/w/m
      53-55  last_event_dir_d/w/m
      56-58  swing_momentum_d/w/m
      59     trend_alignment
    --- PDH/PDL (60-63) ---
      60  dist_to_pdh (signed, clipped +/-1)
      61  dist_to_pdl (signed, clipped +/-1)
      62  position within PDH-PDL range (0=PDL, 1=PDH)
      63  PDH-PDL range width (normalised)
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
        feats[2] = float(np.clip(abs(price - poc) / TICK_SIZE / _DIST_NORM, 0.0, 1.0))
        feats[3] = float(np.clip((price - vah) / TICK_SIZE / _DIST_NORM, -1.0, 1.0))
        feats[4] = float(np.clip((price - val) / TICK_SIZE / _DIST_NORM, -1.0, 1.0))
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

    # --- Session Context (feats 9-19) ---
    ctx = session_context or {}
    daily_range_pct = float(ctx.get("daily_range_pct", 0.5))
    minutes_since_rth = float(ctx.get("minutes_since_rth", 0))
    feats[9] = min(minutes_since_rth / 390.0, 1.0)
    session_volume_pct = float(ctx.get("session_volume_pct", 0.5))
    feats[10] = min(max(session_volume_pct, 0.0), 1.0)
    feats[11] = float(np.clip(daily_range_pct / 0.03, 0.0, 1.0))
    minute_of_day = float(ctx.get("minute_of_day", 0))
    angle = 2.0 * math.pi * minute_of_day / 1440.0
    feats[12] = math.sin(angle)
    feats[13] = math.cos(angle)
    session_type = ctx.get("session_type", "rth")
    feats[14] = 1.0 if session_type == "rth" else 0.0
    feats[15] = 1.0 if session_type == "globex" else 0.0
    feats[16] = 1.0 if session_type == "london" else 0.0
    ib_broken = ctx.get("ib_broken", "none")
    feats[17] = 1.0 if ib_broken == "up" else 0.0
    feats[18] = 1.0 if ib_broken == "down" else 0.0
    feats[19] = 1.0 if ib_broken == "none" else 0.0

    # --- Dow Theory Swing Structure (feats 20-59) ---
    feats[20:60] = _extract_swing_features(price, swing_structure)

    # --- PDH/PDL (feats 60-63) ---
    pdh: float | None = None
    pdl: float | None = None
    if session_levels is not None:
        pdh = session_levels.pdh
        pdl = session_levels.pdl
    if pdh is not None:
        feats[60] = float(np.clip((price - pdh) / TICK_SIZE / _DIST_NORM, -1.0, 1.0))
    if pdl is not None:
        feats[61] = float(np.clip((price - pdl) / TICK_SIZE / _DIST_NORM, -1.0, 1.0))
    if pdh is not None and pdl is not None:
        span = pdh - pdl
        feats[62] = float(np.clip((price - pdl) / span, 0.0, 1.0)) if span > 0 else 0.5
        feats[63] = float(np.clip(span / TICK_SIZE / 400.0, 0.0, 1.0))

    return feats
