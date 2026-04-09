# firevstocks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Local Windows client that streams NQ ticks from TopstepX, forwards them to the Hetzner server for ML inference, receives trading signals back, and executes orders on TopstepX — all from the user's personal device.

**Architecture:** TopstepX SignalR provides live ticks to the local PC. An SSH tunnel connects to the server's new `/ws/signals` WebSocket endpoint, which feeds ticks into the existing LevelMonitor + SpecialistEnsemble. Signals flow back to the local client, which places orders via TopstepX REST API. The existing BrokerAdapter and risk rules are reused unchanged.

**Tech Stack:** Python 3.10+, FastAPI, `requests`/`httpx`, `signalrcore`, `websockets`, SignalR WebSocket (TopstepX), SSH tunneling

---

## File Structure

```
backend/
├── src/
│   ├── stocks/
│   │   ├── __init__.py              # Package marker
│   │   ├── config.py                # TopstepXConfig dataclass
│   │   ├── topstepx_client.py       # REST client (auth, orders, positions)
│   │   ├── topstepx_stream.py       # SignalR client (ticks, fills)
│   │   └── signal_relay.py          # WS client to server /ws/signals
│   └── api/
│       └── routes/
│           └── signals_ws.py        # Server-side /ws/signals endpoint
├── run_firevstocks.py               # Local launcher (like run_mirror.py)
├── tests/
│   ├── test_topstepx_client.py      # TopstepXClient unit tests
│   ├── test_topstepx_stream.py      # TopstepXStream unit tests
│   ├── test_signal_relay.py         # SignalRelayClient unit tests
│   └── test_signals_ws.py           # Server /ws/signals endpoint tests
firevstocks.bat                      # Windows launcher (repo root)
```

---

### Task 1: TopstepXConfig

**Files:**
- Create: `backend/src/stocks/__init__.py`
- Create: `backend/src/stocks/config.py`
- Test: `backend/tests/test_topstepx_client.py`

- [ ] **Step 1: Create package marker**

```python
# backend/src/stocks/__init__.py
```

Empty file.

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_topstepx_client.py
"""Tests for TopstepX client components."""
import os
import pytest
from src.stocks.config import TopstepXConfig


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("TOPSTEPX_USERNAME", "testuser")
    monkeypatch.setenv("TOPSTEPX_API_KEY", "testapikey123")
    monkeypatch.setenv("TOPSTEPX_CONTRACT", "CON.F.US.NQ.U25")
    cfg = TopstepXConfig.from_env()
    assert cfg.username == "testuser"
    assert cfg.api_key == "testapikey123"
    assert cfg.contract_id == "CON.F.US.NQ.U25"


def test_config_defaults():
    cfg = TopstepXConfig()
    assert cfg.contract_id == "CON.F.US.NQ.M25"
    assert cfg.max_position == 2
    assert cfg.max_daily_loss == 1000.0
    assert cfg.flatten_et == "15:55"
    assert cfg.base_url == "https://api.topstepx.com"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_topstepx_client.py::test_config_from_env tests/test_topstepx_client.py::test_config_defaults -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.stocks'`

- [ ] **Step 4: Write minimal implementation**

```python
# backend/src/stocks/config.py
"""TopstepX configuration from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class TopstepXConfig:
    """TopstepX connection settings."""
    username: str = ""
    api_key: str = ""
    contract_id: str = "CON.F.US.NQ.M25"
    base_url: str = "https://api.topstepx.com"
    market_hub_url: str = "wss://rtc.topstepx.com/hubs/market"
    user_hub_url: str = "wss://rtc.topstepx.com/hubs/user"
    server_ws_url: str = "ws://127.0.0.1:18000/ws/signals"

    # Risk (applied locally by BrokerAdapter)
    max_position: int = 2
    max_daily_loss: float = 1000.0
    max_trailing_dd: float = 2000.0
    flatten_et: str = "15:55"

    @property
    def is_configured(self) -> bool:
        return bool(self.username and self.api_key)

    @classmethod
    def from_env(cls) -> TopstepXConfig:
        return cls(
            username=os.environ.get("TOPSTEPX_USERNAME", ""),
            api_key=os.environ.get("TOPSTEPX_API_KEY", ""),
            contract_id=os.environ.get("TOPSTEPX_CONTRACT", "CON.F.US.NQ.M25"),
            base_url=os.environ.get("TOPSTEPX_BASE_URL", "https://api.topstepx.com"),
            server_ws_url=os.environ.get("SIGNAL_RELAY_URL", "ws://127.0.0.1:18000/ws/signals"),
            max_position=int(os.environ.get("BROKER_MAX_POSITION", "2")),
            max_daily_loss=float(os.environ.get("BROKER_MAX_DAILY_LOSS", "1000")),
            max_trailing_dd=float(os.environ.get("BROKER_MAX_TRAILING_DD", "2000")),
            flatten_et=os.environ.get("BROKER_FLATTEN_ET", "15:55"),
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_topstepx_client.py::test_config_from_env tests/test_topstepx_client.py::test_config_defaults -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/stocks/__init__.py backend/src/stocks/config.py backend/tests/test_topstepx_client.py
git commit -m "feat(stocks): add TopstepXConfig dataclass"
```

---

### Task 2: TopstepXClient — REST Client

**Files:**
- Create: `backend/src/stocks/topstepx_client.py`
- Modify: `backend/tests/test_topstepx_client.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_topstepx_client.py`:

