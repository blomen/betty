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
_MAX_TRAIL_LEVELS = 2  # 2026-05-16: lowered from 10 — backtest-vs-live audit
# showed labels at +1.09R/trade mean while live delivered -0.024R/trade (45x
# over-prediction). Multi-level trail up to 10 levels was the dominant
# contributor: it modeled an ideal Phase-2 ride that live only reaches 6.5%
# of the time. 2 levels caps trail bonus at +1.0R, matching realistic
# post-BE-lock behavior. Combined with the _REWARD_LIVE_MAX cap below,
# label distribution should compress toward live realized R distribution.
_TRAIL_TIMEOUT_S = 1200  # 20 min max to scan for levels (was 10 min — missed slow moves)
_STOP_TICKS_TRAIL = 20  # initial stop distance in ticks (was 10 — too tight, got stopped before moves)
_BE_TRIGGER_R = 1.5  # price must move this many R before stop moves to +0.5R.
# Aligned 2026-05-16 with live broker's PHASE_2_THRESHOLD_R = 1.5 (see
# CLAUDE.md "Phase 1 sacred bracket"). Previously 1.0R, which let labels
# lock profit at a point the live policy never even checks for BE-lock.
# Live BE-lock fires at 1.5R; the live policy can never reach profit-lock
# earlier than that, so a 1.0R training trigger handed out wins the model
# could never realize in production.
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
    peak_R_cont: float = 0.0  # Max favorable excursion in R units, continuation side
    peak_R_rev: float = 0.0  # Max favorable excursion in R units, reversal side
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
    for prof, weight in zip(profiles, _WINDOW_WEIGHTS, strict=False):
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
    initial_stop_ticks: float = _STOP_TICKS_TRAIL,
) -> tuple[int, bool]:
    """Count levels captured with full stop lifecycle: initial → profit lock → trail.

    Stop lifecycle:
    1. INITIAL: stop at `initial_stop_ticks` behind entry (was fixed 20 ticks;
       now variable per-touch to simulate Tier 1 stop_policy's
       confidence/regime/structural-anchor scaling)
    2. PROFIT LOCK: at be_trigger_r (1R), stop moves to entry + _BE_LOCK_R (0.5R)
       This locks a small profit — no winner turns into a loser
    3. TRAIL: each new level captured → stop moves to that level minus 2 ticks

    Returns (levels_captured, profit_locked) tuple.
    """
    stop_price = touch_price - direction * initial_stop_ticks * TICK_SIZE
    profit_trigger = touch_price + direction * initial_stop_ticks * TICK_SIZE * be_trigger_r
    profit_locked = False
    captured = 0
    next_level_idx = 0

    timeout = touch_ts + timedelta(seconds=_TRAIL_TIMEOUT_S)

    for j in range(start, end):
        tick = ticks[j]
        if tick["ts"] > timeout:
            break

        price = tick["price"]

        # Phase 1→2: Lock profit once price reaches trigger R in favor
        if not profit_locked:
            if direction == 1 and price >= profit_trigger:
                # Move stop to entry + 0.5R (lock small profit, cover fees)
                lock_distance = initial_stop_ticks * TICK_SIZE * _BE_LOCK_R
                stop_price = touch_price + direction * lock_distance
                profit_locked = True
            elif direction == -1 and price <= profit_trigger:
                lock_distance = initial_stop_ticks * TICK_SIZE * _BE_LOCK_R
                stop_price = touch_price + direction * lock_distance
                profit_locked = True

        # Check stop hit
        if direction == 1 and price <= stop_price or direction == -1 and price >= stop_price:
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

    return captured, profit_locked


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
    # === TIER 1+2-AWARE REWARDS (2026-04-23) ===
    # Training now simulates the live policy stack so the model's rewards
    # reflect what actually happens in production:
    #   - stop_policy: variable per-touch stop [6-50 ticks] instead of fixed 20
    #   - pyramid: compound bonus on strong winners (Tier 2 adds at +0.3R)
    #   - EE lock: cap losses at +0.5R when pump-retrace would have locked
    # The legacy _STOP_TICKS_TRAIL is still used for the COST basis (fees as
    # a fraction of R) since fee $$ is stop-independent. We compute a
    # simulated pre-trade stop per direction below for trail/DD accounting.

    # Pre-trade stop estimate — mirrors live stop_policy's structural-anchor +
    # ATR + OF-score blend, WITHOUT using MAE (which is post-facto). This keeps
    # labels honest: the model sees stops it could realistically have, not
    # hindsight-optimal ones.
    try:
        atr_ticks_pre = max(
            6.0, float(observation[273]) * 100.0 if observation is not None and len(observation) > 273 else 30.0
        )
    except (IndexError, TypeError, ValueError):
        atr_ticks_pre = 30.0
    try:
        of_score_pre = float(observation[282]) if observation is not None and len(observation) > 282 else 0.5
    except (IndexError, TypeError, ValueError):
        of_score_pre = 0.5

    def _struct_stop(direction_sign: int) -> float:
        """Nearest structural level behind, in ticks + 2-tick buffer. Pre-trade."""
        if direction_sign == 1:
            behind = levels_below or []
        else:
            behind = levels_above or []
        if not behind:
            return float(_STOP_TICKS_TRAIL)
        return abs(behind[0] - touch_price) / TICK_SIZE + 2.0

    long_struct = _struct_stop(1)
    short_struct = _struct_stop(-1)
    # Weighted blend: 60% structural + 40% ATR; tighter on strong OF, wider on weak OF.
    of_factor = 1.2 - 0.4 * of_score_pre  # [0.8, 1.2]
    long_stop_ticks = max(6.0, min(50.0, (0.6 * long_struct + 0.4 * atr_ticks_pre) * of_factor))
    short_stop_ticks = max(6.0, min(50.0, (0.6 * short_struct + 0.4 * atr_ticks_pre) * of_factor))

    # Cost_r uses the fixed-20 basis — fees in R are stop-invariant in dollars
    # but grow as R gets larger (tighter stop = cost more painful). Keep stable.
    cost_r = COST_PER_TRADE_TICKS / max(_STOP_TICKS_TRAIL, 1)

    # Base velocity scores
    long_profiles = _measure_movement(touch_price, ticks, start, end, touch_ts, direction=+1)
    short_profiles = _measure_movement(touch_price, ticks, start, end, touch_ts, direction=-1)

    base_long = _score_velocity(long_profiles)
    base_short = _score_velocity(short_profiles)

    # Trail bonus: count levels captured in each direction WITH variable stop.
    levels_up = levels_above or []
    levels_dn = levels_below or []

    long_levels, long_be = _count_levels_captured(
        touch_price,
        ticks,
        start,
        end,
        touch_ts,
        direction=+1,
        levels_ahead=levels_up,
        be_trigger_r=be_trigger_r,
        initial_stop_ticks=long_stop_ticks,
    )
    short_levels, short_be = _count_levels_captured(
        touch_price,
        ticks,
        start,
        end,
        touch_ts,
        direction=-1,
        levels_ahead=levels_dn,
        be_trigger_r=be_trigger_r,
        initial_stop_ticks=short_stop_ticks,
    )

    # Measure breathing room (MAE) for each direction
    long_mae = _measure_mae(touch_price, ticks, start, end, touch_ts, direction=+1)
    short_mae = _measure_mae(touch_price, ticks, start, end, touch_ts, direction=-1)

    # Drawdown penalty normalised by the SIMULATED stop, not fixed _STOP_TICKS_TRAIL.
    _DD_LAMBDA = 0.15
    long_dd_penalty = _DD_LAMBDA * max(0.0, long_mae / max(long_stop_ticks, 1))
    short_dd_penalty = _DD_LAMBDA * max(0.0, short_mae / max(short_stop_ticks, 1))

    # Entry slippage cost. 2026-05-16 live audit measured mean adverse
    # entry slippage of -5 to -14 ticks on stopped trades; conservative
    # 5-tick average for ALL trades models the upfront cost of getting
    # filled at a worse price than signal. At _STOP_TICKS_TRAIL=20 this
    # is ~0.25R per trade — winners win less, losers lose more. Closes
    # part of the remaining sim-vs-live gap (~24x after the 7R cap).
    _SLIPPAGE_TICKS_AVG = 5.0
    slippage_cost_r = _SLIPPAGE_TICKS_AVG / max(_STOP_TICKS_TRAIL, 1)

    reward_long = base_long + long_levels * _TRAIL_BONUS_PER_LEVEL - cost_r - long_dd_penalty - slippage_cost_r
    reward_short = base_short + short_levels * _TRAIL_BONUS_PER_LEVEL - cost_r - short_dd_penalty - slippage_cost_r

    # === TIER 2 PYRAMID BONUS ===
    # Live rule (add_policy.py): when the position reaches +0.3R in profit AND
    # the next touch is aligned + confident, add 0.5x base size. The add runs
    # from entry-at-add to final exit. Approximation here: trades that reach
    # at least 1R have nearly always crossed the 0.3R-and-aligned condition
    # once, so a single 0.5x add of R_after_0.3 captures the compound effect.
    # Formula: pyramid_bonus = min(1.0, max(0, reward - 0.3) * 0.5). Cap at
    # +1R so very long trend runs don't get unrealistic 3-4R compounded adds
    # (in live the pyramid also caps at MAX_POSITION_MULT = 3.0).
    if reward_long >= 1.0:
        reward_long += min(1.0, (reward_long - 0.3) * 0.5)
    if reward_short >= 1.0:
        reward_short += min(1.0, (reward_short - 0.3) * 0.5)

    # === TIER 1 EARLY-EXIT LOCK SAFETY NET ===
    # Live rule: when peak_R >= 0.5 AND EarlyExitModel fires, close at +0.5R
    # instead of letting the trade stop out. Classic pump-retrace protection.
    # In training we don't have the EE model output at label time, but we can
    # use the simple rule: IF peak_R_this_direction >= 0.5 AND the trade
    # ultimately went negative, the live EE lock (at ~50% firing rate at the
    # optimum threshold) would have closed at +0.5R on roughly half of them.
    # Conservative: apply a 50% probability lock — cap loss at +0.5R for half
    # the weight, keep original reward for the other half. This is a blended
    # label that won't overstate the benefit.
    _EE_LOCK_R = 0.5
    _EE_FIRE_RATE = 0.5  # matches τ=0.70 live: flags ~50% of touches
    long_peak_pre = float(long_profiles[-1].max_favorable) / max(long_stop_ticks, 1) if long_profiles else 0.0
    short_peak_pre = float(short_profiles[-1].max_favorable) / max(short_stop_ticks, 1) if short_profiles else 0.0
    if long_peak_pre >= _EE_LOCK_R and reward_long < 0:
        reward_long = (1 - _EE_FIRE_RATE) * reward_long + _EE_FIRE_RATE * (_EE_LOCK_R - cost_r)
    if short_peak_pre >= _EE_LOCK_R and reward_short < 0:
        reward_short = (1 - _EE_FIRE_RATE) * reward_short + _EE_FIRE_RATE * (_EE_LOCK_R - cost_r)

    # Cap rewards to what a live trade can actually realize.
    # 2026-05-16: lowered from +7.0R to +2.5R. The +7R ceiling was modeling
    # a 10-level trail that live never executes — Phase 2 audit showed
    # only 6.5% of live trades reach >1.5R, and the max realized was ~3R.
    # Labels capped at +7R taught the model "go for the moon" setups that
    # in live exit at -1R or +1.5R. +2.5R covers the realistic upper-tail
    # (Phase 2 winners) while eliminating the multi-level-trail fantasy
    # that was driving the +1.09R/trade backtest vs -0.02R/trade live gap.
    # _REWARD_LIVE_MIN widened from -1.0 to -1.5 on 2026-05-16. Live audit
    # showed worst trades hit -2.1R (entry slippage + stop slippage on fast
    # moves). -1.5 gives the slippage cost a place to land without being
    # truncated, while still capping pathological tails (replay finds occasional
    # episodes where reward calc explodes to -15R from spurious price jumps —
    # those need the cap).
    _REWARD_LIVE_MIN = -1.5
    _REWARD_LIVE_MAX = 2.5
    reward_long = float(max(_REWARD_LIVE_MIN, min(_REWARD_LIVE_MAX, reward_long)))
    reward_short = float(max(_REWARD_LIVE_MIN, min(_REWARD_LIVE_MAX, reward_short)))

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

        struct_dist = abs(behind[0] - touch_price) / TICK_SIZE + 2.0 if behind else float(_STOP_TICKS_TRAIL)

        # Blend structural distance + MAE + ATR-volatility. Framework (Fabio):
        # stop = structural invalidation widened by current volatility. ATR
        # gives macro volatility context (high-vol days need more room), MAE
        # gives realized breathing-room, structural gives the actual
        # invalidation level. Weighted blend across all three.
        mae = long_mae if direction == 1 else short_mae

        # Session ATR lives in observation as part of execution features (~idx
        # 273 = execution[4] = session_atr_norm, normalized by /100 in ticks).
        try:
            atr_norm = float(observation[273]) if observation is not None and len(observation) > 273 else 0.3
            # Denormalize: atr_norm was clipped to [0, 1] after /100
            atr_ticks = atr_norm * 100.0
        except (IndexError, TypeError):
            atr_ticks = 30.0  # fallback ~1 pt

        # k*ATR reference: 1.0 × ATR gives ~session-typical move room.
        atr_stop = atr_ticks * 1.0

        if mae > 0:
            mae_floor = mae + 2.0
            struct_mae_blend = 0.7 * max(struct_dist, mae_floor) + 0.3 * min(struct_dist, mae_floor)
        else:
            struct_mae_blend = struct_dist

        # 60% structural/MAE + 40% ATR. ATR keeps stop calibrated to current
        # volatility regime regardless of specific zone layout.
        optimal_stop = 0.6 * struct_mae_blend + 0.4 * atr_stop

        # Orderflow-aware adjustment (Phase 2 logic retained): strong OF →
        # tighter invalidation, weak OF → more breathing room.
        try:
            of_score = float(observation[282]) if observation is not None and len(observation) > 282 else 0.5
        except (IndexError, TypeError):
            of_score = 0.5
        of_factor = 1.2 - 0.4 * of_score
        optimal_stop = optimal_stop * of_factor

        # Widened cap (back to 50) so high-vol regime trades can get appropriate
        # stops; floor at 6 remains to prevent noise-stops.
        optimal_stop = float(max(6.0, min(50.0, optimal_stop)))

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

    # Peak favorable R per side — max_favorable from the last movement profile
    # is the cumulative MFE across all windows, normalized by the trail stop.
    # Feeds Phase 3c early_exit_model: if peak_R ≥ 0.5 but realized_R is
    # small/negative, the trade pumped then retraced.
    _R_BASIS = float(max(_STOP_TICKS_TRAIL, 1))
    long_peak = float(long_profiles[-1].max_favorable) / _R_BASIS if long_profiles else 0.0
    short_peak = float(short_profiles[-1].max_favorable) / _R_BASIS if short_profiles else 0.0
    # Align with reward_cont/reward_rev mapping: continuation follows the
    # approach direction, reversal is opposite.
    if approach_direction == "up":
        peak_R_cont, peak_R_rev = long_peak, short_peak
    else:
        peak_R_cont, peak_R_rev = short_peak, long_peak

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
        peak_R_cont=peak_R_cont,
        peak_R_rev=peak_R_rev,
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
