"""Session simulator — chains specialist signals into multi-level trades.

Instead of evaluating each level touch independently, this simulates
real trading: open on first signal, hold through continuations, flip
on reversals, trail stop behind captured levels.

A single "trade" can span multiple level touches and capture several R
by riding through levels the continuation specialist identifies as breaks.

Usage:
    sim = SessionSimulator(ensemble)
    results = sim.run(observations, rewards_cont, rewards_rev)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Trade:
    """A single trade from entry to exit (may span multiple levels)."""

    entry_idx: int  # episode index where we entered
    exit_idx: int = -1  # episode index where we exited
    direction: str = ""  # "long" or "short"
    entry_signal: str = ""  # "continuation" or "reversal"
    levels_captured: int = 0  # number of CONT signals that extended this trade
    pnl_r: float = 0.0  # total R for this trade
    max_pnl_r: float = 0.0  # peak R (for drawdown calculation)
    flipped: bool = False  # closed by a reversal (vs timeout/end)
    added_size: int = 0  # number of times we added to the position


@dataclass
class SimulationResult:
    """Results from a full simulation run."""

    trades: list[Trade] = field(default_factory=list)
    total_r: float = 0.0
    total_trades: int = 0
    win_rate: float = 0.0
    avg_r_per_trade: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_r: float = 0.0
    avg_levels_captured: float = 0.0
    avg_hold_episodes: float = 0.0
    multi_level_pct: float = 0.0  # % of trades that captured 2+ levels
    cont_signals_total: int = 0
    rev_signals_total: int = 0
    skip_signals_total: int = 0


class SessionSimulator:
    """Simulates trading with specialist ensemble signals.

    Logic per level touch:
    1. Get specialist decision (CONT/REV/SKIP)
    2. If no position:
       - CONT/REV → open position in that direction
       - SKIP → do nothing
    3. If position open:
       - CONT (same direction) → hold, trail stop, optionally add size
       - REV → close position, open new in opposite direction (flip)
       - CONT (opposite direction) → close position (conflicting signal)
       - SKIP → hold position (no new info)

    Reward accumulation:
    - Each level touch has a reward_cont and reward_rev
    - When holding through a CONT signal: accumulate the cont reward
    - When flipping on REV: close at accumulated R, open new trade
    - Trailing stop: if accumulated R drops below -1R from peak, close
    """

    TRAILING_STOP_R = 1.5  # close if R drops this much from peak
    ADD_SIZE_THRESHOLD = 1.0  # add size after capturing 1R+
    MAX_HOLD_EPISODES = 50  # force close after this many touches

    def __init__(self, ensemble) -> None:
        self.ensemble = ensemble

    def run(
        self,
        observations: np.ndarray,
        rewards_cont: np.ndarray,
        rewards_rev: np.ndarray,
    ) -> SimulationResult:
        """Run simulation across all episodes chronologically.

        Episodes are treated as sequential level touches within sessions.
        """
        n = len(observations)
        result = SimulationResult()
        trades: list[Trade] = []

        # Get all decisions at once (batch)
        actions, confidences, sizing = self.ensemble.decide_batch(observations)

        # State
        position: str | None = None  # None, "long", "short"
        current_trade: Trade | None = None
        accumulated_r: float = 0.0
        peak_r: float = 0.0

        for i in range(n):
            action = actions[i]  # 0=CONT, 1=REV, 2=SKIP
            rc = float(rewards_cont[i])
            rr = float(rewards_rev[i])

            if action == 2:
                result.skip_signals_total += 1
                # SKIP: if we have a position, just hold it
                if current_trade is not None:
                    # Check trailing stop
                    if accumulated_r < peak_r - self.TRAILING_STOP_R:
                        # Stop hit — close trade
                        current_trade.exit_idx = i
                        current_trade.pnl_r = accumulated_r
                        trades.append(current_trade)
                        position = None
                        current_trade = None
                        accumulated_r = 0.0
                        peak_r = 0.0
                continue

            signal = "continuation" if action == 0 else "reversal"

            if action == 0:
                result.cont_signals_total += 1
            else:
                result.rev_signals_total += 1

            if position is None:
                # No position — open new trade
                if action == 0:  # CONT
                    # Continuation with no position: go with the approach direction
                    # Use the reward sign to determine which direction is "continuation"
                    position = "long" if rc > rr else "short"
                    accumulated_r = rc if position == "long" else rr
                else:  # REV
                    position = "short" if rc > rr else "long"  # opposite of approach
                    accumulated_r = rr if position == "short" else rc

                peak_r = max(0, accumulated_r)
                current_trade = Trade(
                    entry_idx=i,
                    direction=position,
                    entry_signal=signal,
                )
                continue

            # We have a position
            # Determine what this signal means for our current position
            if action == 0:  # CONT signal
                # Continuation = price breaks through level
                # If we're positioned correctly, this extends our trade
                level_r = rc if position == "long" else rr
                accumulated_r += level_r
                peak_r = max(peak_r, accumulated_r)
                current_trade.levels_captured += 1

                # Add size after 1R+ captured
                if accumulated_r >= self.ADD_SIZE_THRESHOLD and current_trade.added_size == 0:
                    current_trade.added_size += 1

                # Check trailing stop
                if accumulated_r < peak_r - self.TRAILING_STOP_R:
                    current_trade.exit_idx = i
                    current_trade.pnl_r = accumulated_r
                    trades.append(current_trade)
                    position = None
                    current_trade = None
                    accumulated_r = 0.0
                    peak_r = 0.0

            elif action == 1:  # REV signal
                # Reversal = price bounces off level
                # Close current trade and flip
                level_r = rr if position == "long" else rc
                accumulated_r += level_r

                current_trade.exit_idx = i
                current_trade.pnl_r = accumulated_r
                current_trade.flipped = True
                trades.append(current_trade)

                # Open new trade in opposite direction
                old_pos = position
                position = "short" if old_pos == "long" else "long"
                # The reversal reward is what we capture on the flip
                flip_r = rr if old_pos == "long" else rc
                accumulated_r = flip_r
                peak_r = max(0, accumulated_r)
                current_trade = Trade(
                    entry_idx=i,
                    direction=position,
                    entry_signal="reversal",
                )

            # Force close after too many touches
            if current_trade and (i - current_trade.entry_idx) >= self.MAX_HOLD_EPISODES:
                current_trade.exit_idx = i
                current_trade.pnl_r = accumulated_r
                trades.append(current_trade)
                position = None
                current_trade = None
                accumulated_r = 0.0
                peak_r = 0.0

        # Close any remaining position
        if current_trade is not None:
            current_trade.exit_idx = n - 1
            current_trade.pnl_r = accumulated_r
            trades.append(current_trade)

        # Compute result metrics
        result.trades = trades
        result.total_trades = len(trades)

        if not trades:
            return result

        pnls = np.array([t.pnl_r for t in trades])
        result.total_r = float(pnls.sum())
        result.win_rate = float((pnls > 0).sum() / len(pnls))
        result.avg_r_per_trade = float(pnls.mean())

        gross_win = pnls[pnls > 0].sum() if (pnls > 0).any() else 0
        gross_loss = abs(pnls[pnls < 0].sum()) if (pnls < 0).any() else 0
        result.profit_factor = float(gross_win / max(gross_loss, 1))

        # Max drawdown
        equity = np.cumsum(pnls)
        peak_equity = np.maximum.accumulate(equity)
        drawdown = peak_equity - equity
        result.max_drawdown_r = float(drawdown.max())

        levels = np.array([t.levels_captured for t in trades])
        result.avg_levels_captured = float(levels.mean())
        result.multi_level_pct = float((levels >= 2).sum() / len(trades))

        hold_durations = np.array([t.exit_idx - t.entry_idx for t in trades])
        result.avg_hold_episodes = float(hold_durations.mean())

        return result