```python
import asyncio
import json
from unittest.mock import AsyncMock, patch, MagicMock
import httpx
from src.stocks.topstepx_client import TopstepXClient, TopstepXError


@pytest.fixture
def client():
    cfg = TopstepXConfig(username="testuser", api_key="key123", contract_id="CON.F.US.NQ.M25")
    return TopstepXClient(cfg)


def _mock_response(data: dict, status=200):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


@pytest.mark.asyncio
async def test_connect_success(client):
    with patch.object(client, "_post", new_callable=AsyncMock) as mock_post:
        # First call: loginKey returns token
        # Second call: account search returns accounts
        mock_post.side_effect = [
            {"success": True, "token": "jwt_token_123"},
            {"success": True, "accounts": [{"id": 42, "name": "50K Standard"}]},
        ]
        result = await client.connect()
        assert result is True
        assert client._token == "jwt_token_123"
        assert client._account_id == 42


@pytest.mark.asyncio
async def test_connect_bad_creds(client):
    with patch.object(client, "_post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"success": False, "token": None, "errorCode": 3}
        result = await client.connect()
        assert result is False


@pytest.mark.asyncio
async def test_place_market_order(client):
    client._token = "tok"
    client._account_id = 42
    with patch.object(client, "_post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"success": True, "orderId": 999}
        result = await client.place_market_order("Buy", 1)
        mock_post.assert_called_once_with("/api/Order/place", {
            "accountId": 42,
            "contractId": "CON.F.US.NQ.M25",
            "type": 2,
            "side": 0,
            "size": 1,
        })
        assert result["orderId"] == 999


@pytest.mark.asyncio
async def test_place_stop_order(client):
    client._token = "tok"
    client._account_id = 42
    with patch.object(client, "_post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"success": True, "orderId": 1000}
        result = await client.place_stop_order("Sell", 1, 21400.0)
        mock_post.assert_called_once_with("/api/Order/place", {
            "accountId": 42,
            "contractId": "CON.F.US.NQ.M25",
            "type": 3,
            "side": 1,
            "size": 1,
            "stopPrice": 21400.0,
        })


@pytest.mark.asyncio
async def test_cancel_order(client):
    client._token = "tok"
    client._account_id = 42
    with patch.object(client, "_post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"success": True}
        await client.cancel_order(999)
        mock_post.assert_called_once_with("/api/Order/cancel", {
            "accountId": 42,
            "orderId": 999,
        })


@pytest.mark.asyncio
async def test_liquidate_position(client):
    client._token = "tok"
    client._account_id = 42
    with patch.object(client, "_post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"success": True}
        await client.liquidate_position()
        mock_post.assert_called_once_with("/api/Position/closeContract", {
            "accountId": 42,
            "contractId": "CON.F.US.NQ.M25",
        })


@pytest.mark.asyncio
async def test_modify_order(client):
    client._token = "tok"
    client._account_id = 42
    with patch.object(client, "_post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"success": True}
        await client.modify_order(999, 21405.0)
        mock_post.assert_called_once_with("/api/Order/modify", {
            "accountId": 42,
            "orderId": 999,
            "stopPrice": 21405.0,
        })
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_topstepx_client.py -v -k "not config"`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.stocks.topstepx_client'`

- [ ] **Step 3: Write the implementation**

```python
# backend/src/stocks/topstepx_client.py
"""TopstepX REST client — drop-in replacement for TradovateClient."""
from __future__ import annotations

import logging
import time

import httpx

from .config import TopstepXConfig

log = logging.getLogger(__name__)

ACTION_BUY = "Buy"
ACTION_SELL = "Sell"


class TopstepXError(Exception):
    pass


class TopstepXClient:
    """REST client for TopstepX/ProjectX Gateway API.

    Same interface as TradovateClient so BrokerAdapter works unchanged.
    """

    def __init__(self, config: TopstepXConfig) -> None:
        self.config = config
        self._http = httpx.AsyncClient(timeout=15.0)
        self._token: str | None = None
        self._token_ts: float = 0.0
        self._account_id: int | None = None

    async def connect(self) -> bool:
        """Authenticate with API key and discover account."""
        try:
            resp = await self._post("/api/Auth/loginKey", {
                "userName": self.config.username,
                "apiKey": self.config.api_key,
            })
            if not resp.get("success") or not resp.get("token"):
                log.error("TopstepX auth failed: %s", resp.get("errorMessage", resp))
                return False
            self._token = resp["token"]
            self._token_ts = time.time()
            log.info("TopstepX authenticated")

            # Discover account
            accts = await self._post("/api/Account/search", {"onlyActiveAccounts": True})
            accounts = accts.get("accounts", [])
            if not accounts:
                log.error("TopstepX: no active accounts found")
                return False
            self._account_id = accounts[0]["id"]
            log.info("TopstepX account: %s (id=%d)", accounts[0].get("name", "?"), self._account_id)
            return True
        except Exception:
            log.exception("TopstepX connect failed")
            return False

    async def place_market_order(self, action: str, quantity: int) -> dict:
        """Place market order. action: 'Buy' or 'Sell'."""
        return await self._post("/api/Order/place", {
            "accountId": self._account_id,
            "contractId": self.config.contract_id,
            "type": 2,  # market
            "side": 0 if action == ACTION_BUY else 1,
            "size": quantity,
        })

    async def place_stop_order(self, action: str, quantity: int, stop_price: float) -> dict:
        """Place stop-market order."""
        return await self._post("/api/Order/place", {
            "accountId": self._account_id,
            "contractId": self.config.contract_id,
            "type": 3,  # stop
            "side": 0 if action == ACTION_BUY else 1,
            "size": quantity,
            "stopPrice": stop_price,
        })

    async def modify_order(self, order_id: int, new_stop_price: float) -> dict:
        """Modify existing order's stop price."""
        return await self._post("/api/Order/modify", {
            "accountId": self._account_id,
            "orderId": order_id,
            "stopPrice": new_stop_price,
        })

    async def cancel_order(self, order_id: int) -> dict:
        """Cancel a pending order."""
        return await self._post("/api/Order/cancel", {
            "accountId": self._account_id,
            "orderId": order_id,
        })

    async def liquidate_position(self) -> dict:
        """Flatten all positions for configured contract."""
        return await self._post("/api/Position/closeContract", {
            "accountId": self._account_id,
            "contractId": self.config.contract_id,
        })

    async def get_positions(self) -> list[dict]:
        resp = await self._post("/api/Position/searchOpen", {
            "accountId": self._account_id,
        })
        return resp.get("positions", [])

    async def get_orders(self) -> list[dict]:
        resp = await self._post("/api/Order/searchOpen", {
            "accountId": self._account_id,
        })
        return resp.get("orders", [])

    async def close(self) -> None:
        await self._http.aclose()

    async def _ensure_token(self) -> None:
        """Refresh token if older than 23 hours."""
        if self._token and (time.time() - self._token_ts) > 23 * 3600:
            try:
                resp = await self._post("/api/Auth/validate", {})
                new_token = resp.get("newToken") or resp.get("token")
                if new_token:
                    self._token = new_token
                    self._token_ts = time.time()
                    log.info("TopstepX token refreshed")
            except Exception:
                log.warning("Token refresh failed — will re-auth on next call")

    async def _post(self, path: str, body: dict) -> dict:
        """POST with auth header."""
        if self._token and path != "/api/Auth/loginKey":
            await self._ensure_token()
        headers = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        resp = await self._http.post(
            f"{self.config.base_url}{path}", json=body, headers=headers,
        )
        data = resp.json()
        if path != "/api/Auth/loginKey" and not data.get("success", True):
            raise TopstepXError(data.get("errorMessage") or f"API error on {path}: {data}")
        return data
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_topstepx_client.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/stocks/topstepx_client.py backend/tests/test_topstepx_client.py
git commit -m "feat(stocks): add TopstepXClient REST client"
```

