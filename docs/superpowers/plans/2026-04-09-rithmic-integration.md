# Rithmic Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Databento (live data) and Tradovate (broker) with a single `async-rithmic` connection for live ticks, time bars, L2 book, and order execution — free with Apex prop firm account.

**Architecture:** `RithmicClient` connects 4 WebSocket plants (ticker, order, history, pnl). Event callbacks (`on_tick`, `on_time_bar`, `on_exchange_order_notification`) feed LevelMonitor and PositionTracker. The existing BrokerAdapter's interface stays the same — only the underlying client changes.

**Tech Stack:** Python 3.10+, async-rithmic 1.5.9, asyncio, existing FastAPI/PostgreSQL infrastructure.

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| CREATE | `backend/src/rithmic/__init__.py` | Package init |
| CREATE | `backend/src/rithmic/config.py` | Rithmic connection config from env |
| CREATE | `backend/src/rithmic/stream.py` | RithmicStream: ticks, bars, L2 → LevelMonitor + DB |
| CREATE | `backend/src/rithmic/broker_client.py` | RithmicBrokerClient: orders, fills (same interface as TradovateClient) |
| MODIFY | `backend/src/api/__init__.py` | Start Rithmic instead of Databento when configured |
| MODIFY | `backend/src/broker/adapter.py` | Accept RithmicBrokerClient (same interface, no logic change) |
| MODIFY | `requirements.txt` or `pyproject.toml` | Add async-rithmic dependency |

---

### Task 1: Rithmic Config + Package

**Files:**
- Create: `backend/src/rithmic/__init__.py`
- Create: `backend/src/rithmic/config.py`

- [ ] **Step 1: Create package and config**

```python
# backend/src/rithmic/__init__.py
```

```python
# backend/src/rithmic/config.py
"""Rithmic connection configuration from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class RithmicConfig:
    """Rithmic connection settings for Apex/prop firm accounts."""
    user: str = ""
    password: str = ""
    system_name: str = "Rithmic Paper Trading"
    app_name: str = "firev"
    app_version: str = "1.0"
    url: str = "rituz00100.rithmic.com:443"  # paper trading gateway
    symbol: str = "NQM5"
    exchange: str = "CME"

    @classmethod
    def from_env(cls) -> RithmicConfig:
        return cls(
            user=os.environ.get("RITHMIC_USER", ""),
            password=os.environ.get("RITHMIC_PASSWORD", ""),
            system_name=os.environ.get("RITHMIC_SYSTEM_NAME", "Rithmic Paper Trading"),
            app_name=os.environ.get("RITHMIC_APP_NAME", "firev"),
            app_version=os.environ.get("RITHMIC_APP_VERSION", "1.0"),
            url=os.environ.get("RITHMIC_URL", "rituz00100.rithmic.com:443"),
            symbol=os.environ.get("RITHMIC_SYMBOL", "NQM5"),
            exchange=os.environ.get("RITHMIC_EXCHANGE", "CME"),
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.user and self.password)
```

- [ ] **Step 2: Add async-rithmic to dependencies**

Add `async-rithmic>=1.5` to `backend/requirements.txt` (or `pyproject.toml` dependencies).

- [ ] **Step 3: Commit**

```bash
git add backend/src/rithmic/ backend/requirements.txt
git commit -m "feat(rithmic): config module + async-rithmic dependency"
```

---

### Task 2: Rithmic Stream (replaces DatabentoLiveStream)

**Files:**
- Create: `backend/src/rithmic/stream.py`

- [ ] **Step 1: Implement RithmicStream**

