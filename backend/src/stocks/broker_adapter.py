"""TopstepX broker adapter — risk-checked order execution.

Wraps TopstepXClient with the same interface as the server-side
BrokerAdapter (Tradovate/Rithmic), adding risk checks, position
tracking, and EOD flatten support. Persists every trade to the
broker_trades table for stats/bankroll.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)

MIN_TRADE_INTERVAL_S = 30.0

# NQ tick value: $5 per tick (0.25 point), $20 per point
_NQ_POINT_VALUE = 20.0


def _save_broker_trade(**kwargs) -> None:
    """Persist a trade row to broker_trades (fire-and-forget, never blocks)."""
    try:
        from ..db.models import BrokerTrade, get_session

        session = get_session()
        trade = BrokerTrade(**kwargs)
        session.add(trade)
        session.commit()
    except Exception:
        log.exception("Failed to save broker trade")


class TopstepXBrokerAdapter:
    """Risk-enforced order execution for TopstepX."""

    def __init__(self, client, config) -> None:
        from ..broker.position_tracker import PositionTracker

        self.client = client
        self.config = config
        self.tracker = PositionTracker()
        self._halted = False
        self._halt_reason = ""
        self._pending_trade: dict | None = None  # tracks open entry for DB persistence

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

        # Don't close tracker here — real exit price arrives via on_stream_fill.
        # Just log intent; P&L will be calculated when the fill comes through.
        log.info("Flatten requested (%s): session=$%.2f", reason, self.tracker.session_pnl)
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
            is_stop = abs(price - self.tracker.stop_price) < 1.0 if self.tracker.stop_price else False
            entry_px = self.tracker.entry_price
            self.tracker.on_exit(exit_price=price, was_stop=is_stop)
            log.info(
                "Stream fill (exit): %.2f stop=%s session_pnl=$%.2f",
                price,
                is_stop,
                self.tracker.session_pnl,
            )

            # Persist completed trade to DB
            if self._pending_trade and entry_px:
                side = self._pending_trade["side"]
                direction = 1.0 if side == "long" else -1.0
                pnl_pts = direction * (price - entry_px)
                pnl_dollars = pnl_pts * _NQ_POINT_VALUE * self._pending_trade["size"]
                stop_dist = self._pending_trade.get("stop_price", 0)
                risk_pts = abs(entry_px - stop_dist) if stop_dist else 10.0 * 0.25
                pnl_r = pnl_pts / max(risk_pts, 0.25)
                _save_broker_trade(
                    ts=self._pending_trade["ts"],
                    session_date=self._pending_trade["session_date"],
                    symbol=self._pending_trade["symbol"],
                    side=side,
                    size=self._pending_trade["size"],
                    entry_price=entry_px,
                    stop_price=stop_dist,
                    exit_price=price,
                    pnl_dollars=round(pnl_dollars, 2),
                    pnl_r=round(pnl_r, 3),
                    signal_action=self._pending_trade.get("signal_action"),
                    signal_confidence=self._pending_trade.get("signal_confidence"),
                    signal_zone=self._pending_trade.get("signal_zone"),
                    closed_at=datetime.now(timezone.utc),
                )
                self._pending_trade = None
        else:
            self.tracker.entry_price = price
            log.info("Stream fill (entry): %.2f", price)
            # Update pending trade with actual fill price
            if self._pending_trade:
                self._pending_trade["entry_price"] = price

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
            log.info(
                "Signal rejected — too soon (%.0fs < %.0fs)",
                time.time() - self.tracker.last_trade_ts,
                MIN_TRADE_INTERVAL_S,
            )
            return {"rejected": True, "reason": "min_interval"}

        return None

    async def _execute_entry(self, signal: dict) -> dict:
        """Place market + stop orders with position management."""
        action = signal["action"]
        is_long = "long" in action.lower()
        order_action = "Buy" if is_long else "Sell"
        stop_action = "Sell" if is_long else "Buy"
        raw_size = float(signal.get("size", 1) or 1)
        # Model sends fractional Kelly sizing (e.g. 0.25 = 25% of max_position)
        if raw_size < 1:
            size = max(1, round(raw_size * self.config.max_position))
        else:
            size = min(int(raw_size), self.config.max_position)
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

        # Track pending trade for DB persistence (closed on_stream_fill)
        now = datetime.now(timezone.utc)
        self._pending_trade = {
            "ts": now,
            "session_date": now.strftime("%Y-%m-%d"),
            "symbol": "NQ",
            "side": side,
            "size": size,
            "stop_price": stop_price,
            "signal_action": action,
            "signal_confidence": float(signal.get("confidence", 0) or 0),
            "signal_zone": float(signal.get("zone_price", 0) or 0),
        }

        return {
            "action": action,
            "side": side,
            "size": size,
            "stop_price": stop_price,
            "stop_order_id": stop_order_id,
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