---

### Task 3: TopstepXStream — SignalR Tick/Fill Client

**Files:**
- Create: `backend/src/stocks/topstepx_stream.py`
- Create: `backend/tests/test_topstepx_stream.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_topstepx_stream.py
"""Tests for TopstepX SignalR stream."""
import pytest
from unittest.mock import MagicMock, patch
from src.stocks.topstepx_stream import TopstepXStream


@pytest.fixture
def stream():
    return TopstepXStream(
        token="test_jwt",
        contract_id="CON.F.US.NQ.M25",
        account_id=42,
    )


def test_handle_trade_calls_on_tick(stream):
    ticks = []
    stream.on_tick = lambda p, s, t: ticks.append((p, s, t))
    stream._handle_trade([{"price": 21450.25, "size": 3, "timestamp": "2026-04-09T14:30:00Z"}])
    assert len(ticks) == 1
    assert ticks[0][0] == 21450.25
    assert ticks[0][1] == 3


def test_handle_trade_ignores_empty(stream):
    ticks = []
    stream.on_tick = lambda p, s, t: ticks.append((p, s, t))
    stream._handle_trade([])
    assert len(ticks) == 0


def test_handle_fill_calls_on_fill(stream):
    fills = []
    stream.on_fill = lambda f: fills.append(f)
    stream._handle_user_trade([{
        "orderId": 100, "contractId": "CON.F.US.NQ.M25",
        "price": 21450.50, "size": 1, "side": 0,
    }])
    assert len(fills) == 1
    assert fills[0]["price"] == 21450.50


def test_no_callback_no_crash(stream):
    # Shouldn't raise even with no callbacks set
    stream.on_tick = None
    stream.on_fill = None
    stream._handle_trade([{"price": 21450.0, "size": 1, "timestamp": "2026-04-09T14:30:00Z"}])
    stream._handle_user_trade([{"orderId": 1, "price": 21450.0, "size": 1, "side": 0}])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_topstepx_stream.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# backend/src/stocks/topstepx_stream.py
"""TopstepX SignalR stream — live ticks and fill notifications."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable

from signalrcore.hub_connection_builder import HubConnectionBuilder

log = logging.getLogger(__name__)


class TopstepXStream:
    """Connects to TopstepX Market + User SignalR hubs."""

    MARKET_HUB = "wss://rtc.topstepx.com/hubs/market"
    USER_HUB = "wss://rtc.topstepx.com/hubs/user"

    def __init__(self, token: str, contract_id: str, account_id: int,
                 market_hub: str | None = None, user_hub: str | None = None) -> None:
        self._token = token
        self._contract_id = contract_id
        self._account_id = account_id
        self._market_hub = market_hub or self.MARKET_HUB
        self._user_hub = user_hub or self.USER_HUB
        self._market_conn = None
        self._user_conn = None

        # Callbacks — set by the launcher
        self.on_tick: Callable[[float, int, float], None] | None = None
        self.on_fill: Callable[[dict], None] | None = None

    def start(self) -> None:
        """Connect to both hubs and subscribe."""
        # Market hub — live trades
        self._market_conn = (
            HubConnectionBuilder()
            .with_url(
                f"{self._market_hub}?access_token={self._token}",
                options={"skip_negotiation": True, "verify_ssl": True},
            )
            .with_automatic_reconnect({
                "type": "interval",
                "keep_alive_interval": 10,
                "intervals": [0, 2, 5, 10, 30],
            })
            .build()
        )
        self._market_conn.on("GatewayTrade", self._handle_trade)
        self._market_conn.on_open(lambda: log.info("Market hub connected"))
        self._market_conn.on_close(lambda: log.warning("Market hub disconnected"))
        self._market_conn.on_error(lambda e: log.error("Market hub error: %s", e))
        self._market_conn.start()
        self._market_conn.send("SubscribeContractTrades", [self._contract_id])
        log.info("Subscribed to trades: %s", self._contract_id)

        # User hub — fills, positions
        self._user_conn = (
            HubConnectionBuilder()
            .with_url(
                f"{self._user_hub}?access_token={self._token}",
                options={"skip_negotiation": True, "verify_ssl": True},
            )
            .with_automatic_reconnect({
                "type": "interval",
                "keep_alive_interval": 10,
                "intervals": [0, 2, 5, 10, 30],
            })
            .build()
        )
        self._user_conn.on("GatewayUserTrade", self._handle_user_trade)
        self._user_conn.on("GatewayUserPosition", self._handle_position)
        self._user_conn.on("GatewayUserOrder", self._handle_order)
        self._user_conn.on_open(lambda: log.info("User hub connected"))
        self._user_conn.on_close(lambda: log.warning("User hub disconnected"))
        self._user_conn.start()
        self._user_conn.send("SubscribeToPositions", [self._account_id])
        self._user_conn.send("SubscribeToOrders", [self._account_id])
        self._user_conn.send("SubscribeToUserTrades", [self._account_id])
        log.info("Subscribed to user events for account %d", self._account_id)

    def stop(self) -> None:
        """Disconnect both hubs."""
        for conn in (self._market_conn, self._user_conn):
            if conn:
                try:
                    conn.stop()
                except Exception:
                    pass
        log.info("TopstepX stream stopped")

    def _handle_trade(self, args: list) -> None:
        """Market trade tick from GatewayTrade."""
        if not args or not self.on_tick:
            return
        tick = args[0] if isinstance(args, list) else args
        try:
            price = float(tick["price"])
            size = int(tick.get("size", 1))
            ts_raw = tick.get("timestamp", "")
            if isinstance(ts_raw, str) and ts_raw:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp()
            else:
                ts = time.time()
            self.on_tick(price, size, ts)
        except Exception:
            log.debug("Bad trade tick: %s", tick, exc_info=True)

    def _handle_user_trade(self, args: list) -> None:
        """Fill notification from GatewayUserTrade."""
        if not args or not self.on_fill:
            return
        fill = args[0] if isinstance(args, list) else args
        try:
            self.on_fill(fill)
        except Exception:
            log.debug("Bad fill: %s", fill, exc_info=True)

    def _handle_position(self, args: list) -> None:
        """Position update — log for now."""
        pos = args[0] if isinstance(args, list) and args else args
        log.info("Position update: %s", pos)

    def _handle_order(self, args: list) -> None:
        """Order update — log for now."""
        order = args[0] if isinstance(args, list) and args else args
        log.debug("Order update: %s", order)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_topstepx_stream.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/stocks/topstepx_stream.py backend/tests/test_topstepx_stream.py
git commit -m "feat(stocks): add TopstepXStream SignalR client"
```

