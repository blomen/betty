# Tradovate Broker Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the specialist ensemble's trading signals to Tradovate's REST API for automated NQ futures execution with full risk management.

**Architecture:** TradovateClient (HTTP) → BrokerAdapter (risk rules + order translation) → PositionTracker (state) → FlattenScheduler (EOD). Integrated at the LevelMonitor zone touch handler. BROKER_ENABLED=false by default.

**Tech Stack:** Python 3.10, httpx (async HTTP), FastAPI (existing), PostgreSQL (trade log), asyncio.

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| CREATE | `backend/src/broker/__init__.py` | Package init |
| CREATE | `backend/src/broker/config.py` | Risk limits + API config from env vars |
| CREATE | `backend/src/broker/tradovate_client.py` | Low-level Tradovate REST API client |
| CREATE | `backend/src/broker/position_tracker.py` | Real-time position, P&L, drawdown state |
| CREATE | `backend/src/broker/adapter.py` | Signal → order + risk rule enforcement |
| CREATE | `backend/src/broker/flatten_scheduler.py` | 15:55 ET auto-flatten background task |
| CREATE | `backend/tests/test_broker_adapter.py` | Tests for adapter + risk rules |
| CREATE | `backend/tests/test_position_tracker.py` | Tests for position tracking |
| MODIFY | `backend/src/db/models.py` | Add BrokerTrade table |
| MODIFY | `backend/src/api/__init__.py` | Start broker + flatten scheduler in lifespan |
| MODIFY | `backend/src/market_data/level_monitor.py` | Wire broker into zone touch handler |

---

### Task 1: Broker Config

**Files:**
- Create: `backend/src/broker/__init__.py`
- Create: `backend/src/broker/config.py`

- [ ] **Step 1: Create package and config**

```python
# backend/src/broker/__init__.py
```

```python
# backend/src/broker/config.py
"""Broker configuration from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class BrokerConfig:
    """All broker settings. Same rules for demo and live."""
    enabled: bool = False
    env: str = "demo"  # "demo" or "live"

    # Tradovate credentials
    username: str = ""
    password: str = ""
    app_id: str = ""
    cid: str = ""
    device_id: str = "firev-agent"

    # Trading
    symbol: str = "NQM5"  # front-month NQ contract
    max_position: int = 2  # max contracts
    max_daily_loss: float = 1000.0  # dollars
    max_trailing_dd: float = 2000.0  # dollars from peak equity
    flatten_et: str = "15:55"  # ET time to flatten
    min_trade_interval_s: float = 30.0

    @property
    def base_url(self) -> str:
        if self.env == "live":
            return "https://live.tradovateapi.com/v1"
        return "https://demo.tradovateapi.com/v1"

    @classmethod
    def from_env(cls) -> BrokerConfig:
        return cls(
            enabled=os.environ.get("BROKER_ENABLED", "false").lower() == "true",
            env=os.environ.get("TRADOVATE_ENV", "demo"),
            username=os.environ.get("TRADOVATE_USERNAME", ""),
            password=os.environ.get("TRADOVATE_PASSWORD", ""),
            app_id=os.environ.get("TRADOVATE_APP_ID", ""),
            cid=os.environ.get("TRADOVATE_CID", ""),
            device_id=os.environ.get("TRADOVATE_DEVICE_ID", "firev-agent"),
            symbol=os.environ.get("BROKER_SYMBOL", "NQM5"),
            max_position=int(os.environ.get("BROKER_MAX_POSITION", "2")),
            max_daily_loss=float(os.environ.get("BROKER_MAX_DAILY_LOSS", "1000")),
            max_trailing_dd=float(os.environ.get("BROKER_MAX_TRAILING_DD", "2000")),
            flatten_et=os.environ.get("BROKER_FLATTEN_ET", "15:55"),
            min_trade_interval_s=float(os.environ.get("BROKER_MIN_INTERVAL", "30")),
        )
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/broker/
git commit -m "feat(broker): config module with risk limits from env vars"
```

---

### Task 2: Tradovate REST Client

**Files:**
- Create: `backend/src/broker/tradovate_client.py`

- [ ] **Step 1: Implement the HTTP client**

