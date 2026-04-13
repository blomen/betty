"""Tick-level micro features — captures the last 20 ticks at a level touch.

These features give the model the immediate price action context that
1-minute candles can't capture: approach velocity, trade sizes, buy/sell
imbalance in the seconds leading up to the level touch.

Feature layout (20 features):
  0  approach_velocity     — ticks/second in last 20 ticks (signed)
  1  approach_accel        — acceleration: velocity delta (last 10 vs first 10)
  2  net_delta_norm        — net buy-sell delta / total volume
  3  delta_trend           — delta in last 10 ticks vs first 10 (momentum shift)
  4  max_trade_size_norm   — largest single trade / avg trade size
  5  big_trade_ratio       — fraction of volume from trades > 2x avg
  6  buy_volume_ratio      — buy volume / total volume (0.5 = balanced)
  7  tick_spread_norm      — (high - low) / avg tick range in last 20
  8  consec_direction      — max consecutive same-direction ticks / 20
  9  reversal_count_norm   — direction changes / 20 (choppy = high)
  10 time_compression      — 20 / elapsed_seconds (faster = more activity)
  11 last5_velocity        — ticks/second in last 5 ticks only
  12 last5_delta_norm      — net delta in last 5 ticks / volume
  13 bid_side_aggression   — fraction of volume hitting bids (sell aggression)
  14 size_at_touch_norm    — size of the tick that touched the level / avg
  15 approach_linearity    — R² of price vs time (1.0 = straight line approach)
  16 vol_surge             — volume in last 10 ticks / volume in first 10
  17 price_vs_midrange     — touch_price position within recent tick range
  18 big_trade_skew        — big trade buy/sell imbalance
  19 last5_acceleration    — velocity change in final 5 ticks
"""

from __future__ import annotations

import numpy as np

_N_FEATURES = 20


def _ts_to_seconds(ts) -> float:
    """Convert timestamp to float seconds (handles both datetime and numeric)."""
    return float(ts.timestamp()) if hasattr(ts, "timestamp") else float(ts)


