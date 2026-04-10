# TopstepX Broker Adapter Design

## Problem

The firevstocks signal relay (`SignalRelayClient._execute_signal`) places orders on TopstepX with zero risk checks, no position management, no EOD flatten, and broken fill tracking. The server-side `BrokerAdapter` has all of this but targets Tradovate/Rithmic — not TopstepX.

## Solution

Create `TopstepXBrokerAdapter` that wraps `TopstepXClient` with the same interface and safety guarantees as the existing `BrokerAdapter`, then wire it into the local `run_firevstocks.py` startup.

## Architecture

```
Signal from server (/ws/signals)
        │
        ▼
  SignalRelayClient._execute_signal(signal)
        │
        ▼  (was: client.place_market_order directly)
  TopstepXBrokerAdapter.on_signal(signal)
        │
        ├── _check_risk()        → halt if limits breached
        ├── flatten("flip")      → cancel old stop + liquidate if not flat
        ├── client.place_market_order()
        ├── client.place_stop_order()
        └── tracker.on_fill()    → record entry
        
  TopstepXStream (GatewayUserTrade)
        │
        ▼
  adapter.on_stream_fill(fill_dict)
        │
        └── tracker update with real fill price

  FlattenScheduler (every 30s)
        │
        ├── 15:55 ET  → adapter.flatten("eod_flatten")
        └── midnight  → adapter.reset_session()
```

## New File: `backend/src/stocks/broker_adapter.py`

### Class: `TopstepXBrokerAdapter`

**Constructor:**
```python
TopstepXBrokerAdapter(client: TopstepXClient, config: TopstepXConfig)
```
- Creates a `PositionTracker` (reused from `broker/position_tracker.py`)
- `_halted = False`, `_halt_reason = ""`
- `MIN_TRADE_INTERVAL_S = 30.0`

**Public methods (same interface as `BrokerAdapter`):**

| Method | Signature | Purpose |
|--------|-----------|---------|
| `on_signal` | `async (signal: dict) -> dict \| None` | Risk check → execute entry |
| `flatten` | `async (reason: str = "manual") -> dict` | Cancel stop + liquidate |
| `modify_stop` | `async (new_stop_price: float) -> dict \| None` | Move stop order |
| `on_stream_fill` | `(fill: dict) -> None` | Update tracker from real TopstepX fill |
| `reset_session` | `() -> None` | Daily midnight reset |

### `on_signal(signal)` flow

1. Parse `action` from signal. Skip if `"skip"` or `"hold"`.
2. If `_halted` → return rejection dict.
3. Call `_check_risk()` → may halt and return rejection.
4. Route by action:
   - `"enter_long"`, `"enter_short"` → `_execute_entry(signal)`
   - `"flatten"`, `"exit"` → `self.flatten(action)`
   - `"trail_stop"` → `_trail_stop(signal)`

### `_check_risk()` — 4 checks

1. `tracker.exceeds_daily_loss(config.max_daily_loss)` → halt
2. `tracker.exceeds_trailing_dd(config.max_trailing_dd)` → halt
3. `tracker.consecutive_stops >= 3` → halt
4. `time.time() - tracker.last_trade_ts < MIN_TRADE_INTERVAL_S` → reject (no halt)

### `_execute_entry(signal)` flow

1. Determine `is_long` from action string.
2. `size = min(int(signal.get("size", 1) or 1), config.max_position)` — cap at max.
3. If not flat: `await self.flatten("flip")`.
4. `order_action = "Buy" if is_long else "Sell"`.
5. `result = await client.place_market_order(order_action, size)`.
6. `stop_price = signal.get("stop_price", 0)`.
7. If `stop_price > 0`: `stop_result = await client.place_stop_order(stop_action, size, stop_price)`.
8. `tracker.on_fill(side, price=0, size=size, stop_price=stop_price)` — price=0 because real fill comes via stream.
9. Store `tracker.stop_order_id` from stop result.
10. Return result dict.

### `flatten(reason)` flow