```python
# backend/src/broker/tradovate_client.py
"""Low-level Tradovate REST API client.

Handles auth token management, order placement, position queries.
All methods are async. Token auto-refreshes before expiry.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from .config import BrokerConfig

log = logging.getLogger(__name__)

# Tradovate order action constants
ACTION_BUY = "Buy"
ACTION_SELL = "Sell"

# Order types
ORDER_TYPE_MARKET = "Market"
ORDER_TYPE_STOP = "Stop"
ORDER_TYPE_LIMIT = "Limit"


class TradovateClient:
    """Async HTTP client for Tradovate REST API."""

    TOKEN_REFRESH_BUFFER_S = 300  # refresh 5 min before expiry

    def __init__(self, config: BrokerConfig) -> None:
        self._config = config
        self._http = httpx.AsyncClient(timeout=10.0)
        self._token: str | None = None
        self._token_expiry: float = 0
        self._account_id: int | None = None
        self._account_spec: str | None = None  # e.g. "DEMO12345"

    async def connect(self) -> bool:
        """Authenticate and get account info."""
        try:
            token_data = await self._auth()
            if not token_data:
                return False

            self._token = token_data["accessToken"]
            # Tradovate tokens last ~24h, but we refresh early
            self._token_expiry = time.time() + token_data.get("expirationTime", 86400) - self.TOKEN_REFRESH_BUFFER_S

            # Get account ID
            accounts = await self._get("/account/list")
            if accounts:
                self._account_id = accounts[0]["id"]
                self._account_spec = accounts[0].get("name", "")
                log.info("Tradovate connected: account=%s (id=%d)", self._account_spec, self._account_id)
                return True

            log.error("No accounts found")
            return False
        except Exception:
            log.exception("Tradovate connect failed")
            return False

    async def _auth(self) -> dict | None:
        """POST auth token request."""
        payload = {
            "name": self._config.username,
            "password": self._config.password,
            "appId": self._config.app_id,
            "appVersion": "1.0",
            "cid": self._config.cid,
            "deviceId": self._config.device_id,
        }
        resp = await self._http.post(
            f"{self._config.base_url}/auth/accesstokenrequest",
            json=payload,
        )
        if resp.status_code == 200:
            return resp.json()
        log.error("Auth failed: %d %s", resp.status_code, resp.text[:200])
        return None

    async def _ensure_token(self) -> None:
        """Refresh token if near expiry."""
        if time.time() >= self._token_expiry:
            log.info("Refreshing Tradovate token...")
            token_data = await self._auth()
            if token_data:
                self._token = token_data["accessToken"]
                self._token_expiry = time.time() + token_data.get("expirationTime", 86400) - self.TOKEN_REFRESH_BUFFER_S

    async def _get(self, path: str) -> Any:
        await self._ensure_token()
        resp = await self._http.get(
            f"{self._config.base_url}{path}",
            headers={"Authorization": f"Bearer {self._token}"},
        )
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, payload: dict) -> Any:
        await self._ensure_token()
        resp = await self._http.post(
            f"{self._config.base_url}{path}",
            json=payload,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        resp.raise_for_status()
        return resp.json()

    # --- Order Operations ---

    async def place_market_order(self, action: str, quantity: int) -> dict:
        """Place a market order. action: 'Buy' or 'Sell'."""
        payload = {
            "accountSpec": self._account_spec,
            "accountId": self._account_id,
            "action": action,
            "symbol": self._config.symbol,
            "orderQty": quantity,
            "orderType": ORDER_TYPE_MARKET,
            "isAutomated": True,
        }
        result = await self._post("/order/placeorder", payload)
        log.info("Market order placed: %s %d %s → orderId=%s",
                 action, quantity, self._config.symbol, result.get("orderId"))
        return result

    async def place_stop_order(self, action: str, quantity: int, stop_price: float) -> dict:
        """Place a stop order (for stop-loss)."""
        payload = {
            "accountSpec": self._account_spec,
            "accountId": self._account_id,
            "action": action,
            "symbol": self._config.symbol,
            "orderQty": quantity,
            "orderType": ORDER_TYPE_STOP,
            "stopPrice": stop_price,
            "isAutomated": True,
        }
        result = await self._post("/order/placeorder", payload)
        log.info("Stop order placed: %s %d @ %.2f → orderId=%s",
                 action, quantity, stop_price, result.get("orderId"))
        return result

    async def modify_order(self, order_id: int, new_stop_price: float) -> dict:
        """Modify an existing order's stop price."""
        payload = {
            "orderId": order_id,
            "stopPrice": new_stop_price,
        }
        result = await self._post("/order/modifyorder", payload)
        log.info("Order %d modified: stop=%.2f", order_id, new_stop_price)
        return result

    async def cancel_order(self, order_id: int) -> dict:
        """Cancel a pending order."""
        payload = {"orderId": order_id}
        result = await self._post("/order/cancelorder", payload)
        log.info("Order %d cancelled", order_id)
        return result

    async def liquidate_position(self) -> dict:
        """Flatten all positions for the configured symbol."""
        payload = {
            "accountId": self._account_id,
            "symbol": self._config.symbol,
        }
        result = await self._post("/order/liquidateposition", payload)
        log.info("Position liquidated for %s", self._config.symbol)
        return result

    async def get_positions(self) -> list[dict]:
        """Get all open positions."""
        return await self._get("/position/list")

    async def get_orders(self) -> list[dict]:
        """Get all working orders."""
        return await self._get("/order/list")

    async def get_fills(self) -> list[dict]:
        """Get recent fills."""
        return await self._get("/fill/list")

    async def close(self) -> None:
        """Close HTTP client."""
        await self._http.aclose()
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/broker/tradovate_client.py
git commit -m "feat(broker): Tradovate REST API client with auth + orders"
```