---

### Task 4: Server-Side Signal Relay WebSocket Endpoint

**Files:**
- Create: `backend/src/api/routes/signals_ws.py`
- Modify: `backend/src/api/__init__.py` (add router import + include)
- Modify: `backend/src/market_data/level_monitor.py:757-825` (add signal callback)
- Create: `backend/tests/test_signals_ws.py`

- [ ] **Step 1: Add `set_signal_callback` to LevelMonitor**

In `backend/src/market_data/level_monitor.py`, add after `set_broker_adapter` (line 825):

```python
    def set_signal_callback(self, fn) -> None:
        """Set callback for zone signals. Used by /ws/signals relay."""
        self._signal_callback = fn
```

And modify `_emit_zone_dqn_inference` to call the callback. After the existing broker execution block (line 803-819), add:

```python
            # Send signal via relay callback (for firevstocks local client)
            callback = getattr(self, '_signal_callback', None)
            if callback is not None and result is not None:
                action = result.get("action", "SKIP")
                if action not in ("SKIP", "skip"):
                    signal_msg = {
                        "action": "enter_long" if action == "CONTINUATION" else "enter_short",
                        "price": price,
                        "stop_price": price - result.get("stop_ticks", 15) * 0.25
                                     if action == "CONTINUATION"
                                     else price + result.get("stop_ticks", 15) * 0.25,
                        "size": result.get("sizing_signal", 1.0),
                        "confidence": result.get("confidence", 0.0),
                        "zone": zone.center_price,
                        "zone_members": zone.member_count,
                    }
                    try:
                        import asyncio
                        if asyncio.iscoroutinefunction(callback):
                            asyncio.create_task(callback(signal_msg))
                        else:
                            callback(signal_msg)
                    except Exception:
                        logger.warning("Signal callback failed", exc_info=True)
```

- [ ] **Step 2: Write the failing test for signals_ws**

```python
# backend/tests/test_signals_ws.py
"""Tests for /ws/signals relay endpoint."""
import pytest
import json
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocket

from src.api.routes.signals_ws import router, _active_relay


def _make_app():
    app = FastAPI()
    app.include_router(router)
    # Mock level_monitor on app.state
    monitor = MagicMock()
    monitor.on_tick = MagicMock()
    monitor.set_signal_callback = MagicMock()
    app.state.level_monitor = monitor
    return app, monitor


def test_tick_forwarded_to_level_monitor():
    app, monitor = _make_app()
    client = TestClient(app)
    with client.websocket_connect("/ws/signals") as ws:
        ws.send_json({"type": "tick", "price": 21450.25, "size": 3, "ts": 1712678400.0})
        # Give it a moment to process
        import time
        time.sleep(0.1)
        monitor.on_tick.assert_called_with(21450.25, 3, 1712678400.0)


def test_ping_pong():
    app, monitor = _make_app()
    client = TestClient(app)
    with client.websocket_connect("/ws/signals") as ws:
        ws.send_json({"type": "ping"})
        resp = ws.receive_json()
        assert resp["type"] == "pong"


def test_signal_callback_registered():
    app, monitor = _make_app()
    client = TestClient(app)
    with client.websocket_connect("/ws/signals") as ws:
        ws.send_json({"type": "ping"})
        ws.receive_json()
    # Callback should have been set and then cleared
    assert monitor.set_signal_callback.call_count >= 1
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_signals_ws.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Write the signals_ws route**

```python
# backend/src/api/routes/signals_ws.py
"""WebSocket relay: local firevstocks client <-> server LevelMonitor."""
from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)
router = APIRouter()

# Track active relay connection (only one client at a time)
_active_relay: WebSocket | None = None


