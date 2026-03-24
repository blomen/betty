"""Episode builder — velocity-based labeling from forward tick data.

Instead of binary target/stop outcomes, measures the QUALITY of price
movement after a level touch: how far, how fast, how clean.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from src.rl.config import (
    Action,
    COST_PER_TRADE_TICKS,
    STOP_TICKS,
    TICK_SIZE,
)

# Time windows (seconds) to measure movement quality
_WINDOWS = [10, 30, 60, 120, 300]  # 10s, 30s, 1m, 2m, 5m
_WINDOW_WEIGHTS = [0.35, 0.25, 0.20, 0.12, 0.08]  # weight earlier windows more


@dataclass
class MovementProfile:
    """Captures how price moved after a level touch in one direction."""
    net_ticks: float = 0.0       # net movement in ticks (positive = favorable)
    max_favorable: float = 0.0   # max favorable excursion in ticks
    max_adverse: float = 0.0     # max adverse excursion in ticks
    velocity: float = 0.0        # ticks per sqrt(seconds) — speed-adjusted
    cleanliness: float = 0.0     # favorable / (favorable + adverse) — 0 to 1


@dataclass
class Episode:
    observation: np.ndarray
    level_type: str
    touch_price: float
    touch_ts: datetime
    best_action: Action
    reward_continuation: float
    reward_reversal: float
    reward_skip: float
    approach_direction: str  # "up" or "down"


def _measure_movement(
    touch_price: float,
    ticks: list[dict],
    start: int,
    end: int,
    touch_ts: datetime,
    direction: int,  # +1 for long, -1 for short
) -> list[MovementProfile]:
    """Measure movement quality at each time window.

    Args:
        direction: +1 measures upward movement, -1 measures downward.

    Returns one MovementProfile per window in _WINDOWS.
    """
    profiles: list[MovementProfile] = []
    window_idx = 0

    max_fav = 0.0  # max favorable excursion so far (ticks)
    max_adv = 0.0  # max adverse excursion so far (ticks)

    for j in range(start, end):
        if window_idx >= len(_WINDOWS):
            break

        tick = ticks[j]
        elapsed_s = (tick["ts"] - touch_ts).total_seconds()
        if elapsed_s < 0:
            continue

        move_ticks = (tick["price"] - touch_price) / TICK_SIZE * direction
        max_fav = max(max_fav, move_ticks)
        max_adv = max(max_adv, -move_ticks)

        # Check if we've passed the current window boundary
        while window_idx < len(_WINDOWS) and elapsed_s >= _WINDOWS[window_idx]:
            net = move_ticks
            t = max(1.0, _WINDOWS[window_idx])
            vel = net / math.sqrt(t)
            clean = max_fav / max(max_fav + max_adv, 0.01)

            profiles.append(MovementProfile(
                net_ticks=net,
                max_favorable=max_fav,
                max_adverse=max_adv,
                velocity=vel,
                cleanliness=clean,
            ))
            window_idx += 1

    # Fill remaining windows if we ran out of ticks
    while len(profiles) < len(_WINDOWS):
        if profiles:
            profiles.append(profiles[-1])  # repeat last known state
        else:
            profiles.append(MovementProfile())

    return profiles


def _score_movement(profiles: list[MovementProfile]) -> float:
    """Compute a single reward score from movement profiles across windows.

    Combines velocity and cleanliness at each window, weighted toward
    earlier windows (immediate reaction matters most).

    Returns a score roughly in [-3, +3] range.
    """
    score = 0.0
    for prof, weight in zip(profiles, _WINDOW_WEIGHTS):
        # Velocity component: how fast price moved (ticks/sqrt(s))
        # Clip to prevent extreme outliers
        vel_score = max(-3.0, min(3.0, prof.velocity))

        # Cleanliness bonus: clean moves (low adverse) get a boost
        # 1.0 = perfectly clean, 0.5 = equal, 0.0 = all adverse
        clean_mult = 0.5 + prof.cleanliness  # range [0.5, 1.5]

        score += weight * vel_score * clean_mult

    return score


def _compute_rewards(
    touch_price: float,
    approach_direction: str,
    reward_long: float,
    reward_short: float,
) -> tuple[float, float]:
    """Map long/short rewards to continuation/reversal based on approach."""
    if approach_direction == "up":
        return reward_long, reward_short
    else:
        return reward_short, reward_long


def label_outcome_from_array(
    touch_price: float,
    ticks: list[dict],
    start: int,
    end: int,
    observation: np.ndarray,
    level_type: str,
    touch_ts: datetime,
    approach_direction: str = "up",
) -> Episode:
    """Label a level-touch episode using velocity-based movement scoring.

    Measures movement quality in both directions (long/short) across
    multiple time windows. Reward reflects how strongly and cleanly
    price moved, not just whether it hit a fixed target.
    """
    cost_r = COST_PER_TRADE_TICKS / max(STOP_TICKS, 1)

    # Measure movement in both directions
    long_profiles = _measure_movement(
        touch_price, ticks, start, end, touch_ts, direction=+1
    )
    short_profiles = _measure_movement(
        touch_price, ticks, start, end, touch_ts, direction=-1
    )

    reward_long = _score_movement(long_profiles) - cost_r
    reward_short = _score_movement(short_profiles) - cost_r

    reward_cont, reward_rev = _compute_rewards(
        touch_price, approach_direction, reward_long, reward_short
    )
    reward_skip = 0.0

    max_reward = max(reward_cont, reward_rev, reward_skip)
    if reward_skip == max_reward:
        best_action = Action.SKIP
    elif reward_rev == max_reward:
        best_action = Action.REVERSAL
    else:
        best_action = Action.CONTINUATION

    return Episode(
        observation=observation,
        level_type=level_type,
        touch_price=touch_price,
        touch_ts=touch_ts,
        best_action=best_action,
        reward_continuation=reward_cont,
        reward_reversal=reward_rev,
        reward_skip=reward_skip,
        approach_direction=approach_direction,
    )


# Keep attribute-style version for live inference compatibility
def label_outcome(
    touch_price: float,
    forward_ticks: list[Any],
    observation: np.ndarray,
    level_type: str,
    touch_ts: datetime,
    approach_direction: str = "up",
) -> Episode:
    """Label using attribute-style tick objects (.ts, .price)."""
    # Convert to dict format
    tick_dicts = [{"ts": t.ts, "price": t.price} for t in forward_ticks]
    return label_outcome_from_array(
        touch_price=touch_price,
        ticks=tick_dicts,
        start=0,
        end=len(tick_dicts),
        observation=observation,
        level_type=level_type,
        touch_ts=touch_ts,
        approach_direction=approach_direction,
    )
