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

import logging
import time
from datetime import datetime, timezone

from async_rithmic import (
    DataType,
    OrderPlacement,
    RithmicClient,
    TimeBarType,
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
                ts_epoch = tick.ssboe + tick.usecs / 1e6 if hasattr(tick, "ssboe") else time.time()
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
            period = getattr(bar, "type_specifier", 1)
            interval = "5m" if period == 5 else "1m"

            ts = datetime.fromtimestamp(
                bar.ssboe if hasattr(bar, "ssboe") else time.time(),
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
