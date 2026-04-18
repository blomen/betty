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
    COST_PER_TRADE_TICKS,
    TICK_SIZE,
    Action,
)

# Time windows (seconds) to measure immediate velocity
_WINDOWS = [10, 30, 60, 120, 300]
_WINDOW_WEIGHTS = [0.35, 0.25, 0.20, 0.12, 0.08]

# Trailing reward params
_TRAIL_BONUS_PER_LEVEL = 0.5  # R bonus per level captured
_MAX_TRAIL_LEVELS = 6  # cap at 6 levels (3.0R max trail bonus)
_TRAIL_TIMEOUT_S = 1200  # 20 min max to scan for levels (was 10 min — missed slow moves)
_STOP_TICKS_TRAIL = 20  # initial stop distance in ticks (was 10 — too tight, got stopped before moves)
_BE_TRIGGER_R = 1.0  # price must move this many R before stop moves to +0.5R
# At 1R: stop moves to entry + 0.5R (not breakeven). This locks $36 profit
# per contract after fees ($14 RT cost). No winner turns into a loser.
# With 20-tick stop: 1R trigger = 20 ticks (5 pts), stop moves to +10 ticks (2.5 pts).
_BE_LOCK_R = 0.5  # R-multiple to lock when BE trigger fires (profit lock, not just breakeven)


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
            profiles.append(
                MovementProfile(
                    net_ticks=net,
                    max_favorable=max_fav,
                    max_adverse=max_adv,
                    velocity=vel,
                    cleanliness=clean,
                )
            )
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
    be_trigger_r: float = _BE_TRIGGER_R,
) -> tuple[int, bool]:
    """DEPRECATED wrapper — kept for live_collector.py and analyze-be CLI.

    New training uses _simulate_trade_lifecycle directly (full lifecycle
    simulation with uncapped upside). This wrapper just returns the
    levels_captured + breakeven_reached fields the old API exposed.

    The touch_ts and be_trigger_r params are IGNORED — the new simulator
    does not do BE-lock (that's handled by the live session manager now).
    """
    _, captured, be_reached, _ = _simulate_trade_lifecycle(
        touch_price, ticks, start, end, direction=direction, levels_ahead=levels_ahead
    )
    return captured, be_reached


