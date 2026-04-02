"""AMT (Auction Market Theory) feature extraction.

Extracts 20 features encoding Dalton day type, opening type, VA migration,
and session context enrichment:

  Indices 0-5   : Dalton day type (6-way one-hot)
                    0 non_trend, 1 normal, 2 neutral,
                    3 normal_variation, 4 trend, 5 double_distribution
  Indices 6-9   : Opening type (4-way one-hot)
                    6 OD, 7 OTD, 8 ORR, 9 OA
  Index  10     : range_extension       (0-1 normalised)
  Index  11     : va_overlap            (0-1 fraction overlap with prior VA)
  Index  12     : value_migration       (-1 / 0 / +1 mapped to -1/0/+1)
  Index  13     : ib_percentile         (0-1, IB range percentile vs history)
  Index  14     : overnight_gap         (-1 to +1, normalised gap direction)
  Index  15     : open_vs_prior_poc     (-1 to +1, open relative to prior POC)
  Index  16     : composite_va_overlap  (0-1, multi-session VA overlap)
  Index  17     : prior_poor_high       (0/1, prior session had poor high)
  Index  18     : prior_poor_low        (0/1, prior session had poor low)
  Index  19     : prior_excess_quality  (-1 to +1, normalised excess quality)
"""
from __future__ import annotations

import numpy as np

from ...market_data.levels import SessionLevels, VolumeProfile

_N_FEATURES = 20

# Day-type indices
_IDX_NON_TREND = 0
_IDX_NORMAL = 1
_IDX_NEUTRAL = 2
_IDX_NORMAL_VAR = 3
_IDX_TREND = 4
_IDX_DOUBLE_DIST = 5

# Opening-type indices
_IDX_OD = 6
_IDX_OTD = 7
_IDX_ORR = 8
_IDX_OA = 9

# Scalar indices
_IDX_RANGE_EXT = 10
_IDX_VA_OVERLAP = 11
_IDX_VALUE_MIG = 12

# Static AMT enrichment indices
_IDX_IB_PERCENTILE = 13
_IDX_OVERNIGHT_GAP = 14
_IDX_OPEN_VS_PRIOR_POC = 15
_IDX_COMPOSITE_VA_OVERLAP = 16
_IDX_PRIOR_POOR_HIGH = 17
_IDX_PRIOR_POOR_LOW = 18
_IDX_PRIOR_EXCESS_QUALITY = 19


def _classify_dalton_day(
    ib_range: float,
    daily_range: float,
    extensions_up: float,
    extensions_down: float,
) -> int:
    """Return the index (0-5) of the Dalton day type."""
    if ib_range <= 0:
        return _IDX_NORMAL  # fallback

    range_ratio = daily_range / ib_range

    if range_ratio <= 1.15:
        return _IDX_NON_TREND

    if range_ratio <= 1.5:
        return _IDX_NORMAL

    # range_ratio > 1.25 — check balance
    max_ext = max(extensions_up, extensions_down, 1e-9)
    imbalance = abs(extensions_up - extensions_down) / max_ext

    if range_ratio <= 2.0:
        # Neutral: both extensions within 20% of each other
        if imbalance < 0.2:
            return _IDX_NEUTRAL
        return _IDX_NORMAL_VAR

    # range_ratio > 2.0
    # Trend: one side dominates (> 3x the other)
    if extensions_up > 3.0 * max(extensions_down, 1e-9) or \
       extensions_down > 3.0 * max(extensions_up, 1e-9):
        return _IDX_TREND

    return _IDX_DOUBLE_DIST


def _classify_opening(
    open_price: float,
    ib_high: float,
    ib_low: float,
    prior_vah: float,
    prior_val: float,
    ib_range: float,
) -> int:
    """Return the index (6-9) of the opening type."""
    if ib_range <= 0:
        return _IDX_OA  # fallback

    open_vs_ib = (open_price - ib_low) / ib_range  # 0 = at IB low, 1 = at IB high

    # Opened outside prior value area
    outside_va = open_price > prior_vah or open_price < prior_val
    if outside_va:
        # ORR: price came back inside VA during IB (IB mid is inside prior VA)
        ib_mid = (ib_high + ib_low) / 2.0
        if prior_val <= ib_mid <= prior_vah:
            return _IDX_ORR
        return _IDX_OD

    # Opened inside prior value area
    # OD: opened near IB extreme
    if open_vs_ib < 0.1 or open_vs_ib > 0.9:
        return _IDX_OD

    # OTD: open in middle 50% of IB
    if 0.25 <= open_vs_ib <= 0.75:
        return _IDX_OTD

    return _IDX_OA


