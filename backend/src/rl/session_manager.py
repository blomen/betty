"""Session Manager — position tracking, flipping, trailing stops, sizing.

Sits on top of a frozen model (DQN or GBT) and manages the execution layer:
- Tracks open position (long/short/flat)
- Flips position when model signals opposite direction at a new level
- Trails stop using the stop head prediction
- Sizes based on confidence + running session P&L (compounding)
- Optional wick-tolerant stop invalidation (STOP2 / framework rule)

The model itself never changes — SessionManager is pure execution logic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import torch

from .agent.gbt_model import GBTModel
from .agent.network import DQNetwork
from .config import STOP_TICKS, TICK_SIZE, Action
from .data.normalization import RunningNormalizer
from .features.observation import build_observation
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
    BASE_SIZE: float = 1.0  # Base position size in R-units
    COMPOUND_THRESHOLD_R: float = 2.0  # Start compounding after +2R
    COMPOUND_STEP: float = 0.25  # +25% size per compound step
    MAX_COMPOUND: float = 2.0  # Cap at 2x base size
    MIN_Q_SPREAD: float = 0.01  # Minimum Q-spread to consider trading
    FLIP_SPREAD_MULT: float = 2.0  # Only flip if new signal is 2x stronger than entry spread
    MIN_HOLD_SECONDS: float = 120.0  # Don't flip within 2 min of entry
    TRAIL_LOCK_TICKS: float = 0.0  # Structural trailing only (on level touches)
    INDEPENDENT_MODE: bool = True  # Each level touch is independent — no position carry
    MAX_CONSECUTIVE_LOSSES: int = 3  # 3 stops = HALT trading for session (Fabio's rule)
    REVERSAL_CUSHION_R: float = 2.0  # Only take reversal trades after +2R session profit
    IB_NO_TRADE_MINUTES: float = 15.0  # Don't trade during IB formation (first 15 min)
    PROFIT_CAP_R: float = 20.0  # Stop trading after hitting session profit target
    MAX_TRADES_PER_SESSION: int = (
        8  # Framework: 3 trades is ideal; 8 allows for wider interpretation but caps over-trading
    )
    NEWS_BLACKOUT_MINUTES: float = 15.0  # Avoid trading ±15 min around high-importance news (FOMC/NFP/CPI)
    # Orderflow confluence gate (Fabio framework): veto entries with weak orderflow
    # regardless of model confidence. Zone + orderflow must BOTH align.
    ORDERFLOW_SCORE_MIN: float = 0.30  # Below → entry vetoed
    ORDERFLOW_SCORE_TIGHT: float = 0.70  # Above → allow tighter entry stop
    # Model decision: drop CONT from live selection — CONT is a weaker REV and
    # training shows ~0% argmax-CONT. Force REV-only direction.
    FORCE_REV_ONLY: bool = True
    # Entry stop sanity bounds (veto trades with stops outside this range)
    MIN_ENTRY_STOP_TICKS: float = 6.0
    MAX_ENTRY_STOP_TICKS: float = 40.0

    def __init__(
        self,
        network: DQNetwork | GBTModel,
        normalizer: RunningNormalizer | None = None,
    ) -> None:
        self._network = network
        self._normalizer = normalizer
        self._use_gbt = isinstance(network, GBTModel)
        if not self._use_gbt:
            self._network.eval()

        # Try to load specialist ensemble (preferred for CONT/REV decisions)
        self._specialists = None
        try:
            from pathlib import Path

            from .agent.specialists import SpecialistEnsemble

            for search_dir in [Path("data/rl/models"), Path("backend/data/rl/models")]:
                for name in ["specialists_latest.joblib", "specialists_v5.joblib"]:
                    p = search_dir / name
                    if p.exists():
                        self._specialists = SpecialistEnsemble.load(p)
                        log.info("SessionManager: loaded specialists from %s", p)
                        break
                if self._specialists:
                    break
        except Exception:
            log.debug("Specialists not available, using %s", type(network).__name__)

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
        if self.session.trade_count >= self.MAX_TRADES_PER_SESSION:
            return self._signal("skip", current_price, reason="max_trades_reached")

        # IB no-trade zone: skip during first 15 min of session
        touch_epoch = state.get("touch_epoch", 0.0)
        if self.session.session_rth_open_epoch > 0 and touch_epoch > 0:
            minutes_since_open = (touch_epoch - self.session.session_rth_open_epoch) / 60.0
            if 0 < minutes_since_open < self.IB_NO_TRADE_MINUTES:
                return self._signal("skip", current_price, reason="ib_formation")

        # News blackout: skip ±15 min around high-importance scheduled events.
        # macro.news_proximity > threshold AND news_importance == high.
        macro = state.get("macro") or {}
        news_prox = float(macro.get("news_proximity", 0.0))
        news_imp = float(macro.get("news_importance", 0.0))
        # news_proximity = 1 - min_to_event/120, so >0.875 ≈ within 15 min
        if news_prox > 0.875 and news_imp >= 2.5 / 3.0:
            return self._signal("skip", current_price, reason="news_blackout")

        # Run inference — prefer specialists if available
        obs = build_observation(state)
        if self._normalizer is not None:
            obs = self._normalizer.normalize(obs)

        # Try specialist ensemble first
        _specialists = getattr(self, "_specialists", None)
        if _specialists is not None:
            decision = _specialists.decide(obs)
            if decision["action"] == "continuation":
                q_cont, q_rev = decision["cont_ev"], decision["rev_ev"]
                q_spread = decision["confidence"]
                stop_ticks = 15.0  # default, specialists don't have stop head
            elif decision["action"] == "reversal":
                q_cont, q_rev = decision["cont_ev"], decision["rev_ev"]
                q_spread = decision["confidence"]
                stop_ticks = 15.0
            else:
                q_cont, q_rev = 0.0, 0.0
                q_spread = 0.0
                stop_ticks = 10.0
        elif self._use_gbt:
            action_idx, confidence, prob_cont, prob_rev = self._network.predict_direction(obs)
            stop_ticks = self._network.predict_stop(obs)
            q_cont, q_rev = prob_cont, prob_rev
            q_spread = confidence
        else:
            obs_tensor = torch.from_numpy(obs).unsqueeze(0)
            with torch.no_grad():
                q_values, stop_pred = self._network.forward_full(obs_tensor)
            q_cont = float(q_values[0, Action.CONTINUATION.value])
            q_rev = float(q_values[0, Action.REVERSAL.value])
            q_spread = abs(q_cont - q_rev)
            stop_ticks = float(stop_pred[0, 0])

        # Determine model's preferred direction
        approach = state.get("approach_direction", "up")
        if self.FORCE_REV_ONLY:
            # Force REV — CONT is a weaker REV per training analysis (~0% argmax-CONT
            # and Q(CONT) is always more negative than Q(REV)).
            model_side = PositionSide.SHORT if approach == "up" else PositionSide.LONG
            is_reversal = True
        elif q_cont > q_rev:
            model_side = PositionSide.LONG if approach == "up" else PositionSide.SHORT
            is_reversal = False
        else:
            model_side = PositionSide.SHORT if approach == "up" else PositionSide.LONG
            is_reversal = True

        # Compute stop price
        if model_side == PositionSide.LONG:
            stop_price = current_price - stop_ticks * TICK_SIZE
        else:
            stop_price = current_price + stop_ticks * TICK_SIZE

        # Confidence = normalized Q-spread (0-1 scale, capped at 0.1 spread)
        confidence = min(q_spread / 0.10, 1.0)

        # Compute size
        size = self._compute_size(confidence)

        # Compute orderflow score FOR THIS DIRECTION — framework gate requires
        # both zone quality (from GBT confidence) AND orderflow confluence.
        trade_dir_sign = 1 if model_side == PositionSide.LONG else -1
        of_score = self._compute_orderflow_score(state, trade_dir_sign)

        # --- Decision logic ---

        # Reversal cushion: only take reversal trades after session profit (Fabio's rule)
        if is_reversal and self.session.total_pnl_r < self.REVERSAL_CUSHION_R:
            return self._signal(
                "skip", current_price, q_spread=q_spread, confidence=confidence, reason="reversal_no_cushion"
            )

        if self.INDEPENDENT_MODE:
            # Independent mode: each level touch is a standalone signal
            # No position carry, no flipping, no trailing
            # The reward comes from the velocity measurement, not stop/target
            if q_spread < self.MIN_Q_SPREAD:
                return self._signal(
                    "skip", current_price, q_spread=q_spread, confidence=confidence, reason="low_confidence"
                )

            action = f"signal_{model_side.value}"
            return self._signal(
                action,
                current_price,
                q_values=[q_cont, q_rev],
                q_spread=q_spread,
                confidence=confidence,
                stop_price=stop_price,
                size=size,
                reason="independent_signal",
            )

        if not self.position.is_open:
            # No position — enter or skip
            if q_spread < self.MIN_Q_SPREAD:
                return self._signal(
                    "skip", current_price, q_spread=q_spread, confidence=confidence, reason="low_confidence"
                )

            # Orderflow confluence gate — Fabio framework: zone + orderflow must align.
            # A high-confidence GBT signal without orderflow confirmation is noise.
            if of_score < self.ORDERFLOW_SCORE_MIN:
                return self._signal(
                    "skip",
                    current_price,
                    q_spread=q_spread,
                    confidence=confidence,
                    orderflow_score=of_score,
                    reason="orderflow_weak",
                )

            # Entry stop sanity bounds — reject trades where GBT-predicted stop
            # is implausible (too tight = noise-stop, too wide = unclear structure).
            if stop_ticks < self.MIN_ENTRY_STOP_TICKS or stop_ticks > self.MAX_ENTRY_STOP_TICKS:
                return self._signal(
                    "skip",
                    current_price,
                    q_spread=q_spread,
                    confidence=confidence,
                    stop_ticks=stop_ticks,
                    reason="stop_out_of_bounds",
                )

            # Strong orderflow → allow the GBT-predicted stop. Weak orderflow that
            # still cleared the gate → clamp stop to the wider half of [MIN, MAX]
            # (give more breathing room since conviction is borderline).
            if of_score < self.ORDERFLOW_SCORE_TIGHT:
                stop_ticks = max(stop_ticks, (self.MIN_ENTRY_STOP_TICKS + self.MAX_ENTRY_STOP_TICKS) / 2)
                if model_side == PositionSide.LONG:
                    stop_price = current_price - stop_ticks * TICK_SIZE
                else:
                    stop_price = current_price + stop_ticks * TICK_SIZE

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
            return self._signal(
                action,
                current_price,
                q_values=[q_cont, q_rev],
                q_spread=q_spread,
                confidence=confidence,
                stop_price=stop_price,
                size=size,
                reason="new_entry",
            )

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
                return self._signal(
                    action,
                    current_price,
                    q_values=[q_cont, q_rev],
                    q_spread=q_spread,
                    confidence=confidence,
                    stop_price=stop_price,
                    size=size,
                    closed_pnl_r=pnl,
                    reason="direction_flip",
                )

            elif model_side != self.position.side:
                # BOS without conviction to flip — move to breakeven (Fabio's rule)
                # "BOS without volume = breakeven, BOS WITH volume = exit"
                if self.position.side == PositionSide.LONG:
                    be_price = self.position.entry_price + 1 * TICK_SIZE
                    if be_price > self.position.stop_price:
                        self.position.stop_price = be_price
                        return self._signal(
                            "move_to_breakeven",
                            current_price,
                            q_values=[q_cont, q_rev],
                            q_spread=q_spread,
                            confidence=confidence,
                            stop_price=be_price,
                            reason="bos_no_conviction_breakeven",
                        )
                else:
                    be_price = self.position.entry_price - 1 * TICK_SIZE
                    if be_price < self.position.stop_price:
                        self.position.stop_price = be_price
                        return self._signal(
                            "move_to_breakeven",
                            current_price,
                            q_values=[q_cont, q_rev],
                            q_spread=q_spread,
                            confidence=confidence,
                            stop_price=be_price,
                            reason="bos_no_conviction_breakeven",
                        )

            if model_side == self.position.side:
                # Same direction at the ENTRY level = exit into strength.
                entry_dist_ticks = abs(current_price - self.position.entry_price) / TICK_SIZE
                is_entry_retest = entry_dist_ticks <= STOP_TICKS  # within 1R of entry
                if is_entry_retest and q_cont > q_rev:
                    pnl = self._close_position(current_price, "entry_retest_exit")
                    return self._signal(
                        "close_position",
                        current_price,
                        q_values=[q_cont, q_rev],
                        q_spread=q_spread,
                        confidence=confidence,
                        closed_pnl_r=pnl,
                        reason="entry_retest_cont_exit",
                    )

                # Graduated trail: modulate stop-tightness by orderflow confluence.
                # Strong OF → trail tight (level - 2t, lock gains)
                # Weak OF → trail normal (let it breathe)
                trail_ticks = 2.0 if of_score < self.ORDERFLOW_SCORE_TIGHT else 1.0
                aggressive_stop = (
                    current_price - (trade_dir_sign * trail_ticks * TICK_SIZE) * -1
                )  # stop at level - trail_ticks (opposite dir)
                # Actually: stop is behind the trade, so subtract trail_ticks from level
                if self.position.side == PositionSide.LONG:
                    candidate = current_price - trail_ticks * TICK_SIZE
                    new_stop = max(self.position.stop_price, candidate, stop_price)
                else:
                    candidate = current_price + trail_ticks * TICK_SIZE
                    new_stop = min(self.position.stop_price, candidate, stop_price)
                if new_stop != self.position.stop_price:
                    self.position.stop_price = new_stop
                    self.position.levels_captured += 1
                    return self._signal(
                        "trail_stop",
                        current_price,
                        q_values=[q_cont, q_rev],
                        q_spread=q_spread,
                        confidence=confidence,
                        orderflow_score=of_score,
                        stop_price=new_stop,
                        reason=f"level_{self.position.levels_captured}_captured_trail_{trail_ticks:.0f}t",
                    )

            # OPPOSING signal in position — graduated response by signal strength
            else:
                opposing_ratio = q_spread / max(self.position.entry_q_spread, 0.01)
                mode, tight_ticks = self._graduated_trail_ticks(opposing_ratio)

                if mode == "hold":
                    # Weak opposing signal → let position ride, no adjustment
                    return self._signal(
                        "hold",
                        current_price,
                        q_values=[q_cont, q_rev],
                        q_spread=q_spread,
                        confidence=confidence,
                        orderflow_score=of_score,
                        opposing_ratio=opposing_ratio,
                        reason="weak_opposing_ride",
                    )
                elif mode == "tight":
                    # Strong opposing → tighten stop to lock gains (level - 5 ticks)
                    if self.position.side == PositionSide.LONG:
                        candidate = current_price - tight_ticks * TICK_SIZE
                        new_stop = max(self.position.stop_price, candidate)
                    else:
                        candidate = current_price + tight_ticks * TICK_SIZE
                        new_stop = min(self.position.stop_price, candidate)
                    if new_stop != self.position.stop_price:
                        self.position.stop_price = new_stop
                        return self._signal(
                            "trail_stop",
                            current_price,
                            q_values=[q_cont, q_rev],
                            q_spread=q_spread,
                            confidence=confidence,
                            opposing_ratio=opposing_ratio,
                            stop_price=new_stop,
                            reason="strong_opposing_tight_trail",
                        )

            # Model agrees but no stop improvement — hold
            return self._signal(
                "hold",
                current_price,
                q_values=[q_cont, q_rev],
                q_spread=q_spread,
                confidence=confidence,
                reason="hold_position",
            )

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

        if self._use_gbt:
            action_idx, confidence, prob_cont, prob_rev = self._network.predict_direction(obs)
            stop_ticks = self._network.predict_stop(obs)
            q_cont, q_rev = prob_cont, prob_rev
            q_spread = confidence
        else:
            obs_tensor = torch.from_numpy(obs).unsqueeze(0)
            with torch.no_grad():
                q_values, stop_pred = self._network.forward_full(obs_tensor)
            q_cont = float(q_values[0, Action.CONTINUATION.value])
            q_rev = float(q_values[0, Action.REVERSAL.value])
            q_spread = abs(q_cont - q_rev)
            stop_ticks = float(stop_pred[0, 0])

        # Determine model's preferred direction — FORCE_REV_ONLY drops CONT selection
        approach = state.get("approach_direction", "up")
        if self.FORCE_REV_ONLY:
            model_side = PositionSide.SHORT if approach == "up" else PositionSide.LONG
            is_reversal = True
        elif q_cont > q_rev:
            model_side = PositionSide.LONG if approach == "up" else PositionSide.SHORT
            is_reversal = False
        else:
            model_side = PositionSide.SHORT if approach == "up" else PositionSide.LONG
            is_reversal = True

        # Compute stop price from zone BOUNDARY (not center)
        if model_side == PositionSide.LONG:
            stop_price = zone.lower_bound - stop_ticks * TICK_SIZE
        else:
            stop_price = zone.upper_bound + stop_ticks * TICK_SIZE

        confidence = min(q_spread / 0.10, 1.0) if not self._use_gbt else q_spread
        size = self._compute_size(confidence)

        # Orderflow confluence score for this direction
        trade_dir_sign = 1 if model_side == PositionSide.LONG else -1
        of_score = self._compute_orderflow_score(state, trade_dir_sign)

        # Reversal cushion check
        if is_reversal and self.session.total_pnl_r < self.REVERSAL_CUSHION_R:
            return self._signal(
                "skip", current_price, q_spread=q_spread, confidence=confidence, reason="reversal_no_cushion"
            )

        if self.INDEPENDENT_MODE:
            if q_spread < self.MIN_Q_SPREAD:
                return self._signal(
                    "skip", current_price, q_spread=q_spread, confidence=confidence, reason="low_confidence"
                )

            action = f"signal_{model_side.value}"
            return self._signal(
                action,
                current_price,
                q_values=[q_cont, q_rev],
                q_spread=q_spread,
                confidence=confidence,
                stop_price=stop_price,
                size=size,
                zone_members=zone.member_count,
                reason="independent_signal",
            )

        if not self.position.is_open:
            if q_spread < self.MIN_Q_SPREAD:
                return self._signal(
                    "skip", current_price, q_spread=q_spread, confidence=confidence, reason="low_confidence"
                )

            # Orderflow confluence gate — zone quality alone is not enough
            if of_score < self.ORDERFLOW_SCORE_MIN:
                return self._signal(
                    "skip",
                    current_price,
                    q_spread=q_spread,
                    confidence=confidence,
                    orderflow_score=of_score,
                    zone_members=zone.member_count,
                    reason="orderflow_weak",
                )

            # Entry stop sanity bounds
            if stop_ticks < self.MIN_ENTRY_STOP_TICKS or stop_ticks > self.MAX_ENTRY_STOP_TICKS:
                return self._signal(
                    "skip",
                    current_price,
                    q_spread=q_spread,
                    confidence=confidence,
                    stop_ticks=stop_ticks,
                    reason="stop_out_of_bounds",
                )

            # Widen stop on weak-but-passing orderflow (more breathing room for borderline)
            if of_score < self.ORDERFLOW_SCORE_TIGHT:
                widened = max(stop_ticks, (self.MIN_ENTRY_STOP_TICKS + self.MAX_ENTRY_STOP_TICKS) / 2)
                if widened != stop_ticks:
                    stop_ticks = widened
                    if model_side == PositionSide.LONG:
                        stop_price = zone.lower_bound - stop_ticks * TICK_SIZE
                    else:
                        stop_price = zone.upper_bound + stop_ticks * TICK_SIZE

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
            return self._signal(
                action,
                current_price,
                q_values=[q_cont, q_rev],
                q_spread=q_spread,
                confidence=confidence,
                orderflow_score=of_score,
                stop_price=stop_price,
                size=size,
                zone_members=zone.member_count,
                reason="new_entry",
            )

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
                return self._signal(
                    action,
                    current_price,
                    q_values=[q_cont, q_rev],
                    q_spread=q_spread,
                    confidence=confidence,
                    stop_price=stop_price,
                    size=size,
                    closed_pnl_r=pnl,
                    zone_members=zone.member_count,
                    reason="direction_flip",
                )

            elif model_side != self.position.side:
                if self.position.side == PositionSide.LONG:
                    be_price = self.position.entry_price + 1 * TICK_SIZE
                    if be_price > self.position.stop_price:
                        self.position.stop_price = be_price
                        return self._signal(
                            "move_to_breakeven",
                            current_price,
                            q_values=[q_cont, q_rev],
                            q_spread=q_spread,
                            confidence=confidence,
                            stop_price=be_price,
                            reason="bos_no_conviction_breakeven",
                        )
                else:
                    be_price = self.position.entry_price - 1 * TICK_SIZE
                    if be_price < self.position.stop_price:
                        self.position.stop_price = be_price
                        return self._signal(
                            "move_to_breakeven",
                            current_price,
                            q_values=[q_cont, q_rev],
                            q_spread=q_spread,
                            confidence=confidence,
                            stop_price=be_price,
                            reason="bos_no_conviction_breakeven",
                        )

            if model_side == self.position.side:
                new_stop = self._trail_stop(current_price, stop_price)
                if new_stop != self.position.stop_price:
                    self.position.stop_price = new_stop
                    self.position.levels_captured += 1
                    return self._signal(
                        "trail_stop",
                        current_price,
                        q_values=[q_cont, q_rev],
                        q_spread=q_spread,
                        confidence=confidence,
                        stop_price=new_stop,
                        reason=f"level_{self.position.levels_captured}_captured",
                    )

            return self._signal(
                "hold",
                current_price,
                q_values=[q_cont, q_rev],
                q_spread=q_spread,
                confidence=confidence,
                reason="hold_position",
            )

    # STOP2 (framework "close required, not wick"): configurable wick buffer.
    # When >0, price must push past stop_price by this many ticks before the
    # stop triggers. Gives the trade room to wick through the stop level
    # without exiting. Set to 0 for strict tick-level stops (legacy).
    # Pick a value that matches your typical MAE tolerance — e.g. 2 ticks
    # lets small wicks pass, 5 ticks tolerates aggressive fake-outs.
    STOP_WICK_BUFFER_TICKS: float = 0.0

    def on_price_update(self, current_price: float) -> dict | None:
        """Check if stop was hit on a price update (called on every tick/bar).

        When STOP_WICK_BUFFER_TICKS > 0, the stop fires only if the price has
        travelled past the stop level by that many ticks — matching the
        framework rule that a wick through the stop doesn't invalidate the
        trade, only a clean push beyond does.

        Returns signal dict if stop hit, None otherwise.
        """
        if not self.position.is_open:
            return None

        buffer_px = self.STOP_WICK_BUFFER_TICKS * TICK_SIZE
        stopped = False
        if self.position.side == PositionSide.LONG:
            if current_price <= self.position.stop_price - buffer_px:
                stopped = True
        elif self.position.side == PositionSide.SHORT:
            if current_price >= self.position.stop_price + buffer_px:
                stopped = True

        if stopped:
            pnl = self._close_position(current_price, "stop")
            return self._signal("stopped_out", current_price, closed_pnl_r=pnl, reason="stop_hit")
        return None

    def on_bar_close(self, close_price: float) -> dict | None:
        """Strict close-only stop invalidation — framework "close required" rule.

        Fires the stop only when a CANDLE CLOSE is beyond the stop level. Use
        in place of per-tick `on_price_update` when you want the framework-pure
        semantics: wicks through the stop level are tolerated, only a bar
        closing beyond the stop invalidates the trade.

        Intended pairing: disable `on_price_update` stop checks (or set
        STOP_WICK_BUFFER_TICKS very high) and call this on every bar close.
        """
        if not self.position.is_open:
            return None

        stopped = False
        if (
            self.position.side == PositionSide.LONG
            and close_price <= self.position.stop_price
            or self.position.side == PositionSide.SHORT
            and close_price >= self.position.stop_price
        ):
            stopped = True

        if stopped:
            pnl = self._close_position(close_price, "stop_close")
            return self._signal("stopped_out", close_price, closed_pnl_r=pnl, reason="stop_close")
        return None

    def on_session_end(self, current_price: float) -> dict | None:
        """Close any open position at session end."""
        if not self.position.is_open:
            return None
        pnl = self._close_position(current_price, "session_end")
        return self._signal("session_close", current_price, closed_pnl_r=pnl, reason="session_end")

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

    # --- Orderflow confluence gate (Phase 1 live) -----------------------------

    @staticmethod
    def _compute_orderflow_score(state: dict, trade_direction: int) -> float:
        """Orderflow confluence score [0.0, 1.0] — how strongly orderflow
        confirms the intended trade direction.

        Components (Fabio's framework):
        - delta_pct direction match (0.20 weight): initiative in our direction
        - CVD trend alignment (0.20): cumulative flow supports us
        - Stacked imbalance cluster (0.25): 3+ consecutive imbalances
        - Absorption at level (0.20): large wall pattern confirming rejection
        - Big-trade net delta alignment (0.15): institutional flow with us

        Returns score ∈ [0, 1]. Score < 0.30 = too weak to trade.
        trade_direction: +1 for long, -1 for short.
        """
        candles = state.get("candles") or []
        signals = state.get("orderflow_signals")

        score = 0.0

        # 1. Delta direction match (weight 0.20)
        if candles:
            last = candles[-1]
            vol = max(getattr(last, "volume", 1), 1)
            delta_pct = getattr(last, "delta", 0) / vol  # signed
            # Full credit if delta >15% in our direction
            delta_score = max(-1.0, min(1.0, delta_pct * trade_direction / 0.15))
            if delta_score > 0:
                score += 0.20 * delta_score

        # 2. CVD trend alignment (weight 0.20)
        if signals is not None:
            cvd_trend = getattr(signals, "cvd_trend", "flat")
            # For long trade (dir=+1): "rising" is good. For short (dir=-1): "falling".
            if (trade_direction == 1 and cvd_trend == "rising") or (trade_direction == -1 and cvd_trend == "falling"):
                score += 0.20
            elif cvd_trend == "flat":
                score += 0.05  # neutral — partial credit

        # 3. Stacked imbalance cluster (weight 0.25)
        if signals is not None:
            sic = getattr(signals, "stacked_imbalance_count", 0) or 0
            sdir = getattr(signals, "stacked_direction", None)
            # direction map: "buy" = up-push (good for long), "sell" = down-push (good for short)
            wants_buy = trade_direction == 1
            matches = (wants_buy and sdir == "buy") or (not wants_buy and sdir == "sell")
            if matches:
                score += 0.25 * min(sic / 3.0, 1.0)  # full credit at 3+ stacked

        # 4. Absorption at level (weight 0.20) — reversal confirmation
        if signals is not None:
            vsa = float(getattr(signals, "vsa_absorption", 0) or 0)
            absorb_strength = float(getattr(signals, "absorption_strength", 0) or 0)
            # Absorption confirms reversal: large volume + small body = wall
            abs_score = max(vsa, absorb_strength)
            score += 0.20 * min(abs_score, 1.0)

        # 5. Big-trade net delta alignment (weight 0.15)
        if signals is not None:
            big_net = float(getattr(signals, "big_trades_net_delta", 0) or 0)
            if trade_direction == 1 and big_net > 0:
                score += 0.15 * min(big_net / 100.0, 1.0)  # >100 net contracts = full
            elif trade_direction == -1 and big_net < 0:
                score += 0.15 * min(-big_net / 100.0, 1.0)

        return max(0.0, min(1.0, score))

    @staticmethod
    def _graduated_trail_ticks(opposing_spread_ratio: float, base_trail_ticks: float = 2.0) -> tuple[str, float]:
        """Graduated trail behavior based on opposing signal strength at a new level.

        opposing_spread_ratio = new_signal_spread / position_entry_spread

        Returns (mode, trail_ticks_behind_level):
          - "hold"  = no adjustment, leave stop where it was
          - "normal" = trail to level - 2 ticks (current default)
          - "tight"  = trail to level - 5 ticks (strong opposing → lock gains)
          - "flip"   = caller should close and flip (very strong opposing)
        """
        if opposing_spread_ratio < 0.5:
            return "hold", 0.0
        if opposing_spread_ratio < 1.0:
            return "normal", base_trail_ticks
        if opposing_spread_ratio < 2.0:
            return "tight", 5.0
        return "flip", 0.0

    def _compute_size(self, confidence: float) -> float:
        """Compute position size based on composite confidence + session P&L."""
        from src.rl.confidence import size_multiplier

        base = self.BASE_SIZE

        # Composite confidence → sizing tier (0x to 1.5x)
        size = base * size_multiplier(confidence)

        # Intraday compounding: increase after profits ONLY if no recent losses
        # (Fabio: "never raise exposure to recover")
        if self.session.total_pnl_r > self.COMPOUND_THRESHOLD_R and self.session.consecutive_losses == 0:
            compound_steps = int((self.session.total_pnl_r - self.COMPOUND_THRESHOLD_R) / self.COMPOUND_THRESHOLD_R)
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
            **{k: round(v, 4) if isinstance(v, float) else v for k, v in kwargs.items()},
        }

    def on_structural_event(self, event_type: str, state: dict) -> None:
        """Update narrative context when a structural event occurs.

        Events: ib_close, new_swing_high, new_swing_low, value_area_breach,
                single_print_created.
        """
        if hasattr(self, "_inference_v5") and self._inference_v5 is not None:
            self._inference_v5.update_narrative(state)
            log.info("Narrative updated on %s", event_type)

    def get_session_summary(self) -> dict:
        """Get session summary for reporting."""
        trades = self.session.trades
        if not trades:
            return {"trades": 0, "total_pnl_r": 0.0, "win_rate": 0.0, "winners": 0, "losers": 0, "flips": 0}

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
            "profit_factor": round(sum(t.pnl_r for t in winners) / max(abs(sum(t.pnl_r for t in losers)), 0.01), 2)
            if winners
            else 0.0,
        }
