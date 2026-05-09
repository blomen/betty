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
            # tracker.is_flat alone is not enough — the SignalR-vs-HTTP race
            # means an entry order can be filled on TopstepX after we've
            # placed it but before the fill notification reaches the
            # tracker. If shutdown begins in that window, tracker says
            # "flat" but broker has a real position → orphan position
            # inherited by the next container with no protective stop
            # (today's trades 128 / 136 / the just-flattened orphan).
            # Always cross-check with Position/searchOpen.
            try:
                broker_positions: list = []
                try:
                    broker_positions = await self.client.search_open_positions()
                except Exception:
                    log.warning("shutdown: search_open_positions failed; falling back to tracker", exc_info=True)
                contract_id = getattr(self.client, "_config", None)
                contract_id = getattr(contract_id, "contract_id", None) if contract_id else None
                broker_size = sum(
                    int(p.get("size") or 0)
                    for p in broker_positions
                    if not contract_id or p.get("contractId") == contract_id
                )
                tracker_flat = self.adapter.tracker.is_flat
                if not tracker_flat:
                    log.warning(
                        "position open at shutdown (tracker side=%s size=%s entry=%.2f, broker_size=%d) — flattening",
                        self.adapter.tracker.side,
                        self.adapter.tracker.size,
                        self.adapter.tracker.entry_price,
                        broker_size,
                    )
                    await self.adapter.flatten("server_shutdown")
                elif broker_size > 0:
                    # Tracker says flat but broker has a position → orphan
                    # we never finished tracking. Hit the broker directly
                    # via liquidate_position; can't go through adapter.flatten
                    # because it gates on tracker.is_flat.
                    log.error(
                        "shutdown: tracker says flat but broker has size=%d (contract=%s) — "
                        "orphan position; calling liquidate_position directly",
                        broker_size,
                        contract_id,
                    )
                    try:
                        await self.client.liquidate_position()
                        log.info("shutdown: orphan position liquidated")
                    except Exception:
                        log.exception("shutdown: orphan liquidate failed — POSITION WILL BE INHERITED NAKED")
                else:
                    log.info("position already flat (tracker + broker confirm)")
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


# 2026-05-06: dedupe lock for _persist_broker_trade_direct. The persist
# callback spawns a thread per close event; if two close events for the same
# trade fire close in time (stop-hit replay + signal flatten arriving back-
# to-back), both threads run the dedupe SELECT before either has committed,
# both see no match, both INSERT — producing duplicate broker_trades rows
# (#430/#431 today: identical ts/prices/PnL written twice). Serializing the
# check-then-insert under a single lock kills the race deterministically.
# Trade execution itself stays async — only the DB write path serializes,
# which is sub-ms work.
_PERSIST_LOCK = threading.Lock()


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
            # Hold the global persist lock across the dedupe SELECT + INSERT
            # so two threads writing the same close event can't both pass
            # the dedupe lookup before either commits. See _PERSIST_LOCK
            # comment for the failure mode this prevents.
            _PERSIST_LOCK.acquire()
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

                # Resolve owning profile by mapping the active TopstepX account
                # back to whichever sports profile claimed it. Falls back to the
                # currently-active sports profile so single-account setups work
                # without explicit binding.
                from ..db.models import Profile

                tsx_account_id = p.get("topstepx_account_id")
                profile_row = None
                if tsx_account_id is not None:
                    profile_row = db.query(Profile).filter(Profile.topstepx_account_id == tsx_account_id).first()
                if profile_row is None:
                    profile_row = db.query(Profile).filter(Profile.is_active).first()

                row = BrokerTrade(
                    ts=ts_open,
                    profile_id=profile_row.id if profile_row else None,
                    session_date=p.get("session_date") or ts_open.strftime("%Y-%m-%d"),
                    symbol=p.get("symbol", "NQ"),
                    side=p.get("side"),
                    size=p.get("size"),
                    entry_price=p.get("entry_price"),
                    stop_price=p.get("stop_price"),
                    final_stop_price=p.get("final_stop_price"),
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
                    exit_reason=p.get("exit_reason"),
                    entry_order_id=p.get("entry_order_id"),
                    exit_order_id=p.get("exit_order_id"),
                )
                db.add(row)
                db.commit()
            finally:
                db.close()
                _PERSIST_LOCK.release()
        except Exception:
            log.warning("broker_trades direct persist failed", exc_info=True)

    threading.Thread(target=_worker, args=(payload,), daemon=True, name="broker-trade-persist").start()

    # ALSO push the close to /ws/signals listeners so the local TV overlay
    # appends the closed trade instantly (no 30s broker-trades poll lag).
    try:
        _broadcast_via_signal_callbacks({"type": "trade_closed", "trade": _trade_payload_to_dict(payload)})
    except Exception:
        log.warning("trade_closed broadcast failed", exc_info=True)


