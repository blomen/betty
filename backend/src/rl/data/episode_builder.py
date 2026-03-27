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
    optimal_stop_ticks: float  # MAE-optimal initial stop distance
    breakeven_reached: bool = False  # did price reach 1R before retracing?
    levels_captured_best: int = 0  # levels captured by best action with full lifecycle
    state: dict | None = None  # original state dict (for backtest/session manager)


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


def _measure_mae(
    touch_price: float,
    ticks: list[dict],
    start: int,
    end: int,
    touch_ts: datetime,
    direction: int,
) -> float:
    """Measure Maximum Adverse Excursion before MFE is reached.

    Scans forward ticks in the trade direction. Tracks:
    - MFE: furthest favorable point
    - MAE: worst adverse excursion BEFORE reaching MFE

    Returns MAE in ticks. This is the "breathing room" the trade needs —
    how far price moves against you before moving in your favor.
    """
    max_favorable = 0.0
    max_adverse_before_mfe = 0.0
    current_adverse = 0.0
    mfe_price = touch_price

    timeout = touch_ts + timedelta(seconds=300)  # 5 min window
    scan_end = min(end, start + 60_000)  # ~10 seconds of NQ

    for j in range(start, scan_end):
        tick = ticks[j]
        if tick["ts"] > timeout:
            break

        price = tick["price"]
        move_ticks = direction * (price - touch_price) / TICK_SIZE

        if move_ticks > max_favorable:
            # New MFE — record the MAE we saw getting here
            max_favorable = move_ticks
            max_adverse_before_mfe = max(max_adverse_before_mfe, current_adverse)
            mfe_price = price
            current_adverse = 0.0
        elif move_ticks < 0:
            current_adverse = max(current_adverse, abs(move_ticks))

    return max_adverse_before_mfe


def _count_levels_captured(
    touch_price: float,
    ticks: list[dict],
    start: int,
    end: int,
    touch_ts: datetime,
    direction: int,
    levels_ahead: list[float],
) -> int:
    """Count levels captured with full stop lifecycle: initial → breakeven → trail.

    Stop lifecycle:
    1. INITIAL: stop at `initial_stop_ticks` behind entry
    2. BREAKEVEN: once price moves 1R in favor, stop moves to entry (risk-free)
    3. TRAIL: each new level captured → stop moves to that level minus 2 ticks

    Returns (levels_captured, exit_pnl_ticks) tuple.
    """
    if not levels_ahead:
        return 0

    initial_stop_ticks = _STOP_TICKS_TRAIL
    stop_price = touch_price - direction * initial_stop_ticks * TICK_SIZE
    breakeven_trigger = touch_price + direction * initial_stop_ticks * TICK_SIZE  # 1R move
    at_breakeven = False
    captured = 0
    next_level_idx = 0

    timeout = touch_ts + timedelta(seconds=_TRAIL_TIMEOUT_S)

    for j in range(start, end):
        tick = ticks[j]
        if tick["ts"] > timeout:
            break

        price = tick["price"]

        # Phase 1→2: Move to breakeven once price reaches 1R in favor
        if not at_breakeven:
            if direction == 1 and price >= breakeven_trigger:
                stop_price = touch_price + direction * 1 * TICK_SIZE  # 1 tick above entry
                at_breakeven = True
            elif direction == -1 and price <= breakeven_trigger:
                stop_price = touch_price - direction * 1 * TICK_SIZE
                at_breakeven = True

        # Check stop hit
        if direction == 1 and price <= stop_price:
            break
        elif direction == -1 and price >= stop_price:
            break

        # Phase 2→3: Check if we captured a new level → trail stop there
        if next_level_idx < len(levels_ahead):
            target = levels_ahead[next_level_idx]
            if (direction == 1 and price >= target) or (direction == -1 and price <= target):
                captured += 1
                # Trail stop to this level minus 2 ticks (lock profit at level)
                stop_price = target - direction * 2 * TICK_SIZE
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

    # Measure breathing room (MAE) for each direction
    # MAE = max adverse ticks BEFORE price reaches its MFE
    # This tells the model how much room the trade needs to work
    long_mae = _measure_mae(touch_price, ticks, start, end, touch_ts, direction=+1)
    short_mae = _measure_mae(touch_price, ticks, start, end, touch_ts, direction=-1)

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

    # Compute optimal stop via MAE (Maximum Adverse Excursion) analysis.
    # For the best direction, scan forward and find the stop distance that
    # maximizes realized P&L: tight enough to limit losses, wide enough to
    # not get clipped by noise before the move develops.
    #
    # Method: test stop distances 6-40 ticks and pick the one that gives
    # the best R-multiple on this specific episode.
    be_reached = False
    if best_action == Action.SKIP:
        optimal_stop = float(_STOP_TICKS_TRAIL)
    else:
        if best_action == Action.CONTINUATION:
            direction = 1 if approach_direction == "up" else -1
        else:  # REVERSAL
            direction = -1 if approach_direction == "up" else 1

        # Structural stop: nearest level behind the trade + 2 ticks buffer
        if best_action == Action.CONTINUATION:
            behind = (levels_below or []) if approach_direction == "up" else (levels_above or [])
        else:
            behind = (levels_above or []) if approach_direction == "up" else (levels_below or [])

        if behind:
            struct_dist = abs(behind[0] - touch_price) / TICK_SIZE + 2.0
            optimal_stop = max(6.0, min(40.0, struct_dist))
        else:
            optimal_stop = float(_STOP_TICKS_TRAIL)

        # Use MAE as a floor — stop must be wider than breathing room
        mae = long_mae if direction == 1 else short_mae
        if mae > 0:
            mae_floor = mae + 2.0
            optimal_stop = max(optimal_stop, mae_floor)
            optimal_stop = min(40.0, optimal_stop)

    # Best direction stats
    if best_action == Action.CONTINUATION:
        best_dir = 1 if approach_direction == "up" else -1
        best_levels = long_levels if best_dir == 1 else short_levels
        best_be = be_reached if best_action != Action.SKIP else False
    elif best_action == Action.REVERSAL:
        best_dir = -1 if approach_direction == "up" else 1
        best_levels = short_levels if best_dir == -1 else long_levels
        best_be = be_reached if best_action != Action.SKIP else False
    else:
        best_levels = 0
        best_be = False

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
        breakeven_reached=best_be,
        levels_captured_best=best_levels,
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
