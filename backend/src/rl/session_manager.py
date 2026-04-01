"""Session Manager — position tracking, flipping, trailing stops, sizing.

Sits on top of the frozen DQN model and manages the execution layer:
- Tracks open position (long/short/flat)
- Flips position when model signals opposite direction at a new level
- Trails stop using the stop head prediction
- Sizes based on Q-spread confidence + running session P&L (compounding)

The model itself never changes — SessionManager is pure execution logic.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import torch

from .agent.network import DQNetwork
from .config import Action, TICK_SIZE, STOP_TICKS
from .data.normalization import RunningNormalizer
from .features.observation import build_observation, OBSERVATION_DIM
from .zone_builder import Zone

log = logging.getLogger(__name__)


class PositionSide(str, Enum):
    FLAT = "flat"
    LONG = "long"
    SHORT = "short"


@dataclass
class Position:
    """An open position with entry, stop, and target tracking."""
    side: PositionSide
    entry_price: float
    stop_price: float
    size: float = 1.0  # in R-units (1.0 = base risk)
    entry_level: str = ""
    entry_q_spread: float = 0.0  # Q-spread at entry (for flip conviction check)
    entry_timestamp: float = 0.0  # epoch seconds at entry
    unrealized_pnl_ticks: float = 0.0
    levels_captured: int = 0  # how many levels the trailing stop has locked

    @property
    def is_open(self) -> bool:
        return self.side != PositionSide.FLAT


@dataclass
class TradeRecord:
    """Completed trade for session P&L tracking."""
    side: str
    entry_price: float
    exit_price: float
    entry_level: str
    exit_reason: str  # "stop", "flip", "session_end"
    size: float
    pnl_ticks: float
    pnl_r: float
    levels_captured: int


@dataclass
class SessionState:
    """Running session state for intraday compounding."""
    trades: list[TradeRecord] = field(default_factory=list)
    total_pnl_r: float = 0.0
    max_pnl_r: float = 0.0
    drawdown_r: float = 0.0
    consecutive_losses: int = 0
    total_stop_hits: int = 0  # count of stop-loss exits this session
    session_rth_open_epoch: float = 0.0  # RTH open time for IB gating
    max_daily_loss_r: float = -6.0  # circuit breaker: stop after -6R

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def is_stopped_out(self) -> bool:
        """Circuit breaker: stop trading after max daily loss OR 3 stop hits."""
        return self.total_pnl_r <= self.max_daily_loss_r


class SessionManager:
    """Manages position state across level touches within a session.

    Usage:
        sm = SessionManager(network, normalizer)
        for level_touch in level_touches:
            signal = sm.on_level_touch(state_dict, current_price)
            # signal tells you what to do: enter, flip, trail, skip
    """

    # --- Configuration ---
    BASE_SIZE: float = 1.0          # Base position size in R-units
    COMPOUND_THRESHOLD_R: float = 2.0  # Start compounding after +2R
    COMPOUND_STEP: float = 0.25     # +25% size per compound step
    MAX_COMPOUND: float = 2.0       # Cap at 2x base size
    MIN_Q_SPREAD: float = 0.01     # Minimum Q-spread to consider trading
    FLIP_SPREAD_MULT: float = 2.0   # Only flip if new signal is 2x stronger than entry spread
    MIN_HOLD_SECONDS: float = 120.0 # Don't flip within 2 min of entry
    TRAIL_LOCK_TICKS: float = 0.0   # Structural trailing only (on level touches)
    INDEPENDENT_MODE: bool = True   # Each level touch is independent — no position carry
    MAX_CONSECUTIVE_LOSSES: int = 3  # 3 stops = HALT trading for session (Fabio's rule)
    REVERSAL_CUSHION_R: float = 2.0 # Only take reversal trades after +2R session profit
    IB_NO_TRADE_MINUTES: float = 15.0  # Don't trade during IB formation (first 15 min)
    PROFIT_CAP_R: float = 20.0     # Stop trading after hitting session profit target

    def __init__(
        self,
        network: DQNetwork,
        normalizer: RunningNormalizer | None = None,
    ) -> None:
        self._network = network
        self._normalizer = normalizer
        self._network.eval()

        self.position = Position(side=PositionSide.FLAT, entry_price=0.0, stop_price=0.0)
        self.session = SessionState()

    def reset_session(self) -> None:
        """Reset for a new trading session."""
        self.position = Position(side=PositionSide.FLAT, entry_price=0.0, stop_price=0.0)
        self.session = SessionState()

    def on_level_touch(self, state: dict, current_price: float) -> dict:
        """Process a level touch event.

        Args:
            state: Full market state dict (same format as build_observation expects)
            current_price: Current price at the level touch

        Returns:
            Signal dict with:
                action: "enter_long", "enter_short", "flip_long", "flip_short",
                        "trail_stop", "skip", "stopped_out"
                q_values: [q_cont, q_rev]
                q_spread: float
                confidence: float (0-1)
                stop_price: float (from stop head)
                size: float (position size in R-units)
                reason: str
        """
        # Circuit breakers (Fabio's rules)
        if self.session.is_stopped_out:
            return self._signal("skip", current_price, reason="daily_loss_limit")
        if self.session.total_stop_hits >= self.MAX_CONSECUTIVE_LOSSES:
            return self._signal("skip", current_price, reason="3_stops_halt")
        if self.session.total_pnl_r >= self.PROFIT_CAP_R:
            return self._signal("skip", current_price, reason="profit_cap_reached")

        # IB no-trade zone: skip during first 15 min of session
        touch_epoch = state.get("touch_epoch", 0.0)
        if self.session.session_rth_open_epoch > 0 and touch_epoch > 0:
            minutes_since_open = (touch_epoch - self.session.session_rth_open_epoch) / 60.0
            if 0 < minutes_since_open < self.IB_NO_TRADE_MINUTES:
                return self._signal("skip", current_price, reason="ib_formation")

        # Run inference
        obs = build_observation(state)
        if self._normalizer is not None:
            obs = self._normalizer.normalize(obs)
        obs_tensor = torch.from_numpy(obs).unsqueeze(0)

        with torch.no_grad():
            q_values, stop_pred = self._network.forward_full(obs_tensor)

        q_cont = float(q_values[0, Action.CONTINUATION.value])
        q_rev = float(q_values[0, Action.REVERSAL.value])
        q_spread = abs(q_cont - q_rev)
        stop_ticks = float(stop_pred[0, 0])

        # Determine model's preferred direction
        approach = state.get("approach_direction", "up")
        if q_cont > q_rev:
            # Model says continuation
            model_side = PositionSide.LONG if approach == "up" else PositionSide.SHORT
        else:
            # Model says reversal
            model_side = PositionSide.SHORT if approach == "up" else PositionSide.LONG

        # Compute stop price
        if model_side == PositionSide.LONG:
            stop_price = current_price - stop_ticks * TICK_SIZE
        else:
            stop_price = current_price + stop_ticks * TICK_SIZE

        # Confidence = normalized Q-spread (0-1 scale, capped at 0.1 spread)
        confidence = min(q_spread / 0.10, 1.0)

        # Compute size
        size = self._compute_size(confidence)

        # --- Decision logic ---

        # Reversal cushion: only take reversal trades after session profit (Fabio's rule)
        is_reversal = q_rev > q_cont
        if is_reversal and self.session.total_pnl_r < self.REVERSAL_CUSHION_R:
            return self._signal("skip", current_price,
                                q_spread=q_spread, confidence=confidence,
                                reason="reversal_no_cushion")

        if self.INDEPENDENT_MODE:
            # Independent mode: each level touch is a standalone signal
            # No position carry, no flipping, no trailing
            # The reward comes from the velocity measurement, not stop/target
            if q_spread < self.MIN_Q_SPREAD:
                return self._signal("skip", current_price,
                                    q_spread=q_spread, confidence=confidence,
                                    reason="low_confidence")

            action = f"signal_{model_side.value}"
            return self._signal(action, current_price,
                                q_values=[q_cont, q_rev],
                                q_spread=q_spread, confidence=confidence,
                                stop_price=stop_price, size=size,
                                reason="independent_signal")

        if not self.position.is_open:
            # No position — enter or skip
            if q_spread < self.MIN_Q_SPREAD:
                return self._signal("skip", current_price,
                                    q_spread=q_spread, confidence=confidence,
                                    reason="low_confidence")

            action = f"enter_{model_side.value}"
            import time
            self.position = Position(
                side=model_side,
                entry_price=current_price,
                stop_price=stop_price,
                size=size,
                entry_level=str(state.get("level_type", "")),
                entry_q_spread=q_spread,
                entry_timestamp=state.get("touch_epoch", time.time()),
            )
            return self._signal(action, current_price,
                                q_values=[q_cont, q_rev],
                                q_spread=q_spread, confidence=confidence,
                                stop_price=stop_price, size=size,
                                reason="new_entry")

        else:
            # Position open — check for flip, trail, or skip
            import time
            current_epoch = state.get("touch_epoch", time.time())
            hold_time = current_epoch - self.position.entry_timestamp

            # Flip requires: opposite direction + high conviction + minimum hold time
            flip_ok = (
                model_side != self.position.side
                and q_spread >= self.MIN_Q_SPREAD
                and q_spread >= self.position.entry_q_spread * self.FLIP_SPREAD_MULT
                and hold_time >= self.MIN_HOLD_SECONDS
            )

            if flip_ok:
                # FLIP: close current + open opposite
                pnl = self._close_position(current_price, "flip")
                action = f"flip_{model_side.value}"
                self.position = Position(
                    side=model_side,
                    entry_price=current_price,
                    stop_price=stop_price,
                    size=size,
                    entry_level=str(state.get("level_type", "")),
                    entry_q_spread=q_spread,
                    entry_timestamp=current_epoch,
                )
                return self._signal(action, current_price,
                                    q_values=[q_cont, q_rev],
                                    q_spread=q_spread, confidence=confidence,
                                    stop_price=stop_price, size=size,
                                    closed_pnl_r=pnl,
                                    reason="direction_flip")

            elif model_side != self.position.side:
                # BOS without conviction to flip — move to breakeven (Fabio's rule)
                # "BOS without volume = breakeven, BOS WITH volume = exit"
                if self.position.side == PositionSide.LONG:
                    be_price = self.position.entry_price + 1 * TICK_SIZE
                    if be_price > self.position.stop_price:
                        self.position.stop_price = be_price
                        return self._signal("move_to_breakeven", current_price,
                                            q_values=[q_cont, q_rev],
                                            q_spread=q_spread, confidence=confidence,
                                            stop_price=be_price,
                                            reason="bos_no_conviction_breakeven")
                else:
                    be_price = self.position.entry_price - 1 * TICK_SIZE
                    if be_price < self.position.stop_price:
                        self.position.stop_price = be_price
                        return self._signal("move_to_breakeven", current_price,
                                            q_values=[q_cont, q_rev],
                                            q_spread=q_spread, confidence=confidence,
                                            stop_price=be_price,
                                            reason="bos_no_conviction_breakeven")

            if model_side == self.position.side:
                # Same direction — trail the stop
                new_stop = self._trail_stop(current_price, stop_price)
                if new_stop != self.position.stop_price:
                    self.position.stop_price = new_stop
                    self.position.levels_captured += 1
                    return self._signal("trail_stop", current_price,
                                        q_values=[q_cont, q_rev],
                                        q_spread=q_spread, confidence=confidence,
                                        stop_price=new_stop,
                                        reason=f"level_{self.position.levels_captured}_captured")

            # Model agrees but no stop improvement — hold
            return self._signal("hold", current_price,
                                q_values=[q_cont, q_rev],
                                q_spread=q_spread, confidence=confidence,
                                reason="hold_position")

    def on_zone_entry(self, state: dict, current_price: float) -> dict:
        """Process a zone entry event — like on_level_touch but with zone-boundary stops.

        If state contains a "zone" key (a Zone dataclass), the stop is computed
        from the zone boundary instead of the center price:
          LONG  → stop = zone.lower_bound - stop_ticks * TICK_SIZE
          SHORT → stop = zone.upper_bound + stop_ticks * TICK_SIZE

        Falls back to on_level_touch() when no zone is present.
        """
        zone: Zone | None = state.get("zone")
        if zone is None:
            return self.on_level_touch(state, current_price)

        # Circuit breakers (same as on_level_touch)
        if self.session.is_stopped_out:
            return self._signal("skip", current_price, reason="daily_loss_limit")
        if self.session.total_stop_hits >= self.MAX_CONSECUTIVE_LOSSES:
            return self._signal("skip", current_price, reason="3_stops_halt")
        if self.session.total_pnl_r >= self.PROFIT_CAP_R:
            return self._signal("skip", current_price, reason="profit_cap_reached")

        # IB no-trade zone
        touch_epoch = state.get("touch_epoch", 0.0)
        if self.session.session_rth_open_epoch > 0 and touch_epoch > 0:
            minutes_since_open = (touch_epoch - self.session.session_rth_open_epoch) / 60.0
            if 0 < minutes_since_open < self.IB_NO_TRADE_MINUTES:
                return self._signal("skip", current_price, reason="ib_formation")

        # Run inference
        obs = build_observation(state)
        if self._normalizer is not None:
            obs = self._normalizer.normalize(obs)
        obs_tensor = torch.from_numpy(obs).unsqueeze(0)

        with torch.no_grad():
            q_values, stop_pred = self._network.forward_full(obs_tensor)

        q_cont = float(q_values[0, Action.CONTINUATION.value])
        q_rev = float(q_values[0, Action.REVERSAL.value])
        q_spread = abs(q_cont - q_rev)
        stop_ticks = float(stop_pred[0, 0])

        # Determine model's preferred direction
        approach = state.get("approach_direction", "up")
        if q_cont > q_rev:
            model_side = PositionSide.LONG if approach == "up" else PositionSide.SHORT
        else:
            model_side = PositionSide.SHORT if approach == "up" else PositionSide.LONG

        # Compute stop price from zone BOUNDARY (not center)
        if model_side == PositionSide.LONG:
            stop_price = zone.lower_bound - stop_ticks * TICK_SIZE
        else:
            stop_price = zone.upper_bound + stop_ticks * TICK_SIZE

        confidence = min(q_spread / 0.10, 1.0)
        size = self._compute_size(confidence)

        # Reversal cushion check
        is_reversal = q_rev > q_cont
        if is_reversal and self.session.total_pnl_r < self.REVERSAL_CUSHION_R:
            return self._signal("skip", current_price,
                                q_spread=q_spread, confidence=confidence,
                                reason="reversal_no_cushion")

        if self.INDEPENDENT_MODE:
            if q_spread < self.MIN_Q_SPREAD:
                return self._signal("skip", current_price,
                                    q_spread=q_spread, confidence=confidence,
                                    reason="low_confidence")

            action = f"signal_{model_side.value}"
            return self._signal(action, current_price,
                                q_values=[q_cont, q_rev],
                                q_spread=q_spread, confidence=confidence,
                                stop_price=stop_price, size=size,
                                zone_members=zone.member_count,
                                reason="independent_signal")

        if not self.position.is_open:
            if q_spread < self.MIN_Q_SPREAD:
                return self._signal("skip", current_price,
                                    q_spread=q_spread, confidence=confidence,
                                    reason="low_confidence")

            action = f"enter_{model_side.value}"
            import time
            self.position = Position(
                side=model_side,
                entry_price=current_price,
                stop_price=stop_price,
                size=size,
                entry_level=str(state.get("level_type", "")),
                entry_q_spread=q_spread,
                entry_timestamp=state.get("touch_epoch", time.time()),
            )
            return self._signal(action, current_price,
                                q_values=[q_cont, q_rev],
                                q_spread=q_spread, confidence=confidence,
                                stop_price=stop_price, size=size,
                                zone_members=zone.member_count,
                                reason="new_entry")

        else:
            import time
            current_epoch = state.get("touch_epoch", time.time())
            hold_time = current_epoch - self.position.entry_timestamp

            flip_ok = (
                model_side != self.position.side
                and q_spread >= self.MIN_Q_SPREAD
                and q_spread >= self.position.entry_q_spread * self.FLIP_SPREAD_MULT
                and hold_time >= self.MIN_HOLD_SECONDS
            )

            if flip_ok:
                pnl = self._close_position(current_price, "flip")
                action = f"flip_{model_side.value}"
                self.position = Position(
                    side=model_side,
                    entry_price=current_price,
                    stop_price=stop_price,
                    size=size,
                    entry_level=str(state.get("level_type", "")),
                    entry_q_spread=q_spread,
                    entry_timestamp=current_epoch,
                )
                return self._signal(action, current_price,
                                    q_values=[q_cont, q_rev],
                                    q_spread=q_spread, confidence=confidence,
                                    stop_price=stop_price, size=size,
                                    closed_pnl_r=pnl,
                                    zone_members=zone.member_count,
                                    reason="direction_flip")

            elif model_side != self.position.side:
                if self.position.side == PositionSide.LONG:
                    be_price = self.position.entry_price + 1 * TICK_SIZE
                    if be_price > self.position.stop_price:
                        self.position.stop_price = be_price
                        return self._signal("move_to_breakeven", current_price,
                                            q_values=[q_cont, q_rev],
                                            q_spread=q_spread, confidence=confidence,
                                            stop_price=be_price,
                                            reason="bos_no_conviction_breakeven")
                else:
                    be_price = self.position.entry_price - 1 * TICK_SIZE
                    if be_price < self.position.stop_price:
                        self.position.stop_price = be_price
                        return self._signal("move_to_breakeven", current_price,
                                            q_values=[q_cont, q_rev],
                                            q_spread=q_spread, confidence=confidence,
                                            stop_price=be_price,
                                            reason="bos_no_conviction_breakeven")

            if model_side == self.position.side:
                new_stop = self._trail_stop(current_price, stop_price)
                if new_stop != self.position.stop_price:
                    self.position.stop_price = new_stop
                    self.position.levels_captured += 1
                    return self._signal("trail_stop", current_price,
                                        q_values=[q_cont, q_rev],
                                        q_spread=q_spread, confidence=confidence,
                                        stop_price=new_stop,
                                        reason=f"level_{self.position.levels_captured}_captured")

            return self._signal("hold", current_price,
                                q_values=[q_cont, q_rev],
                                q_spread=q_spread, confidence=confidence,
                                reason="hold_position")

    def on_price_update(self, current_price: float) -> dict | None:
        """Check if stop was hit on a price update (called on every tick/bar).

        Returns signal dict if stop hit, None otherwise.
        """
        if not self.position.is_open:
            return None

        stopped = False
        if self.position.side == PositionSide.LONG and current_price <= self.position.stop_price:
            stopped = True
        elif self.position.side == PositionSide.SHORT and current_price >= self.position.stop_price:
            stopped = True

        if stopped:
            pnl = self._close_position(current_price, "stop")
            return self._signal("stopped_out", current_price,
                                closed_pnl_r=pnl,
                                reason="stop_hit")
        return None

    def on_session_end(self, current_price: float) -> dict | None:
        """Close any open position at session end."""
        if not self.position.is_open:
            return None
        pnl = self._close_position(current_price, "session_end")
        return self._signal("session_close", current_price,
                            closed_pnl_r=pnl,
                            reason="session_end")

    # --- Private helpers ---

    def _close_position(self, exit_price: float, reason: str) -> float:
        """Close the current position and record the trade."""
        if self.position.side == PositionSide.LONG:
            pnl_ticks = (exit_price - self.position.entry_price) / TICK_SIZE
        else:
            pnl_ticks = (self.position.entry_price - exit_price) / TICK_SIZE

        pnl_r = pnl_ticks / STOP_TICKS * self.position.size

        trade = TradeRecord(
            side=self.position.side.value,
            entry_price=self.position.entry_price,
            exit_price=exit_price,
            entry_level=self.position.entry_level,
            exit_reason=reason,
            size=self.position.size,
            pnl_ticks=pnl_ticks,
            pnl_r=pnl_r,
            levels_captured=self.position.levels_captured,
        )
        self.session.trades.append(trade)
        self.session.total_pnl_r += pnl_r
        self.session.max_pnl_r = max(self.session.max_pnl_r, self.session.total_pnl_r)
        self.session.drawdown_r = self.session.total_pnl_r - self.session.max_pnl_r

        if pnl_r < 0:
            self.session.consecutive_losses += 1
            if reason == "stop":
                self.session.total_stop_hits += 1
        else:
            self.session.consecutive_losses = 0

        # Reset position
        self.position = Position(side=PositionSide.FLAT, entry_price=0.0, stop_price=0.0)

        return pnl_r

    def _trail_stop(self, current_price: float, new_stop_from_model: float) -> float:
        """Trail the stop on level touches only — structural trailing, not price-based.

        The stop only moves when the model evaluates a NEW level and confirms
        the same direction. This prevents noise-based stop tightening.
        The new stop comes from the model's stop head at the new level.
        """
        if self.position.side == PositionSide.LONG:
            # For longs, stop can only move UP — use model's stop prediction
            return max(self.position.stop_price, new_stop_from_model)
        else:
            # For shorts, stop can only move DOWN
            return min(self.position.stop_price, new_stop_from_model)

    def _compute_size(self, confidence: float) -> float:
        """Compute position size based on confidence + session P&L."""
        base = self.BASE_SIZE

        # Confidence scaling: 50% to 100% of base
        size = base * (0.5 + 0.5 * confidence)

        # Intraday compounding: increase after profits ONLY if no recent losses
        # (Fabio: "never raise exposure to recover")
        if (self.session.total_pnl_r > self.COMPOUND_THRESHOLD_R
                and self.session.consecutive_losses == 0):
            compound_steps = int(
                (self.session.total_pnl_r - self.COMPOUND_THRESHOLD_R)
                / self.COMPOUND_THRESHOLD_R
            )
            compound_mult = 1.0 + compound_steps * self.COMPOUND_STEP
            size *= min(compound_mult, self.MAX_COMPOUND)

        # Reduce after consecutive losses
        if self.session.consecutive_losses >= 2:
            size *= 0.5

        return round(size, 2)

    def _signal(self, action: str, price: float, **kwargs) -> dict:
        """Build a signal dict."""
        return {
            "action": action,
            "price": price,
            "position": self.position.side.value,
            "session_pnl_r": round(self.session.total_pnl_r, 2),
            "trade_count": self.session.trade_count,
            **{k: round(v, 4) if isinstance(v, float) else v
               for k, v in kwargs.items()},
        }

    def get_session_summary(self) -> dict:
        """Get session summary for reporting."""
        trades = self.session.trades
        if not trades:
            return {"trades": 0, "total_pnl_r": 0.0, "win_rate": 0.0}

        winners = [t for t in trades if t.pnl_r > 0]
        losers = [t for t in trades if t.pnl_r <= 0]
        flips = [t for t in trades if t.exit_reason == "flip"]

        return {
            "trades": len(trades),
            "winners": len(winners),
            "losers": len(losers),
            "flips": len(flips),
            "total_pnl_r": round(self.session.total_pnl_r, 2),
            "avg_winner_r": round(np.mean([t.pnl_r for t in winners]), 2) if winners else 0.0,
            "avg_loser_r": round(np.mean([t.pnl_r for t in losers]), 2) if losers else 0.0,
            "max_pnl_r": round(self.session.max_pnl_r, 2),
            "max_drawdown_r": round(self.session.drawdown_r, 2),
            "avg_levels_captured": round(np.mean([t.levels_captured for t in trades]), 1),
            "profit_factor": round(
                sum(t.pnl_r for t in winners) / max(abs(sum(t.pnl_r for t in losers)), 0.01), 2
            ) if winners else 0.0,
        }