```python
# backend/src/rithmic/stream.py
"""Rithmic live data stream — replaces DatabentoLiveStream.

Connects to Rithmic's Ticker Plant for live ticks and time bars,
History Plant for gap backfill. Feeds LevelMonitor.on_tick() and
persists bars to market_candles DB.

Uses async-rithmic's event callbacks:
  client.on_tick → LevelMonitor.on_tick()
  client.on_time_bar → upsert_candle() to DB
  client.on_exchange_order_notification → fill tracking
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from async_rithmic import (
    RithmicClient,
    DataType,
    TimeBarType,
    OrderPlacement,
)

from .config import RithmicConfig

log = logging.getLogger(__name__)


class RithmicStream:
    """Manages Rithmic live data connection + bar persistence."""

    def __init__(
        self,
        config: RithmicConfig,
        db_session_factory=None,
    ) -> None:
        self._config = config
        self._db_session_factory = db_session_factory
        self._client: RithmicClient | None = None
        self._level_monitor = None
        self._running = False
        self._tick_count = 0
        self._last_log_count = 0

    def set_level_monitor(self, monitor) -> None:
        """Set the LevelMonitor to receive tick callbacks."""
        self._level_monitor = monitor

    @property
    def is_connected(self) -> bool:
        if self._client is None:
            return False
        # Check if ticker plant is connected
        return self._client.plants["ticker"].is_connected

    async def start(self) -> None:
        """Connect to Rithmic and start streaming."""
        cfg = self._config

        self._client = RithmicClient(
            user=cfg.user,
            password=cfg.password,
            system_name=cfg.system_name,
            app_name=cfg.app_name,
            app_version=cfg.app_version,
            url=cfg.url,
            manual_or_auto=OrderPlacement.AUTO,
        )

        # Register event handlers
        self._client.on_tick += self._on_tick
        self._client.on_time_bar += self._on_time_bar
        self._client.on_connected += self._on_connected
        self._client.on_disconnected += self._on_disconnected

        # Connect (all plants)
        log.info("Rithmic connecting to %s as %s...", cfg.url, cfg.user)
        await self._client.connect()
        self._running = True

        # Subscribe to market data
        symbol = cfg.symbol
        exchange = cfg.exchange

        await self._client.subscribe_to_market_data(symbol, exchange, DataType.LAST_TRADE)
        await self._client.subscribe_to_market_data(symbol, exchange, DataType.BBO)
        log.info("Subscribed to %s %s ticks + BBO", symbol, exchange)

        # Subscribe to 1m and 5m bars
        await self._client.subscribe_to_time_bar_data(symbol, exchange, TimeBarType.MINUTE_BAR, 1)
        await self._client.subscribe_to_time_bar_data(symbol, exchange, TimeBarType.MINUTE_BAR, 5)
        log.info("Subscribed to %s 1m + 5m bars", symbol)

    async def stop(self) -> None:
        """Disconnect from Rithmic."""
        self._running = False
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                log.debug("Disconnect error (non-fatal)", exc_info=True)
            self._client = None
        log.info("Rithmic stream stopped")

    def _on_tick(self, tick) -> None:
        """Handle incoming trade tick."""
        self._tick_count += 1

        # Log periodically
        if self._tick_count >= self._last_log_count * 10 or self._tick_count == 1:
            log.info("Rithmic stream: %d ticks received", self._tick_count)
            self._last_log_count = max(self._tick_count, 1)

        # Feed to LevelMonitor
        if self._level_monitor is not None:
            try:
                price = float(tick.trade_price)
                size = int(tick.trade_size)
                ts_epoch = tick.ssboe + tick.usecs / 1e6 if hasattr(tick, 'ssboe') else time.time()
                self._level_monitor.on_tick(price, size, ts_epoch)
            except Exception:
                log.debug("Tick processing error", exc_info=True)

    def _on_time_bar(self, bar) -> None:
        """Handle incoming time bar — persist to DB."""
        if self._db_session_factory is None:
            return

        try:
            from ..repositories.market_repo import MarketRepo

            # Determine interval from bar period
            period = getattr(bar, 'type_specifier', 1)
            if period == 5:
                interval = "5m"
            else:
                interval = "1m"

            ts = datetime.fromtimestamp(
                bar.ssboe if hasattr(bar, 'ssboe') else time.time(),
                tz=timezone.utc,
            )

            db = self._db_session_factory()
            try:
                MarketRepo(db).upsert_candle(
                    symbol=self._config.symbol.split(".")[0].rstrip("0123456789").rstrip("FGHJKMNQUVXZ") or "NQ",
                    interval=interval,
                    ts=ts,
                    o=float(bar.open_price),
                    h=float(bar.high_price),
                    l=float(bar.low_price),
                    c=float(bar.close_price),
                    v=int(bar.volume),
                )
            finally:
                db.close()
        except Exception:
            log.debug("Bar persistence error", exc_info=True)

    def _on_connected(self, plant_type) -> None:
        log.info("Rithmic %s plant connected", plant_type)

    def _on_disconnected(self, plant_type) -> None:
        log.warning("Rithmic %s plant disconnected (auto-reconnect will handle)", plant_type)
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/rithmic/stream.py
git commit -m "feat(rithmic): RithmicStream — live ticks + bars replacing Databento"
```

