"""Narrative feature extractor — 18 slow-moving signals for the hierarchical RL observation.

These signals summarise market regime, session context, and structural position.
All outputs are bounded in [-1, 1] (float32).

Feature layout:
  Market Regime (0-2):
    0  regime_score        — macro regime score mapped to [-1, 1]
    1  htf_trend           — multi-timeframe trend alignment (from SwingStructure)
    2  volatility_regime   — normalised VIX to [-1, 1] (0=low vol, 1=high vol)

  Session Context (3-9):
    3  day_type            — Dalton developing day type (-1=balanced .. +1=trend)
    4  opening_type        — OD/OTD/ORR/OA ordinal
    5  ib_type             — IB range percentile mapped to [-1, 1]
    6  value_migration     — current POC vs prior VA (-1/0/+1)
    7  session_phase       — 0=pre-market, 0.5=IB, 1=post-IB (mapped to [-1,+1])
    8  initiative_direction — initiative vs responsive ratio (-1=responsive, +1=initiative)
    9  balance_width       — developing balance width normalised to [-1, 1]

  Structural Position (10-14):
   10  price_vs_value      — price relative to current VA (negative=below, positive=above)
   11  price_vs_poc        — signed distance to POC in SD units, clipped [-1, 1]
   12  price_vs_ib         — price position relative to IB midpoint, normalised
   13  trend_alignment     — daily/weekly/monthly alignment (-1=all down, +1=all up)
   14  excess_nearby       — proximity to single-print/excess zone (-1=no excess, +1=at excess)
"""

from __future__ import annotations

import numpy as np

from ...market_data.levels import SessionLevels, SwingStructure, VolumeProfile, VWAPBands
from ..config import TICK_SIZE

NARRATIVE_DIM: int = 18

NARRATIVE_NAMES: list[str] = [
    # Market Regime
    "regime_score",
    "htf_trend",
    "volatility_regime",
    # Session Context
    "day_type",
    "opening_type",
    "ib_type",
    "value_migration",
    "session_phase",
    "initiative_direction",
    "balance_width",
    # Structural Position
    "price_vs_value",
    "price_vs_poc",
    "price_vs_ib",
    "trend_alignment",
    "excess_nearby",
    # Breakout Likelihood (distinguishes CONT vs REV scenarios)
    "breakout_score",  # composite: trend_day × initiative × outside_value × post_ib
    "ib_extension_ready",  # post-IB + price at IB edge + initiative flow
    "trend_conviction",  # htf_trend × day_type × initiative alignment
]

assert len(NARRATIVE_NAMES) == NARRATIVE_DIM, "NARRATIVE_NAMES length mismatch"

# Normalisation
_DIST_NORM = 200.0  # ticks — same as structure_features

# Developing day type → ordinal score used by amt_dynamics
# developing_day_type is 0-1 normalised inside amt_dynamics_features; here we
# map it from [0,1] to [-1,1] so balanced=0 and trend=1.
_DAY_TYPE_ORDINAL: dict[str, float] = {
    "non_trend": -1.0,
    "normal": -0.5,
    "neutral": 0.0,
    "normal_variation": 0.25,
    "trend": 1.0,
    "double_distribution": 0.75,
}

_OPENING_TYPE_ORDINAL: dict[str, float] = {
    "OD": 1.0,
    "OTD": 0.5,
    "ORR": -0.5,
    "OA": 0.0,
}


