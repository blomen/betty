"""Temporal derivative and candle pattern computation for level touch ML features."""

from __future__ import annotations

import numpy as np


def _get(candle, key: str, default=None):
    """Duck-typing accessor: works for both dicts and dataclass/object instances."""
    if isinstance(candle, dict):
        return candle.get(key, default)
    return getattr(candle, key, default)


def compute_temporal_derivatives(candles, lookback: int = 10) -> dict:
    """Compute temporal derivative features from a sequence of candles.

    Args:
        candles: List of candle dicts or CandleFlow dataclass objects.
        lookback: Maximum number of candles to consider (default 10).

    Returns:
        Dict with 8 temporal derivative features. All values are None if
        fewer than 5 candles are provided.
    """
    _null = {
        "delta_slope_5m": None,
        "delta_slope_10m": None,
        "cvd_acceleration": None,
        "volume_roc_5m": None,
        "tick_rate_roc": None,
        "spread_compression": None,
        "absorption_building": None,
        "imbalance_trend": None,
    }

    # Work with the most recent `lookback` candles
    window = list(candles)[-lookback:]

    if len(window) < 5:
        return _null

    deltas = [_get(c, "delta", 0) or 0 for c in window]
    volumes = [_get(c, "volume", 0) or 0 for c in window]
    ticks = [_get(c, "tick_count", 0) or 0 for c in window]
    spreads = [_get(c, "spread", 0) or 0 for c in window]
    body_ratios = [_get(c, "body_ratio", 0) or 0 for c in window]
    stacked = [_get(c, "stacked_imbalance_count", 0) or 0 for c in window]

    n = len(window)
    last5 = slice(n - 5, n)
    prior5 = slice(max(0, n - 10), n - 5)

    # --- delta_slope_5m: linear regression slope over last 5 candles ---
    d5 = deltas[last5]
    xs5 = np.arange(len(d5), dtype=float)
    delta_slope_5m = float(np.polyfit(xs5, d5, 1)[0]) if len(d5) >= 2 else None

    # --- delta_slope_10m: linear regression slope over all window candles ---
    xs_all = np.arange(n, dtype=float)
    delta_slope_10m = float(np.polyfit(xs_all, deltas, 1)[0]) if n >= 2 else None

    # --- cvd_acceleration: sum(delta last 5) - sum(delta prior 5) ---
    d_last5 = deltas[last5]
    d_prior5 = deltas[prior5]
    cvd_acceleration = float(sum(d_last5) - sum(d_prior5))

    # --- volume_roc_5m: avg vol last 5 / avg vol prior 5 ---
    v_last5 = volumes[last5]
    v_prior5 = volumes[prior5]
    avg_v_prior5 = float(np.mean(v_prior5)) if len(v_prior5) > 0 else 0.0
    volume_roc_5m = (float(np.mean(v_last5)) / avg_v_prior5) if avg_v_prior5 != 0 else None

    # --- tick_rate_roc: avg ticks last 3 / avg ticks prior 3 ---
    last3 = slice(n - 3, n)
    prior3 = slice(max(0, n - 6), n - 3)
    t_last3 = ticks[last3]
    t_prior3 = ticks[prior3]
    avg_t_prior3 = float(np.mean(t_prior3)) if len(t_prior3) > 0 else 0.0
    tick_rate_roc = (float(np.mean(t_last3)) / avg_t_prior3) if avg_t_prior3 != 0 else None

    # --- spread_compression: avg spread last 3 / avg spread all ---
    s_last3 = spreads[last3]
    avg_s_all = float(np.mean(spreads)) if len(spreads) > 0 else 0.0
    spread_compression = (float(np.mean(s_last3)) / avg_s_all) if avg_s_all != 0 else None

    # --- absorption_building: count candles where body_ratio < 0.3 ---
    absorption_building = int(sum(1 for br in body_ratios if br < 0.3))

    # --- imbalance_trend: sum stacked last 5 - sum prior 5 ---
    st_last5 = stacked[last5]
    st_prior5 = stacked[prior5]
    imbalance_trend = float(sum(st_last5) - sum(st_prior5))

    return {
        "delta_slope_5m": delta_slope_5m,
        "delta_slope_10m": delta_slope_10m,
        "cvd_acceleration": cvd_acceleration,
        "volume_roc_5m": volume_roc_5m,
        "tick_rate_roc": tick_rate_roc,
        "spread_compression": spread_compression,
        "absorption_building": absorption_building,
        "imbalance_trend": imbalance_trend,
    }


