"""Pattern detector features (Phase 3a).

Explicit pattern detectors for the setups Fabio/Dalton frameworks name:
- Pin bar rejection: long wick into level, small body opposite
- Absorption wall: large volume at level with tiny price progress
- Imbalance cluster: 3+ consecutive stacked imbalances in trade direction
- Delta divergence: price making new extreme while CVD weakens
- Trapped breakout: break of level on weak vol + strong reversal

Binary (0/1) or [0, 1] signals that the GBT/DQN can consume directly
instead of having to combine primitive orderflow features implicitly.

Output: 5 dims.
"""

from __future__ import annotations

import numpy as np

_N_FEATURES = 5


def extract_pattern_features(
    touch_bar_partial,
    reaction_ticks: list | None,
    recent_ticks: list | None,
    orderflow_signals,
    touch_price: float,
    approach_direction: str,
) -> np.ndarray:
    """Compute 5-dim pattern detector vector.

    0  pin_bar_rejection   — long wick into level + small body + reversal [0,1]
    1  absorption_wall     — large vol + tiny price progress = wall [0,1]
    2  imbalance_cluster   — stacked imbalances ≥3 in REV direction [0,1]
    3  delta_divergence    — price new extreme while cumulative delta weakens [0,1]
    4  trapped_breakout    — break of level on low vol + high-vol reversal [0,1]
    """
    features = np.zeros(_N_FEATURES, dtype=np.float32)

    # REV direction: +1 long, -1 short. Approach up → rev is short.
    rev_dir = -1 if approach_direction == "up" else 1

    # --- 0. Pin bar rejection ---
    # Touch bar has long wick toward the approach side, small body, close back
    # across entry. Use partial candle if available; else use closed last candle.
    tb = touch_bar_partial
    if tb is not None:
        spread = max(float(tb.high) - float(tb.low), 1e-6)
        body = abs(float(tb.close) - float(tb.open))
        # Wick size AGAINST approach direction: if approach up, wick above level
        if approach_direction == "up":
            # We want a BIG upper wick (price went up, got rejected back)
            wick = float(tb.high) - max(float(tb.close), float(tb.open))
        else:
            wick = min(float(tb.close), float(tb.open)) - float(tb.low)
        body_ratio = body / spread
        wick_ratio = wick / spread
        # Pin bar: wick > 60% of range, body < 30%, close on rev side of midrange
        midrange = (float(tb.high) + float(tb.low)) / 2.0
        close_on_rev_side = (rev_dir == 1 and float(tb.close) > midrange) or (
            rev_dir == -1 and float(tb.close) < midrange
        )
        if wick_ratio > 0.6 and body_ratio < 0.3 and close_on_rev_side:
            features[0] = float(min(wick_ratio, 1.0))

    # --- 1. Absorption wall ---
    # Large post-touch volume with minimal price progress = wall absorbing flow.
    if reaction_ticks and len(reaction_ticks) >= 5 and recent_ticks:
        post_vol = sum(float(t.get("size", 1)) for t in reaction_ticks)
        pre_vol_avg = sum(float(t.get("size", 1)) for t in recent_ticks) / len(recent_ticks) if recent_ticks else 1.0
        # Price progress in reaction window (abs)
        rprices = [float(t["price"]) for t in reaction_ticks]
        price_range = max(rprices) - min(rprices)
        # Wall signal: vol spike >2x + price_range < 6 ticks
        vol_ratio = post_vol / max(pre_vol_avg * len(reaction_ticks), 1.0)
        if vol_ratio > 2.0 and price_range < 6 * 0.25:
            features[1] = float(min(vol_ratio / 5.0, 1.0))

    # --- 2. Imbalance cluster ---
    # From orderflow_signals: stacked_imbalance_count >= 3 AND stacked_direction
    # matches REV direction.
    if orderflow_signals is not None:
        sic = int(getattr(orderflow_signals, "stacked_imbalance_count", 0) or 0)
        sdir = getattr(orderflow_signals, "stacked_direction", None)
        rev_wants_buy = rev_dir == 1
        matches = (rev_wants_buy and sdir == "buy") or (not rev_wants_buy and sdir == "sell")
        if sic >= 3 and matches:
            features[2] = float(min(sic / 5.0, 1.0))

    # --- 3. Delta divergence ---
    # Price making new extreme in APPROACH direction, but recent delta weakening.
    # Uses reaction_ticks: last third vs first third delta comparison.
    if reaction_ticks and len(reaction_ticks) >= 12:
        third = len(reaction_ticks) // 3
        first = reaction_ticks[:third]
        last = reaction_ticks[-third:]

        def _delta(tt):
            return sum(float(t.get("size", 1)) * (1 if t.get("side") == "B" else -1) for t in tt)

        first_d = _delta(first) * -rev_dir  # approach-direction delta
        last_d = _delta(last) * -rev_dir
        # Divergence: approach-aligned delta weakening in second half while price
        # still pushing into approach direction.
        first_p = float(first[0]["price"])
        last_p = float(last[-1]["price"])
        price_push = (last_p - first_p) * -rev_dir  # +ve = extended into approach
        if price_push > 0 and last_d < first_d * 0.5:
            features[3] = float(min((first_d - last_d) / max(abs(first_d), 1.0), 1.0))

    # --- 4. Trapped breakout ---
    # Approach broke through level on LOW volume; reaction reverses on HIGH volume.
    # Heuristic: pre-touch 20-tick avg vol vs post-touch 20-tick avg vol.
    if recent_ticks and reaction_ticks and len(recent_ticks) >= 10 and len(reaction_ticks) >= 10:
        pre_sizes = [float(t.get("size", 1)) for t in recent_ticks[-20:]]
        post_sizes = [float(t.get("size", 1)) for t in reaction_ticks[:20]]
        pre_avg = sum(pre_sizes) / max(len(pre_sizes), 1)
        post_avg = sum(post_sizes) / max(len(post_sizes), 1)
        # Trapped: post/pre vol ratio > 1.8 AND post direction opposite of approach
        if pre_avg > 0 and post_avg / pre_avg > 1.8:
            post_prices = [float(t["price"]) for t in reaction_ticks[:20]]
            post_move = (post_prices[-1] - post_prices[0]) * rev_dir
            if post_move > 0:
                features[4] = float(min(post_avg / pre_avg / 4.0, 1.0))

    return features