def extract_narrative_features(state: dict) -> np.ndarray:
    """Extract 18 narrative (slow-layer) signals from the RL state dict.

    Args:
        state: Dict with keys including ``macro``, ``swing_structure``,
               ``session_context``, ``session_tpos``, ``session_levels``,
               ``volume_profile``, ``amt_dynamics``, ``single_print_zones``,
               ``price``.  All keys are optional; missing data yields 0.

    Returns:
        np.ndarray of shape ``(18,)`` with dtype ``float32``, values in [-1, 1].
    """
    out = np.zeros(NARRATIVE_DIM, dtype=np.float32)

    price: float = float(state.get("price", 0.0))
    macro: dict | None = state.get("macro")
    swing: SwingStructure | None = state.get("swing_structure")
    session_ctx: dict | None = state.get("session_context") or {}
    session_levels: SessionLevels | None = state.get("session_levels")
    vp: VolumeProfile | None = state.get("volume_profile")
    vwap: VWAPBands | None = state.get("vwap_bands")
    amt_dyn: dict | None = state.get("amt_dynamics")
    single_prints: list | None = state.get("single_print_zones")

    ctx = session_ctx or {}

    # -------------------------------------------------------------------------
    # 0: regime_score — macro regime 0→1, mapped to [-1, 1]
    # -------------------------------------------------------------------------
    if macro is not None:
        regime_raw = float(macro.get("regime_score", 0.5))
        out[0] = float(np.clip(regime_raw * 2.0 - 1.0, -1.0, 1.0))

    # -------------------------------------------------------------------------
    # 1: htf_trend — weighted average of daily/weekly/monthly trend scores
    # -------------------------------------------------------------------------
    if swing is not None:
        trend_map = {
            "uptrend": 1.0,
            "reversing_up": 0.5,
            "ranging": 0.0,
            "reversing_down": -0.5,
            "downtrend": -1.0,
        }
        # Weight daily more than weekly, weekly more than monthly
        d = trend_map.get(swing.daily.structure, 0.0)
        w = trend_map.get(swing.weekly.structure, 0.0)
        m = trend_map.get(swing.monthly.structure, 0.0)
        htf = (3.0 * d + 2.0 * w + 1.0 * m) / 6.0
        out[1] = float(np.clip(htf, -1.0, 1.0))

    # -------------------------------------------------------------------------
    # 2: volatility_regime — VIX normalised; VIX=15 → 0, VIX≥40 → 1
    # -------------------------------------------------------------------------
    if macro is not None:
        vix = float(macro.get("vix", 20.0))
        # Map [10, 40] → [-1, 1]
        out[2] = float(np.clip((vix - 25.0) / 15.0, -1.0, 1.0))

    # -------------------------------------------------------------------------
    # 3: day_type — developing day type from AMT dynamics
    # amt_dynamics["developing_day_type"] is already 0-1 normalised (continuous
    # encoding). We map it to [-1, 1] (0=most balanced, 1=pure trend).
    # -------------------------------------------------------------------------
    if amt_dyn is not None:
        # developing_day_type is stored as continuous 0-1 by AMTDynamicsTracker
        ddt = float(amt_dyn.get("developing_day_type", 0.5))
        out[3] = float(np.clip(ddt * 2.0 - 1.0, -1.0, 1.0))
    else:
        # Fallback: read string day_type from session context if available
        day_type_str = ctx.get("day_type")
        if day_type_str is not None:
            out[3] = _DAY_TYPE_ORDINAL.get(str(day_type_str), 0.0)

    # -------------------------------------------------------------------------
    # 4: opening_type — OD/OTD/ORR/OA from session_context
    # -------------------------------------------------------------------------
    opening_type_str = ctx.get("opening_type", "OA")
    out[4] = _OPENING_TYPE_ORDINAL.get(str(opening_type_str), 0.0)

    # -------------------------------------------------------------------------
    # 5: ib_type — IB range percentile [0,1] → [-1, 1]
    # -------------------------------------------------------------------------
    ib_pct = float(ctx.get("ib_range_percentile", 0.5))
    out[5] = float(np.clip(ib_pct * 2.0 - 1.0, -1.0, 1.0))

    # -------------------------------------------------------------------------
    # 6: value_migration — current POC vs prior value area
    # -------------------------------------------------------------------------
    prior_vah: float | None = ctx.get("prior_vah")
    prior_val: float | None = ctx.get("prior_val")
    if prior_vah is None and session_levels is not None:
        prior_vah = session_levels.pdh
    if prior_val is None and session_levels is not None:
        prior_val = session_levels.pdl

    if vp is not None and prior_vah is not None and prior_val is not None:
        poc = vp.poc
        if poc > prior_vah:
            out[6] = 1.0
        elif poc < prior_val:
            out[6] = -1.0
        else:
            out[6] = 0.0
    elif amt_dyn is not None:
        # amt_features stores value_migration at index 12 in [-1, 0, 1]
        # amt_dynamics doesn't have it directly; use 0
        pass

    # -------------------------------------------------------------------------
    # 7: session_phase — where are we in the RTH session
    # minutes_since_rth: 0 = open, 30 = end of IB, 390 = close
    # Map to [-1, 1]: -1=open, 0=mid-session, +1=close
    # -------------------------------------------------------------------------
    minutes_since_rth = float(ctx.get("minutes_since_rth", 0.0))
    out[7] = float(np.clip(minutes_since_rth / 195.0 - 1.0, -1.0, 1.0))

    # -------------------------------------------------------------------------
    # 8: initiative_direction — initiative vs responsive balance
    # initiative_ratio and responsive_ratio from amt_dynamics, each 0-1.
    # net = initiative - responsive in [-1, 1]
    # -------------------------------------------------------------------------
    if amt_dyn is not None:
        init_ratio = float(amt_dyn.get("initiative_ratio", 0.5))
        resp_ratio = float(amt_dyn.get("responsive_ratio", 0.5))
        out[8] = float(np.clip(init_ratio - resp_ratio, -1.0, 1.0))

    # -------------------------------------------------------------------------
    # 9: balance_width — developing balance width from amt_dynamics (0-1 → [-1,1])
    # -------------------------------------------------------------------------
    if amt_dyn is not None:
        bw = float(amt_dyn.get("balance_width", 0.0))
        # balance_width normalised by 200 ticks in amt_dynamics_features
        # Here we get the raw value; map [0, 200 ticks] to [0, 1] then [-1, 1]
        # If it's already 0-1 normalised (from snapshot), treat directly.
        # AMTDynamicsTracker.snapshot() returns raw tick value; features normalise it.
        # So we normalise here too.
        if bw > 1.0:  # raw tick value
            bw_norm = float(np.clip(bw / (200.0 * TICK_SIZE), 0.0, 1.0))
        else:
            bw_norm = float(np.clip(bw, 0.0, 1.0))
        out[9] = float(bw_norm * 2.0 - 1.0)

    # -------------------------------------------------------------------------
    # 10: price_vs_value — where is price relative to current value area
    # +1 = above VAH, -1 = below VAL, 0 = at POC
    # -------------------------------------------------------------------------
    if vp is not None:
        va_width = max(vp.vah - vp.val, 1e-6)
        if price > vp.vah:
            dist_above = (price - vp.vah) / va_width
            out[10] = float(np.clip(dist_above + 1.0, 0.0, 1.0))
        elif price < vp.val:
            dist_below = (vp.val - price) / va_width
            out[10] = float(np.clip(-(dist_below + 1.0), -1.0, 0.0))
        else:
            # inside VA: 0 at val, 0 at vah; map to [-0.5, 0.5] centred on POC
            pos = (price - vp.val) / va_width  # 0→1
            out[10] = float(pos - 0.5)  # centred around 0

    # -------------------------------------------------------------------------
    # 11: price_vs_poc — signed distance to POC
    # -------------------------------------------------------------------------
    if vp is not None and vwap is not None:
        sd = max(vwap.sd1_upper - vwap.vwap, 1e-6)
        out[11] = float(np.clip((price - vp.poc) / sd, -1.0, 1.0))
    elif vp is not None:
        out[11] = float(np.clip((price - vp.poc) / TICK_SIZE / _DIST_NORM, -1.0, 1.0))

    # -------------------------------------------------------------------------
    # 12: price_vs_ib — price relative to IB midpoint, normalised by IB range
    # -------------------------------------------------------------------------
    ib_high: float | None = None
    ib_low: float | None = None
    if session_levels is not None:
        ib_high = session_levels.ib_high
        ib_low = session_levels.ib_low
    if ib_high is None:
        ib_high = ctx.get("ib_high")
    if ib_low is None:
        ib_low = ctx.get("ib_low")

    if ib_high is not None and ib_low is not None:
        ib_range = max(ib_high - ib_low, 1e-6)
        ib_mid = (ib_high + ib_low) / 2.0
        out[12] = float(np.clip((price - ib_mid) / (ib_range / 2.0), -1.0, 1.0))

    # -------------------------------------------------------------------------
    # 13: trend_alignment — directly from SwingStructure.trend_alignment [-1, 1]
    # -------------------------------------------------------------------------
    if swing is not None:
        out[13] = float(np.clip(swing.trend_alignment, -1.0, 1.0))

    # -------------------------------------------------------------------------
    # 14: excess_nearby — is price near a single-print/excess zone?
    # single_print_zones is a list of (low, high) tuples or similar.
    # We compute the nearest zone distance and map to [0, 1] → [-1, 1].
    # -------------------------------------------------------------------------
    if amt_dyn is not None:
        spp = float(amt_dyn.get("single_print_proximity", 0.0))
        # Normalised 0-1 in amt_dynamics snapshot (divisor=200 in _NORM)
        # If >1 it's raw; normalise
        spp = float(np.clip(spp / _DIST_NORM, 0.0, 1.0)) if spp > 1.0 else float(np.clip(spp, 0.0, 1.0))
        out[14] = float(spp * 2.0 - 1.0)
    elif single_prints is not None and len(single_prints) > 0 and price > 0:
        min_dist_ticks = float("inf")
        for zone in single_prints:
            try:
                if isinstance(zone, (list, tuple)) and len(zone) >= 2:
                    lo, hi = float(zone[0]), float(zone[1])
                    mid = (lo + hi) / 2.0
                    dist = abs(price - mid) / TICK_SIZE
                else:
                    dist = abs(price - float(zone)) / TICK_SIZE
                min_dist_ticks = min(min_dist_ticks, dist)
            except (TypeError, ValueError):
                continue
        if min_dist_ticks < float("inf"):
            # 0 ticks = +1, 200+ ticks = -1
            proximity = float(np.clip(1.0 - min_dist_ticks / _DIST_NORM, 0.0, 1.0))
            out[14] = float(proximity * 2.0 - 1.0)

    # -------------------------------------------------------------------------
    # 15: breakout_score — composite signal for "this level will break"
    # Combines: trend day + initiative aligned + price outside value + post-IB
    # Each component is 0 or 1, final score = average → [0, 1] mapped to [-1, 1]
    # -------------------------------------------------------------------------
    bo_signals = 0.0
    bo_count = 4.0
    # Is it a trend day? (day_type > 0.3 on our -1 to 1 scale)
    if out[3] > 0.3:
        bo_signals += 1.0
    # Is initiative aligned with price direction?
    if abs(out[8]) > 0.3:  # initiative_direction has conviction
        bo_signals += 1.0
    # Is price outside value area? (either side)
    if abs(out[10]) > 0.5:  # price_vs_value beyond VA edge
        bo_signals += 1.0
    # Are we post-IB? (session_phase > 0 means post-IB)
    if out[7] > 0.0:
        bo_signals += 1.0
    out[15] = float(bo_signals / bo_count * 2.0 - 1.0)

    # -------------------------------------------------------------------------
    # 16: ib_extension_ready — specific signal for IB breakout
    # Post-IB + price at IB edge + initiative flow in breakout direction
    # -------------------------------------------------------------------------
    ib_ext = 0.0
    if out[7] > 0.0:  # post-IB
        ib_ext += 0.33
    if abs(out[12]) > 0.7:  # price near IB high or low
        ib_ext += 0.33
    if abs(out[8]) > 0.4:  # initiative flow
        ib_ext += 0.34
    out[16] = float(np.clip(ib_ext * 2.0 - 1.0, -1.0, 1.0))

    # -------------------------------------------------------------------------
    # 17: trend_conviction — do all narrative layers agree on direction?
    # htf_trend × day_type × initiative — all same sign = strong conviction
    # -------------------------------------------------------------------------
    htf = out[1]  # htf_trend
    dt = out[3]  # day_type
    init = out[8]  # initiative_direction
    if abs(htf) > 0.1 and abs(dt) > 0.1 and abs(init) > 0.1:
        # All three have a direction — check if they agree
        signs_agree = (htf > 0) == (dt > 0) == (init > 0)
        magnitude = (abs(htf) + abs(dt) + abs(init)) / 3.0
        if signs_agree:
            # Strong conviction: direction × magnitude
            direction = 1.0 if htf > 0 else -1.0
            out[17] = float(np.clip(direction * magnitude, -1.0, 1.0))
        else:
            out[17] = 0.0  # conflicting signals = no conviction

    # Final safety: clip all to [-1, 1] and ensure finite
    np.clip(out, -1.0, 1.0, out=out)
    out = np.where(np.isfinite(out), out, np.float32(0.0))

    return out