def compute_approach_volume_features(candles, lookback: int = 10) -> dict:
    """Compute volume behavior on approach to a level.

    Tracks whether volume is increasing or decreasing as price moves toward
    the level. Decreasing volume on approach = exhaustion = reversal signal.
    Increasing volume = conviction = continuation signal.

    Args:
        candles: Recent candles leading up to the level touch.
        lookback: Number of candles to analyze.

    Returns:
        Dict with approach volume features.
    """
    _null = {
        "approach_vol_slope": None,
        "approach_vol_ratio": None,
        "approach_delta_slope": None,
        "approach_buy_pct_trend": None,
        "approach_vol_accel": None,
        "approach_big_vol_count": None,
    }

    window = list(candles)[-lookback:]
    if len(window) < 4:
        return _null

    volumes = [_get(c, "volume", 0) or 0 for c in window]
    deltas = [_get(c, "delta", 0) or 0 for c in window]
    buy_vols = [_get(c, "buy_volume", 0) or _get(c, "volume", 0) or 0 for c in window]
    sell_vols = [_get(c, "sell_volume", 0) or 0 for c in window]

    n = len(window)

    # Volume slope — linear regression of volume over approach
    # Negative slope = volume decreasing into level (exhaustion)
    # Positive slope = volume increasing into level (conviction)
    xs = np.arange(n, dtype=float)
    vol_arr = np.array(volumes, dtype=float)
    approach_vol_slope = float(np.polyfit(xs, vol_arr, 1)[0]) if n >= 2 else None

    # Volume ratio — last 3 candles avg volume / prior candles avg volume
    # < 1.0 = fading, > 1.0 = surging into level
    last3_vol = volumes[-3:] if n >= 3 else volumes
    prior_vol = volumes[:-3] if n > 3 else volumes[:1]
    avg_prior = float(np.mean(prior_vol)) if prior_vol else 0
    approach_vol_ratio = (float(np.mean(last3_vol)) / avg_prior) if avg_prior > 0 else None

    # Delta slope — is buying/selling pressure increasing or decreasing?
    delta_arr = np.array(deltas, dtype=float)
    approach_delta_slope = float(np.polyfit(xs, delta_arr, 1)[0]) if n >= 2 else None

    # Buy percentage trend — is buy share of volume growing?
    buy_pcts = []
    for bv, sv in zip(buy_vols, sell_vols):
        total = bv + sv
        buy_pcts.append(bv / total if total > 0 else 0.5)
    bp_arr = np.array(buy_pcts, dtype=float)
    approach_buy_pct_trend = float(np.polyfit(xs, bp_arr, 1)[0]) if n >= 2 else None

    # Volume acceleration — is the rate of change itself changing?
    # Compare vol RoC of last 3 vs prior 3
    if n >= 6:
        roc_recent = float(np.mean(volumes[-3:])) / max(float(np.mean(volumes[-6:-3])), 1)
        roc_prior = float(np.mean(volumes[-6:-3])) / max(float(np.mean(volumes[:max(1, n - 6)])), 1)
        approach_vol_accel = roc_recent - roc_prior
    else:
        approach_vol_accel = None

    # Big volume candle count on approach — candles with vol > 1.5x average
    avg_vol = float(np.mean(volumes)) if volumes else 0
    approach_big_vol_count = sum(1 for v in volumes if v > avg_vol * 1.5) if avg_vol > 0 else 0

    return {
        "approach_vol_slope": round(approach_vol_slope, 4) if approach_vol_slope is not None else None,
        "approach_vol_ratio": round(approach_vol_ratio, 4) if approach_vol_ratio is not None else None,
        "approach_delta_slope": round(approach_delta_slope, 4) if approach_delta_slope is not None else None,
        "approach_buy_pct_trend": round(approach_buy_pct_trend, 6) if approach_buy_pct_trend is not None else None,
        "approach_vol_accel": round(approach_vol_accel, 4) if approach_vol_accel is not None else None,
        "approach_big_vol_count": approach_big_vol_count,
    }


def compute_candle_pattern_features(candles) -> dict:
    """Compute candle pattern features from a sequence of candles.

    Args:
        candles: List of candle dicts or CandleFlow dataclass objects.

    Returns:
        Dict with 5 candle pattern features. Values are None for an empty list.
    """
    _null = {
        "last_3_candles_direction": None,
        "last_candle_is_doji": None,
        "consecutive_same_direction": None,
        "highest_volume_candle_position": None,
        "range_expansion": None,
    }

    if not candles:
        return _null

    window = list(candles)

    # --- last_3_candles_direction: count up candles (close > open) in last 3 ---
    last3 = window[-3:]
    last_3_candles_direction = sum(
        1 for c in last3 if (_get(c, "close", 0) or 0) > (_get(c, "open", 0) or 0)
    )

    # --- last_candle_is_doji: body_ratio < 0.1 ---
    last = window[-1]
    last_body_ratio = _get(last, "body_ratio", None)
    last_candle_is_doji = (last_body_ratio is not None) and (last_body_ratio < 0.1)

    # --- consecutive_same_direction: candles in a row moved same way as the last ---
    last_close = _get(last, "close", 0) or 0
    last_open = _get(last, "open", 0) or 0
    last_dir = 1 if last_close > last_open else (-1 if last_close < last_open else 0)

    consecutive_same_direction = 0
    for c in reversed(window):
        c_close = _get(c, "close", 0) or 0
        c_open = _get(c, "open", 0) or 0
        c_dir = 1 if c_close > c_open else (-1 if c_close < c_open else 0)
        if c_dir == last_dir:
            consecutive_same_direction += 1
        else:
            break

    # --- highest_volume_candle_position: index (0=oldest) of peak volume in last 10 ---
    vol_window = window[-10:]
    volumes = [_get(c, "volume", 0) or 0 for c in vol_window]
    offset = len(window) - len(vol_window)  # offset so index is relative to full window start
    if volumes:
        local_idx = int(np.argmax(volumes))
        # Express as position in the last-10 sub-window (0 = oldest in that slice)
        highest_volume_candle_position = local_idx
    else:
        highest_volume_candle_position = None

    # --- range_expansion: last candle spread / avg spread of last 10 ---
    spread_window = window[-10:]
    spreads = [_get(c, "spread", 0) or 0 for c in spread_window]
    last_spread = _get(last, "spread", None)
    avg_spread = float(np.mean(spreads)) if spreads else 0.0
    if last_spread is not None and avg_spread != 0:
        range_expansion = float(last_spread) / avg_spread
    else:
        range_expansion = None

    return {
        "last_3_candles_direction": last_3_candles_direction,
        "last_candle_is_doji": last_candle_is_doji,
        "consecutive_same_direction": consecutive_same_direction,
        "highest_volume_candle_position": highest_volume_candle_position,
        "range_expansion": range_expansion,
    }