---

### Task 3: Position Tracker

**Files:**
- Create: `backend/src/broker/position_tracker.py`
- Create: `backend/tests/test_position_tracker.py`

- [ ] **Step 1: Write tests**

```python
# backend/tests/test_position_tracker.py
"""Tests for broker position tracking."""
import pytest
from src.broker.position_tracker import PositionTracker


def test_initial_state_is_flat():
    pt = PositionTracker()
    assert pt.is_flat
    assert pt.side is None
    assert pt.session_pnl == 0.0


def test_entry_long():
    pt = PositionTracker()
    pt.on_fill(side="long", price=25000.0, size=1, stop_price=24990.0)
    assert not pt.is_flat
    assert pt.side == "long"
    assert pt.entry_price == 25000.0
    assert pt.size == 1


def test_exit_pnl():
    pt = PositionTracker()
    pt.on_fill(side="long", price=25000.0, size=1, stop_price=24990.0)
    pnl = pt.on_exit(exit_price=25010.0)
    assert pnl == 10.0 * 20  # 10 pts * $20/pt for NQ
    assert pt.is_flat
    assert pt.session_pnl == 200.0


def test_peak_equity_tracking():
    pt = PositionTracker()
    pt.on_fill(side="long", price=25000.0, size=1, stop_price=24990.0)
    pt.on_exit(exit_price=25020.0)  # +$400
    assert pt.peak_equity == 400.0
    pt.on_fill(side="short", price=25020.0, size=1, stop_price=25030.0)
    pt.on_exit(exit_price=25025.0)  # -$100
    assert pt.session_pnl == 300.0
    assert pt.peak_equity == 400.0  # peak doesn't drop
    assert pt.trailing_dd == 100.0


def test_daily_loss_check():
    pt = PositionTracker()
    pt.on_fill(side="long", price=25000.0, size=1, stop_price=24990.0)
    pt.on_exit(exit_price=24950.0)  # -$1000
    assert pt.session_pnl == -1000.0
    assert pt.exceeds_daily_loss(1000.0)


def test_consecutive_stops():
    pt = PositionTracker()
    for _ in range(3):
        pt.on_fill(side="long", price=25000.0, size=1, stop_price=24990.0)
        pt.on_exit(exit_price=24990.0, was_stop=True)
    assert pt.consecutive_stops == 3


def test_trade_count():
    pt = PositionTracker()
    pt.on_fill(side="long", price=25000.0, size=1, stop_price=24990.0)
    pt.on_exit(exit_price=25010.0)
    pt.on_fill(side="short", price=25010.0, size=1, stop_price=25020.0)
    pt.on_exit(exit_price=25005.0)
    assert pt.trade_count == 2


def test_reset_session():
    pt = PositionTracker()
    pt.on_fill(side="long", price=25000.0, size=1, stop_price=24990.0)
    pt.on_exit(exit_price=25010.0)
    pt.reset_session()
    assert pt.is_flat
    assert pt.session_pnl == 0.0
    assert pt.trade_count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && pytest tests/test_position_tracker.py -v
```

