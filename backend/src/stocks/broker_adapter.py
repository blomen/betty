"""TopstepX broker adapter — dynamic stop management with model signals.

Instead of flattening on every new signal, manages the position:
- Same direction signal at new zone → trail stop to previous zone (let winners ride)
- Opposite direction signal → exit and flip
- SKIP → hold current position

This implements the hybrid design: GBT decides at each level whether to
hold, tighten, or exit.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)

MIN_TRADE_INTERVAL_S = 30.0
MIN_CONFIDENCE = 0.30  # reject signals below this confidence
ZONE_COOLDOWN_S = 120.0  # don't re-enter same zone within 2 minutes
DEFAULT_STOP_TICKS = 25  # sensible default if model returns None
MIN_STOP_TICKS = 15  # minimum stop distance (prevent too-tight stops)
MAX_STOP_TICKS = 40  # maximum stop distance

# NQ tick value: $5 per tick (0.25 point), $20 per point
_NQ_TICK_VALUE = 5.0
_NQ_POINT_VALUE = 20.0

# Risk-based sizing: risk 1-2% of max drawdown per trade
RISK_PCT_BASE = 0.015  # 1.5% of drawdown for normal signals (conf 0.30-0.70)
RISK_PCT_HIGH = 0.02  # 2% for high confidence (conf > 0.70)


def _round_tick(price: float) -> float:
    """Round price to NQ tick increment (0.25 points)."""
    return round(price * 4) / 4


# Optional persistence sink — set by stocks_runtime.bootstrap_stocks() to a
# callable that ships the trade dict to the production DB. We keep this as a
# module-level hook so _log_broker_trade stays a free function and the adapter
# class doesn't need to know about transport.
_persist_callback = None


def set_persist_callback(cb) -> None:
    """Register a callable that persists each closed round-trip somewhere durable.

    The callable receives the same kwargs dict that gets logged. Called once
    per closed trade. Exceptions are swallowed (we don't want a transient DB
    write failure to mask the trade outcome in logs).
    """
    global _persist_callback
    _persist_callback = cb


def _log_broker_trade(**kwargs) -> None:
    """Log completed trade with full context and persist to dashboard."""
    result = "WIN" if kwargs.get("pnl_dollars", 0) > 0 else "LOSS"
    exit_reason = "STOP" if kwargs.get("was_stop") else "SIGNAL"
    if kwargs.get("trail_count", 0) > 0:
        exit_reason = f"TRAILED({kwargs['trail_count']})"

    log.info(
        "=== TRADE %s === %s %s %dx | entry=%.2f exit=%.2f stop=%.2f | "
        "PnL=$%.2f (%.2fR) | exit=%s trails=%d | "
        "signal=%s conf=%.3f cont_p=%.3f rev_p=%.3f zone=%.2f | "
        "stop_ticks=%s session_pnl=$%.2f",
        result,
        kwargs.get("symbol"),
        kwargs.get("side"),
        kwargs.get("size", 1),
        kwargs.get("entry_price", 0),
        kwargs.get("exit_price", 0),
        kwargs.get("stop_price", 0),
        kwargs.get("pnl_dollars", 0),
        kwargs.get("pnl_r", 0),
        exit_reason,
        kwargs.get("trail_count", 0),
        kwargs.get("signal_action", "?"),
        kwargs.get("signal_confidence", 0),
        kwargs.get("signal_cont_p", 0),
        kwargs.get("signal_rev_p", 0),
        kwargs.get("signal_zone", 0),
        kwargs.get("stop_ticks", "?"),
        kwargs.get("session_pnl", 0),
    )
    # Why we took it — surface the 1-line summary alongside the trade row so
    # `docker logs` is enough to scan the day's reasoning without joining DB.
    reasoning = kwargs.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("summary"):
        log.info("    why: %s", reasoning["summary"])
    # Add session_pnl to kwargs for dashboard
    from . import dashboard

    dashboard.record_trade(kwargs)

    if _persist_callback is not None:
        try:
            _persist_callback(kwargs)
        except Exception:
            log.exception("BrokerTrade persist callback failed (trade still in logs)")


class TopstepXBrokerAdapter:
    """Risk-enforced order execution with dynamic stop management."""

    def __init__(self, client, config) -> None:
        from ..broker.position_tracker import PositionTracker

        self.client = client
        self.config = config
        self.tracker = PositionTracker()
        self._halted = False
        self._halt_reason = ""
        self._pending_trade: dict | None = None
        self._zone_last_entry: dict[float, float] = {}  # zone_price → last_entry_ts
        self._trail_count = 0  # how many times we've trailed the stop

    async def on_signal(self, signal: dict) -> dict | None:
        """Handle signal with dynamic position management.

        - Flat + signal → enter position
        - In position + same direction → trail stop to this zone (let it ride)
        - In position + opposite direction → exit and flip
        """
        action = signal.get("action", "")
        if action.lower() in ("skip", "hold", ""):
            return None

        if self._halted:
            log.warning("Signal rejected — halted: %s", self._halt_reason)
            return {"rejected": True, "reason": self._halt_reason}

        # Confidence filter
        confidence = float(signal.get("confidence", 0) or 0)
        if confidence < MIN_CONFIDENCE:
            return None  # silent skip for low confidence

        rejection = self._check_risk()
        if rejection:
            return rejection

        is_long_signal = "long" in action.lower()
        signal_side = "long" if is_long_signal else "short"
        price = float(signal.get("price", 0) or 0)
        zone_price = float(signal.get("zone", 0) or 0)

        # --- FLAT: enter new position ---
        if self.tracker.is_flat:
            # Zone cooldown only applies to new entries
            if zone_price > 0:
                last_entry = self._zone_last_entry.get(zone_price, 0)
                if time.time() - last_entry < ZONE_COOLDOWN_S:
                    log.info("Signal rejected — zone %.2f cooldown", zone_price)
                    return {"rejected": True, "reason": "zone_cooldown"}

            result = await self._execute_entry(signal)
            if result and not result.get("rejected") and zone_price > 0:
                self._zone_last_entry[zone_price] = time.time()
                self._trail_count = 0
            return result

        # --- IN POSITION: same direction → trail stop ---
        if signal_side == self.tracker.side:
            if self.tracker.entry_price == 0.0:
                log.info("Signal skipped — awaiting entry fill confirmation")
                return None

            # Move stop to this zone (lock in profit at the level we just passed)
            entry = self.tracker.entry_price
            stop_dist = abs(entry - self.tracker.stop_price) if self.tracker.stop_price else DEFAULT_STOP_TICKS * 0.25
            new_stop = _round_tick(zone_price if zone_price > 0 else price)

            # First trail: lock +0.5R profit (covers $14 fees + $36 profit)
            if self.tracker.side == "long" and new_stop <= entry:
                if self._trail_count == 0:
                    new_stop = _round_tick(entry + stop_dist * 0.5)
                    log.info(
                        "CONT signal at %.2f — locking +0.5R profit, stop → %.2f (trail #%d)",
                        price,
                        new_stop,
                        self._trail_count + 1,
                    )
                else:
                    log.info("CONT signal at %.2f — stop already above entry, holding", price)
                    return None
            elif self.tracker.side == "short" and new_stop >= entry:
                if self._trail_count == 0:
                    new_stop = _round_tick(entry - stop_dist * 0.5)
                    log.info(
                        "CONT signal at %.2f — locking +0.5R profit, stop → %.2f (trail #%d)",
                        price,
                        new_stop,
                        self._trail_count + 1,
                    )
                else:
                    log.info("CONT signal at %.2f — stop already above entry, holding", price)
                    return None
            else:
                log.info(
                    "CONT signal at %.2f — trailing stop to %.2f (trail #%d, locking %.1fR profit)",
                    price,
                    new_stop,
                    self._trail_count + 1,
                    abs(new_stop - self.tracker.entry_price)
                    / (abs(self.tracker.stop_price - self.tracker.entry_price) or 1),
                )

            self._trail_count += 1
            if self._pending_trade:
                self._pending_trade["trail_count"] = self._trail_count
            return await self.modify_stop(new_stop)

        # --- IN POSITION: opposite direction → exit and flip ---
        if self.tracker.entry_price == 0.0:
            log.info("REV signal — awaiting entry fill confirmation before flipping")
            return None

        log.info(
            "REV signal at %.2f (conf=%.3f) — exiting %s and flipping to %s",
            price,
            confidence,
            self.tracker.side,
            signal_side,
        )
        await self.flatten("flip_on_reversal")
        self._trail_count = 0

        # Now enter the opposite direction
        if zone_price > 0:
            self._zone_last_entry[zone_price] = time.time()
        return await self._execute_entry(signal)

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
            if self.tracker.entry_price == 0.0:
                self.tracker.on_exit(0.0)
                self._pending_trade = None

        log.info("Flatten requested (%s): session=$%.2f", reason, self.tracker.session_pnl)
        return {"action": "flatten", "reason": reason, "session_pnl": self.tracker.session_pnl}

    async def add_to_position(self, add_contracts: int, price: float) -> dict | None:
        """Pyramid add — submit a market order in the same direction as the
        existing position and update the tracker. Stop stays where it is
        (risk unit unchanged; the add just compounds into the winner).
        """
        if self.tracker.is_flat or add_contracts <= 0:
            return None
        if self.tracker.size + add_contracts > self.config.max_position:
            log.info(
                "Pyramid add clipped: size=%d + add=%d > max=%d",
                self.tracker.size,
                add_contracts,
                self.config.max_position,
            )
            add_contracts = max(0, self.config.max_position - self.tracker.size)
            if add_contracts == 0:
                return {"rejected": True, "reason": "pyramid_at_cap"}

        order_action = "Buy" if self.tracker.side == "long" else "Sell"
        try:
            result = await self.client.place_market_order(order_action, add_contracts)
        except Exception:
            log.exception("Pyramid add order failed")
            return {"rejected": True, "reason": "order_failed"}

        if isinstance(result, dict) and not result.get("success", True):
            err = result.get("errorMessage", "order_rejected")
            log.warning("Pyramid add rejected: %s", err)
            return {"rejected": True, "reason": err}

        self.tracker.on_add(price=price, add_size=add_contracts)

        # Widen the stop order to cover the new total size so a hit closes
        # the whole position, not just the original contracts.
        if self.tracker.stop_order_id and self.tracker.stop_price > 0:
            try:
                await self.client.modify_order(self.tracker.stop_order_id, size=self.tracker.size)
            except Exception:
                log.warning("Failed to resize stop after pyramid add", exc_info=True)

        return {
            "action": "pyramid_add",
            "add_contracts": add_contracts,
            "total_size": self.tracker.size,
            "avg_entry": self.tracker.entry_price,
        }

    async def modify_stop(self, new_stop_price: float) -> dict | None:
        """Move existing stop order to new price.

        Defense-in-depth: a stop must never relax. Long stops only move up,
        short stops only move down. A misordered trail call that tried to
        widen risk would otherwise quietly increase exposure.
        """
        new_stop_price = _round_tick(new_stop_price)
        side = self.tracker.side
        cur_stop = self.tracker.stop_price
        if side == "long" and cur_stop > 0 and new_stop_price < cur_stop:
            log.warning("Refusing to relax long stop: %.2f → %.2f", cur_stop, new_stop_price)
            return {"action": "reject", "reason": "stop_relaxed", "stop_price": cur_stop}
        if side == "short" and cur_stop > 0 and new_stop_price > cur_stop:
            log.warning("Refusing to relax short stop: %.2f → %.2f", cur_stop, new_stop_price)
            return {"action": "reject", "reason": "stop_relaxed", "stop_price": cur_stop}
        if not self.tracker.stop_order_id:
            # No existing stop — place a new one
            try:
                stop_action = "Sell" if self.tracker.side == "long" else "Buy"
                result = await self.client.place_stop_order(stop_action, self.tracker.size or 1, new_stop_price)
                self.tracker.stop_order_id = result.get("orderId") if isinstance(result, dict) else None
                self.tracker.stop_price = new_stop_price
                log.info("New stop placed at %.2f", new_stop_price)
                return {"action": "new_stop", "stop_price": new_stop_price}
            except Exception:
                log.exception("Failed to place stop order")
                return None
        try:
            await self.client.modify_order(self.tracker.stop_order_id, stop_price=new_stop_price)
            self.tracker.stop_price = new_stop_price
            log.info("Stop moved to %.2f", new_stop_price)
            return {"action": "modify_stop", "stop_price": new_stop_price}
        except Exception:
            log.exception("Failed to modify stop")
            return None

    def on_stream_fill(self, fill: dict) -> None:
        """Update tracker from real TopstepX fill (GatewayUserTrade).

        Correlates by orderId when present (entry_order_id vs stop_order_id) so that
        out-of-order entry/exit fills can't get swapped. Falls back to the
        entry_price==0.0 sentinel only when orderId is missing.
        """
        data = fill.get("data", fill)
        price = float(data.get("price", 0))
        if price == 0:
            return
        # TopstepX has used both camelCase and snake_case in the past — accept either.
        order_id = data.get("orderId") or data.get("order_id") or data.get("OrderId")
        log.info(
            "Fill processing: price=%.2f side=%s size=%s order_id=%s",
            price,
            data.get("side"),
            data.get("size"),
            order_id,
        )

        if self.tracker.is_flat:
            log.warning("Stream fill (%.2f, order_id=%s) arrived while flat — dropping", price, order_id)
            return

        # Decide entry vs exit. Prefer orderId match; fall back to sentinel.
        if order_id is not None and self.tracker.entry_order_id is not None:
            is_entry = order_id == self.tracker.entry_order_id
            is_stop = order_id == self.tracker.stop_order_id
        else:
            is_entry = self.tracker.entry_price == 0.0
            is_stop = not is_entry and self.tracker.stop_price > 0 and abs(price - self.tracker.stop_price) < 1.0

        if is_entry:
            # Idempotent: a duplicate entry fill with the same orderId must not double-set.
            if self.tracker.entry_price == 0.0:
                self.tracker.entry_price = price
                if self._pending_trade:
                    self._pending_trade["entry_price"] = price
                    self._pending_trade["entry_fill_ts"] = datetime.now(timezone.utc)
                log.info("Stream fill (entry confirmed): %.2f order_id=%s", price, order_id)
            else:
                log.debug("Duplicate entry fill ignored: %.2f order_id=%s", price, order_id)
            return

        # Exit path — but if the entry fill hasn't reconciled yet, we can't compute PnL.
        entry_px = self.tracker.entry_price
        if entry_px == 0.0:
            log.error(
                "Out-of-order exit fill (%.2f, order_id=%s) before entry confirmation — "
                "skipping; tracker left open until entry fill arrives",
                price,
                order_id,
            )
            return

        self.tracker.on_exit(exit_price=price, was_stop=is_stop)
        log.info(
            "Stream fill (exit): %.2f stop=%s order_id=%s trails=%d session_pnl=$%.2f",
            price,
            is_stop,
            order_id,
            self._trail_count,
            self.tracker.session_pnl,
        )

        if self._pending_trade and entry_px:
            side = self._pending_trade["side"]
            direction = 1.0 if side == "long" else -1.0
            pnl_pts = direction * (price - entry_px)
            pnl_dollars = pnl_pts * _NQ_POINT_VALUE * self._pending_trade["size"]
            stop_price = self._pending_trade.get("stop_price", 0)
            risk_pts = abs(entry_px - stop_price) if stop_price else DEFAULT_STOP_TICKS * 0.25
            pnl_r = pnl_pts / max(risk_pts, 0.25)

            # Slippage = adverse fill vs. intended signal price, in NQ ticks.
            # Positive = paid worse than signal (long filled higher / short filled lower).
            signal_price = self._pending_trade.get("signal_price")
            slippage_ticks = None
            if signal_price:
                slippage_ticks = round(direction * (entry_px - signal_price) / 0.25, 2)

            # Latency = signal-dispatch → entry-fill (ms). End-to-end including order ack.
            submit_ts = self._pending_trade.get("entry_submit_ts")
            fill_ts = self._pending_trade.get("entry_fill_ts")
            fill_latency_ms = None
            if submit_ts and fill_ts:
                fill_latency_ms = round((fill_ts - submit_ts).total_seconds() * 1000.0, 1)

            _log_broker_trade(
                session_pnl=round(self.tracker.session_pnl, 2),
                ts=self._pending_trade["ts"],
                session_date=self._pending_trade["session_date"],
                symbol=self._pending_trade["symbol"],
                side=side,
                size=self._pending_trade["size"],
                entry_price=entry_px,
                stop_price=stop_price,
                tp_price=self._pending_trade.get("tp_price"),
                exit_price=price,
                pnl_dollars=round(pnl_dollars, 2),
                pnl_r=round(pnl_r, 3),
                fill_latency_ms=fill_latency_ms,
                slippage_ticks=slippage_ticks,
                was_stop=is_stop,
                trail_count=self._pending_trade.get("trail_count", 0),
                stop_ticks=self._pending_trade.get("stop_ticks"),
                signal_action=self._pending_trade.get("signal_action"),
                signal_confidence=self._pending_trade.get("signal_confidence"),
                signal_zone=self._pending_trade.get("signal_zone"),
                signal_trigger=self._pending_trade.get("signal_trigger"),
                signal_cont_p=self._pending_trade.get("signal_cont_p"),
                signal_rev_p=self._pending_trade.get("signal_rev_p"),
                orderflow_score=self._pending_trade.get("orderflow_score"),
                reasoning=self._pending_trade.get("reasoning"),
                closed_at=datetime.now(timezone.utc),
            )
            self._pending_trade = None

    def reset_session(self) -> None:
        """Daily midnight reset."""
        self._halted = False
        self._halt_reason = ""
        self._zone_last_entry.clear()
        self._trail_count = 0
        self.tracker.reset_session()
        log.info("Session reset")

    def _check_risk(self) -> dict | None:
        """Run risk checks."""
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
            return {"rejected": True, "reason": "min_interval"}

        return None

    async def _execute_entry(self, signal: dict) -> dict:
        """Place market + stop orders with risk-based sizing."""
        action = signal["action"]
        is_long = "long" in action.lower()
        order_action = "Buy" if is_long else "Sell"
        stop_action = "Sell" if is_long else "Buy"
        price = float(signal.get("price", 0) or 0)
        stop_price = float(signal.get("stop_price", 0) or 0)
        confidence = float(signal.get("confidence", 0) or 0)

        # Validate/adjust stop distance
        stop_dist_ticks = abs(stop_price - price) / 0.25 if stop_price > 0 else DEFAULT_STOP_TICKS
        stop_dist_ticks = int(max(MIN_STOP_TICKS, min(MAX_STOP_TICKS, stop_dist_ticks)))
        offset = stop_dist_ticks * 0.25
        stop_price = _round_tick(price - offset if is_long else price + offset)

        # Risk-based sizing
        risk_pct = RISK_PCT_HIGH if confidence > 0.70 else RISK_PCT_BASE
        risk_dollars = self.config.max_trailing_dd * risk_pct
        risk_per_contract = stop_dist_ticks * _NQ_TICK_VALUE
        size = max(
            1,
            min(
                int(risk_dollars / risk_per_contract),
                self.config.max_position,
            ),
        )
        log.info(
            "Sizing: risk=$%.0f (%.1f%% of $%.0f DD), stop=%d ticks ($%.0f/contract) → %d contracts",
            risk_dollars,
            risk_pct * 100,
            self.config.max_trailing_dd,
            stop_dist_ticks,
            risk_per_contract,
            size,
        )

        if not self.tracker.is_flat:
            await self.flatten("flip")

        log.info(
            "=== ENTRY === %s size=%d stop=%.2f (%d ticks) conf=%.3f cont_p=%.3f rev_p=%.3f zone=%.2f",
            action,
            size,
            stop_price,
            stop_dist_ticks,
            confidence,
            float(signal.get("cont_p", 0) or 0),
            float(signal.get("rev_p", 0) or 0),
            float(signal.get("zone", 0) or 0),
        )

        try:
            result = await self.client.place_market_order(order_action, size)
        except Exception:
            log.exception("Market order failed")
            return {"rejected": True, "reason": "order_failed"}

        if isinstance(result, dict) and not result.get("success", True):
            err = result.get("errorMessage", "order_rejected")
            err_code = result.get("errorCode")
            log.warning("Market order rejected (errorCode=%s): %s", err_code, err)
            if err_code == 2 or "permanent violation" in str(err).lower():
                self._halt(f"account permanent violation: {err}")
            return {"rejected": True, "reason": err}

        entry_order_id = result.get("orderId") if isinstance(result, dict) else None

        stop_order_id = None
        if stop_price > 0:
            for attempt in (1, 2):
                try:
                    stop_result = await self.client.place_stop_order(stop_action, size, stop_price)
                except Exception:
                    log.exception("Stop placement raised (attempt %d/2)", attempt)
                    stop_result = None
                if isinstance(stop_result, dict):
                    if stop_result.get("success", True):
                        stop_order_id = stop_result.get("orderId")
                        break
                    log.warning(
                        "Stop placement rejected (attempt %d/2): %s",
                        attempt,
                        stop_result.get("errorMessage"),
                    )

            if stop_order_id is None:
                # We have a filled (or about-to-fill) market order but no stop. Sitting
                # naked is worse than reverting — liquidate immediately to bound risk.
                log.error(
                    "Stop placement failed twice — flattening entry to avoid unhedged position",
                )
                try:
                    await self.client.liquidate_position()
                except Exception:
                    log.exception("Emergency liquidate after failed stop also failed — POSITION MAY BE OPEN")
                self._halt("stop_placement_failed")
                return {"rejected": True, "reason": "stop_placement_failed"}

        side = "long" if is_long else "short"
        self.tracker.on_fill(side, price=0.0, size=size, stop_price=stop_price)
        self.tracker.entry_order_id = entry_order_id
        self.tracker.stop_order_id = stop_order_id

        now = datetime.now(timezone.utc)
        # TP = 2R from entry
        tp_price = _round_tick(price + offset * 2 if is_long else price - offset * 2)
        self._pending_trade = {
            "ts": now,
            "session_date": now.strftime("%Y-%m-%d"),
            "symbol": "NQ",
            "side": side,
            "size": size,
            "stop_price": stop_price,
            "tp_price": tp_price,
            "stop_ticks": stop_dist_ticks,
            "signal_price": price,
            "entry_submit_ts": now,
            "entry_fill_ts": None,
            "signal_action": action,
            "signal_confidence": float(signal.get("confidence", 0) or 0),
            "signal_zone": float(signal.get("zone", signal.get("zone_price", 0)) or 0),
            "signal_trigger": str(signal.get("trigger", "")),
            "signal_cont_p": float(signal.get("cont_p", 0) or 0),
            "signal_rev_p": float(signal.get("rev_p", 0) or 0),
            "orderflow_score": float(signal.get("orderflow_score", 0) or 0),
            "reasoning": signal.get("reasoning") if isinstance(signal.get("reasoning"), dict) else None,
            "trail_count": 0,
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

    def halt(self, reason: str) -> None:
        """Halt trading for the rest of the session."""
        self._halt(reason)

    def _halt(self, reason: str) -> None:
        self._halted = True
        self._halt_reason = reason
        log.warning("HALTED: %s (session_pnl=$%.2f)", reason, self.tracker.session_pnl)
