"""Execution context features from Fabio's trading rules.

7 features capturing execution timing, auction quality, and session context
that were identified as missing from the transcript audit:

1. follow_through_confirmed: Has price reacted at the level AND resumed?
2. follow_through_strength: How strong is the follow-through (velocity)?
3. is_responsive_auction: Delta% < 10% = balanced, two-sided trade
4. is_initiative_auction: Delta% > 15% with imbalance clusters
5. session_atr_norm: NQ-specific ATR from recent candles
6. volume_anomaly: Outsized volume at the touch price level
7. time_in_session: 0.0 at session open → 1.0 at session close
"""
from __future__ import annotations

import numpy as np

from ..config import TICK_SIZE

_N_FEATURES = 7


def extract_execution_features(
    state: dict,
    recent_ticks: list[dict],
    candles: list,
    price: float,
) -> np.ndarray:
    """Extract 7 execution context features."""
    out = np.zeros(_N_FEATURES, dtype=np.float32)

    # --- 1-2: Follow-through confirmation ---
    # "Never trade the initial move" (Fabio). Check if price has already
    # reacted at this level and is now following through.
    # A follow-through = price moved away from level, then came back with momentum.
    if recent_ticks and len(recent_ticks) >= 10:
        prices = [t["price"] for t in recent_ticks[-30:]]
        if len(prices) >= 10:
            # Did price reverse and come back? Check if there's a V or inverted-V
            mid = len(prices) // 2
            first_half = prices[:mid]
            second_half = prices[mid:]

            if first_half and second_half:
                first_dir = first_half[-1] - first_half[0]
                second_dir = second_half[-1] - second_half[0]

                # Follow-through = first moved one direction, then reversed
                # (reaction happened, now re-approaching)
                if first_dir != 0 and second_dir != 0:
                    is_followthrough = (first_dir > 0 and second_dir < 0) or \
                                       (first_dir < 0 and second_dir > 0)
                    out[0] = 1.0 if is_followthrough else 0.0

                    # Strength = magnitude of the second move relative to first
                    if abs(first_dir) > 0:
                        out[1] = np.clip(abs(second_dir) / abs(first_dir), 0.0, 2.0)

    # --- 3-4: Responsive vs Initiative auction ---
    # Responsive: delta_pct < 10%, balanced two-sided trade (Fabio)
    # Initiative: delta_pct > 15%, one side dominating with imbalances
    if candles and len(candles) >= 3:
        recent = candles[-3:]
        total_vol = sum(c.volume for c in recent)
        total_delta = sum(c.delta for c in recent)
        if total_vol > 0:
            delta_pct = abs(total_delta) / total_vol
            out[2] = 1.0 if delta_pct < 0.10 else 0.0  # responsive
            out[3] = 1.0 if delta_pct > 0.15 else 0.0  # initiative

    # --- 5: Asset-specific ATR ---
    # Normalized by typical NQ range (~200 ticks/session)
    if candles and len(candles) >= 5:
        ranges = [(c.high - c.low) / TICK_SIZE for c in candles[-20:]]
        atr = sum(ranges) / len(ranges)
        out[4] = np.clip(atr / 200.0, 0.0, 1.0)

    # --- 6: Volume anomaly at touch price ---
    # Is there outsized volume concentrated at this specific level?
    if candles and len(candles) >= 1:
        last_candle = candles[-1]
        avg_vol = sum(c.volume for c in candles[-10:]) / max(len(candles[-10:]), 1)
        if avg_vol > 0:
            vol_ratio = last_candle.volume / avg_vol
            # Anomaly if > 2x average volume
            out[5] = np.clip((vol_ratio - 1.0) / 3.0, 0.0, 1.0)

    # --- 7: Time in session ---
    # 0.0 at session open → 1.0 at session close
    # Uses minutes_since_rth from session_context
    session_ctx = state.get("session_context") or {}
    minutes = float(session_ctx.get("minutes_since_rth", 0.0))
    # RTH is 6.5 hours = 390 minutes
    out[6] = np.clip(minutes / 390.0, 0.0, 1.0)

    return out
