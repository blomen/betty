"""Server-side TopstepX bootstrap — runs trading autonomously on the
Hetzner server instead of depending on a local app being connected.

Enabled by STOCKS_AUTONOMOUS=true. When set:
  - TopstepXClient authenticates from the server (uses TOPSTEPX_* env)
  - TopstepXStream receives ticks + fills directly server-side
  - BrokerAdapter attaches to LevelMonitor via set_broker_adapter (same
    pattern as the Rithmic / Tradovate paths in api/__init__.py)
  - Every tick feeds market_trades + candle flows via the same helpers
    used by the /ws/signals WebSocket path (so data persistence is
    identical whether the local app is connected or not)
  - Every fill feeds adapter.on_stream_fill → broker_trades persists
    via a direct DB insert (no HTTP POST needed — same process)
  - FlattenScheduler fires at 15:55 ET as before
  - Shutdown handler flattens open positions before closing

The local arnold/stocks_runtime.py checks this env var and no-ops when
set, so there's no duplicate broker instance.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class ServerStocksRuntime:
    client: Any
    adapter: Any
    stream: Any
    flatten_scheduler: Any
    tasks: dict = field(default_factory=dict)

    async def shutdown(self, flatten_positions: bool = True) -> None:
        """Graceful teardown. Flatten first (safest for deploy/restart),
        then stop the stream + close the client.
        """
        log.info("ServerStocksRuntime shutting down (flatten=%s)", flatten_positions)

        if flatten_positions:
            try:
                if not self.adapter.tracker.is_flat:
                    log.warning(
                        "position open at shutdown (side=%s size=%s entry=%.2f) — flattening",
                        self.adapter.tracker.side,
                        self.adapter.tracker.size,
                        self.adapter.tracker.entry_price,
                    )
                    await self.adapter.flatten("server_shutdown")
                else:
                    log.info("position already flat")
            except Exception:
                log.exception("flatten-on-shutdown failed — position may be open")

        for name, task in list(self.tasks.items()):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        try:
            if self.flatten_scheduler:
                self.flatten_scheduler.stop()
        except Exception:
            log.exception("flatten_scheduler.stop failed")
        try:
            await self.stream.stop()
        except Exception:
            log.exception("stream.stop failed")
        try:
            await self.client.close()
        except Exception:
            log.exception("client.close failed")
        log.info("ServerStocksRuntime stopped")


def _persist_broker_trade_direct(payload: dict) -> None:
    """Threaded direct DB insert for closed broker_trades — no HTTP.

    Matches the shape of BrokerTradeIn / the existing persist-callback
    signature so broker_adapter._log_broker_trade just works.
    """
    def _worker(p: dict) -> None:
        try:
            from ..db.models import BrokerTrade, get_session

            def _ts(v):
                if v is None:
                    return None
                if isinstance(v, datetime):
                    return v.replace(tzinfo=None) if v.tzinfo else v
                if isinstance(v, (int, float)):
                    return datetime.fromtimestamp(v, tz=timezone.utc).replace(tzinfo=None)
                try:
                    return datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None)
                except Exception:
                    return None

            closed_at = _ts(p.get("closed_at"))
            ts_open = _ts(p.get("ts")) or datetime.utcnow()

            db = get_session()
            try:
                # Dedupe on (closed_at, symbol, side, entry_price, size)
                if closed_at is not None:
                    exists = (
                        db.query(BrokerTrade.id)
                        .filter(
                            BrokerTrade.closed_at == closed_at,
                            BrokerTrade.symbol == p.get("symbol", "NQ"),
                            BrokerTrade.side == p.get("side"),
                            BrokerTrade.entry_price == p.get("entry_price"),
                            BrokerTrade.size == p.get("size"),
                        )
                        .first()
                    )
                    if exists:
                        return

                row = BrokerTrade(
                    ts=ts_open,
                    session_date=p.get("session_date")
                    or ts_open.strftime("%Y-%m-%d"),
                    symbol=p.get("symbol", "NQ"),
                    side=p.get("side"),
                    size=p.get("size"),
                    entry_price=p.get("entry_price"),
                    stop_price=p.get("stop_price"),
                    tp_price=p.get("tp_price"),
                    exit_price=p.get("exit_price"),
                    stop_ticks=p.get("stop_ticks"),
                    was_stop=p.get("was_stop"),
                    trail_count=p.get("trail_count"),
                    pnl_dollars=p.get("pnl_dollars"),
                    pnl_r=p.get("pnl_r"),
                    signal_action=p.get("signal_action"),
                    signal_confidence=p.get("signal_confidence"),
                    signal_zone=p.get("signal_zone"),
                    signal_trigger=p.get("signal_trigger"),
                    signal_cont_p=p.get("signal_cont_p"),
                    signal_rev_p=p.get("signal_rev_p"),
                    orderflow_score=p.get("orderflow_score"),
                    closed_at=closed_at,
                )
                db.add(row)
                db.commit()
            finally:
                db.close()
        except Exception:
            log.warning("broker_trades direct persist failed", exc_info=True)

    threading.Thread(target=_worker, args=(payload,), daemon=True, name="broker-trade-persist").start()


def _build_server_tick_handler(app, level_monitor):
    """Build a TopstepXStream.on_tick callback that mirrors the logic in
    signals_ws.py's tick branch — but called directly from the stream
    thread, not via WebSocket.
    """
    from ..api.routes.signals_ws import _buffer_tick, _persist_candle, _start_tick_flusher

    def _on_tick(price: float, size: int, ts: float, side: str = "B") -> None:
        try:
            # Tick buffer for micro/orderflow features
            tick_buffer = getattr(app.state, "stocks_tick_buffer", None)
            if tick_buffer:
                tick_ts = datetime.fromtimestamp(ts, tz=timezone.utc)
                tick_buffer.add(tick_ts, price, size, side)
                _buffer_tick(app, price, size, tick_ts, side)
                _start_tick_flusher(app)

            # Candle flows + persistence
            candle_5m = getattr(app.state, "stocks_candle_flow_5m", None)
            candle_1m = getattr(app.state, "stocks_candle_flow_1m", None)
            if candle_5m:
                _, closed = candle_5m.update(price, size, ts)
                if closed:
                    threading.Thread(target=_persist_candle, args=(app, closed, "5m"), daemon=True).start()
            if candle_1m:
                _, closed = candle_1m.update(price, size, ts)
                if closed:
                    threading.Thread(target=_persist_candle, args=(app, closed, "1m"), daemon=True).start()

            # Level monitor — this is what fires signals
            level_monitor.on_tick(price, size, ts)
        except Exception:
            log.exception("server on_tick handler raised")

    return _on_tick


async def bootstrap_stocks_on_server(app) -> ServerStocksRuntime | None:
    """Start TopstepX client + stream + broker adapter inside the FastAPI
    process. Returns None when disabled or when auth fails.
    """
    if os.environ.get("STOCKS_AUTONOMOUS", "").lower() != "true":
        log.info("STOCKS_AUTONOMOUS not set — skipping server-side stocks bootstrap")
        return None

    # Deferred imports — keep startup cheap when flag is off
    from ..broker.flatten_scheduler import FlattenScheduler
    from ..stocks import broker_adapter as _broker_adapter_mod
    from ..stocks.broker_adapter import TopstepXBrokerAdapter
    from ..stocks.config import TopstepXConfig
    from ..stocks.topstepx_client import TopstepXClient
    from ..stocks.topstepx_stream import TopstepXStream

    config = TopstepXConfig.from_env()
    if not config.is_configured:
        log.warning("TOPSTEPX_USERNAME/API_KEY missing — stocks bootstrap skipped")
        return None

    level_monitor = getattr(app.state, "level_monitor", None)
    if level_monitor is None:
        log.error("LevelMonitor not initialized yet — stocks bootstrap must run after market startup")
        return None

    # Startup grace: on container recreate, the previous container's TopstepX
    # SignalR session takes a few seconds to be torn down on TopstepX's side.
    # If we auth immediately, TopstepX sees both sessions and kicks the newer
    # one with "Multiple sessions detected". Waiting lets the old session
    # clean up so our auth becomes the sole session. Configurable via
    # STOCKS_AUTH_STARTUP_DELAY_SEC (default 30, set 0 to disable).
    delay_s = int(os.environ.get("STOCKS_AUTH_STARTUP_DELAY_SEC", "30"))
    if delay_s > 0:
        log.info("Waiting %ds before TopstepX auth (startup grace for prior session cleanup)", delay_s)
        await asyncio.sleep(delay_s)

    log.info("Authenticating with TopstepX (server-side)...")
    client = TopstepXClient(config)
    if not await client.connect():
        log.error("TopstepX auth failed — stocks bootstrap aborted")
        await client.close()
        return None
    log.info("TopstepX authenticated: account=%s", client._account_id)

    adapter = TopstepXBrokerAdapter(client, config)

    # Wire the adapter to LevelMonitor — this replaces the /ws/signals →
    # local relay → adapter round trip with a direct in-process call.
    level_monitor.set_broker_adapter(adapter)
    app.state.broker_adapter = adapter

    # Direct DB insert for closed trades (no HTTP needed — same process)
    _broker_adapter_mod.set_persist_callback(_persist_broker_trade_direct)

    # Tick stream — same tick-handler as the /ws/signals path so data flow
    # is identical whether ticks come from the local relay or here.
    stream = TopstepXStream(
        token=lambda: client._token,
        contract_id=config.contract_id,
        account_id=client._account_id,
        market_hub=config.market_hub_url,
        user_hub=config.user_hub_url,
    )
    stream.on_tick = _build_server_tick_handler(app, level_monitor)
    stream.on_fill = adapter.on_stream_fill

    log.info("Starting TopstepX stream (server-side)...")
    await stream.start()

    flatten_scheduler = FlattenScheduler(adapter, config.flatten_et)
    flatten_scheduler.start()
    log.info("FlattenScheduler started (flatten at %s ET)", config.flatten_et)

    runtime = ServerStocksRuntime(
        client=client,
        adapter=adapter,
        stream=stream,
        flatten_scheduler=flatten_scheduler,
        tasks={},
    )
    app.state.stocks_runtime = runtime
    log.info("ServerStocksRuntime active — autonomous trading ON")
    return runtime
