"""Episode builder — labels outcomes from forward tick data for RL training."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from src.rl.config import (
    Action,
    REWARD_STOP_HIT,
    REWARD_TARGET_HIT,
    REWARD_TIMEOUT,
    STOP_TICKS,
    TARGET_TICKS,
    TICK_SIZE,
    TIMEOUT_MINUTES,
)


@dataclass
class Episode:
    observation: np.ndarray
    level_type: str
    touch_price: float
    touch_ts: datetime
    best_action: Action
    reward_long: float
    reward_short: float
    reward_skip: float


def label_outcome(
    touch_price: float,
    forward_ticks: list[Any],
    observation: np.ndarray,
    level_type: str,
    touch_ts: datetime,
) -> Episode:
    """Label a level-touch episode by scanning forward ticks.

    Each tick in forward_ticks must expose:
      - tick.ts  (datetime)
      - tick.price (float) — last traded price or mid

    For LONG:  target = touch_price + TARGET_TICKS * TICK_SIZE
               stop   = touch_price - STOP_TICKS  * TICK_SIZE
    For SHORT: target = touch_price - TARGET_TICKS * TICK_SIZE
               stop   = touch_price + STOP_TICKS  * TICK_SIZE

    Scanning stops when both directions are resolved or timeout is reached.
    """
    long_target = touch_price + TARGET_TICKS * TICK_SIZE
    long_stop = touch_price - STOP_TICKS * TICK_SIZE

    short_target = touch_price - TARGET_TICKS * TICK_SIZE
    short_stop = touch_price + STOP_TICKS * TICK_SIZE

    timeout_delta = timedelta(minutes=TIMEOUT_MINUTES)

    reward_long: float | None = None
    reward_short: float | None = None

    for tick in forward_ticks:
        ts: datetime = tick.ts
        price: float = tick.price

        elapsed = ts - touch_ts
        if elapsed > timeout_delta:
            # Timeout — resolve any still-open directions
            if reward_long is None:
                reward_long = REWARD_TIMEOUT
            if reward_short is None:
                reward_short = REWARD_TIMEOUT
            break

        if reward_long is None:
            if price >= long_target:
                reward_long = REWARD_TARGET_HIT
            elif price <= long_stop:
                reward_long = REWARD_STOP_HIT

        if reward_short is None:
            if price <= short_target:
                reward_short = REWARD_TARGET_HIT
            elif price >= short_stop:
                reward_short = REWARD_STOP_HIT

        if reward_long is not None and reward_short is not None:
            break

    # If we ran out of ticks without resolving, treat as timeout
    if reward_long is None:
        reward_long = REWARD_TIMEOUT
    if reward_short is None:
        reward_short = REWARD_TIMEOUT

    reward_skip = 0.0

    # Best action: highest reward; on a tie SKIP wins (no position = no risk),
    # then SHORT beats LONG arbitrarily.
    best_action: Action
    max_reward = max(reward_long, reward_short, reward_skip)
    if reward_skip == max_reward:
        best_action = Action.SKIP
    elif reward_short == max_reward:
        best_action = Action.SHORT
    else:
        best_action = Action.LONG

    return Episode(
        observation=observation,
        level_type=level_type,
        touch_price=touch_price,
        touch_ts=touch_ts,
        best_action=best_action,
        reward_long=reward_long,
        reward_short=reward_short,
        reward_skip=reward_skip,
    )
