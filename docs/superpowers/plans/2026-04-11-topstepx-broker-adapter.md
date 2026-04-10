# TopstepX Broker Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap TopstepX order execution with risk checks, position tracking, EOD flatten, and real fill prices — replacing the current "dumb relay" that fires orders with no safety.

**Architecture:** New `TopstepXBrokerAdapter` class reuses `PositionTracker` and `FlattenScheduler` from the existing server-side broker path. `SignalRelayClient` delegates to the adapter instead of calling `TopstepXClient` directly. Stream fills update the tracker with real prices.

**Tech Stack:** Python 3.10, asyncio, existing `PositionTracker`/`FlattenScheduler` classes

---

### Task 1: Create `TopstepXBrokerAdapter`

**Files:**
- Create: `backend/src/stocks/broker_adapter.py`

- [ ] **Step 1: Create the adapter file with full implementation**

```python
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

    # ------------------------------------------------------------------
    # Public API (matches BrokerAdapter interface)
    # ------------------------------------------------------------------

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
            # Exit fill — position was open, this closes it
            is_stop = (
                abs(price - self.tracker.stop_price) < 1.0
                if self.tracker.stop_price else False
            )
            self.tracker.on_exit(exit_price=price, was_stop=is_stop)
            log.info("Stream fill (exit): %.2f stop=%s session_pnl=$%.2f",
                     price, is_stop, self.tracker.session_pnl)
        else:
            # Entry fill — update entry price from real fill
            self.tracker.entry_price = price
            log.info("Stream fill (entry): %.2f", price)

    def reset_session(self) -> None:
        """Daily midnight reset."""
        self._halted = False
        self._halt_reason = ""
        self.tracker.reset_session()
        log.info("Session reset")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

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

        # Flatten before flip
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
```

- [ ] **Step 2: Verify the file parses correctly**

Run: `cd backend && python -c "from src.stocks.broker_adapter import TopstepXBrokerAdapter; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/src/stocks/broker_adapter.py
git commit -m "feat(stocks): add TopstepXBrokerAdapter with risk checks and position tracking"
```

---

### Task 2: Wire adapter into `SignalRelayClient`

**Files:**
- Modify: `backend/src/stocks/signal_relay.py`

- [ ] **Step 1: Add `adapter` parameter and delegate `_execute_signal`**

Replace the `__init__` method to accept an optional adapter:

```python
class SignalRelayClient:
    """WebSocket client that relays ticks to the server and executes signals from it."""

    def __init__(self, server_ws_url: str, topstepx_client, adapter=None) -> None:
        self._url = server_ws_url
        self._client = topstepx_client  # TopstepXClient instance
        self._adapter = adapter         # TopstepXBrokerAdapter (if set, handles execution)
        self._ws = None
        self._connected = False
        self._listen_task: asyncio.Task | None = None
        self.on_signal: Callable[[dict], None] | None = None   # UI callback
        self.on_zone_update: Callable[[dict], None] | None = None
```

Replace `_execute_signal` to delegate when adapter is present:

```python
    async def _execute_signal(self, signal: dict) -> None:
        """Parse signal and execute via adapter (if set) or directly on TopstepX."""
        if self._adapter:
            try:
                result = await self._adapter.on_signal(signal)
                if result and not result.get("rejected"):
                    side = result.get("side", "long")
                    price = 0.0  # real price comes via stream fill
                    size = result.get("size", 1)
                    stop_price = result.get("stop_price", 0)
                    await self.forward_fill(side, price, size, stop_price)
            except Exception:
                log.exception("SignalRelay: adapter execution failed for signal %r", signal)
            return

        # Legacy direct path (no adapter)
        action = signal.get("action", "")
        is_long = "long" in action.lower()
        order_action = "Buy" if is_long else "Sell"
        stop_action = "Sell" if is_long else "Buy"
        size = int(signal.get("size", 1) or 1)
        stop_price = float(signal.get("stop_price", 0) or 0)

        log.info("SignalRelay: executing signal: %s size=%d stop=%.2f", action, size, stop_price)

        try:
            result = await self._client.place_market_order(order_action, size)
            fill_price = float(result.get("price", 0) if isinstance(result, dict) else 0)

            if stop_price > 0:
                await self._client.place_stop_order(stop_action, size, stop_price)
        except Exception:
            log.exception("SignalRelay: order execution failed for signal %r", signal)
            return

        await self.forward_fill(order_action, fill_price, size, stop_price)
```