def _trade_payload_to_dict(p: dict) -> dict:
    """Match the shape of /api/stocks/broker-trades row dicts so the local
    overlay's reconcile_trades treats this just like a polled trade row."""

    def _iso(v):
        if v is None:
            return None
        if isinstance(v, datetime):
            return v.isoformat()
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(float(v), tz=timezone.utc).isoformat()
        return str(v)

    return {
        "id": f"live:{int(datetime.now(tz=timezone.utc).timestamp() * 1000)}",  # synthetic — DB row may not exist yet
        "ts": _iso(p.get("ts")),
        "session_date": p.get("session_date"),
        "symbol": p.get("symbol", "NQ"),
        "side": p.get("side"),
        "size": p.get("size"),
        "entry_price": p.get("entry_price"),
        "stop_price": p.get("stop_price"),
        "final_stop_price": p.get("final_stop_price"),
        "tp_price": p.get("tp_price"),
        "exit_price": p.get("exit_price"),
        "pnl_dollars": p.get("pnl_dollars"),
        "pnl_r": p.get("pnl_r"),
        "was_stop": p.get("was_stop"),
        "trail_count": p.get("trail_count"),
        "exit_reason": p.get("exit_reason"),
        "closed_at": _iso(p.get("closed_at")),
    }


# Captured at bootstrap so threaded broker-trade persists + the position
# watcher task can reach the live LevelMonitor's signal_callbacks set.
_LIVE_LEVEL_MONITOR: Any = None


async def _levels_watcher_loop(level_monitor: Any) -> None:
    """Emit `level_update` over /ws/signals when individual dim levels
    change. Each entry carries name (e.g. 'fvg_bullish'), price, and where
    available top/bottom (price_high/price_low). The local TV overlay
    draws each as a primitive matched to its family.

    Polls every 5s; rebuild_zones runs on a 5-min cadence so changes are
    rare. Diff via JSON-equality of the raw list.
    """
    # FVGs and order blocks are zone members only — they strengthen zones via
    # zone_builder._HIERARCHY_WEIGHTS but should never paint their own line on
    # the chart. Filtered out here so they don't even cross the wire.
    _SUPPRESSED_LEVEL_TYPES = {
        "order_block_bullish",
        "order_block_bearish",
        "fvg_bullish",
        "fvg_bearish",
    }
    last_emit: list | None = None
    while True:
        try:
            raw = level_monitor.get_raw_levels() if hasattr(level_monitor, "get_raw_levels") else []
            # Snapshot a stable subset of fields for diff + emission.
            snap = [
                {
                    "name": str(lv.get("type") or lv.get("name") or "unknown"),
                    "price": (
                        float(lv.get("price"))
                        if lv.get("price") is not None
                        else (
                            (float(lv["price_high"]) + float(lv["price_low"])) / 2.0
                            if lv.get("price_high") is not None and lv.get("price_low") is not None
                            else None
                        )
                    ),
                    "top": float(lv["price_high"]) if lv.get("price_high") is not None else None,
                    "bottom": float(lv["price_low"]) if lv.get("price_low") is not None else None,
                }
                for lv in raw
                if lv and str(lv.get("type") or lv.get("name") or "") not in _SUPPRESSED_LEVEL_TYPES
            ]
            snap = [s for s in snap if s["price"] is not None]
            if snap != last_emit:
                _broadcast_via_signal_callbacks({"type": "level_update", "levels": snap})
                last_emit = snap
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("levels_watcher iteration failed")
        await asyncio.sleep(5.0)