1. If `tracker.stop_order_id`: `await client.cancel_order(tracker.stop_order_id)`.
2. If not flat: `await client.liquidate_position()`.
3. `tracker.on_exit(exit_price=0.0, was_stop=(reason == "stop"))` — real exit price comes via stream.
4. Return result dict with session P&L.

### `on_stream_fill(fill)` — real fill from TopstepX stream

Called from `stream.on_fill` callback. Updates the tracker with the actual fill price from `GatewayUserTrade`:

```python
def on_stream_fill(self, fill: dict) -> None:
    price = float(fill.get("price", 0))
    side_raw = fill.get("side", 0)  # 0=Buy, 1=Sell
    
    if not self.tracker.is_flat:
        # This is an exit fill (position was open)
        is_stop = abs(price - self.tracker.stop_price) < 1.0 if self.tracker.stop_price else False
        self.tracker.on_exit(exit_price=price, was_stop=is_stop)
    else:
        # This is an entry fill — update the entry price
        side = "long" if side_raw == 0 else "short"
        self.tracker.entry_price = price
```

This replaces the broken `forward_fill` that always had `price=0`.

### `_halt(reason)`

Sets `_halted = True`, logs session P&L. Does NOT flatten — the FlattenScheduler or manual action handles that.

## Wiring Changes in `run_firevstocks.py`

### Before
```python
relay = SignalRelayClient(config.server_ws_url, client)
# relay._execute_signal calls client.place_market_order directly
```

### After
```python
from src.stocks.broker_adapter import TopstepXBrokerAdapter
from src.broker.flatten_scheduler import FlattenScheduler

adapter = TopstepXBrokerAdapter(client, config)
relay = SignalRelayClient(config.server_ws_url, client, adapter=adapter)

# Wire stream fills to adapter
def _on_fill(fill: dict):
    adapter.on_stream_fill(fill)  # real fill tracking
    # ... existing dashboard/relay forwarding

# Start EOD flatten scheduler
flatten_scheduler = FlattenScheduler(adapter, config.flatten_et)
flatten_scheduler.start()
```

### Changes to `SignalRelayClient`

Add optional `adapter` parameter. If present, `_execute_signal` delegates to it:

```python
class SignalRelayClient:
    def __init__(self, server_ws_url, topstepx_client, adapter=None):
        self._adapter = adapter
        ...
    
    async def _execute_signal(self, signal):
        if self._adapter:
            result = await self._adapter.on_signal(signal)
            if result:
                await self.forward_fill(...)
        else:
            # legacy direct path (kept for backwards compat)
            ...
```

## Signal Format

The server sends signals via `/ws/signals` with this shape (from `level_monitor.py` line 826-840):

```json
{
    "type": "signal",
    "action": "CONTINUATION" | "REVERSAL",
    "price": 25340.0,
    "stop_price": 25332.0,
    "size": 0.3,
    "confidence": 0.57,
    "zone_center": 25340.5,
    "zone_members": 2
}
```

The adapter maps `CONTINUATION` → `enter_long` if approach is up, `enter_short` if down. The `approach` field is included in the signal from `level_monitor.py`.

## What We Reuse

- `PositionTracker` from `broker/position_tracker.py` — no changes needed
- `FlattenScheduler` from `broker/flatten_scheduler.py` — no changes needed
- `TopstepXClient` — no changes needed
- `TopstepXConfig` — no changes needed (already has all risk fields)

## What We Create

- `backend/src/stocks/broker_adapter.py` — `TopstepXBrokerAdapter` class (~120 lines)

## What We Modify

- `backend/src/stocks/signal_relay.py` — add `adapter` parameter, delegate `_execute_signal`
- `backend/run_firevstocks.py` — wire adapter + flatten scheduler at startup

## Out of Scope

- Server-side BrokerAdapter changes (Tradovate/Rithmic path untouched)
- Dashboard UI changes (fills/exits already display via existing callbacks)
- Advanced order types (limit orders, bracket orders)
- Multi-account support
