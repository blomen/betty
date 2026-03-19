"""Tests for rl.data.episode_builder — outcome labeling from forward ticks."""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

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
from src.rl.data.episode_builder import Episode, label_outcome


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_TS = datetime(2024, 1, 15, 9, 30, 0)
BASE_PRICE = 18_000.0
OBS = np.zeros(8, dtype=np.float32)
LEVEL = "poc_session"


def _ticks_sequence(
    start_price: float,
    offsets: list[float],
    interval_seconds: int = 1,
    start_ts: datetime | None = None,
) -> list[SimpleNamespace]:
    """Build a list of tick-like objects at `interval_seconds` intervals.

    Each offset in `offsets` is ADDED to start_price to get the tick price,
    mimicking a price path.
    """
    ts = start_ts or BASE_TS
    ticks = []
    for i, offset in enumerate(offsets):
        tick_ts = ts + timedelta(seconds=i * interval_seconds)
        ticks.append(SimpleNamespace(ts=tick_ts, price=start_price + offset))
    return ticks


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_long_winner() -> None:
    """Price moves up TARGET_TICKS*TICK_SIZE (5.0 pts) → LONG wins, SHORT stop hit."""
    target_move = TARGET_TICKS * TICK_SIZE  # +5.0
    stop_move = STOP_TICKS * TICK_SIZE  # +2.5 (SHORT stop is +2.5 above entry)

    # Price climbs steadily past the SHORT stop (+2.5) then reaches LONG target (+5.0)
    offsets = [0.25 * i for i in range(1, TARGET_TICKS + 2)]
    ticks = _ticks_sequence(BASE_PRICE, offsets)

    ep = label_outcome(BASE_PRICE, ticks, OBS, LEVEL, BASE_TS)

    assert ep.reward_long == REWARD_TARGET_HIT, "LONG should hit target"
    assert ep.reward_short == REWARD_STOP_HIT, "SHORT stop should be hit as price rises"
    assert ep.best_action == Action.LONG


def test_short_winner() -> None:
    """Price moves down TARGET_TICKS*TICK_SIZE (5.0 pts) → SHORT wins, LONG stop hit."""
    # Price drops steadily, past LONG stop (-2.5) then reaches SHORT target (-5.0)
    offsets = [-0.25 * i for i in range(1, TARGET_TICKS + 2)]
    ticks = _ticks_sequence(BASE_PRICE, offsets)

    ep = label_outcome(BASE_PRICE, ticks, OBS, LEVEL, BASE_TS)

    assert ep.reward_short == REWARD_TARGET_HIT, "SHORT should hit target"
    assert ep.reward_long == REWARD_STOP_HIT, "LONG stop should be hit as price falls"
    assert ep.best_action == Action.SHORT


def test_timeout_skip() -> None:
    """Price stays flat within ±1 pt for >30 min → both timeout (0.0), SKIP is best."""
    # Generate ticks every 60 seconds for 35 minutes — tiny oscillation ±0.25
    n_ticks = 36  # 0..35 minutes
    offsets = [0.25 * (1 if i % 2 == 0 else -1) for i in range(n_ticks)]
    ticks = _ticks_sequence(BASE_PRICE, offsets, interval_seconds=60)

    ep = label_outcome(BASE_PRICE, ticks, OBS, LEVEL, BASE_TS)

    assert ep.reward_long == REWARD_TIMEOUT
    assert ep.reward_short == REWARD_TIMEOUT
    assert ep.reward_skip == 0.0
    assert ep.best_action == Action.SKIP


def test_long_stop_hit() -> None:
    """Price drops STOP_TICKS*TICK_SIZE (2.5 pts) then stays flat.

    LONG stop hit (-1.0).  SHORT doesn't reach its target either → timeout (0.0).
    Best action = SKIP (0.0 > -1.0 for LONG; SHORT 0.0 ties SKIP but SKIP wins tie).
    """
    # Drop to stop level quickly, then oscillate near there for the rest of 30 min
    stop_drop = STOP_TICKS * TICK_SIZE  # 2.5

    # First tick hits LONG stop exactly
    drop_ticks = [SimpleNamespace(ts=BASE_TS + timedelta(seconds=1), price=BASE_PRICE - stop_drop)]

    # Remaining ticks stay flat near that level for >30 min
    flat_count = 35
    flat_ticks = [
        SimpleNamespace(
            ts=BASE_TS + timedelta(minutes=1) + timedelta(minutes=i),
            price=BASE_PRICE - stop_drop + 0.25,  # slightly above stop, won't hit SHORT target
        )
        for i in range(flat_count)
    ]

    ticks = drop_ticks + flat_ticks

    ep = label_outcome(BASE_PRICE, ticks, OBS, LEVEL, BASE_TS)

    assert ep.reward_long == REWARD_STOP_HIT, "LONG stop should be hit"
    assert ep.reward_short == REWARD_TIMEOUT, "SHORT should time out (never reaches -5.0)"
    # best: SKIP (0.0) > SHORT (0.0) ties but SKIP wins; SHORT > LONG (-1.0)
    assert ep.best_action == Action.SKIP


def test_episode_has_all_fields() -> None:
    """Episode dataclass must expose all required fields with correct types."""
    ticks = _ticks_sequence(BASE_PRICE, [0.0] * 5)
    ep = label_outcome(BASE_PRICE, ticks, OBS, LEVEL, BASE_TS)

    field_names = {f.name for f in fields(ep)}
    required = {
        "observation",
        "level_type",
        "touch_price",
        "touch_ts",
        "best_action",
        "reward_long",
        "reward_short",
        "reward_skip",
    }
    assert required == field_names

    assert isinstance(ep.observation, np.ndarray)
    assert isinstance(ep.level_type, str)
    assert isinstance(ep.touch_price, float)
    assert isinstance(ep.touch_ts, datetime)
    assert isinstance(ep.best_action, Action)
    assert isinstance(ep.reward_long, float)
    assert isinstance(ep.reward_short, float)
    assert isinstance(ep.reward_skip, float)
    assert ep.reward_skip == 0.0