@router.websocket("/ws/signals")
async def signal_relay(ws: WebSocket):
    """Accept ticks from local client, feed to LevelMonitor, send signals back."""
    global _active_relay
    await ws.accept()
    _active_relay = ws
    log.info("Signal relay connected from %s", ws.client)

    level_monitor = getattr(ws.app.state, "level_monitor", None)
    if level_monitor is None:
        await ws.send_json({"type": "error", "message": "LevelMonitor not initialized"})
        await ws.close()
        return

    # Register signal callback — sends specialist signals to local client
    async def _on_signal(signal: dict):
        try:
            await ws.send_json({"type": "signal", **signal})
        except Exception:
            log.debug("Failed to send signal to relay client")

    level_monitor.set_signal_callback(_on_signal)

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "tick":
                level_monitor.on_tick(msg["price"], msg["size"], msg["ts"])

            elif msg_type == "fill":
                adapter = getattr(ws.app.state, "broker_adapter", None)
                if adapter:
                    side = msg.get("side", "long")
                    adapter.tracker.on_fill(
                        side=side,
                        price=msg["price"],
                        size=msg.get("size", 1),
                        stop_price=msg.get("stop_price", 0.0),
                        signal_price=msg.get("signal_price", 0.0),
                    )

            elif msg_type == "exit":
                adapter = getattr(ws.app.state, "broker_adapter", None)
                if adapter:
                    adapter.tracker.on_exit(
                        exit_price=msg["price"],
                        was_stop=msg.get("was_stop", False),
                    )

            elif msg_type == "ping":
                await ws.send_json({"type": "pong", "ts": time.time()})

    except WebSocketDisconnect:
        log.info("Signal relay disconnected")
    except Exception:
        log.exception("Signal relay error")
    finally:
        level_monitor.set_signal_callback(None)
        _active_relay = None
```

- [ ] **Step 5: Wire the router into `api/__init__.py`**

In `backend/src/api/__init__.py`, add the import alongside other route imports (around line 48):

```python
from .routes.signals_ws import router as signals_ws_router
```

And include it in the app (after other `app.include_router` calls):

```python
app.include_router(signals_ws_router)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_signals_ws.py -v`
Expected: ALL PASS

- [ ] **Step 7: Run existing broker adapter tests to confirm no regression**

Run: `cd backend && python -m pytest tests/test_broker_adapter.py tests/test_position_tracker.py -v`
Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
git add backend/src/api/routes/signals_ws.py backend/src/market_data/level_monitor.py backend/src/api/__init__.py backend/tests/test_signals_ws.py
git commit -m "feat(stocks): add /ws/signals relay endpoint + LevelMonitor signal callback"
```

---

### Task 5: SignalRelayClient — Local WS Client to Server

**Files:**
- Create: `backend/src/stocks/signal_relay.py`
- Create: `backend/tests/test_signal_relay.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_signal_relay.py
"""Tests for SignalRelayClient."""
import pytest
import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from src.stocks.signal_relay import SignalRelayClient
from src.stocks.config import TopstepXConfig


@pytest.fixture
def relay():
    client = AsyncMock()  # mock TopstepXClient
    return SignalRelayClient(
        server_ws_url="ws://localhost:18000/ws/signals",
        topstepx_client=client,
    )


def test_build_tick_message(relay):
    msg = relay._tick_msg(21450.25, 3, 1712678400.0)
    assert msg == {"type": "tick", "price": 21450.25, "size": 3, "ts": 1712678400.0}


def test_build_fill_message(relay):
    msg = relay._fill_msg("long", 21450.50, 1, 21446.25)
    assert msg == {
        "type": "fill", "side": "long",
        "price": 21450.50, "size": 1, "stop_price": 21446.25,
    }


@pytest.mark.asyncio
async def test_execute_signal_long(relay):
    signal = {
        "type": "signal",
        "action": "enter_long",
        "price": 21450.0,
        "stop_price": 21446.0,
        "size": 1,
        "confidence": 0.82,
    }
    await relay._execute_signal(signal)
    relay._client.place_market_order.assert_called_once_with("Buy", 1)
    relay._client.place_stop_order.assert_called_once_with("Sell", 1, 21446.0)


@pytest.mark.asyncio
async def test_execute_signal_short(relay):
    signal = {
        "type": "signal",
        "action": "enter_short",
        "price": 21450.0,
        "stop_price": 21454.0,
        "size": 1,
        "confidence": 0.75,
    }
    await relay._execute_signal(signal)
    relay._client.place_market_order.assert_called_once_with("Sell", 1)
    relay._client.place_stop_order.assert_called_once_with("Buy", 1, 21454.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_signal_relay.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# backend/src/stocks/signal_relay.py
"""WebSocket client connecting to server's /ws/signals endpoint."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable

import websockets

log = logging.getLogger(__name__)


class SignalRelayClient:
    """Connects to server, forwards ticks, receives trading signals."""

    def __init__(self, server_ws_url: str, topstepx_client) -> None:
        self._url = server_ws_url
        self._client = topstepx_client
        self._ws = None
        self._connected = False
        self._listen_task: asyncio.Task | None = None

        # Callbacks for UI updates
        self.on_signal: Callable[[dict], None] | None = None
        self.on_zone_update: Callable[[dict], None] | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        """Connect to server signal relay with retry."""
        while True:
            try:
                self._ws = await websockets.connect(self._url, ping_interval=20)
                self._connected = True
                log.info("Signal relay connected to %s", self._url)
                self._listen_task = asyncio.create_task(self._listen())
                return
            except Exception as e:
                log.warning("Signal relay connect failed: %s — retrying in 5s", e)
                await asyncio.sleep(5)

    async def disconnect(self) -> None:
        self._connected = False
        if self._listen_task:
            self._listen_task.cancel()
        if self._ws:
            await self._ws.close()

    async def forward_tick(self, price: float, size: int, ts: float) -> None:
        """Forward a tick from TopstepX to server for ML inference."""
        if not self._ws:
            return
        try:
            await self._ws.send(json.dumps(self._tick_msg(price, size, ts)))
        except Exception:
            log.debug("Failed to forward tick")

    async def forward_fill(self, side: str, price: float, size: int, stop_price: float) -> None:
        """Forward a fill from TopstepX to server for tracking."""
        if not self._ws:
            return
        try:
            await self._ws.send(json.dumps(self._fill_msg(side, price, size, stop_price)))
        except Exception:
            log.debug("Failed to forward fill")

    async def _listen(self) -> None:
        """Listen for signals from server and execute them."""
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                msg_type = msg.get("type")

                if msg_type == "signal":
                    log.info("Signal received: %s %.2f stop=%.2f conf=%.2f",
                             msg.get("action"), msg.get("price", 0),
                             msg.get("stop_price", 0), msg.get("confidence", 0))
                    await self._execute_signal(msg)
                    if self.on_signal:
                        self.on_signal(msg)

                elif msg_type == "zone_update":
                    if self.on_zone_update:
                        self.on_zone_update(msg)

                elif msg_type == "pong":
                    pass

        except websockets.ConnectionClosed:
            log.warning("Signal relay connection lost — reconnecting")
            self._connected = False
            await self.connect()
        except Exception:
            log.exception("Signal relay listen error")
            self._connected = False

    async def _execute_signal(self, signal: dict) -> None:
        """Execute a signal by placing orders on TopstepX."""
        action = signal.get("action", "")
        stop_price = signal.get("stop_price", 0.0)
        size = int(signal.get("size", 1) or 1)

        is_long = "long" in action
        order_action = "Buy" if is_long else "Sell"
        stop_action = "Sell" if is_long else "Buy"

        try:
            result = await self._client.place_market_order(order_action, size)
            log.info("Market order placed: %s x%d → %s", order_action, size, result)

            if stop_price:
                stop_result = await self._client.place_stop_order(stop_action, size, stop_price)
                log.info("Stop order placed: %s x%d @ %.2f → %s",
                         stop_action, size, stop_price, stop_result)

            # Forward fill to server for tracking
            await self.forward_fill(
                side="long" if is_long else "short",
                price=signal.get("price", 0.0),
                size=size,
                stop_price=stop_price,
            )
        except Exception:
            log.exception("Order execution failed for signal: %s", signal)

    @staticmethod
    def _tick_msg(price: float, size: int, ts: float) -> dict:
        return {"type": "tick", "price": price, "size": size, "ts": ts}

    @staticmethod
    def _fill_msg(side: str, price: float, size: int, stop_price: float) -> dict:
        return {"type": "fill", "side": side, "price": price, "size": size, "stop_price": stop_price}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_signal_relay.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/stocks/signal_relay.py backend/tests/test_signal_relay.py
git commit -m "feat(stocks): add SignalRelayClient — WS bridge to server"
```

