"""Broker adapter — translates signals to orders with risk enforcement."""
from __future__ import annotations

import logging
import time

from .config import BrokerConfig
from .tradovate_client import TradovateClient, ACTION_BUY, ACTION_SELL
from .position_tracker import PositionTracker

log = logging.getLogger(__name__)

# Actions that trigger order placement
_ENTRY_ACTIONS = {"enter_long", "enter_short", "signal_long", "signal_short"}
_FLIP_ACTIONS = {"flip_long", "flip_short"}
_TRAIL_ACTIONS = {"trail_stop"}
_EXIT_ACTIONS = {"exit", "flatten"}


class BrokerAdapter:
    """Translates session manager signals into broker orders."""

    def __init__(self, client: TradovateClient, config: BrokerConfig) -> None:
        self.client = client
        self.config = config
        self.tracker = PositionTracker()
        self._halted = False
        self._halt_reason = ""

    async def on_signal(self, signal: dict) -> dict | None:
        """Process a session manager signal. Returns fill info or None if rejected."""
        action = signal.get("action", "")
        price = signal.get("price", 0.0)

        # Ignore non-trade actions
        if action in ("skip", "hold", "move_to_breakeven"):
            return None

        # Check if halted
        if self._halted:
            log.warning("Trading halted (%s) — ignoring signal %s", self._halt_reason, action)
            return None

        # Risk checks
        rejection = self._check_risk(signal)
        if rejection:
            log.info("Signal rejected: %s — %s", action, rejection)
            return None

        # Trail stop
        if action in _TRAIL_ACTIONS:
            return await self._trail_stop(signal)

        # Flatten/exit
        if action in _EXIT_ACTIONS:
            return await self.flatten(action)

        # Entry or flip
        if action in _ENTRY_ACTIONS or action in _FLIP_ACTIONS:
            return await self._execute_entry(signal)

        log.debug("Unhandled action: %s", action)
        return None

    def _check_risk(self, signal: dict) -> str | None:
        """Check risk rules. Returns rejection reason or None if OK."""
        # Daily loss limit
        if self.tracker.exceeds_daily_loss(self.config.max_daily_loss):
            self._halt("daily_loss_limit")
            return "daily loss exceeded"

        # Trailing drawdown
        if self.tracker.exceeds_trailing_dd(self.config.max_trailing_dd):
            self._halt("trailing_drawdown")
            return "trailing drawdown exceeded"

        # Consecutive stops
        if self.tracker.consecutive_stops >= 3:
            self._halt("3_consecutive_stops")
            return "3 consecutive stops"

        # Min trade interval (applies when we recently had a trade, regardless of current position)
        elapsed = time.time() - self.tracker.last_trade_ts
        if self.tracker.last_trade_ts > 0 and elapsed < self.config.min_trade_interval_s:
            return "too soon (%.0fs < %.0fs)" % (elapsed, self.config.min_trade_interval_s)

        return None

    async def _execute_entry(self, signal: dict) -> dict | None:
        """Execute an entry or flip signal."""
        action = signal["action"]
        price = signal.get("price", 0.0)
        stop_price = signal.get("stop_price", 0.0)
        size = min(int(signal.get("size", 1) or 1), self.config.max_position)

        # If we have a position, flatten first
        if not self.tracker.is_flat:
            await self.flatten("flip")

        # Determine direction
        is_long = "long" in action
        order_action = ACTION_BUY if is_long else ACTION_SELL
        stop_action = ACTION_SELL if is_long else ACTION_BUY

        # Place market order
        try:
            order_result = await self.client.place_market_order(order_action, size)
        except Exception as e:
            log.error("Market order failed: %s", e)
            return None

        # Place stop-loss
        stop_order_id = None
        if stop_price > 0:
            try:
                stop_result = await self.client.place_stop_order(stop_action, size, stop_price)
                stop_order_id = stop_result.get("orderId")
            except Exception as e:
                log.error("Stop order failed: %s — position open WITHOUT stop!", e)

        # Update tracker
        self.tracker.on_fill(
            side="long" if is_long else "short",
            price=price, size=size, stop_price=stop_price,
            signal_price=price,
        )
        self.tracker.stop_order_id = stop_order_id

        return {
            "action": action,
            "side": "long" if is_long else "short",
            "price": price,
            "size": size,
            "stop_price": stop_price,
            "order_id": order_result.get("orderId"),
            "stop_order_id": stop_order_id,
        }

    async def _trail_stop(self, signal: dict) -> dict | None:
        """Modify the stop order to a new price."""
        new_stop = signal.get("stop_price", 0.0)
        if not new_stop or self.tracker.stop_order_id is None:
            return None
        try:
            await self.client.modify_order(self.tracker.stop_order_id, new_stop)
            self.tracker.stop_price = new_stop
            return {"action": "trail_stop", "new_stop": new_stop}
        except Exception as e:
            log.error("Stop modify failed: %s", e)
            return None

    async def flatten(self, reason: str = "manual") -> dict:
        """Close all positions and cancel pending orders."""
        log.info("Flattening: %s", reason)

        # Cancel stop order
        if self.tracker.stop_order_id:
            try:
                await self.client.cancel_order(self.tracker.stop_order_id)
            except Exception:
                log.warning("Failed to cancel stop order %d", self.tracker.stop_order_id)

        # Liquidate position
        if not self.tracker.is_flat:
            try:
                await self.client.liquidate_position()
            except Exception:
                log.error("CRITICAL: Liquidation failed!")

            # Estimate exit at current price (will be updated when fill confirmed)
            self.tracker.on_exit(exit_price=0.0, was_stop=(reason == "stop"))

        return {"action": "flatten", "reason": reason, "session_pnl": self.tracker.session_pnl}

    async def modify_stop(self, new_stop_price: float) -> dict | None:
        """Public method to move stop."""
        return await self._trail_stop({"stop_price": new_stop_price})

    def _halt(self, reason: str) -> None:
        """Halt trading for the session."""
        self._halted = True
        self._halt_reason = reason
        log.warning("TRADING HALTED: %s (session P&L: $%.2f)", reason, self.tracker.session_pnl)

    def reset_session(self) -> None:
        """Reset for new trading day."""
        self.tracker.reset_session()
        self._halted = False
        self._halt_reason = ""