- [ ] **Step 3: Implement position tracker**

```python
# backend/src/broker/position_tracker.py
"""Real-time position and P&L tracking from fills."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# NQ point value: $20 per point (4 ticks per point, $5 per tick)
NQ_POINT_VALUE = 20.0


@dataclass
class FillRecord:
    """Record of a single fill."""
    ts: float
    side: str
    price: float
    size: int
    signal_price: float = 0.0  # price when signal fired (for slippage calc)


class PositionTracker:
    """Tracks position state, session P&L, and risk metrics."""

    def __init__(self, point_value: float = NQ_POINT_VALUE) -> None:
        self._point_value = point_value
        self.reset_session()

    def reset_session(self) -> None:
        """Reset all session state (call at start of day)."""
        self.side: str | None = None
        self.entry_price: float = 0.0
        self.stop_price: float = 0.0
        self.size: int = 0
        self.stop_order_id: int | None = None

        self.session_pnl: float = 0.0
        self.peak_equity: float = 0.0
        self.trade_count: int = 0
        self.consecutive_stops: int = 0
        self.last_trade_ts: float = 0.0
        self.fills: list[FillRecord] = []

    @property
    def is_flat(self) -> bool:
        return self.side is None

    @property
    def trailing_dd(self) -> float:
        """Current drawdown from peak equity."""
        return max(0, self.peak_equity - self.session_pnl)

    def on_fill(self, side: str, price: float, size: int, stop_price: float,
                signal_price: float = 0.0) -> None:
        """Record a new position entry."""
        self.side = side
        self.entry_price = price
        self.stop_price = stop_price
        self.size = size
        self.last_trade_ts = time.time()
        self.fills.append(FillRecord(
            ts=self.last_trade_ts, side=side, price=price, size=size,
            signal_price=signal_price,
        ))
        log.info("Position opened: %s %d @ %.2f stop=%.2f", side, size, price, stop_price)

    def on_exit(self, exit_price: float, was_stop: bool = False) -> float:
        """Record position exit. Returns P&L in dollars."""
        if self.side is None:
            return 0.0

        if self.side == "long":
            pnl_pts = exit_price - self.entry_price
        else:
            pnl_pts = self.entry_price - exit_price

        pnl_dollars = pnl_pts * self._point_value * self.size
        self.session_pnl += pnl_dollars
        self.peak_equity = max(self.peak_equity, self.session_pnl)
        self.trade_count += 1

        if was_stop:
            self.consecutive_stops += 1
        else:
            self.consecutive_stops = 0

        log.info("Position closed: %s @ %.2f pnl=$%.2f (session=$%.2f)",
                 self.side, exit_price, pnl_dollars, self.session_pnl)

        self.side = None
        self.entry_price = 0.0
        self.stop_price = 0.0
        self.size = 0
        self.stop_order_id = None

        return pnl_dollars

    def exceeds_daily_loss(self, max_loss: float) -> bool:
        return self.session_pnl <= -abs(max_loss)

    def exceeds_trailing_dd(self, max_dd: float) -> bool:
        return self.trailing_dd >= abs(max_dd)

    def slippage_ticks(self) -> float:
        """Average slippage in ticks for this session."""
        fills_with_signal = [f for f in self.fills if f.signal_price > 0]
        if not fills_with_signal:
            return 0.0
        total = sum(abs(f.price - f.signal_price) / 0.25 for f in fills_with_signal)
        return total / len(fills_with_signal)
```

- [ ] **Step 4: Run tests**

```bash
cd backend && pytest tests/test_position_tracker.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/src/broker/position_tracker.py backend/tests/test_position_tracker.py
git commit -m "feat(broker): position tracker with P&L, drawdown, and risk metrics"
```