def _simulate_trade_lifecycle(
    touch_price: float,
    ticks: list[dict],
    start: int,
    end: int,
    direction: int,
    levels_ahead: list[float],
    stop_ticks: float = _STOP_TICKS_TRAIL,
) -> tuple[float, int, bool, str]:
    """Simulate a full trade lifecycle: fixed initial stop + structural trail.

    Lifecycle:
    1. Entry at touch_price, stop at -stop_ticks behind entry.
    2. Each time price touches a structural level ahead of entry → trail stop
       to that level minus 2 ticks (lock gains at each structural support).
    3. Exit on stop-hit OR end of forward-tick window (session close).

    No BE-lock, no upper cap on captured levels, no timeout. The live broker
    stop (-1R) bounds downside at stop-out; structural trail keeps upside
    uncapped so the model can learn the asymmetric-payoff distribution the
    user actually wants: lose small, catch tails.

    Returns (realized_r, levels_captured, breakeven_reached, exit_reason):
      - realized_r: signed R-multiple of the final exit (≥ -1, unbounded upside
        minus cost/dd applied by caller).
      - levels_captured: number of structural levels crossed before exit.
      - breakeven_reached: true if price reached +1R in favor at any point.
      - exit_reason: 'stop', 'session_close', 'no_ticks'.
    """
    if end <= start:
        return 0.0, 0, False, "no_ticks"

    stop_distance = stop_ticks * TICK_SIZE
    stop_price = touch_price - direction * stop_distance
    captured = 0
    level_idx = 0
    be_reached = False
    be_target = touch_price + direction * stop_distance  # +1R
    exit_price = touch_price
    exit_reason = "session_close"

    for j in range(start, end):
        tick = ticks[j]
        price = tick["price"]

        # Track BE-reached for analytics (not for reward modification)
        if not be_reached:
            if (direction == 1 and price >= be_target) or (direction == -1 and price <= be_target):
                be_reached = True

        # Stop hit?
        if (direction == 1 and price <= stop_price) or (direction == -1 and price >= stop_price):
            exit_price = stop_price
            exit_reason = "stop"
            break

        # Level captured → trail stop (no cap on how many)
        while level_idx < len(levels_ahead):
            target = levels_ahead[level_idx]
            if (direction == 1 and price >= target) or (direction == -1 and price <= target):
                captured += 1
                stop_price = target - direction * 2 * TICK_SIZE
                level_idx += 1
            else:
                break
    else:
        # Reached end of tick window without a stop — exit at last price
        exit_price = float(ticks[end - 1]["price"])

    realized_ticks = direction * (exit_price - touch_price) / TICK_SIZE
    realized_r = realized_ticks / max(stop_ticks, 1.0)
    return realized_r, captured, be_reached, exit_reason


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
    be_trigger_r: float = _BE_TRIGGER_R,
) -> Episode:
    """Label a level-touch episode with multi-level trailing reward.

    Args:
        levels_above: Structural levels above touch_price, sorted ascending.
        levels_below: Structural levels below touch_price, sorted descending.
        be_trigger_r: R-multiple at which stop moves to breakeven (default _BE_TRIGGER_R).
            Pass different values to sweep the optimal threshold via analyze-be.
    """
    # Round-trip cost in R units (tied to the trail-stop basis).
    cost_r = COST_PER_TRADE_TICKS / max(_STOP_TICKS_TRAIL, 1)

    # Full lifecycle simulation — uncapped upside, trail-stop enforces -1R floor.
    # This replaces the old fragmented reward (velocity + 0.5R × min(levels, 6))
    # which capped tails at +6R and artificially locked BE at +0.5R. The user's
    # thesis is asymmetric payoffs — lose small (-1R hard floor), catch tails
    # unbounded on the upside. Training reward must expose the true right tail
    # of the R distribution; previously clipped to +6R, losing every runner.
    levels_up = levels_above or []
    levels_dn = levels_below or []

    long_r, long_levels, long_be, long_exit = _simulate_trade_lifecycle(
        touch_price, ticks, start, end, direction=+1, levels_ahead=levels_up
    )
    short_r, short_levels, short_be, short_exit = _simulate_trade_lifecycle(
        touch_price, ticks, start, end, direction=-1, levels_ahead=levels_dn
    )

    # MAE still useful for optimal-stop computation further below.
    long_mae = _measure_mae(touch_price, ticks, start, end, touch_ts, direction=+1)
    short_mae = _measure_mae(touch_price, ticks, start, end, touch_ts, direction=-1)

    # Drawdown penalty — kept as a soft penalty for choppy paths. Even a trade
    # that ends +3R but took -0.9R of heat is worse than a clean +3R. 0.15 weight.
    _DD_LAMBDA = 0.15
    long_dd_penalty = _DD_LAMBDA * max(0.0, long_mae / max(_STOP_TICKS_TRAIL, 1))
    short_dd_penalty = _DD_LAMBDA * max(0.0, short_mae / max(_STOP_TICKS_TRAIL, 1))

    reward_long = long_r - cost_r - long_dd_penalty
    reward_short = short_r - cost_r - short_dd_penalty

    # Only a DOWNSIDE floor at -1R (matches live broker stop). UPSIDE IS UNCAPPED.
    # The lifecycle simulator already enforces -1R via the trail stop exit — this
    # floor just guards against cost+dd pushing a stopped-out trade below -1R.
    _REWARD_LIVE_MIN = -1.0
    reward_long = float(max(_REWARD_LIVE_MIN, reward_long))
    reward_short = float(max(_REWARD_LIVE_MIN, reward_short))

    reward_cont, reward_rev = _compute_rewards(
        touch_price,
        approach_direction,
        reward_long,
        reward_short,
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
        else:
            struct_dist = float(_STOP_TICKS_TRAIL)

        # Blend structural distance with MAE rather than hard-clamping. Previous
        # approach produced a bimodal stop_target distribution (50% at floor 6,
        # 45% at cap 40) which left the stop head with almost no gradient.
        # Blend gives a smoother target distribution the GBT can learn.
        mae = long_mae if direction == 1 else short_mae
        if mae > 0:
            mae_floor = mae + 2.0
            # Weighted average: structural is primary anchor, MAE softens it
            optimal_stop = 0.7 * max(struct_dist, mae_floor) + 0.3 * min(struct_dist, mae_floor)
        else:
            optimal_stop = struct_dist
        optimal_stop = float(max(4.0, min(50.0, optimal_stop)))

    # Best direction stats — use breakeven from _count_levels_captured
    if best_action == Action.CONTINUATION:
        best_dir = 1 if approach_direction == "up" else -1
        best_levels = long_levels if best_dir == 1 else short_levels
        best_be = long_be if best_dir == 1 else short_be
    elif best_action == Action.REVERSAL:
        best_dir = -1 if approach_direction == "up" else 1
        best_levels = short_levels if best_dir == -1 else long_levels
        best_be = short_be if best_dir == -1 else long_be
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