---

### Task 3: Rithmic Broker Client (replaces TradovateClient)

**Files:**
- Create: `backend/src/rithmic/broker_client.py`

- [ ] **Step 1: Implement RithmicBrokerClient**

```python
# backend/src/rithmic/broker_client.py
"""Rithmic order execution client — replaces TradovateClient.

Same interface as TradovateClient so BrokerAdapter doesn't change.
Uses async-rithmic's OrderPlant for order management.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from async_rithmic import (
    RithmicClient,
    OrderType,
    TransactionType,
    ExchangeOrderNotificationType,
)

from .config import RithmicConfig

log = logging.getLogger(__name__)


class RithmicBrokerClient:
    """Order execution via Rithmic — drop-in replacement for TradovateClient."""

    def __init__(self, client: RithmicClient, config: RithmicConfig) -> None:
        self._client = client
        self._config = config
        self._account_id: str | None = None
        self._fills: list[dict] = []

        # Register fill callback
        self._client.on_exchange_order_notification += self._on_order_notification

    async def connect(self) -> bool:
        """Get account info (client should already be connected via RithmicStream)."""
        try:
            accounts = self._client.accounts
            if accounts:
                self._account_id = accounts[0].account_id if hasattr(accounts[0], 'account_id') else str(accounts[0])
                log.info("Rithmic broker: account=%s", self._account_id)
                return True
            # Try listing accounts
            account_list = await self._client.list_accounts()
            if account_list:
                self._account_id = str(account_list[0])
                log.info("Rithmic broker: account=%s", self._account_id)
                return True
            log.error("No Rithmic accounts found")
            return False
        except Exception:
            log.exception("Rithmic broker connect failed")
            return False

    async def place_market_order(self, action: str, quantity: int) -> dict:
        """Place a market order. action: 'Buy' or 'Sell'."""
        tx_type = TransactionType.BUY if action == "Buy" else TransactionType.SELL
        order_id = f"firev-{uuid.uuid4().hex[:8]}"

        await self._client.submit_order(
            order_id=order_id,
            symbol=self._config.symbol,
            exchange=self._config.exchange,
            qty=quantity,
            transaction_type=tx_type,
            order_type=OrderType.MARKET,
        )
        log.info("Market order: %s %d %s (id=%s)", action, quantity, self._config.symbol, order_id)
        return {"orderId": order_id}

    async def place_stop_order(self, action: str, quantity: int, stop_price: float) -> dict:
        """Place a stop-market order (for stop-loss)."""
        tx_type = TransactionType.BUY if action == "Buy" else TransactionType.SELL
        order_id = f"firev-stop-{uuid.uuid4().hex[:8]}"

        await self._client.submit_order(
            order_id=order_id,
            symbol=self._config.symbol,
            exchange=self._config.exchange,
            qty=quantity,
            transaction_type=tx_type,
            order_type=OrderType.STOP_MARKET,
            trigger_price=stop_price,
        )
        log.info("Stop order: %s %d @ %.2f (id=%s)", action, quantity, stop_price, order_id)
        return {"orderId": order_id}

    async def modify_order(self, order_id: str, new_stop_price: float) -> dict:
        """Modify an existing order's stop price."""
        await self._client.modify_order(
            order_id=order_id,
            trigger_price=new_stop_price,
        )
        log.info("Order %s modified: stop=%.2f", order_id, new_stop_price)
        return {"orderId": order_id}

    async def cancel_order(self, order_id: str) -> dict:
        """Cancel a pending order."""
        await self._client.cancel_order(order_id=order_id)
        log.info("Order %s cancelled", order_id)
        return {"orderId": order_id}

    async def liquidate_position(self) -> dict:
        """Flatten all positions for the configured symbol."""
        await self._client.exit_position(
            symbol=self._config.symbol,
            exchange=self._config.exchange,
        )
        log.info("Position liquidated for %s", self._config.symbol)
        return {"status": "liquidated"}

    async def get_positions(self) -> list[dict]:
        """Get open positions (via PnL plant)."""
        return []  # TODO: implement via pnl plant if needed

    async def get_orders(self) -> list[dict]:
        """Get working orders."""
        result = await self._client.list_orders()
        return result if result else []

    async def close(self) -> None:
        """No-op — client lifecycle managed by RithmicStream."""
        pass

    def _on_order_notification(self, notification) -> None:
        """Handle exchange order notifications (fills, rejects, etc)."""
        notify_type = getattr(notification, 'notify_type', None)
        if notify_type == ExchangeOrderNotificationType.FILL:
            fill = {
                "order_id": getattr(notification, 'order_id', ''),
                "price": float(getattr(notification, 'fill_price', 0)),
                "size": int(getattr(notification, 'fill_size', 0)),
                "side": getattr(notification, 'transaction_type', ''),
            }
            self._fills.append(fill)
            log.info("Fill: %s %d @ %.2f", fill["side"], fill["size"], fill["price"])
        elif notify_type == ExchangeOrderNotificationType.REJECT:
            log.warning("Order rejected: %s", getattr(notification, 'text', 'unknown'))
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/rithmic/broker_client.py
git commit -m "feat(rithmic): RithmicBrokerClient — order execution replacing Tradovate"
```

