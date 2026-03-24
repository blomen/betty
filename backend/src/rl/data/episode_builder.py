"""Episode builder — multi-level trailing reward from forward tick data.

Reward measures how many structural levels price runs through after entry.
If price touches level A and continues through B, C, D — the reward grows
with each level captured. This teaches the model to hold winners, not exit
at fixed targets.

Reward structure:
  Base: velocity score at the touch (immediate reaction quality)
  Trail bonus: +0.5R for each subsequent level reached in the trade direction
  Stop: if price retraces past the initial stop, base velocity score only
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from src.rl.config import (
    Action,
    COST_PER_TRADE_TICKS,
    STOP_TICKS,
    TICK_SIZE,
)

# Time windows (seconds) to measure immediate velocity
_WINDOWS = [10, 30, 60, 120, 300]
_WINDOW_WEIGHTS = [0.35, 0.25, 0.20, 0.12, 0.08]

# Trailing reward params
_TRAIL_BONUS_PER_LEVEL = 0.5  # R bonus per level captured
_MAX_TRAIL_LEVELS = 6         # cap at 6 levels (3.0R max trail bonus)
_TRAIL_TIMEOUT_S = 600        # 10 min max to scan for levels
_STOP_TICKS_TRAIL = 10        # initial stop distance in ticks


@dataclass
class MovementProfile:
    """Captures how price moved after a level touch in one direction."""
    net_ticks: float = 0.0
    max_favorable: float = 0.0
    max_adverse: float = 0.0
    velocity: float = 0.0
    cleanliness: float = 0.0


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
    approach_direction: str
    optimal_stop_ticks: float  # distance to nearest structural level behind trade


def _measure_movement(
    touch_price: float,
    ticks: list[dict],
    start: int,
    end: int,
    touch_ts: datetime,
    direction: int,
) -> list[MovementProfile]:
    """Measure movement quality at each time window."""
    profiles: list[MovementProfile] = []
    window_idx = 0
    max_fav = 0.0
    max_adv = 0.0

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

        while window_idx < len(_WINDOWS) and elapsed_s >= _WINDOWS[window_idx]:
            net = move_ticks
            t = max(1.0, _WINDOWS[window_idx])
            vel = net / math.sqrt(t)
            clean = max_fav / max(max_fav + max_adv, 0.01)
            profiles.append(MovementProfile(
                net_ticks=net, max_favorable=max_fav, max_adverse=max_adv,
                velocity=vel, cleanliness=clean,
            ))
            window_idx += 1

    while len(profiles) < len(_WINDOWS):
        profiles.append(profiles[-1] if profiles else MovementProfile())
    return profiles


def _score_velocity(profiles: list[MovementProfile]) -> float:
    """Score immediate velocity (base reward component)."""
    score = 0.0
    for prof, weight in zip(profiles, _WINDOW_WEIGHTS):
        vel_score = max(-3.0, min(3.0, prof.velocity))
        clean_mult = 0.5 + prof.cleanliness
        score += weight * vel_score * clean_mult
    return score


def _count_levels_captured(
    touch_price: float,
    ticks: list[dict],
    start: int,
    end: int,
    touch_ts: datetime,
    direction: int,
    levels_ahead: list[float],
) -> int:
    """Count how many levels in `levels_ahead` price reaches.

    levels_ahead should be sorted by distance from touch_price in the
    trade direction (nearest first).

    Also enforces a trailing stop: if price retraces past the initial
    stop distance before reaching the next level, stop counting.
    """
    if not levels_ahead:
        return 0

    stop_price = touch_price - direction * _STOP_TICKS_TRAIL * TICK_SIZE
    captured = 0
    next_level_idx = 0
    best_price = touch_price  # track MFE for trailing stop

    timeout = touch_ts + timedelta(seconds=_TRAIL_TIMEOUT_S)

    for j in range(start, end):
        tick = ticks[j]
        if tick["ts"] > timeout:
            break

        price = tick["price"]

        # Update trailing stop: once we capture a level, move stop to that level
        if direction == 1:
            best_price = max(best_price, price)
            # Check if stopped out (price fell below stop)
            if price < stop_price:
                break
        else:
            best_price = min(best_price, price)
            if price > stop_price:
                break

        # Check if we reached the next level
        if next_level_idx < len(levels_ahead):
            target = levels_ahead[next_level_idx]
            if (direction == 1 and price >= target) or (direction == -1 and price <= target):
                captured += 1
                # Move trailing stop to this level (lock in profit)
                stop_price = target - direction * 2 * TICK_SIZE  # 2 ticks behind the level
                next_level_idx += 1
                if captured >= _MAX_TRAIL_LEVELS:
                    break

    return captured


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
    levels_above: list[float] | None = None,
    levels_below: list[float] | None = None,
) -> Episode:
    """Label a level-touch episode with multi-level trailing reward.

    Args:
        levels_above: Structural levels above touch_price, sorted ascending.
        levels_below: Structural levels below touch_price, sorted descending.
    """
    cost_r = COST_PER_TRADE_TICKS / max(STOP_TICKS, 1)

    # Base velocity scores
    long_profiles = _measure_movement(touch_price, ticks, start, end, touch_ts, direction=+1)
    short_profiles = _measure_movement(touch_price, ticks, start, end, touch_ts, direction=-1)

    base_long = _score_velocity(long_profiles)
    base_short = _score_velocity(short_profiles)

    # Trail bonus: count levels captured in each direction
    levels_up = levels_above or []
    levels_dn = levels_below or []

    long_levels = _count_levels_captured(
        touch_price, ticks, start, end, touch_ts, direction=+1, levels_ahead=levels_up,
    )
    short_levels = _count_levels_captured(
        touch_price, ticks, start, end, touch_ts, direction=-1, levels_ahead=levels_dn,
    )

    reward_long = base_long + long_levels * _TRAIL_BONUS_PER_LEVEL - cost_r
    reward_short = base_short + short_levels * _TRAIL_BONUS_PER_LEVEL - cost_r

    reward_cont, reward_rev = _compute_rewards(
        touch_price, approach_direction, reward_long, reward_short,
    )
    reward_skip = 0.0

    max_reward = max(reward_cont, reward_rev, reward_skip)
    if reward_skip == max_reward:
        best_action = Action.SKIP
    elif reward_rev == max_reward:
        best_action = Action.REVERSAL
    else:
        best_action = Action.CONTINUATION

    # Compute optimal stop: nearest structural level behind the best trade direction
    # For CONT (approach=up → long): stop = nearest level below
    # For REV (approach=up → short): stop = nearest level above (behind the short)
    if best_action == Action.SKIP:
        optimal_stop = float(_STOP_TICKS_TRAIL)  # default
    elif best_action == Action.CONTINUATION:
        if approach_direction == "up":
            # Long: stop below → nearest level below
            behind = levels_below or []
        else:
            # Short: stop above → nearest level above
            behind = levels_above or []
        if behind:
            dist = abs(behind[0] - touch_price) / TICK_SIZE
            optimal_stop = max(8.0, min(30.0, dist + 2.0))  # 2 ticks past the level
        else:
            optimal_stop = float(_STOP_TICKS_TRAIL)
    else:  # REVERSAL
        if approach_direction == "up":
            # Short (reversal from up): stop above → nearest level above
            behind = levels_above or []
        else:
            # Long (reversal from down): stop below → nearest level below
            behind = levels_below or []
        if behind:
            dist = abs(behind[0] - touch_price) / TICK_SIZE
            optimal_stop = max(8.0, min(30.0, dist + 2.0))
        else:
            optimal_stop = float(_STOP_TICKS_TRAIL)

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
        optimal_stop_ticks=optimal_stop,
    )


def label_outcome(
    touch_price: float,
    forward_ticks: list[Any],
    observation: np.ndarray,
    level_type: str,
    touch_ts: datetime,
    approach_direction: str = "up",
    levels_above: list[float] | None = None,
    levels_below: list[float] | None = None,
) -> Episode:
    """Label using attribute-style tick objects (.ts, .price)."""
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
        levels_above=levels_above,
        levels_below=levels_below,
    )