---

### Task 6: Launcher (`run_firevstocks.py` + `firevstocks.bat`)

**Files:**
- Create: `backend/run_firevstocks.py`
- Create: `firevstocks.bat` (repo root)

- [ ] **Step 1: Write the launcher**

```python
# backend/run_firevstocks.py
"""
firevstocks launcher — local NQ futures trading client.

Connects to:
  1. Hetzner server via SSH tunnel (DB reads + signal relay WS)
  2. TopstepX API (market data + order execution)

Double-click firevstocks.bat or run `python run_firevstocks.py` to start.
"""

import asyncio
import logging
import os
import socket
import subprocess
import sys
import time
import threading
import webbrowser

from dotenv import load_dotenv
load_dotenv()

SERVER = "148.251.40.251"
LOCAL_PG_PORT = 15432
LOCAL_WS_PORT = 18000       # SSH tunnel to server backend (for WS)
LOCAL_BACKEND_PORT = 8001   # local FastAPI for UI (8000 is used by firevsports)
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

log = logging.getLogger("firevstocks")


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _kill_port(port: int, label: str):
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if f"127.0.0.1:{port}" in line and "LISTENING" in line:
                pid = line.strip().split()[-1]
                log.info("Killing old %s (PID %s) on port %d", label, pid, port)
                subprocess.run(["taskkill", "/PID", pid, "/F"],
                               capture_output=True, timeout=5)
                time.sleep(0.5)
                return
    except Exception:
        pass


def _start_tunnels() -> bool:
    """Start SSH tunnels: DB + backend WS."""
    tunnels = [
        (LOCAL_PG_PORT, "postgres"),
        (LOCAL_WS_PORT, "backend-ws"),
    ]
    for port, label in tunnels:
        if _port_in_use(port):
            log.info("Tunnel already open on localhost:%d (%s)", port, label)
            continue

    # Get postgres container IP
    try:
        result = subprocess.run(
            ["ssh", f"root@{SERVER}",
             "docker inspect firev-postgres-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'"],
            capture_output=True, text=True, timeout=10,
        )
        pg_ip = result.stdout.strip().strip("'") or "172.18.0.2"
    except Exception:
        pg_ip = "172.18.0.2"

    # SSH tunnel: local:15432 → postgres:5432 AND local:18000 → localhost:8000 (server backend)
    if not _port_in_use(LOCAL_PG_PORT) or not _port_in_use(LOCAL_WS_PORT):
        log.info("Opening SSH tunnels to %s...", SERVER)
        subprocess.Popen(
            ["ssh", "-N",
             "-L", f"{LOCAL_PG_PORT}:{pg_ip}:5432",
             "-L", f"{LOCAL_WS_PORT}:127.0.0.1:8000",
             f"root@{SERVER}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        for _ in range(20):
            time.sleep(0.5)
            if _port_in_use(LOCAL_PG_PORT) and _port_in_use(LOCAL_WS_PORT):
                log.info("SSH tunnels ready")
                return True
        log.error("SSH tunnels failed to start")
        return False
    return True


async def _run():
    """Main async entrypoint."""
    from src.stocks.config import TopstepXConfig
    from src.stocks.topstepx_client import TopstepXClient
    from src.stocks.topstepx_stream import TopstepXStream
    from src.stocks.signal_relay import SignalRelayClient

    config = TopstepXConfig.from_env()
    if not config.is_configured:
        log.error("TopstepX not configured. Set TOPSTEPX_USERNAME and TOPSTEPX_API_KEY.")
        return

    # 1. Authenticate with TopstepX
    log.info("Authenticating with TopstepX...")
    client = TopstepXClient(config)
    if not await client.connect():
        log.error("TopstepX authentication failed")
        return
    log.info("TopstepX authenticated — account ready")

    # 2. Connect signal relay to server
    relay_url = f"ws://127.0.0.1:{LOCAL_WS_PORT}/ws/signals"
    relay = SignalRelayClient(server_ws_url=relay_url, topstepx_client=client)
    await relay.connect()
    log.info("Signal relay connected to server")

    # 3. Start TopstepX stream (ticks + fills)
    stream = TopstepXStream(
        token=client._token,
        contract_id=config.contract_id,
        account_id=client._account_id,
    )

    # Wire: tick from TopstepX → forward to server
    def on_tick(price, size, ts):
        asyncio.create_task(relay.forward_tick(price, size, ts))

    stream.on_tick = on_tick
    stream.on_fill = lambda fill: log.info("Fill: %s", fill)
    stream.start()
    log.info("TopstepX stream started — ticks flowing")

    # 4. Keep alive
    log.info("firevstocks running. Ctrl+C to stop.")
    try:
        while True:
            await asyncio.sleep(30)
            # Periodic health check
            if not relay.is_connected:
                log.warning("Signal relay disconnected — reconnecting")
                await relay.connect()
    except asyncio.CancelledError:
        pass
    finally:
        stream.stop()
        await relay.disconnect()
        await client.close()
        log.info("firevstocks stopped")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log.info("firevstocks — NQ Futures Trading Client")
    log.info("Server: %s", SERVER)

    # Kill previous instance
    _kill_port(LOCAL_BACKEND_PORT, "old firevstocks")

    # Start SSH tunnels
    if not _start_tunnels():
        input("Press Enter to exit...")
        return

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        log.info("Shutting down...")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write the bat launcher**

```bat
@echo off
cd /d "%~dp0backend"
python run_firevstocks.py
```

Save as `firevstocks.bat` in repo root.

- [ ] **Step 3: Smoke test the launcher starts (will fail on auth — that's expected)**

Run: `cd backend && timeout 5 python run_firevstocks.py 2>&1 || true`
Expected: Starts, logs "TopstepX not configured" (no env vars set), exits cleanly.

- [ ] **Step 4: Commit**

```bash
git add backend/run_firevstocks.py firevstocks.bat
git commit -m "feat(stocks): add firevstocks launcher + bat file"
```

---

### Task 7: Server Startup Mode — FIREV_STOCKS_MODE

**Files:**
- Modify: `backend/src/api/__init__.py:147-315`

- [ ] **Step 1: Add FIREV_STOCKS_MODE gate**

In `backend/src/api/__init__.py`, in the lifespan function, after the `_mirror_only` check (line 147), add:

```python
    _stocks_mode = bool(os.environ.get("FIREV_STOCKS_MODE"))
    if _stocks_mode:
        logger.info("[Startup] Stocks mode — LevelMonitor + Specialists active, no Databento, no local broker")