- [ ] **Step 2: Verify the file parses correctly**

Run: `cd backend && python -c "from src.stocks.signal_relay import SignalRelayClient; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/src/stocks/signal_relay.py
git commit -m "feat(stocks): delegate signal execution to adapter in SignalRelayClient"
```

---

### Task 3: Wire adapter + flatten scheduler into `run_firevstocks.py`

**Files:**
- Modify: `backend/run_firevstocks.py`

- [ ] **Step 1: Add adapter and flatten scheduler to `main()`**

After the `relay = SignalRelayClient(...)` line (line 318), replace with:

```python
    # Build adapter with risk checks, then relay with adapter
    from src.stocks.broker_adapter import TopstepXBrokerAdapter
    adapter = TopstepXBrokerAdapter(client, config)

    relay = SignalRelayClient(config.server_ws_url, client, adapter=adapter)
```

- [ ] **Step 2: Wire stream fills to adapter in `_run()`**

Replace the `_on_fill` function (line 219-224) with:

```python
    def _on_fill(fill: dict) -> None:
        side = "long" if fill.get("side", 0) == 0 else "short"
        price = float(fill.get("price", 0))
        size = int(fill.get("size", 1))
        # Update adapter tracker with real fill price
        adapter.on_stream_fill(fill)
        # Forward to server and dashboard
        asyncio.create_task(relay.forward_fill(side, price, size, 0.0))
        dash_fill({"side": side, "price": price, "size": size, "ts": time.time()})
```

- [ ] **Step 3: Start FlattenScheduler after stream starts**

After `await stream.start()` (line 238), add:

```python
    # Start EOD flatten scheduler (15:55 ET by default)
    from src.broker.flatten_scheduler import FlattenScheduler
    flatten_scheduler = FlattenScheduler(adapter, config.flatten_et)
    flatten_scheduler.start()
    log.info("FlattenScheduler started (flatten at %s ET)", config.flatten_et)
```

And in the `finally` block (after line 250), add cleanup:

```python
    finally:
        log.info("Shutting down...")
        flatten_scheduler.stop()
        await stream.stop()
```

- [ ] **Step 4: Verify the file parses correctly**

Run: `cd backend && python -c "import ast; ast.parse(open('run_firevstocks.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add backend/run_firevstocks.py
git commit -m "feat(stocks): wire TopstepXBrokerAdapter + FlattenScheduler into launcher"
```

---

### Task 4: Integration verification

- [ ] **Step 1: Verify all imports chain correctly**

Run: `cd backend && python -c "from src.stocks.broker_adapter import TopstepXBrokerAdapter; from src.stocks.signal_relay import SignalRelayClient; from src.broker.flatten_scheduler import FlattenScheduler; from src.broker.position_tracker import PositionTracker; print('All imports OK')"`
Expected: `All imports OK`

- [ ] **Step 2: Verify adapter interface matches FlattenScheduler expectations**

Run: `cd backend && python -c "
from src.stocks.broker_adapter import TopstepXBrokerAdapter
# FlattenScheduler needs: adapter.flatten(reason), adapter.tracker.is_flat, adapter.reset_session()
assert hasattr(TopstepXBrokerAdapter, 'flatten')
assert hasattr(TopstepXBrokerAdapter, 'reset_session')
print('Interface check OK')
"`
Expected: `Interface check OK`

- [ ] **Step 3: Final commit with all files**

```bash
git add -A
git commit -m "feat(stocks): TopstepXBrokerAdapter — risk-checked order execution for TopstepX

Adds position tracking, risk checks (daily loss, trailing DD, consecutive stops),
flatten-before-flip, EOD auto-flatten, and real fill price tracking via stream.
Replaces the previous dumb relay that fired orders with no safety checks."
```