async def _position_watcher_loop(adapter: Any) -> None:
    """Emit `position_update` over /ws/signals whenever the tracker /
    pending-trade state changes. Replaces the local arnold's HTTP polling
    of /api/stocks/runtime-status — flat→open, stop trail, tp set, and
    flat transitions all push live.

    Also mirrors position state into dashboard._state["positions"] so the
    TV overlay broadcaster (which reads dash_state every 2s, not /ws/signals)
    can emit position_upsert shapes onto the chart. 2026-05-05: the local
    UI's TradeTicket shows the position via this loop's /ws/signals path,
    but TV stayed empty because update_positions() was defined and never
    called — wiring it now keeps both surfaces in sync.

    1Hz tick is plenty: stops typically trail in chunks of seconds, not
    sub-second, and entry/tp move only at trade open/close.
    """
    import time as _time

    from . import dashboard as _dashboard

    last_payload: dict | None = None
    last_dash_positions: list[dict] | None = None
    while True:
        try:
            tracker = adapter.tracker
            pending = getattr(adapter, "_pending_trade", None) or {}
            if tracker.is_flat:
                payload = {"type": "position_update", "flat": True}
                dash_positions: list[dict] = []
            else:
                entry = (
                    tracker.entry_price
                    or float(pending.get("entry_price") or 0.0)
                    or float(pending.get("signal_price") or 0.0)
                )
                stop = tracker.stop_price or pending.get("stop_price")
                tp = pending.get("tp_price")
                # entry_time: first tracker fill timestamp if available,
                # otherwise pending-trade's submit ts.
                entry_ts = pending.get("entry_fill_ts") or pending.get("entry_submit_ts") or pending.get("ts")
                if isinstance(entry_ts, datetime):
                    entry_time = entry_ts.timestamp()
                elif isinstance(entry_ts, (int, float)):
                    entry_time = float(entry_ts)
                else:
                    entry_time = _time.time()
                payload = {
                    "type": "position_update",
                    "flat": False,
                    "side": tracker.side,
                    "size": int(tracker.size),
                    "entry_price": float(entry) if entry else 0.0,
                    "stop_price": float(stop) if stop else 0.0,
                    "tp_price": float(tp) if tp else None,
                    "entry_time": entry_time,
                    # Halt cue for the chart: the active widget recolors
                    # amber when this is true (DD limit, max-stops, manual
                    # halt). Sourced from the adapter's _halted flag.
                    "halted": bool(getattr(adapter, "_halted", False)),
                }
                # Shape expected by tv_overlay/broadcaster.py:loop — it reads
                # `price`, `side`, `size`, `entry_time`, `tp_price` and pulls
                # stop_price separately from dash_state["adapter"].tracker.
                dash_positions = [
                    {
                        "side": tracker.side,
                        "size": int(tracker.size),
                        "price": float(entry) if entry else 0.0,
                        "stop_price": float(stop) if stop else 0.0,
                        "tp_price": float(tp) if tp else None,
                        "entry_time": entry_time,
                    }
                ]
            if payload != last_payload:
                _broadcast_via_signal_callbacks(payload)
                last_payload = payload
            if dash_positions != last_dash_positions:
                try:
                    _dashboard.update_positions(dash_positions)
                except Exception:
                    log.exception("update_positions failed")
                last_dash_positions = dash_positions
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("position_watcher iteration failed")
        await asyncio.sleep(1.0)


def _broadcast_via_signal_callbacks(msg: dict) -> None:
    """Push a JSON-serializable dict to every /ws/signals client by
    invoking the active LevelMonitor's _signal_callbacks (same channel as
    zones / depth). Cross-thread safe via call_soon_threadsafe on the
    dashboard's captured loop.
    """
    import asyncio as _asyncio

    from . import dashboard as _dash

    lm = _LIVE_LEVEL_MONITOR
    if lm is None:
        return
    callbacks = getattr(lm, "_signal_callbacks", None) or set()
    if not callbacks:
        return

    loop = getattr(_dash, "_dash_loop", None)
    if loop is None or loop.is_closed():
        # On-loop fallback (caller already on the event loop)
        for cb in list(callbacks):
            try:
                _asyncio.create_task(cb(msg))
            except Exception:
                pass
        return

    def _on_loop():
        for cb in list(callbacks):
            try:
                _asyncio.create_task(cb(msg))
            except Exception:
                pass

    try:
        loop.call_soon_threadsafe(_on_loop)
    except Exception:
        pass


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