```

Then in the trading features section (around line 197), modify the Databento gate to also skip when `_stocks_mode` is set. After `if databento_key and not _mirror_only:`, add an additional check so the Databento stream only starts when stocks mode is NOT active:

The existing code at line 197 is:
```python
    databento_key = os.environ.get("DATABENTO_API_KEY")
    _databento_stream = None
    if databento_key and not _mirror_only:
```

Change to:
```python
    databento_key = os.environ.get("DATABENTO_API_KEY")
    _databento_stream = None
    if databento_key and not _mirror_only and not _stocks_mode:
```

Then after the entire Databento/Rithmic block, add the stocks mode initialization:

```python
    # ── Stocks mode: LevelMonitor + Specialists without Databento stream ──
    # Ticks come from the local client via /ws/signals WebSocket.
    if _stocks_mode and not _mirror_only:
        from ..market_data.level_monitor import LevelMonitor
        from ..market_data.stream import CandleFlow
        from ..db.models import get_market_session as _get_market_session
        from ..repositories.market_repo import MarketRepo

        # CandleFlow instances for tick→candle aggregation (fed from WS ticks)
        _candle_flow_5m = CandleFlow(bucket_seconds=300)
        _candle_flow_1m = CandleFlow(bucket_seconds=60)

        def _stocks_publish(event: dict) -> None:
            """Publish events to SSE subscribers (same as Databento mode)."""
            pass  # SSE handled by StreamState if needed later

        level_monitor = LevelMonitor(publish_fn=_stocks_publish)
        app.state.level_monitor = level_monitor
        logger.info("[Startup] Stocks mode: LevelMonitor initialized (ticks via /ws/signals)")

        # Load initial levels in background
        import threading
        from ..services.market_service import MarketService
        from ..db.models import get_session as _get_db_session

        def _load_initial_data_stocks():
            loop = asyncio.new_event_loop()
            async def _run():
                try:
                    svc = MarketService(_get_db_session())
                    try:
                        from datetime import date, timedelta
                        for attempt_date in [None, "yesterday"]:
                            try:
                                if attempt_date == "yesterday":
                                    yesterday = (date.today() - timedelta(days=1)).isoformat()
                                    await svc.compute_session(yesterday)
                                else:
                                    await svc.compute_session()
                                expanded = await svc.build_expanded_session()
                                if expanded:
                                    level_monitor.load_levels(expanded)
                                    logger.info("[Stocks] Initial levels loaded")
                                    break
                            except Exception as exc:
                                logger.debug("compute attempt %s failed: %s", attempt_date, exc)
                    finally:
                        svc.db.close()
                except Exception:
                    logger.exception("[Stocks] Initial data load failed")
            loop.run_until_complete(_run())
            loop.close()
        threading.Thread(target=_load_initial_data_stocks, daemon=True, name="stocks-init").start()