---

### Task 4: Broker Adapter (Signal → Order + Risk Rules)

**Files:**
- Create: `backend/src/broker/adapter.py`
- Create: `backend/tests/test_broker_adapter.py`

- [ ] **Step 1: Write tests**

```python
# backend/tests/test_broker_adapter.py
"""Tests for broker adapter risk rules."""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.broker.adapter import BrokerAdapter
from src.broker.config import BrokerConfig
from src.broker.position_tracker import PositionTracker


@pytest.fixture
def mock_client():
    client = AsyncMock()
    client.place_market_order = AsyncMock(return_value={"orderId": 1})
    client.place_stop_order = AsyncMock(return_value={"orderId": 2})
    client.cancel_order = AsyncMock(return_value={})
    client.liquidate_position = AsyncMock(return_value={})
    client.modify_order = AsyncMock(return_value={})
    return client


@pytest.fixture
def adapter(mock_client):
    config = BrokerConfig(enabled=True, max_daily_loss=500, max_position=2)
    return BrokerAdapter(client=mock_client, config=config)


def test_enter_long(adapter, mock_client):
    signal = {"action": "enter_long", "price": 25000.0, "stop_price": 24990.0, "size": 1.0}
    result = asyncio.get_event_loop().run_until_complete(adapter.on_signal(signal))
    assert result is not None
    mock_client.place_market_order.assert_called_once_with("Buy", 1)
    mock_client.place_stop_order.assert_called_once()


def test_enter_short(adapter, mock_client):
    signal = {"action": "enter_short", "price": 25000.0, "stop_price": 25010.0, "size": 1.0}
    result = asyncio.get_event_loop().run_until_complete(adapter.on_signal(signal))
    mock_client.place_market_order.assert_called_once_with("Sell", 1)


def test_reject_when_daily_loss_exceeded(adapter, mock_client):
    adapter.tracker.session_pnl = -600  # exceeds $500 limit
    signal = {"action": "enter_long", "price": 25000.0, "stop_price": 24990.0, "size": 1.0}
    result = asyncio.get_event_loop().run_until_complete(adapter.on_signal(signal))
    assert result is None
    mock_client.place_market_order.assert_not_called()


def test_reject_when_too_soon(adapter, mock_client):
    import time
    adapter.tracker.last_trade_ts = time.time()  # just traded
    signal = {"action": "enter_long", "price": 25000.0, "stop_price": 24990.0, "size": 1.0}
    result = asyncio.get_event_loop().run_until_complete(adapter.on_signal(signal))
    assert result is None


def test_reject_exceeds_max_position(adapter, mock_client):
    signal = {"action": "enter_long", "price": 25000.0, "stop_price": 24990.0, "size": 5.0}
    result = asyncio.get_event_loop().run_until_complete(adapter.on_signal(signal))
    # Should clamp to max_position=2
    mock_client.place_market_order.assert_called_once_with("Buy", 2)


def test_flatten(adapter, mock_client):
    adapter.tracker.on_fill(side="long", price=25000.0, size=1, stop_price=24990.0)
    adapter.tracker.stop_order_id = 42
    result = asyncio.get_event_loop().run_until_complete(adapter.flatten("test"))
    mock_client.liquidate_position.assert_called_once()
    mock_client.cancel_order.assert_called_once_with(42)


def test_skip_and_hold_ignored(adapter, mock_client):
    for action in ["skip", "hold", "move_to_breakeven"]:
        signal = {"action": action, "price": 25000.0}
        result = asyncio.get_event_loop().run_until_complete(adapter.on_signal(signal))
        assert result is None
    mock_client.place_market_order.assert_not_called()
```

- [ ] **Step 2: Implement broker adapter**

```python
# backend/src/broker/adapter.py
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

        # Min trade interval
        elapsed = time.time() - self.tracker.last_trade_ts
        if elapsed < self.config.min_trade_interval_s and not self.tracker.is_flat:
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
```

- [ ] **Step 3: Run tests**

```bash
cd backend && pytest tests/test_broker_adapter.py -v
```

- [ ] **Step 4: Commit**