def extract_micro_features(
    recent_ticks: list[dict],
    touch_price: float,
) -> np.ndarray:
    """Extract 20 micro features from the last ~20 ticks before a level touch.

    Each tick dict must have: ts (datetime), price (float), size (int), side ("A"|"B").
    Returns zeros if fewer than 2 ticks available.
    """
    if len(recent_ticks) < 2:
        return np.zeros(_N_FEATURES, dtype=np.float32)

    ticks = recent_ticks[-20:]  # last 20
    n = len(ticks)

    prices = [t["price"] for t in ticks]
    sizes = [t["size"] for t in ticks]
    sides = [t.get("side", "A") for t in ticks]
    timestamps = [t["ts"] for t in ticks]

    total_vol = sum(sizes) or 1
    avg_size = total_vol / n
    t0_s = _ts_to_seconds(timestamps[0])
    t_last_s = _ts_to_seconds(timestamps[-1])
    elapsed_s = max(0.001, t_last_s - t0_s)

    # 0: approach_velocity (ticks/second, signed)
    price_change = (prices[-1] - prices[0]) / TICK_SIZE  # convert price to ticks
    approach_vel = price_change / elapsed_s
    approach_vel = np.clip(approach_vel / 5.0, -1.0, 1.0)  # normalise

    # 1: approach_accel (velocity change)
    mid = n // 2
    t_mid_s = _ts_to_seconds(timestamps[mid]) if mid > 0 else t0_s
    if mid > 0 and n > mid:
        t1 = max(0.001, t_mid_s - t0_s)
        t2 = max(0.001, t_last_s - t_mid_s)
        v1 = (prices[mid] - prices[0]) / t1
        v2 = (prices[-1] - prices[mid]) / t2
        accel = np.clip((v2 - v1) / 5.0, -1.0, 1.0)
    else:
        accel = 0.0

    # 2: net_delta_norm
    buy_vol = sum(s for s, side in zip(sizes, sides) if side == "B")
    sell_vol = sum(s for s, side in zip(sizes, sides) if side == "A")
    net_delta = buy_vol - sell_vol
    net_delta_norm = np.clip(net_delta / max(total_vol, 1), -1.0, 1.0)

    # 3: delta_trend (momentum shift)
    first_half = ticks[:mid] if mid > 0 else ticks[:1]
    second_half = ticks[mid:] if mid > 0 else ticks[1:]
    d1 = sum(t["size"] if t.get("side") == "B" else -t["size"] for t in first_half)
    d2 = sum(t["size"] if t.get("side") == "B" else -t["size"] for t in second_half)
    vol_half = max(sum(t["size"] for t in first_half), 1)
    delta_trend = np.clip((d2 - d1) / max(vol_half, 1), -1.0, 1.0)

    # 4: max_trade_size_norm
    max_size = max(sizes)
    max_trade_norm = min(max_size / max(avg_size, 1), 5.0) / 5.0

    # 5: big_trade_ratio
    big_threshold = avg_size * 2
    big_vol = sum(s for s in sizes if s >= big_threshold)
    big_ratio = big_vol / max(total_vol, 1)

    # 6: buy_volume_ratio
    buy_ratio = buy_vol / max(total_vol, 1)

    # 7: tick_spread_norm
    spread = max(prices) - min(prices)
    avg_tick_range = sum(abs(prices[i] - prices[i - 1]) for i in range(1, n)) / max(n - 1, 1)
    spread_norm = min(spread / max(avg_tick_range * n, 0.25), 3.0) / 3.0

    # 8: consec_direction (max run of same direction)
    max_run = 1
    run = 1
    for i in range(1, n):
        if (prices[i] - prices[i - 1]) * (prices[i - 1] - prices[max(0, i - 2)]) > 0:
            run += 1
            max_run = max(max_run, run)
        else:
            run = 1
    consec_dir = max_run / max(n, 1)

    # 9: reversal_count_norm
    reversals = sum(1 for i in range(2, n) if (prices[i] - prices[i - 1]) * (prices[i - 1] - prices[i - 2]) < 0)
    reversal_norm = reversals / max(n - 2, 1)

    # 10: time_compression (activity density)
    time_comp = min(n / max(elapsed_s, 0.001), 100.0) / 100.0

    # 11-12: last 5 ticks features
    last5 = ticks[-5:] if n >= 5 else ticks
    l5_elapsed = max(0.001, _ts_to_seconds(last5[-1]["ts"]) - _ts_to_seconds(last5[0]["ts"]))
    l5_vel = np.clip((last5[-1]["price"] - last5[0]["price"]) / TICK_SIZE / l5_elapsed / 5.0, -1.0, 1.0)
    l5_buy = sum(t["size"] for t in last5 if t.get("side") == "B")
    l5_sell = sum(t["size"] for t in last5 if t.get("side") == "A")
    l5_vol = max(l5_buy + l5_sell, 1)
    l5_delta = np.clip((l5_buy - l5_sell) / l5_vol, -1.0, 1.0)

    # 13: bid_side_aggression (sell aggression)
    bid_aggression = sell_vol / max(total_vol, 1)

    # 14: size_at_touch_norm
    touch_size = sizes[-1] / max(avg_size, 1)
    touch_size_norm = min(touch_size, 5.0) / 5.0

    # 15: approach_linearity (R² of price vs time)
    if n >= 3:
        t_arr = np.array([_ts_to_seconds(ts) - t0_s for ts in timestamps])
        p_arr = np.array(prices)
        if t_arr[-1] > 0:
            t_norm = t_arr / t_arr[-1]
            corr = np.corrcoef(t_norm, p_arr)
            r_sq = corr[0, 1] ** 2 if np.isfinite(corr[0, 1]) else 0.0
        else:
            r_sq = 0.0
    else:
        r_sq = 0.0

    # 16: vol_surge (second half volume / first half)
    v1_vol = max(sum(t["size"] for t in first_half), 1)
    v2_vol = max(sum(t["size"] for t in second_half), 1)
    vol_surge = min(v2_vol / v1_vol, 5.0) / 5.0

    # 17: price vs midrange (where touch is relative to recent price range)
    p_mid = (max(prices) + min(prices)) / 2.0
    p_range = max(prices) - min(prices)
    _price_vs_midrange = np.clip((touch_price - p_mid) / max(p_range, 0.25), -1.0, 1.0)

    # 18: big trade skew (are big trades biased buy or sell?)
    big_buy = sum(s for s, side in zip(sizes, sides) if s >= big_threshold and side == "B")
    big_sell = sum(s for s, side in zip(sizes, sides) if s >= big_threshold and side == "A")
    _big_trade_skew = np.clip((big_buy - big_sell) / max(big_buy + big_sell, 1), -1.0, 1.0)

    # 19: acceleration in last 5 ticks (are we speeding up into the touch?)
    if n >= 5:
        l5_prices = prices[-5:]
        l5_ts = timestamps[-5:]
        l5_mid_idx = len(l5_prices) // 2
        t_a = max(0.001, _ts_to_seconds(l5_ts[l5_mid_idx]) - _ts_to_seconds(l5_ts[0]))
        t_b = max(0.001, _ts_to_seconds(l5_ts[-1]) - _ts_to_seconds(l5_ts[l5_mid_idx]))
        va = (l5_prices[l5_mid_idx] - l5_prices[0]) / t_a
        vb = (l5_prices[-1] - l5_prices[l5_mid_idx]) / t_b
        _l5_accel = np.clip((vb - va) / 5.0, -1.0, 1.0)
    else:
        _l5_accel = 0.0

    feats = np.array(
        [
            float(approach_vel),  # 0
            float(accel),  # 1
            float(net_delta_norm),  # 2
            float(delta_trend),  # 3
            float(max_trade_norm),  # 4
            float(big_ratio),  # 5
            float(buy_ratio),  # 6
            float(spread_norm),  # 7
            float(consec_dir),  # 8
            float(reversal_norm),  # 9
            float(time_comp),  # 10
            float(l5_vel),  # 11
            float(l5_delta),  # 12
            float(bid_aggression),  # 13
            float(touch_size_norm),  # 14
            float(r_sq),  # 15
            float(vol_surge),  # 16
            float(_price_vs_midrange),  # 17: price vs midrange of recent ticks
            float(_big_trade_skew),  # 18: big trade buy/sell imbalance
            float(_l5_accel),  # 19: acceleration in last 5 ticks
        ],
        dtype=np.float32,
    )

    feats = np.clip(feats, -5.0, 5.0)
    return feats
