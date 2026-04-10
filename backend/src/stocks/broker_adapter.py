"""TopstepX broker adapter — risk-checked order execution.

Wraps TopstepXClient with the same interface as the server-side
BrokerAdapter (Tradovate/Rithmic), adding risk checks, position
tracking, and EOD flatten support.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

MIN_TRADE_INTERVAL_S = 30.0


class TopstepXBrokerAdapter:
    """Risk-enforced order execution for TopstepX."""

    def __init__(self, client, config) -> None:
        from ..broker.position_tracker import PositionTracker
        self.client = client
        self.config = config
        self.tracker = PositionTracker()
        self._halted = False
        self._halt_reason = ""

    async def on_signal(self, signal: dict) -> dict | None:
        """Risk check then execute. Returns result dict or None if skipped."""
        action = signal.get("action", "")
        if action.lower() in ("skip", "hold", ""):
            return None

        if self._halted:
            log.warning("Signal rejected — halted: %s", self._halt_reason)
            return {"rejected": True, "reason": self._halt_reason}

        rejection = self._check_risk()
        if rejection:
            return rejection

        if action in ("enter_long", "enter_short"):
            return await self._execute_entry(signal)
        elif action in ("flatten", "exit"):
            return await self.flatten(action)
        elif action == "trail_stop":
            return await self._trail_stop(signal)

        log.warning("Unknown signal action: %s", action)
        return None

    async def flatten(self, reason: str = "manual") -> dict:
        """Cancel stop order and liquidate position."""
        if self.tracker.stop_order_id:
            try:
                await self.client.cancel_order(self.tracker.stop_order_id)
            except Exception:
                log.warning("Failed to cancel stop order %s", self.tracker.stop_order_id)

        if not self.tracker.is_flat:
            try:
                await self.client.liquidate_position()
            except Exception:
                log.exception("Failed to liquidate position")

        pnl = self.tracker.on_exit(exit_price=0.0, was_stop=(reason == "stop"))
        log.info("Flattened (%s): pnl=$%.2f session=$%.2f", reason, pnl, self.tracker.session_pnl)
        return {"action": "flatten", "reason": reason, "session_pnl": self.tracker.session_pnl}

    async def modify_stop(self, new_stop_price: float) -> dict | None:
        """Move existing stop order to new price."""
        if not self.tracker.stop_order_id:
            return None
        try:
            await self.client.modify_order(self.tracker.stop_order_id, new_stop_price)
            self.tracker.stop_price = new_stop_price
            log.info("Stop moved to %.2f", new_stop_price)
            return {"action": "modify_stop", "stop_price": new_stop_price}
        except Exception:
            log.exception("Failed to modify stop")
            return None

    def on_stream_fill(self, fill: dict) -> None:
        """Update tracker from real TopstepX fill (GatewayUserTrade)."""
        price = float(fill.get("price", 0))
        if price == 0:
            return

        if not self.tracker.is_flat:
            is_stop = (
                abs(price - self.tracker.stop_price) < 1.0
                if self.tracker.stop_price else False
            )
            self.tracker.on_exit(exit_price=price, was_stop=is_stop)
            log.info("Stream fill (exit): %.2f stop=%s session_pnl=$%.2f",
                     price, is_stop, self.tracker.session_pnl)
        else:
            self.tracker.entry_price = price
            log.info("Stream fill (entry): %.2f", price)

    def reset_session(self) -> None:
        """Daily midnight reset."""
        self._halted = False
        self._halt_reason = ""
        self.tracker.reset_session()
        log.info("Session reset")

    def _check_risk(self) -> dict | None:
        """Run risk checks. Returns rejection dict or None if OK."""
        if self.tracker.exceeds_daily_loss(self.config.max_daily_loss):
            self._halt(f"daily loss limit ${self.config.max_daily_loss}")
            return {"rejected": True, "reason": self._halt_reason}

        if self.tracker.exceeds_trailing_dd(self.config.max_trailing_dd):
            self._halt(f"trailing DD limit ${self.config.max_trailing_dd}")
            return {"rejected": True, "reason": self._halt_reason}

        if self.tracker.consecutive_stops >= 3:
            self._halt("3 consecutive stops")
            return {"rejected": True, "reason": self._halt_reason}

        if time.time() - self.tracker.last_trade_ts < MIN_TRADE_INTERVAL_S:
            log.info("Signal rejected — too soon (%.0fs < %.0fs)",
                     time.time() - self.tracker.last_trade_ts, MIN_TRADE_INTERVAL_S)
            return {"rejected": True, "reason": "min_interval"}

        return None

    async def _execute_entry(self, signal: dict) -> dict:
        """Place market + stop orders with position management."""
        action = signal["action"]
        is_long = "long" in action.lower()
        order_action = "Buy" if is_long else "Sell"
        stop_action = "Sell" if is_long else "Buy"
        size = min(int(signal.get("size", 1) or 1), self.config.max_position)
        stop_price = float(signal.get("stop_price", 0) or 0)

        if not self.tracker.is_flat:
            await self.flatten("flip")

        log.info("Executing: %s size=%d stop=%.2f", action, size, stop_price)

        try:
            result = await self.client.place_market_order(order_action, size)
        except Exception:
            log.exception("Market order failed")
            return {"rejected": True, "reason": "order_failed"}

        stop_order_id = None
        if stop_price > 0:
            try:
                stop_result = await self.client.place_stop_order(stop_action, size, stop_price)
                stop_order_id = stop_result.get("orderId") if isinstance(stop_result, dict) else None
            except Exception:
                log.exception("Stop order failed (market order was placed)")

        side = "long" if is_long else "short"
        self.tracker.on_fill(side, price=0.0, size=size, stop_price=stop_price)
        self.tracker.stop_order_id = stop_order_id

        return {
            "action": action, "side": side, "size": size,
            "stop_price": stop_price, "stop_order_id": stop_order_id,
        }

    async def _trail_stop(self, signal: dict) -> dict | None:
        """Move stop to new price from signal."""
        new_stop = signal.get("stop_price", 0)
        if new_stop and new_stop > 0:
            return await self.modify_stop(new_stop)
        return None

    def _halt(self, reason: str) -> None:
        """Halt trading for the session."""
        self._halted = True
        self._halt_reason = reason
        log.warning("HALTED: %s (session_pnl=$%.2f)", reason, self.tracker.session_pnl)