def extract_amt_features(
    session_levels: SessionLevels | None,
    volume_profile: VolumeProfile | None,
    session_context: dict | None,
    price: float,
) -> np.ndarray:
    """Extract 20 AMT features: 6 day type + 4 opening type + 3 scalars + 7 enrichment.

    Degrades gracefully — returns zeros when data is missing.

    Args:
        session_levels: IB levels, pdh/pdl for prior VA proxy.
        volume_profile: Current session's VP (poc/vah/val).
        session_context: Dict with 'daily_range_pct', 'open_price', etc.
        price: Current market price (used for daily_range reconstruction).

    Returns:
        np.ndarray of shape (20,), float32.
    """
    feats = np.zeros(_N_FEATURES, dtype=np.float32)

    if session_levels is None and session_context is None:
        return feats

    ctx = session_context or {}

    # --- Resolve IB levels ---
    ib_high: float | None = None
    ib_low: float | None = None
    if session_levels is not None:
        ib_high = session_levels.ib_high
        ib_low = session_levels.ib_low
    # Fallback: read from context if not in session_levels
    if ib_high is None:
        ib_high = ctx.get("ib_high")
    if ib_low is None:
        ib_low = ctx.get("ib_low")

    if ib_high is None or ib_low is None:
        return feats  # can't compute without IB

    ib_range = ib_high - ib_low
    if ib_range <= 0:
        return feats

    # --- Resolve daily high/low ---
    daily_high: float | None = ctx.get("daily_high")
    daily_low: float | None = ctx.get("daily_low")
    if daily_high is None or daily_low is None:
        daily_range_pct = float(ctx.get("daily_range_pct", 0.0))
        if daily_range_pct > 0 and price > 0:
            half = (daily_range_pct * price) / 2.0
            daily_high = price + half
            daily_low = price - half
        else:
            return feats  # can't reconstruct daily range

    daily_range = daily_high - daily_low
    extensions_up = max(0.0, daily_high - ib_high)
    extensions_down = max(0.0, ib_low - daily_low)

    # --- Dalton day type (one-hot, indices 0-5) ---
    day_type_idx = _classify_dalton_day(ib_range, daily_range, extensions_up, extensions_down)
    feats[day_type_idx] = 1.0

    # --- Opening type (one-hot, indices 6-9) ---
    open_price: float | None = ctx.get("open_price")
    if open_price is None and session_levels is not None:
        # Proxy: session open is unavailable; skip opening type
        pass

    # Prior VA: prefer explicit keys, fall back to pdh/pdl
    prior_vah: float | None = ctx.get("prior_vah")
    prior_val: float | None = ctx.get("prior_val")
    if prior_vah is None and session_levels is not None:
        prior_vah = session_levels.pdh
    if prior_val is None and session_levels is not None:
        prior_val = session_levels.pdl

    if open_price is not None and prior_vah is not None and prior_val is not None:
        opening_idx = _classify_opening(
            open_price, ib_high, ib_low,
            prior_vah, prior_val, ib_range,
        )
        feats[opening_idx] = 1.0

    # --- Scalar: range extension (index 10) ---
    range_extension = float(np.clip((daily_range - ib_range) / max(ib_range, 1.0), 0.0, 3.0)) / 3.0
    feats[_IDX_RANGE_EXT] = range_extension

    # --- Scalar: VA overlap with prior VA (index 11) ---
    if (
        volume_profile is not None
        and prior_vah is not None
        and prior_val is not None
    ):
        curr_vah = volume_profile.vah
        curr_val = volume_profile.val
        curr_width = max(curr_vah - curr_val, 1e-9)
        prior_width = max(prior_vah - prior_val, 1e-9)
        overlap = max(0.0, min(curr_vah, prior_vah) - max(curr_val, prior_val))
        va_overlap = overlap / max(curr_width, prior_width)
        feats[_IDX_VA_OVERLAP] = float(np.clip(va_overlap, 0.0, 1.0))

    # --- Scalar: value migration (index 12) ---
    if (
        volume_profile is not None
        and prior_vah is not None
        and prior_val is not None
    ):
        poc = volume_profile.poc
        if poc > prior_vah:
            feats[_IDX_VALUE_MIG] = 1.0
        elif poc < prior_val:
            feats[_IDX_VALUE_MIG] = -1.0
        # else: 0.0 (inside prior VA)

    # --- Static AMT enrichment (indices 13-19) ---
    feats[_IDX_IB_PERCENTILE] = float(np.clip(ctx.get("ib_range_percentile", 0.5), 0.0, 1.0))
    feats[_IDX_OVERNIGHT_GAP] = float(np.clip(ctx.get("overnight_gap", 0), -1.0, 1.0))

    open_vs_poc = ctx.get("open_vs_prior_poc")
    if open_vs_poc is not None:
        feats[_IDX_OPEN_VS_PRIOR_POC] = float(np.clip(open_vs_poc, -1.0, 1.0))

    feats[_IDX_COMPOSITE_VA_OVERLAP] = float(np.clip(ctx.get("composite_va_overlap", 0), 0.0, 1.0))
    feats[_IDX_PRIOR_POOR_HIGH] = 1.0 if ctx.get("prior_poor_high") else 0.0
    feats[_IDX_PRIOR_POOR_LOW] = 1.0 if ctx.get("prior_poor_low") else 0.0
    feats[_IDX_PRIOR_EXCESS_QUALITY] = float(np.clip(ctx.get("prior_excess_quality", 0) / 10.0, -1.0, 1.0))

    return feats
