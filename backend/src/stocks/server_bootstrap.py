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
                # Dedupe within a ±2s window on (symbol, side, entry_price, size).
                # Two close events can fire for the same logical trade (signal
                # flatten arrives, then the stop-hit fill lands a moment later
                # via SignalR replay) — without windowing, both pass the
                # exact-equality dedupe and we get duplicate rows. If the new
                # event is the actual stop-hit (was_stop=True), prefer it: stop
                # outcomes are the truthful close. Otherwise drop as duplicate.
                if closed_at is not None:
                    from datetime import timedelta as _td

                    window_lo = closed_at - _td(seconds=2)
                    window_hi = closed_at + _td(seconds=2)
                    existing = (
                        db.query(BrokerTrade)
                        .filter(
                            BrokerTrade.closed_at >= window_lo,
                            BrokerTrade.closed_at <= window_hi,
                            BrokerTrade.symbol == p.get("symbol", "NQ"),
                            BrokerTrade.side == p.get("side"),
                            BrokerTrade.entry_price == p.get("entry_price"),
                            BrokerTrade.size == p.get("size"),
                        )
                        .first()
                    )
                    if existing is not None:
                        new_was_stop = bool(p.get("was_stop"))
                        old_was_stop = bool(existing.was_stop)
                        if new_was_stop and not old_was_stop:
                            # Upgrade: stop-hit close is more informative
                            existing.was_stop = True
                            if p.get("exit_price") is not None:
                                existing.exit_price = p.get("exit_price")
                            if p.get("pnl_dollars") is not None:
                                existing.pnl_dollars = p.get("pnl_dollars")
                            if p.get("pnl_r") is not None:
                                existing.pnl_r = p.get("pnl_r")
                            db.commit()
                            log.info(
                                "broker_trades dedupe: upgraded id=%d to was_stop=True",
                                existing.id,
                            )
                        else:
                            log.debug(
                                "broker_trades dedupe: dropped duplicate close (existing id=%d)",
                                existing.id,
                            )
                        return

                row = BrokerTrade(
                    ts=ts_open,
                    session_date=p.get("session_date") or ts_open.strftime("%Y-%m-%d"),
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
                    fill_latency_ms=p.get("fill_latency_ms"),
                    slippage_ticks=p.get("slippage_ticks"),
                    signal_action=p.get("signal_action"),
                    signal_confidence=p.get("signal_confidence"),
                    signal_zone=p.get("signal_zone"),
                    signal_trigger=p.get("signal_trigger"),
                    signal_cont_p=p.get("signal_cont_p"),
                    signal_rev_p=p.get("signal_rev_p"),
                    orderflow_score=p.get("orderflow_score"),
                    reasoning=p.get("reasoning") if isinstance(p.get("reasoning"), dict) else None,
                    closed_at=closed_at,
                )
                db.add(row)
                db.commit()
            finally:
                db.close()
        except Exception:
            log.warning("broker_trades direct persist failed", exc_info=True)

    threading.Thread(target=_worker, args=(payload,), daemon=True, name="broker-trade-persist").start()


def _build_server_depth_handler(level_monitor):
    """Build a TopstepXStream.on_depth callback that maintains a price->size
    book and broadcasts a throttled top-20 snapshot to all attached
    /ws/signals clients via level_monitor's signal callbacks. This is what
    feeds the local L2Ladder card in autonomous mode (where the local app
    can't see GatewayDepth directly because the server owns TopstepX)."""
    import asyncio as _asyncio
    import time as _time

    state = {"bids": {}, "asks": {}, "ts": 0.0}
    throttle_s = 0.2
    last_emit = [0.0]

    def _on_depth(level: dict) -> None:
        try:
            price = float(level.get("price", 0))
            if price == 0:
                return
            size = int(level.get("currentVolume", 0))
            side = level.get("type")
            if side not in (1, 2):
                return
            book = state["bids"] if side == 1 else state["asks"]
            if size <= 0:
                book.pop(price, None)
            else:
                book[price] = size

            now = _time.time()
            if now - last_emit[0] < throttle_s:
                return
            last_emit[0] = now
            state["ts"] = now

            bids_sorted = sorted(state["bids"].items(), key=lambda kv: -kv[0])[:20]
            asks_sorted = sorted(state["asks"].items(), key=lambda kv: kv[0])[:20]
            msg = {
                "type": "depth",
                "bids": [{"price": p, "size": s} for p, s in bids_sorted],
                "asks": [{"price": p, "size": s} for p, s in asks_sorted],
                "ts": now,
            }
            callbacks = getattr(level_monitor, "_signal_callbacks", set())
            for cb in list(callbacks):
                try:
                    _asyncio.create_task(cb(msg))
                except Exception:
                    pass
        except Exception:
            log.exception("server _on_depth handler raised")

    return _on_depth


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

    # Wire LevelMonitor zone broadcasts into the dashboard state so the
    # in-container Stocks chart renders zones. Without this, only the
    # local arnoldstocks app (via the /ws/signals relay) gets zone_update
    # payloads — the server's own dashboard.update_zones is never called
    # and the chart shows no zone overlay.
    from . import dashboard as _dashboard

    def _dashboard_zone_forwarder(msg: dict) -> None:
        if msg.get("type") == "zone_update":
            _dashboard.update_zones(msg.get("zones", []))

    level_monitor.add_signal_callback(_dashboard_zone_forwarder)

    # Sync current zones into the dashboard state immediately. Going through
    # _broadcast_zones() races against the init thread that runs
    # set_session_context — when init finishes first, the broadcast at that
    # time has zero callbacks (forwarder not yet registered). When init
    # finishes last, _zones is empty when we register. Skip the dance:
    # serialize whatever the LevelMonitor currently has and hand it straight
    # to update_zones. Future zone_update broadcasts (next rebuild) keep it
    # in sync via the forwarder.
    def _serialize_zones(zones) -> list[dict]:
        return [
            {
                "price": round(z.center_price, 2),
                "members": z.member_count,
                "upper": round(z.upper_bound, 2),
                "lower": round(z.lower_bound, 2),
                "hierarchy": round(z.hierarchy_score, 3),
            }
            for z in zones
        ]

    async def _seed_dashboard_zones_when_ready():
        # Poll _zones every 5s for up to 10 min. Init thread (runs in parallel
        # off the FastAPI loop) populates _zones once session data + levels
        # finish loading; that can land before or after this bootstrap. Once
        # zones are present, snapshot them into the dashboard state so the
        # chart renders immediately. Future rebuild_zones calls (5-min
        # periodic recompute) keep state fresh via _dashboard_zone_forwarder.
        log.info("Zone seed task started (polling every 5s for up to 10m)")
        last_count = -1
        for i in range(120):
            try:
                zs = getattr(level_monitor, "_zones", []) or []
                if len(zs) != last_count:
                    log.info("Zone seed tick %d: _zones=%d", i, len(zs))
                    last_count = len(zs)
                if zs:
                    _dashboard.update_zones(_serialize_zones(zs))
                    log.info("Seeded dashboard with %d zones from LevelMonitor", len(zs))
                    return
            except Exception:
                log.exception("Zone seed attempt failed")
            await asyncio.sleep(5)
        log.warning("Gave up waiting for LevelMonitor zones after 10 min")

    _seed_task = asyncio.create_task(_seed_dashboard_zones_when_ready())

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
    stream.on_depth = _build_server_depth_handler(level_monitor)

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
        tasks={"zone_seed": _seed_task},
    )
    app.state.stocks_runtime = runtime
    log.info("ServerStocksRuntime active — autonomous trading ON")
    return runtime