```bash
git add backend/src/broker/adapter.py backend/tests/test_broker_adapter.py
git commit -m "feat(broker): adapter with risk rules + order execution"
```

---

### Task 5: Flatten Scheduler

**Files:**
- Create: `backend/src/broker/flatten_scheduler.py`

- [ ] **Step 1: Implement flatten scheduler**

```python
# backend/src/broker/flatten_scheduler.py
"""Auto-flatten scheduler — closes all positions before market close."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
ET = ZoneInfo("US/Eastern")


class FlattenScheduler:
    """Background task that auto-flattens at a configured ET time."""

    def __init__(self, adapter, flatten_et: str = "15:55") -> None:
        self._adapter = adapter
        h, m = flatten_et.split(":")
        self._flatten_time = time(int(h), int(m))
        self._verify_time = time(int(h), int(m) + 4)  # +4 min safety check
        self._flattened_today = False
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        self._task.set_name("flatten-scheduler")

    async def _loop(self) -> None:
        while True:
            now_et = datetime.now(ET)
            current_time = now_et.time()

            # Reset flag at midnight
            if current_time < time(0, 5):
                self._flattened_today = False
                self._adapter.reset_session()
                log.info("Flatten scheduler: new day, session reset")

            # Flatten at scheduled time
            if not self._flattened_today and current_time >= self._flatten_time:
                log.info("Flatten scheduler: 15:55 ET — closing all positions")
                try:
                    result = await self._adapter.flatten("eod_flatten")
                    self._flattened_today = True
                    log.info("EOD flatten complete: session P&L=$%.2f", result.get("session_pnl", 0))
                except Exception:
                    log.exception("EOD flatten failed!")

            # Safety verify at +4 min
            if self._flattened_today and current_time >= self._verify_time:
                if not self._adapter.tracker.is_flat:
                    log.error("SAFETY: Still not flat at %s — forcing liquidation!", current_time)
                    await self._adapter.flatten("safety_verify")

            await asyncio.sleep(30)  # check every 30s

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/broker/flatten_scheduler.py
git commit -m "feat(broker): auto-flatten scheduler at 15:55 ET with safety verify"
```

---

### Task 6: BrokerTrade DB Model

**Files:**
- Modify: `backend/src/db/models.py`

- [ ] **Step 1: Add BrokerTrade model**

Add at the end of `backend/src/db/models.py`, before any `if __name__` block:

```python
class BrokerTrade(Base):
    """Automated trade execution log."""
    __tablename__ = "broker_trades"

    id = Column(Integer, primary_key=True)
    ts = Column(DateTime, nullable=False, default=_utcnow)
    session_date = Column(String, nullable=False)  # YYYY-MM-DD
    symbol = Column(String, nullable=False)
    side = Column(String, nullable=False)  # "long" or "short"
    size = Column(Integer, nullable=False)

    entry_price = Column(Float, nullable=False)
    stop_price = Column(Float, nullable=True)
    exit_price = Column(Float, nullable=True)

    pnl_dollars = Column(Float, nullable=True)
    pnl_r = Column(Float, nullable=True)
    fill_latency_ms = Column(Float, nullable=True)
    slippage_ticks = Column(Float, nullable=True)

    signal_action = Column(String, nullable=True)
    signal_confidence = Column(Float, nullable=True)
    signal_zone = Column(Float, nullable=True)

    closed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_broker_trades_session", "session_date"),
        Index("ix_broker_trades_ts", "ts"),
    )
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/db/models.py
git commit -m "feat(broker): BrokerTrade model for execution log"
```

---

### Task 7: Integration — Wire Broker into Lifespan + Level Monitor

**Files:**
- Modify: `backend/src/api/__init__.py`
- Modify: `backend/src/market_data/level_monitor.py`

- [ ] **Step 1: Add broker startup to lifespan**

Add after the trading features startup in `backend/src/api/__init__.py`, inside the `_start_trading_features` function:

