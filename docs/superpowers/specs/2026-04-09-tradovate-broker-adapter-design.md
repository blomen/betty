# Tradovate Broker Adapter

**Date:** 2026-04-09
**Status:** Approved

## Purpose

Connect the specialist ensemble's trading signals to Tradovate's REST API for automated NQ futures execution. Same rules for demo and live — only the API endpoint changes.

## Architecture

```
Zone Touch → Specialist Ensemble → Session Manager → BrokerAdapter → Tradovate API
                                                         ↓
                                                   Risk Rules Check
                                                         ↓
                                                   Place Order + Stop
                                                         ↓
                                                   Fill Confirmation
                                                         ↓
                                                   Position Tracker
                                                         ↓
                                                   Trade Log (DB)
```

## Components

### TradovateClient (`broker/tradovate_client.py`)

Low-level HTTP client for Tradovate REST API.

- **Auth:** POST `/auth/accesstokenrequest` with username/password/appId/cid. Returns access token (valid ~24h). Auto-refresh before expiry.
- **Place order:** POST `/order/placeorder` — market order with attached bracket (stop-loss).
- **Cancel order:** DELETE `/order/cancelorder/{id}`
- **Modify order:** PUT `/order/modifyorder` — for trailing stop moves.
- **Get positions:** GET `/position/list` — current open positions.
- **Get fills:** GET `/fill/list` — recent fills for latency/slippage tracking.
- **Flatten:** POST `/order/liquidateposition` — close all for a contract.
- **WebSocket:** Connect to `/ws` for real-time order/fill/position updates (optional, can poll initially).

Base URLs:
- Demo: `https://demo.tradovateapi.com/v1`
- Live: `https://live.tradovateapi.com/v1`

### BrokerAdapter (`broker/adapter.py`)

Translates session manager signals into broker orders. Enforces all risk rules.

Interface:
```python
class BrokerAdapter:
    async def on_signal(self, signal: dict) -> dict | None:
        """Process a session manager signal. Returns fill info or None if rejected."""

    async def flatten(self, reason: str) -> dict:
        """Close all positions and cancel pending orders."""

    async def modify_stop(self, new_stop_price: float) -> dict:
        """Move the stop-loss order for the current position."""
```

Signal types handled:
- `enter_long` / `enter_short` → place market order + stop
- `flip` → flatten current + enter opposite
- `trail_stop` → modify existing stop order
- `exit` → flatten

### PositionTracker (`broker/position_tracker.py`)

Real-time state tracking from fills.

Tracks:
- Current position (side, size, entry_price, stop_price)
- Session P&L (realized + unrealized)
- Peak equity (for trailing drawdown)
- Trade count today
- Consecutive stops
- Fill latency (signal_ts → fill_ts)
- Slippage (signal_price → fill_price)

### Risk Rules (`broker/config.py`)

Always enforced, same for demo and live:

| Rule | Default | Configurable |
|------|---------|-------------|
| Max daily loss | $1,000 | BROKER_MAX_DAILY_LOSS |
| Max position size | 2 contracts | BROKER_MAX_POSITION |
| Flatten time | 15:55 ET | BROKER_FLATTEN_ET |
| Circuit breaker | -6R | From session manager |
| Max consecutive stops | 3 → halt | From session manager |
| Min trade interval | 30s | hardcoded |
| Trailing drawdown | Track from peak | Always on |
| Max trailing drawdown | $2,000 | BROKER_MAX_TRAILING_DD |

### Trade Log

Each executed trade persisted to PostgreSQL `broker_trades` table:

```
id, ts, symbol, side, size, entry_price, stop_price, exit_price,
fill_latency_ms, slippage_ticks, pnl_dollars, pnl_r,
signal_action, signal_confidence, signal_zone, session_date
```

### Flatten Schedule

Background task runs in the event loop:
- At 15:55 ET: flatten all positions, cancel stops
- At 15:59 ET: verify flat (safety check)
- Log session summary (trades, P&L, max drawdown)

### Integration Point

In `level_monitor.py` `_emit_zone_dqn_inference()`, after the specialist inference:

```python
# Existing: log signal
log_signal(...)

# New: execute via broker if enabled
if broker_adapter is not None:
    session_mgr_signal = session_manager.on_level_touch(state, price)
    if session_mgr_signal["action"] not in ("skip", "hold"):
        await broker_adapter.on_signal(session_mgr_signal)
```

### Configuration

```env
# .env.docker
TRADOVATE_ENV=demo
TRADOVATE_USERNAME=
TRADOVATE_PASSWORD=
TRADOVATE_APP_ID=
TRADOVATE_CID=
TRADOVATE_DEVICE_ID=firev-agent
BROKER_ENABLED=false
BROKER_MAX_DAILY_LOSS=1000
BROKER_MAX_POSITION=2
BROKER_FLATTEN_ET=15:55
BROKER_MAX_TRAILING_DD=2000
BROKER_SYMBOL=NQM5
```

`BROKER_ENABLED=false` by default — must explicitly enable. Safety first.

## File Structure

```
backend/src/broker/
├── __init__.py
├── tradovate_client.py    # REST API client
├── adapter.py             # Signal → order + risk rules
├── position_tracker.py    # Real-time P&L and state
├── config.py              # Risk limits from env vars
└── flatten_scheduler.py   # 15:55 ET auto-flatten
```

## Error Handling

- Auth failure → retry 3x with backoff, then halt trading
- Order rejection → log reason, don't retry (risk rule likely)
- Connection loss → flatten immediately (safety), reconnect
- Fill timeout (>5s) → cancel order, log as missed
- Any unhandled exception → flatten + halt + alert