async def _reconcile_position_loop(adapter, client, contract_id: str) -> None:
    """Periodically (60s) verify tracker.size matches TopstepX position size.
    On mismatch, halt + flatten — better to take a wash trade than to
    operate with diverged state.
    """
    while True:
        try:
            await asyncio.sleep(60)
            try:
                positions = await client.search_open_positions()
            except Exception:
                log.warning("reconcile loop: REST query failed; skipping cycle", exc_info=True)
                continue
            matching = [p for p in positions if p.get("contractId") == contract_id]
            broker_size = sum(int(p.get("size") or 0) for p in matching)
            local_size = int(adapter.tracker.size or 0)
            # Orphan-while-flat: tracker says flat but broker has a position.
            # 2026-05-05 saw a zombie BUY-STOP fire 47s after trade 377 closed,
            # opening an unprotected LONG that arnold never noticed (broker had
            # to be flattened manually for +$715). The loop previously skipped
            # this branch when is_flat=True. Now: if broker has size and we
            # don't, halt + liquidate via the broker directly (adapter.flatten
            # gates on tracker.is_flat so it would no-op).
            if adapter.tracker.is_flat:
                if broker_size > 0:
                    log.error(
                        "reconcile loop: ORPHAN POSITION — tracker flat but broker has size=%d "
                        "(contract=%s); halting + liquidating",
                        broker_size,
                        contract_id,
                    )
                    adapter._halt("orphan_position")
                    try:
                        await client.liquidate_position()
                        log.info("reconcile loop: orphan position liquidated")
                    except Exception:
                        log.exception("reconcile loop: orphan liquidate failed — manual intervention required")
                continue
            if broker_size != local_size:
                # 2026-05-08: skip the halt during the pre-claim window
                # (on_signal sets tracker.side + size BEFORE awaiting
                # place_market_order so fills aren't dropped, but during that
                # await the broker hasn't filled yet — local=1 broker=0 is
                # transient, not a desync). Detection: side set but
                # entry_price still 0 means we're waiting for the entry
                # fill to confirm. The watchdog at update_mark_and_check_be_lock
                # handles the case where the fill never arrives.
                if not adapter.tracker.is_flat and adapter.tracker.entry_price <= 0:
                    log.info(
                        "reconcile loop: size mismatch (broker=%d local=%d) ignored — "
                        "entry fill pending (side=%s entry_price=0)",
                        broker_size,
                        local_size,
                        adapter.tracker.side,
                    )
                    continue
                log.error(
                    "reconcile loop: SIZE MISMATCH — broker=%d local=%d; halting + flattening",
                    broker_size,
                    local_size,
                )
                adapter._halt("size_mismatch")
                try:
                    await adapter.flatten("size_mismatch_recovery")
                except Exception:
                    log.exception("reconcile loop: flatten after mismatch failed")
                # flatten() now invokes _recover_via_broker_truth which
                # reconciles the tracker and writes any missing broker_trades
                # row. The halt was a safety wall during the inconsistent
                # moment; once we're back in sync (broker flat AND tracker
                # flat), lift it so trading resumes without manual /recover
                # calls. If the recovery left tracker still non-flat, leave
                # halt in place so a human can inspect.
                if adapter._halted and adapter._halt_reason.startswith("size_mismatch") and adapter.tracker.is_flat:
                    adapter._halted = False
                    adapter._halt_reason = ""
                    log.info("reconcile loop: auto-cleared size_mismatch halt after recovery")
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("reconcile loop: unexpected error; continuing")


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

    from .tracker_reconciler import reconcile_tracker_from_broker

    reconcile_result = await reconcile_tracker_from_broker(adapter, client, config.contract_id)
    if reconcile_result.degraded and adapter._pending_trade:
        # Layer 2 fallback: restore from disk snapshot if REST failed.
        snap = adapter._pending_trade.get("tracker_snapshot")
        if snap:
            log.warning("reconcile: REST failed, falling back to disk snapshot")
            adapter.tracker.restore_from_snapshot(snap)
        else:
            log.error(
                "reconcile: REST failed AND no disk snapshot — broker_adapter is in unknown state; halting trading"
            )
            adapter._halt("reconcile_failed")

    # Wire the adapter to LevelMonitor — this replaces the /ws/signals →
    # local relay → adapter round trip with a direct in-process call.
    level_monitor.set_broker_adapter(adapter)
    app.state.broker_adapter = adapter

    # Register the adapter with the dashboard state so the TV overlay
    # broadcaster (tv_overlay/broadcaster.py:loop) can read tracker stop/tp
    # without a separate reference. Without this, dash_state["adapter"] stays
    # None and the TV overlay's model_status block never resolves a stop
    # price → the active-trade shape on TV ends up with stop=None and the
    # long/short widget can't render the R:R bands.
    from . import dashboard as _dashboard

    _dashboard.register_adapter(adapter)

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
        # Match level_monitor._broadcast_zones payload shape exactly — the
        # userscript draws thin per-member lines from members_detail, and
        # without it the seeded zones render as bare rectangles until the
        # next rebuild_zones tick refreshes them.
        from src.rl.zone_builder import _LEVEL_FAMILY, _weight

        def _members(z) -> list[dict]:
            seen: set[tuple[str, float]] = set()
            out: list[dict] = []
            for m in z.members:
                family = _LEVEL_FAMILY.get(m.level_type, m.level_type.value)
                price = round(m.price / 0.25) * 0.25
                if (family, price) in seen:
                    continue
                seen.add((family, price))
                out.append(
                    {
                        "name": m.name,
                        "type": m.level_type.value,
                        "family": family,
                        "price": price,
                        "weight": round(_weight(m.level_type), 3),
                    }
                )
            out.sort(key=lambda d: d["price"])
            return out

        return [
            {
                "price": round(z.center_price, 2),
                "members": z.member_count,
                "upper": round(z.upper_bound, 2),
                "lower": round(z.lower_bound, 2),
                "hierarchy": round(z.hierarchy_score, 3),
                "members_detail": _members(z),
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

    # Capture the active LevelMonitor so threaded broker-trade callbacks +
    # the position watcher can reach _signal_callbacks (the /ws/signals
    # broadcast channel). Done before any task that might emit.
    global _LIVE_LEVEL_MONITOR
    _LIVE_LEVEL_MONITOR = level_monitor

    # Direct DB insert for closed trades (no HTTP needed — same process).
    # Persist callback also broadcasts trade_closed via signal_callbacks.
    _broker_adapter_mod.set_persist_callback(_persist_broker_trade_direct)

    # Position watcher — emits position_update on every tracker delta so
    # the local TV overlay can drop its 2s polling. Reads tracker + the
    # adapter's pending-trade dict (which carries tp_price + entry fallback)
    # to assemble the full y-axis picture for the long/short shape.
    _pos_task = asyncio.create_task(_position_watcher_loop(adapter), name="server-position-watcher")
    _lvl_task = asyncio.create_task(_levels_watcher_loop(level_monitor), name="server-levels-watcher")

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

    def _on_quote_mark(quote_payload) -> None:
        """Use GatewayQuote as the mark-to-market price source. NQ trades are
        sparse on this feed; quotes fire reliably on every bid/ask change."""
        try:
            # quote_payload is the raw GatewayQuote dict (handle_quote unpacks args[1])
            last_price = float(quote_payload.get("lastPrice") or 0)
            if last_price <= 0:
                bid = float(quote_payload.get("bestBid") or 0)
                ask = float(quote_payload.get("bestAsk") or 0)
                if bid > 0 and ask > 0:
                    last_price = (bid + ask) / 2.0
            if last_price > 0:
                adapter.update_mark_and_check_be_lock(last_price)
        except Exception:
            log.debug("on_quote_mark error", exc_info=True)

    stream.on_quote = _on_quote_mark
    stream.on_depth = _build_server_depth_handler(level_monitor)

    # 2026-05-08: post-reconnect tracker sync. TopstepX SignalR drops + reconnects
    # roughly every ~15 minutes (idle hub timeout). Any GatewayUserTrade event
    # that arrived during the disconnect window is permanently lost — TopstepX
    # does NOT replay missed events. Without this hook, a fill landing in that
    # gap leaves tracker.entry_price=0 (the dropped-fill bug we already wrote
    # the watchdog for). The watchdog catches it but takes 30-60s. Reconciling
    # immediately on reconnect closes that gap to seconds.
    def _on_user_reconnect() -> None:
        try:
            asyncio.create_task(reconcile_tracker_from_broker(adapter, client, config.contract_id))
        except Exception:
            log.exception("on_user_reconnect: failed to schedule reconcile")

    stream.on_user_reconnect = _on_user_reconnect

    log.info("Starting TopstepX stream (server-side)...")
    await stream.start()

    _reconcile_task = asyncio.create_task(
        _reconcile_position_loop(adapter, client, config.contract_id),
        name="server-position-reconciler",
    )

    flatten_scheduler = FlattenScheduler(adapter, config.flatten_et)
    flatten_scheduler.start()
    log.info("FlattenScheduler started (flatten at %s ET)", config.flatten_et)

    runtime = ServerStocksRuntime(
        client=client,
        adapter=adapter,
        stream=stream,
        flatten_scheduler=flatten_scheduler,
        tasks={"zone_seed": _seed_task, "reconcile": _reconcile_task},
    )
    app.state.stocks_runtime = runtime
    log.info("ServerStocksRuntime active — autonomous trading ON")
    return runtime