```

- [ ] **Step 2: Test that server starts in stocks mode**

Run on server (or local): `FIREV_STOCKS_MODE=1 DATABENTO_API_KEY="" python -c "from src.api import app; print('OK')"`
Expected: Prints "OK" without Databento errors.

- [ ] **Step 3: Commit**

```bash
git add backend/src/api/__init__.py
git commit -m "feat(stocks): add FIREV_STOCKS_MODE startup path"
```

---

### Task 8: Integration Test — Full Pipeline Smoke Test

**Files:**
- Create: `backend/tests/test_stocks_integration.py`

- [ ] **Step 1: Write integration test**

```python
# backend/tests/test_stocks_integration.py
"""Integration test: TopstepXClient + SignalRelay + BrokerAdapter."""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.stocks.config import TopstepXConfig
from src.stocks.topstepx_client import TopstepXClient
from src.stocks.signal_relay import SignalRelayClient
from src.broker.adapter import BrokerAdapter
from src.broker.config import BrokerConfig


@pytest.fixture
def topstepx_client():
    cfg = TopstepXConfig(username="test", api_key="key", contract_id="CON.F.US.NQ.M25")
    client = TopstepXClient(cfg)
    client._token = "test_token"
    client._account_id = 42
    # Mock the HTTP layer
    client._post = AsyncMock()
    return client


@pytest.fixture
def relay(topstepx_client):
    return SignalRelayClient(
        server_ws_url="ws://localhost:18000/ws/signals",
        topstepx_client=topstepx_client,
    )


def test_topstepx_client_is_broker_compatible(topstepx_client):
    """TopstepXClient has same interface as TradovateClient for BrokerAdapter."""
    required_methods = [
        "connect", "place_market_order", "place_stop_order",
        "modify_order", "cancel_order", "liquidate_position",
        "get_positions", "get_orders", "close",
    ]
    for method in required_methods:
        assert hasattr(topstepx_client, method), f"Missing method: {method}"
        assert asyncio.iscoroutinefunction(getattr(topstepx_client, method)), f"Not async: {method}"


@pytest.mark.asyncio
async def test_broker_adapter_works_with_topstepx(topstepx_client):
    """BrokerAdapter accepts TopstepXClient and executes signals."""
    topstepx_client._post.return_value = {"success": True, "orderId": 123}
    config = BrokerConfig(enabled=True, max_daily_loss=1000, max_position=2)
    adapter = BrokerAdapter(client=topstepx_client, config=config)

    signal = {"action": "enter_long", "price": 21450.0, "stop_price": 21446.0, "size": 1}
    result = await adapter.on_signal(signal)
    assert result is not None
    assert result["side"] == "long"
    # Verify TopstepX was called (via _post mock)
    assert topstepx_client._post.call_count == 2  # market + stop order


@pytest.mark.asyncio
async def test_relay_executes_signal_on_topstepx(relay, topstepx_client):
    """SignalRelayClient places orders on TopstepX when receiving signal."""
    topstepx_client._post.return_value = {"success": True, "orderId": 456}
    relay._ws = AsyncMock()  # mock websocket

    signal = {
        "type": "signal",
        "action": "enter_short",
        "price": 21450.0,
        "stop_price": 21454.0,
        "size": 1,
        "confidence": 0.78,
    }
    await relay._execute_signal(signal)

    # Should have called place_market_order + place_stop_order
    topstepx_client.place_market_order.assert_called_once_with("Sell", 1)
    topstepx_client.place_stop_order.assert_called_once_with("Buy", 1, 21454.0)
```

- [ ] **Step 2: Run integration tests**

Run: `cd backend && python -m pytest tests/test_stocks_integration.py -v`
Expected: ALL PASS

- [ ] **Step 3: Run full test suite to confirm no regressions**

Run: `cd backend && python -m pytest tests/ -v --timeout=30 -x`
Expected: No new failures

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_stocks_integration.py
git commit -m "test(stocks): add integration tests for full pipeline"
```

---

### Task 9: Rename mirror.bat → firevsports.bat

**Files:**
- Rename: `mirror.bat` → `firevsports.bat`

- [ ] **Step 1: Rename the file**

```bash
git mv mirror.bat firevsports.bat
```

- [ ] **Step 2: Verify contents are correct**

`firevsports.bat` should contain:
```bat
@echo off
cd /d "%~dp0backend"
python run_mirror.py
```

No change to contents — `run_mirror.py` stays the same internally.

- [ ] **Step 3: Commit**

```bash
git add mirror.bat firevsports.bat
git commit -m "refactor: rename mirror.bat → firevsports.bat"
```

---

### Task 10: Install Dependencies

**Files:**
- Modify: `backend/requirements.txt` (if it exists) or `pyproject.toml`

- [ ] **Step 1: Check current dependency file**

Run: `ls backend/requirements*.txt backend/pyproject.toml 2>/dev/null`

- [ ] **Step 2: Add dependencies**

Add these to whatever dependency file exists:

```
signalrcore>=1.0.2
websockets>=12.0
```

`httpx` and `requests` should already be present (used by existing code).

- [ ] **Step 3: Install locally**

Run: `cd backend && python -m pip install signalrcore websockets`
Expected: Already installed (signalrcore was installed earlier in this session).

- [ ] **Step 4: Commit**

```bash
git add backend/requirements.txt  # or pyproject.toml
git commit -m "deps: add signalrcore + websockets for firevstocks"
```

---

## Summary

| Task | Component | What it does |
|------|-----------|-------------|
| 1 | TopstepXConfig | Config dataclass with env var loading |
| 2 | TopstepXClient | REST client for auth, orders, positions |
| 3 | TopstepXStream | SignalR client for live ticks + fills |
| 4 | /ws/signals endpoint | Server-side WS relay + LevelMonitor callback |
| 5 | SignalRelayClient | Local WS client that bridges server ↔ TopstepX |
| 6 | Launcher | `run_firevstocks.py` + `firevstocks.bat` |
| 7 | Server stocks mode | `FIREV_STOCKS_MODE` startup path |
| 8 | Integration tests | Full pipeline smoke test |
| 9 | Rename mirror.bat | `mirror.bat` → `firevsports.bat` |
| 10 | Dependencies | signalrcore + websockets |