```python
# --- Broker (automated execution) ---
from ..broker.config import BrokerConfig
broker_config = BrokerConfig.from_env()
_broker_adapter = None
if broker_config.enabled:
    from ..broker.tradovate_client import TradovateClient
    from ..broker.adapter import BrokerAdapter
    from ..broker.flatten_scheduler import FlattenScheduler

    tv_client = TradovateClient(broker_config)
    connected = await tv_client.connect()
    if connected:
        _broker_adapter = BrokerAdapter(tv_client, broker_config)
        app.state.broker_adapter = _broker_adapter
        level_monitor.set_broker_adapter(_broker_adapter)

        flatten_sched = FlattenScheduler(_broker_adapter, broker_config.flatten_et)
        flatten_sched.start()
        logger.info("Broker enabled: %s %s (max_pos=%d, max_loss=$%.0f)",
                     broker_config.env, broker_config.symbol,
                     broker_config.max_position, broker_config.max_daily_loss)
    else:
        logger.error("Broker: Tradovate connection failed — trading disabled")
else:
    logger.info("Broker disabled (BROKER_ENABLED != true)")
```

- [ ] **Step 2: Add broker execution to level_monitor zone touch**

Add to `_emit_zone_dqn_inference` in `level_monitor.py`, after the signal log:

```python
# Execute via broker if enabled
broker = getattr(self, '_broker_adapter', None)
if broker is not None and result is not None:
    action = result.get("action", "SKIP")
    if action not in ("SKIP", "skip"):
        import asyncio
        try:
            # Build signal for session manager format
            broker_signal = {
                "action": "enter_long" if action == "CONTINUATION" else "enter_short",
                "price": price,
                "stop_price": price - result.get("stop_ticks", 15) * 0.25 if action == "CONTINUATION"
                             else price + result.get("stop_ticks", 15) * 0.25,
                "size": result.get("sizing_signal", 1.0),
            }
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(broker.on_signal(broker_signal))
            else:
                loop.run_until_complete(broker.on_signal(broker_signal))
        except Exception:
            logger.warning("Broker execution failed", exc_info=True)
```

Add setter method to LevelMonitor:

```python
def set_broker_adapter(self, adapter) -> None:
    """Set broker adapter for automated execution."""
    self._broker_adapter = adapter
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/api/__init__.py backend/src/market_data/level_monitor.py
git commit -m "feat(broker): wire into lifespan + level monitor zone touch"
```

---

### Task 8: API Endpoint for Broker Status

**Files:**
- Modify: `backend/src/api/routes/trading.py`

- [ ] **Step 1: Add broker status endpoint**

Add to `backend/src/api/routes/trading.py`:

```python
@router.get("/broker/status")
def broker_status(request: Request):
    """Get current broker state — position, P&L, risk status."""
    adapter = getattr(request.app.state, "broker_adapter", None)
    if adapter is None:
        return {"enabled": False, "message": "Broker not enabled"}

    t = adapter.tracker
    return {
        "enabled": True,
        "halted": adapter._halted,
        "halt_reason": adapter._halt_reason,
        "position": {
            "side": t.side,
            "size": t.size,
            "entry_price": t.entry_price,
            "stop_price": t.stop_price,
        },
        "session": {
            "pnl_dollars": round(t.session_pnl, 2),
            "peak_equity": round(t.peak_equity, 2),
            "trailing_dd": round(t.trailing_dd, 2),
            "trade_count": t.trade_count,
            "consecutive_stops": t.consecutive_stops,
            "avg_slippage_ticks": round(t.slippage_ticks(), 2),
        },
    }
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/api/routes/trading.py
git commit -m "feat(broker): GET /api/trading/broker/status endpoint"
```

---

## Verification Checklist

After all tasks complete:

- [ ] `pytest backend/tests/test_position_tracker.py -v` — all pass
- [ ] `pytest backend/tests/test_broker_adapter.py -v` — all pass
- [ ] `BROKER_ENABLED=false` by default — no orders placed unless explicitly enabled
- [ ] Broker starts on lifespan when `BROKER_ENABLED=true` + valid credentials
- [ ] Flatten scheduler runs at 15:55 ET
- [ ] Risk rules enforce: daily loss, trailing DD, consecutive stops, min interval
- [ ] Signal rejected if any risk rule fails
- [ ] `/api/trading/broker/status` returns current state
- [ ] All orders are market + stop bracket
- [ ] Position tracker correctly calculates P&L and drawdown
