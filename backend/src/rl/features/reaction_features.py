"""Post-touch reaction features (Phase 3a).

Captures HOW THE MARKET REACTED to a level touch, not how price arrived.
Replaces the implicit assumption that "approach momentum" predicts reaction.

The framework (Fabio + Dalton) is explicit: take a trade when the market's
orderflow REACTION at the level confirms the setup. This module measures
that reaction directly.

Inputs are post-touch ticks (typically 50 ticks ≈ 5-10s of NQ). In live
inference, the session_manager will wait N ticks after touch, then rebuild
state and run inference — so the reaction window is observable.

Features (8 dims, all ∈ [-1, 1] or [0, 1]):
  0  reaction_velocity       — signed net ticks moved / stop_ticks within window
  1  reaction_aggression     — (buy_vol - sell_vol) / total_vol, signed
  2  rejection_speed         — how quickly price reversed vs approach direction
  3  vol_spike_ratio         — post-touch vol / pre-touch 50-tick avg
  4  tape_compression_post   — post-touch ticks/sec vs recent baseline
  5  delta_alignment_with_dir — reaction delta aligned with REV direction
  6  opposing_momentum_build — momentum against trade direction growing
  7  reaction_linearity      — reaction move R² (clean move vs chop)
"""

from __future__ import annotations

import numpy as np

_N_FEATURES = 8


def extract_reaction_features(
    reaction_ticks: list | None,
    recent_ticks: list | None,
    touch_price: float,
    approach_direction: str,
    stop_ticks: float = 20.0,
) -> np.ndarray:
    """Compute 8-dim post-touch reaction feature vector.

    Args:
        reaction_ticks: ticks FROM touch forward (~50 ticks ≈ 5-10s). Empty = zeros.
        recent_ticks: ticks BEFORE touch (for baseline comparison).
        touch_price: the price at which the zone was touched.
        approach_direction: "up" (approach came from below) or "down".
        stop_ticks: reference stop distance, used to normalize velocity.

    REV direction: short if approach=up, long if approach=down.
    All features signed relative to the REV trade direction (positive = good).
    """
    if not reaction_ticks or len(reaction_ticks) < 2:
        return np.zeros(_N_FEATURES, dtype=np.float32)

    # REV trade direction: +1 = long (we want price UP), -1 = short
    rev_dir = -1 if approach_direction == "up" else 1
    tick_size = 0.25

    # Extract arrays
    prices = np.array([float(t["price"]) for t in reaction_ticks], dtype=np.float64)
    sizes = np.array([float(t.get("size", 1)) for t in reaction_ticks], dtype=np.float64)
    sides = [t.get("side", "B") for t in reaction_ticks]
    ts = [t["ts"] for t in reaction_ticks]

    total_vol = float(sizes.sum())
    buy_vol = float(sizes[[s == "B" for s in sides]].sum())
    sell_vol = total_vol - buy_vol

    # 0. Reaction velocity — net ticks moved in REV direction / stop_ticks
    net_ticks = (prices[-1] - touch_price) / tick_size * rev_dir
    reaction_velocity = float(np.clip(net_ticks / max(stop_ticks, 1.0), -2.0, 2.0)) / 2.0

    # 1. Reaction aggression — buy/sell delta ratio, signed for REV direction
    delta = buy_vol - sell_vol
    aggression_raw = delta / max(total_vol, 1.0)
    reaction_aggression = float(np.clip(aggression_raw * rev_dir, -1.0, 1.0))

    # 2. Rejection speed — price reversed against approach quickly?
    # Measure: at what tick did price first move stop_ticks/4 in rev_dir?
    threshold = stop_ticks / 4.0 * tick_size
    rejection_speed = 0.0
    for j, p in enumerate(prices):
        move = (p - touch_price) * rev_dir
        if move >= threshold:
            rejection_speed = float(1.0 - j / max(len(prices), 1))
            break

    # 3. Vol spike ratio — post-touch activity vs pre-touch baseline
    if recent_ticks and len(recent_ticks) > 5:
        pre_sizes = np.array([float(t.get("size", 1)) for t in recent_ticks], dtype=np.float64)
        pre_avg = pre_sizes.mean()
        if pre_avg > 0:
            post_avg = sizes.mean()
            vol_spike = float(np.clip(post_avg / pre_avg, 0.0, 5.0) / 5.0)
        else:
            vol_spike = 0.0
    else:
        vol_spike = 0.0

    # 4. Tape compression — post-touch ticks/sec vs pre-touch
    try:
        post_elapsed = max((ts[-1] - ts[0]).total_seconds(), 0.01)
        post_rate = len(reaction_ticks) / post_elapsed
        if recent_ticks and len(recent_ticks) > 5:
            pre_elapsed = max((recent_ticks[-1]["ts"] - recent_ticks[0]["ts"]).total_seconds(), 0.01)
            pre_rate = len(recent_ticks) / pre_elapsed
            tape_compression = float(np.clip(post_rate / max(pre_rate, 1e-3), 0.0, 4.0) / 4.0)
        else:
            tape_compression = 0.0
    except Exception:
        tape_compression = 0.0

    # 5. Delta alignment — cumulative delta in REV direction
    cum_delta = np.cumsum([1 if s == "B" else -1 for s in sides] * sizes)
    delta_alignment = float(np.clip(cum_delta[-1] * rev_dir / max(total_vol, 1.0), -1.0, 1.0))

    # 6. Opposing momentum build — is price moving AGAINST rev_dir in second half?
    half = max(1, len(prices) // 2)
    second_half_move = (prices[-1] - prices[half]) * rev_dir
    opposing_momentum = float(np.clip(-second_half_move / (tick_size * 10.0), -1.0, 1.0))

    # 7. Reaction linearity — R² of price over time in REV direction
    if len(prices) >= 5:
        x = np.arange(len(prices), dtype=np.float64)
        y = prices * rev_dir
        try:
            slope, intercept = np.polyfit(x, y, 1)
            pred = slope * x + intercept
            ss_res = float(((y - pred) ** 2).sum())
            ss_tot = float(((y - y.mean()) ** 2).sum())
            linearity = 1.0 - ss_res / max(ss_tot, 1e-6) if slope > 0 else 0.0
            reaction_linearity = float(np.clip(linearity, 0.0, 1.0))
        except Exception:
            reaction_linearity = 0.0
    else:
        reaction_linearity = 0.0

    return np.array(
        [
            reaction_velocity,
            reaction_aggression,
            rejection_speed,
            vol_spike,
            tape_compression,
            delta_alignment,
            opposing_momentum,
            reaction_linearity,
        ],
        dtype=np.float32,
    )