---

### Task 4: Wire Rithmic into App Lifespan

**Files:**
- Modify: `backend/src/api/__init__.py`

- [ ] **Step 1: Add Rithmic startup alongside existing Databento**

Read `backend/src/api/__init__.py`. Find the trading features startup section (where `DatabentoLiveStream` is created). Add a Rithmic alternative that takes priority when configured:

```python
# Add at the start of _start_trading_features(), before Databento stream setup:

# --- Check if Rithmic is configured (takes priority over Databento) ---
from ..rithmic.config import RithmicConfig
rithmic_config = RithmicConfig.from_env()

if rithmic_config.is_configured:
    # Use Rithmic for live data + execution
    from ..rithmic.stream import RithmicStream
    from ..rithmic.broker_client import RithmicBrokerClient

    rithmic_stream = RithmicStream(rithmic_config, db_session_factory=_get_market_session)
    rithmic_stream.set_level_monitor(level_monitor)
    app.state.rithmic_stream = rithmic_stream

    await rithmic_stream.start()
    logger.info("Rithmic stream started (replaces Databento for live data)")

    # Setup broker via Rithmic (replaces Tradovate)
    from ..broker.config import BrokerConfig
    broker_config = BrokerConfig.from_env()
    if broker_config.enabled:
        rithmic_broker = RithmicBrokerClient(rithmic_stream._client, rithmic_config)
        connected = await rithmic_broker.connect()
        if connected:
            from ..broker.adapter import BrokerAdapter
            from ..broker.flatten_scheduler import FlattenScheduler
            _broker_adapter = BrokerAdapter(rithmic_broker, broker_config)
            app.state.broker_adapter = _broker_adapter
            level_monitor.set_broker_adapter(_broker_adapter)
            flatten_sched = FlattenScheduler(_broker_adapter, broker_config.flatten_et)
            flatten_sched.start()
            logger.info("Broker enabled via Rithmic: %s", rithmic_config.symbol)

else:
    # Fallback to Databento (existing code stays as-is)
    ...
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/api/__init__.py
git commit -m "feat(rithmic): wire into app lifespan — Rithmic takes priority over Databento"
```

---

### Task 5: Add async-rithmic to Docker Image

**Files:**
- Modify: `Dockerfile` or `requirements.txt`

- [ ] **Step 1: Add dependency**

Add `async-rithmic>=1.5` to the Python dependencies so it's installed in the Docker image.

- [ ] **Step 2: Commit**

```bash
git add requirements.txt Dockerfile
git commit -m "feat(rithmic): add async-rithmic to Docker dependencies"
```

---

## Verification Checklist

After all tasks complete:

- [ ] `python -c "from src.rithmic.config import RithmicConfig; print('OK')"` — imports work
- [ ] `python -c "from src.rithmic.stream import RithmicStream; print('OK')"` — imports work
- [ ] `python -c "from src.rithmic.broker_client import RithmicBrokerClient; print('OK')"` — imports work
- [ ] When `RITHMIC_USER` is set → RithmicStream starts instead of Databento
- [ ] When `RITHMIC_USER` is NOT set → falls back to Databento (backward compatible)
- [ ] RithmicBrokerClient has same interface as TradovateClient (BrokerAdapter unchanged)
- [ ] Ticks flow to LevelMonitor.on_tick()
- [ ] Time bars persist to market_candles DB
- [ ] BROKER_ENABLED=false still prevents order execution
- [ ] Auto-reconnect handled by async-rithmic (no manual watchdog needed)
