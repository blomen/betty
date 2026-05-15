"""WebSocket relay: local arnoldstocks client <-> server LevelMonitor."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

_CET = ZoneInfo("Europe/Stockholm")

# Loopback hosts that can connect to /ws/signals without an API key. The
# legitimate client is the local relay reached via SSH tunnel, which arrives
# at the backend as 127.0.0.1. Anything coming through the docker bridge or
# nginx will have a non-loopback peer and must present X-API-Key.
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}

log = logging.getLogger(__name__)
router = APIRouter()


def _get_market_db(app):
    """Get or cache the market DB session factory."""
    factory = getattr(app.state, "_market_db_factory", None)
    if not factory:
        from ...db.models import get_market_session

        factory = get_market_session
        app.state._market_db_factory = factory
    return factory


def _persist_candle(app, candle: dict, interval: str) -> None:
    """Persist a closed candle to DB (background, non-blocking)."""

    try:
        db = _get_market_db(app)()
        try:
            from ...repositories.market_repo import MarketRepo

            ts = datetime.fromtimestamp(candle["t"], tz=timezone.utc)
            MarketRepo(db).upsert_candle(
                "NQ",
                interval,
                ts,
                candle["o"],
                candle["h"],
                candle["l"],
                candle["c"],
                candle["v"],
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        log.warning("Failed to persist %s candle: %s", interval, candle, exc_info=True)


# Batch tick writer for market_trades (same table Databento path uses)
_tick_batch: list[dict] = []
_tick_batch_lock = threading.Lock()
_TICK_FLUSH_SIZE = 500
_TICK_FLUSH_INTERVAL = 5.0
_tick_flush_thread: threading.Thread | None = None

# Dedicated pool for signals_ws work that must not block the event loop.
# Mirrors the pattern in market.py (commit efb525a): the process-wide default
# asyncio executor (8 threads on the 4-core Hetzner box) is shared with
# extraction and RL training, so per-connection seed/flush work routed through
# asyncio.to_thread can queue behind long-running jobs and stall this handler's
# event loop long enough to trip the client's 60s ping_timeout.
_SIGNALS_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="signals-")
_SEED_TIMEOUT_S = 30.0


def _start_tick_flusher(app) -> None:
    """Start background thread that flushes ticks to market_trades every 5s."""
    global _tick_flush_thread
    if _tick_flush_thread is not None:
        return

    def _loop():
        while True:
            time.sleep(_TICK_FLUSH_INTERVAL)
            _flush_tick_batch(app)

    _tick_flush_thread = threading.Thread(target=_loop, daemon=True, name="ws-tick-flusher")
    _tick_flush_thread.start()


def _buffer_tick(app, price: float, size: int, ts_dt, side: str) -> None:
    """Add a tick to the batch buffer, flush if full.

    Size-triggered flush runs in a background thread — the caller is the async
    WS handler and bulk_insert_trades is blocking psycopg2, so inlining it on
    the event loop was the direct cause of 60 s keepalive ping timeouts during
    tick bursts.
    """
    batch = None
    with _tick_batch_lock:
        _tick_batch.append(
            {
                "symbol": "NQ",
                "price": price,
                "size": size,
                "ts": ts_dt,
                "side": side,
            }
        )
        if len(_tick_batch) >= _TICK_FLUSH_SIZE:
            batch = list(_tick_batch)
            _tick_batch.clear()
    if batch:
        threading.Thread(target=_do_flush, args=(app, batch), daemon=True, name="ws-tick-flush").start()


def _flush_tick_batch(app) -> None:
    """Flush current batch to DB."""
    with _tick_batch_lock:
        if not _tick_batch:
            return
        batch = list(_tick_batch)
        _tick_batch.clear()
    _do_flush(app, batch)


def _do_flush(app, batch: list[dict]) -> None:
    """Write batch to market_trades."""
    try:
        db = _get_market_db(app)()
        try:
            from ...repositories.market_repo import MarketRepo

            MarketRepo(db).bulk_insert_trades(batch)
        finally:
            db.close()
    except Exception:
        log.debug("Failed to flush %d ticks to market_trades", len(batch))


class _RunningVWAP:
    """Developing VWAP anchored at midnight CET, resets daily."""

    def __init__(self):
        self._date: object = None  # CET date of current accumulation
        self._cum_pv = 0.0
        self._cum_vol = 0.0
        self._cum_pv2 = 0.0

    def update(self, candle: dict) -> dict | None:
        """Ingest a closed 1m candle (keys: h, l, c, v, t). Returns VWAP band dict or None."""
        ts = datetime.fromtimestamp(candle["t"], tz=timezone.utc)
        cet_date = ts.astimezone(_CET).date()
        if cet_date != self._date:
            self._cum_pv = self._cum_vol = self._cum_pv2 = 0.0
            self._date = cet_date

        tp = (candle["h"] + candle["l"] + candle["c"]) / 3
        vol = candle["v"] or 1
        self._cum_pv += tp * vol
        self._cum_vol += vol
        self._cum_pv2 += tp * tp * vol

        if self._cum_vol == 0:
            return None

        vwap = self._cum_pv / self._cum_vol
        variance = max(0.0, self._cum_pv2 / self._cum_vol - vwap * vwap)
        sd = math.sqrt(variance)
        return {
            "vwap": round(vwap, 2),
            "sd1_u": round(vwap + sd, 2),
            "sd1_l": round(vwap - sd, 2),
            "sd2_u": round(vwap + 2 * sd, 2),
            "sd2_l": round(vwap - 2 * sd, 2),
            "sd3_u": round(vwap + 3 * sd, 2),
            "sd3_l": round(vwap - 3 * sd, 2),
        }


@router.websocket("/ws/signals")
async def signal_relay(ws: WebSocket):
    """Accept ticks from local client, feed to LevelMonitor, send signals back.

    Auth: loopback peers (SSH tunnel) are trusted. Any other peer must present
    a valid X-API-Key header matching ARNOLD_API_KEY. This protects the
    fill/exit branches below from spoofed messages corrupting broker state.
    """
    peer_host = ws.client.host if ws.client else ""
    if peer_host not in _LOOPBACK_HOSTS:
        expected = os.environ.get("ARNOLD_API_KEY")
        provided = ws.headers.get("x-api-key")
        if not expected or provided != expected:
            log.warning("Signal relay rejected: peer=%s reason=auth", peer_host)
            await ws.close(code=1008, reason="unauthorized")
            return

    await ws.accept()
    log.info("Signal relay connected from %s", ws.client)

    level_monitor = getattr(ws.app.state, "level_monitor", None)
    if level_monitor is None:
        await ws.send_json({"type": "error", "message": "LevelMonitor not initialized"})
        await ws.close()
        return

    # Register message callback — forwards signals + dqn_inference to local client
    async def _on_signal(msg: dict):
        try:
            await ws.send_json(msg)
        except Exception:
            log.debug("Failed to send message to relay client")

    level_monitor.add_signal_callback(_on_signal)

    # Replay current zones to the new client. `_broadcast_zones` only
    # fires on `_rebuild_zones` (1-min candle close + initial level load).
    # A client that reconnects between rebuilds — every WS heartbeat
    # blip, every backend rebuild, every local arnold restart — would
    # otherwise sit with an empty zone overlay until the next minute
    # closes. The local TV overlay broadcaster's chart goes blank and
    # the user has to wait. Send the current zone snapshot inline on
    # connect; same payload shape as the periodic broadcast so the
    # passive listener's `zone_update` handler treats it identically.
    try:
        zones = getattr(level_monitor, "_zones", None) or []
        if zones:
            from src.rl.zone_builder import _LEVEL_FAMILY, _weight

            def _stable_members(zone) -> list[dict]:
                seen: set[tuple[str, float]] = set()
                out: list[dict] = []
                for m in zone.members:
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

            await ws.send_json(
                {
                    "type": "zone_update",
                    "zones": [
                        {
                            "price": round(z.center_price, 2),
                            "members": z.member_count,
                            "upper": round(z.upper_bound, 2),
                            "lower": round(z.lower_bound, 2),
                            "hierarchy": round(z.hierarchy_score, 3),
                            "members_detail": _stable_members(z),
                        }
                        for z in zones
                    ],
                }
            )
    except Exception:
        log.exception("zone replay on connect failed (non-fatal)")

    # Replay current position to the new client. _position_watcher_loop only
    # emits on payload changes, so a client connecting mid-trade would
    # otherwise sit blank until the next stop-trail / tp move / flat
    # transition. Two surfaces consume position_update — the local
    # TradeTicket UI and the local TV overlay broadcaster (via the passive
    # listener mirroring into dash_state). Without this replay, a backend
    # rebuild during an open trade leaves both surfaces blind to the live
    # position until the trade closes.
    adapter_for_replay = getattr(ws.app.state, "broker_adapter", None)
    if adapter_for_replay is not None:
        try:
            tracker = adapter_for_replay.tracker
            pending = getattr(adapter_for_replay, "_pending_trade", None) or {}
            if tracker.is_flat:
                await ws.send_json({"type": "position_update", "flat": True})
            else:
                entry = (
                    tracker.entry_price
                    or float(pending.get("entry_price") or 0.0)
                    or float(pending.get("signal_price") or 0.0)
                )
                stop = tracker.stop_price or pending.get("stop_price")
                tp = pending.get("tp_price")
                entry_ts = pending.get("entry_fill_ts") or pending.get("entry_submit_ts") or pending.get("ts")
                if isinstance(entry_ts, datetime):
                    entry_time = entry_ts.timestamp()
                elif isinstance(entry_ts, (int, float)):
                    entry_time = float(entry_ts)
                else:
                    import time as _time

                    entry_time = _time.time()
                await ws.send_json(
                    {
                        "type": "position_update",
                        "flat": False,
                        "side": tracker.side,
                        "size": int(tracker.size),
                        "entry_price": float(entry) if entry else 0.0,
                        "stop_price": float(stop) if stop else 0.0,
                        "tp_price": float(tp) if tp else None,
                        "entry_time": entry_time,
                    }
                )
        except Exception:
            log.exception("position replay on connect failed (non-fatal)")

    # Replay recent closed trades so a reconnecting passive listener
    # catches every close it missed during disconnect. _broadcast_via_signal_callbacks
    # is fire-and-forget — when /ws/signals churns under server load
    # (1011 keepalive timeouts within ~100ms of open), closes between
    # disconnect and reconnect would otherwise only arrive via the 30s
    # broker-trades HTTP poller, freezing the chart's closed-trade widgets.
    try:
        from ...stocks.server_bootstrap import get_recent_closed_trades

        for trade_dict in get_recent_closed_trades():
            await ws.send_json({"type": "trade_closed", "trade": trade_dict})
    except Exception:
        log.exception("trade_closed replay on connect failed (non-fatal)")

    # Running VWAP tracker — anchored midnight CET, updated on every 1m candle close.
    # Seed from DB so reconnects start with correct accumulated VWAP, not zero.
    # Runs in a thread so the blocking psycopg2 call can't freeze the event loop:
    # py-spy dumps showed the event loop frozen on a pool checkout here during
    # reconnect storms from trading_service, which wedged every other request.
    _vwap_tracker = _RunningVWAP()

    def _seed_vwap_sync() -> list:
        db_factory = _get_market_db(ws.app)
        db = db_factory()
        try:
            from ...repositories.market_repo import MarketRepo

            today_cet = datetime.now(timezone.utc).astimezone(_CET).date()
            day_start = datetime(today_cet.year, today_cet.month, today_cet.day, tzinfo=_CET).astimezone(timezone.utc)
            rows = MarketRepo(db).get_candles("NQ", "1m", day_start, datetime.now(timezone.utc))
            return [{"h": r.h, "l": r.l, "c": r.c, "v": r.v or 1, "t": r.ts.timestamp()} for r in rows]
        finally:
            db.close()

    try:
        # Use the dedicated signals pool (not asyncio.to_thread's shared default
        # executor) so a hot default pool can't queue the seed behind extraction
        # or RL work. Bounded wait releases the worker and the WS setup path if
        # the DB is genuinely stuck — better an un-seeded VWAP than a hung accept.
        seed_rows = await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(_SIGNALS_POOL, _seed_vwap_sync),
            timeout=_SEED_TIMEOUT_S,
        )
        bands = None
        for row in seed_rows:
            bands = _vwap_tracker.update(row)
        if bands:
            level_monitor.update_vwap(
                vwap=bands["vwap"],
                sd1_upper=bands["sd1_u"],
                sd1_lower=bands["sd1_l"],
                sd2_upper=bands["sd2_u"],
                sd2_lower=bands["sd2_l"],
                sd3_upper=bands["sd3_u"],
                sd3_lower=bands["sd3_l"],
            )
            log.info("VWAP seeded from %d DB candles: %.2f", len(seed_rows), bands["vwap"])
    except Exception:
        log.warning("Failed to seed VWAP from DB — will accumulate from ticks", exc_info=True)

    # Store WS reference so trading routes can send commands to trading_service
    ws.app.state._signals_ws_client = ws

    try:
        while True:
            raw = await ws.receive_text()
            # Yield to the event loop so uvicorn's ping/pong task can run between
            # ticks. Without this, back-to-back ticks during market bursts starve
            # the keepalive handler and trigger 1011 keepalive-timeout disconnects.
            await asyncio.sleep(0)
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "tick":
                price = msg["price"]
                size = msg["size"]
                ts = msg["ts"]

                # Feed tick buffer for micro/orderflow features
                tick_buffer = getattr(ws.app.state, "stocks_tick_buffer", None)
                if tick_buffer:
                    tick_ts = datetime.fromtimestamp(ts, tz=timezone.utc)
                    side = msg.get("side", "B")  # A=sell aggressor, B=buy aggressor
                    tick_buffer.add(tick_ts, price, size, side)

                    # Persist to market_trades (used by LiveEpisodeCollector + charts)
                    _buffer_tick(ws.app, price, size, tick_ts, side)
                    _start_tick_flusher(ws.app)

                # Feed candle flows for candle features + DB persistence
                candle_5m = getattr(ws.app.state, "stocks_candle_flow_5m", None)
                candle_1m = getattr(ws.app.state, "stocks_candle_flow_1m", None)
                if candle_5m:
                    _, closed = candle_5m.update(price, size, ts)
                    if closed:
                        threading.Thread(target=_persist_candle, args=(ws.app, closed, "5m"), daemon=True).start()
                if candle_1m:
                    _, closed = candle_1m.update(price, size, ts)
                    if closed:
                        threading.Thread(target=_persist_candle, args=(ws.app, closed, "1m"), daemon=True).start()
                        bands = _vwap_tracker.update(closed)
                        if bands:
                            level_monitor.update_vwap(
                                vwap=bands["vwap"],
                                sd1_upper=bands["sd1_u"],
                                sd1_lower=bands["sd1_l"],
                                sd2_upper=bands["sd2_u"],
                                sd2_lower=bands["sd2_l"],
                                sd3_upper=bands["sd3_u"],
                                sd3_lower=bands["sd3_l"],
                            )

                # Feed level monitor (triggers zone detection + inference)
                level_monitor.on_tick(price, size, ts)

                # Echo tick to other WS clients so passive listeners (the
                # local Arnold UI's _passive_dashboard_listener) can show a
                # live last price. Only the trading_service is supposed to
                # PUSH ticks, so other clients never receive them otherwise.
                # Downsampled to every 10th tick to mirror dashboard cadence
                # and avoid flooding callbacks during high-volume bursts.
                _echo_n = getattr(ws.app.state, "_signal_ws_tick_echo_n", 0) + 1
                ws.app.state._signal_ws_tick_echo_n = _echo_n
                if _echo_n % 10 == 0:
                    echo_msg = {
                        "type": "tick",
                        "price": price,
                        "size": size,
                        "ts": ts,
                        "side": msg.get("side", "B"),
                    }
                    for cb in list(getattr(level_monitor, "_signal_callbacks", set())):
                        if cb is _on_signal:
                            continue  # don't echo to sender
                        try:
                            if asyncio.iscoroutinefunction(cb):
                                asyncio.create_task(cb(echo_msg))
                            else:
                                cb(echo_msg)
                        except Exception:
                            log.debug("tick echo failed", exc_info=True)
            elif msg_type == "fill":
                adapter = getattr(ws.app.state, "broker_adapter", None)
                if adapter:
                    adapter.tracker.on_fill(
                        side=msg.get("side", "long"),
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
            elif msg_type == "command_result":
                # Trading service reports result of a command we sent
                cmd_id = msg.get("cmd_id")
                if cmd_id and hasattr(ws.app.state, "_pending_commands"):
                    fut = ws.app.state._pending_commands.pop(cmd_id, None)
                    if fut and not fut.done():
                        fut.set_result(msg.get("result", {}))
            elif msg_type == "ping":
                await ws.send_json({"type": "pong", "ts": time.time()})
    except WebSocketDisconnect:
        log.info("Signal relay disconnected")
    except Exception:
        log.exception("Signal relay error")
    finally:
        level_monitor.remove_signal_callback(_on_signal)
        if getattr(ws.app.state, "_signals_ws_client", None) is ws:
            ws.app.state._signals_ws_client = None
