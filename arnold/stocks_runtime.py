"""Arnold stocks runtime — TopstepX client + relay + stream, bootstrapped by arnold/server.py.

Replaces the standalone backend/run_arnoldstocks.py process: the TopstepX side
now runs as asyncio tasks inside the unified Arnold FastAPI process.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Make backend.src.* importable from arnold/
_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

log = logging.getLogger("arnold.stocks")


def _serialize_trade_for_post(kwargs: dict) -> dict:
    """Convert _log_broker_trade kwargs into the BrokerTradeIn schema shape.

    Strips Nones, converts datetime → ISO string, and only includes fields the
    server endpoint accepts.
    """
    from datetime import datetime as _dt

    allowed = {
        "ts",
        "session_date",
        "symbol",
        "side",
        "size",
        "entry_price",
        "stop_price",
        "exit_price",
        "tp_price",
        "pnl_dollars",
        "pnl_r",
        "fill_latency_ms",
        "slippage_ticks",
        "was_stop",
        "trail_count",
        "stop_ticks",
        "signal_action",
        "signal_confidence",
        "signal_zone",
        "signal_trigger",
        "signal_cont_p",
        "signal_rev_p",
        "closed_at",
    }
    out: dict = {}
    for k, v in kwargs.items():
        if k not in allowed or v is None:
            continue
        if isinstance(v, _dt):
            out[k] = v.isoformat()
        else:
            out[k] = v
    out.setdefault("symbol", "NQ")
    return out


@dataclass
class StocksRuntime:
    client: Any
    adapter: Any
    relay: Any
    stream: Any
    flatten_scheduler: Any
    # Mutable task registry — heartbeat's supervisor may replace the "relay"
    # entry when it restarts a dead task, so shutdown must read through this
    # dict to cancel the current task rather than a stale reference.
    tasks: dict

    async def shutdown(self) -> None:
        log.info("Stocks runtime shutting down...")
        # Cancel heartbeat first so its supervisor doesn't restart the relay
        # we're about to kill.
        hb = self.tasks.get("heartbeat")
        if hb is not None:
            hb.cancel()
            try:
                await hb
            except (asyncio.CancelledError, Exception):
                pass
        relay_task = self.tasks.get("relay")
        if relay_task is not None:
            relay_task.cancel()
            try:
                await relay_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
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
        log.info("Stocks runtime stopped")


class _TrackerShim:
    """Quacks like the broker_adapter.tracker the broadcaster reads. Holds
    just enough fields for `reconcile_position` to compute entry/stop/tp."""

    __slots__ = ("entry_price", "stop_price")

    def __init__(self, entry_price: float | None, stop_price: float | None) -> None:
        self.entry_price = entry_price
        self.stop_price = stop_price


class _AdapterShim:
    __slots__ = ("tracker",)

    def __init__(self, tracker: _TrackerShim) -> None:
        self.tracker = tracker


async def _passive_position_poller() -> None:
    """Safety-net poller for position state. The primary path is the
    `/ws/signals` push handled in `_passive_dashboard_listener` —
    `position_update` messages mirror the same fields (entry_price, size,
    side, entry_time, stop_price, tp_price) into dash_state instantly.

    This poller exists for one specific server-side bug: tracker.entry_price
    can stay at 0.0 even when a position is open, which would draw the
    position shape at price 0 (off-chart). When that happens the poller
    substitutes the last tick price so the chart still renders.

    Used to run at 2s — that produced 30 round-trips/min through the SSH
    tunnel for state the WS push already delivers. 30s is enough for the
    entry_price=0 fallback because positions are slow-changing and the
    fallback only kicks when the WS push has already failed to populate
    entry_price.
    """
    import json

    import httpx

    from arnold.http_client import tunnel_client
    from src.stocks.dashboard import _state as dash_state
    from src.stocks.dashboard import update_positions

    url = "/api/stocks/runtime-status"

    # Track flat ↔ open transitions so we can stamp entry_time when the
    # position first opens. Server-side tracker doesn't expose this — the
    # closest fill timestamp lives only in broker_trades after close.
    # Also capture a fallback entry price from the last tick: server-side
    # tracker.entry_price is buggy and stays at 0.0 even when a position is
    # open, which would draw the long/short shape at price 0 (off-chart).
    last_was_flat = True
    entry_time: float | None = None
    fallback_entry: float | None = None

    client = tunnel_client()
    while True:
        try:
            r = await client.get(url, timeout=5.0)
            data = r.json() if r.status_code == 200 else {}
            pos = (data or {}).get("position") or {}
            if pos and not pos.get("flat"):
                side_raw = pos.get("side", "long")
                side = "long" if str(side_raw).lower() == "long" else "short"
                size = int(pos.get("size", 0))
                entry = float(pos.get("entry_price") or 0.0)
                stop = pos.get("stop_price")
                original_stop = pos.get("original_stop_price")
                tp = pos.get("tp_price")
                if size > 0:
                    if last_was_flat:
                        entry_time = time.time()
                        fallback_entry = None
                    last_was_flat = False
                    # Keep refreshing fallback from the latest tick while
                    # tracker.entry_price stays 0 — server tracker often
                    # never populates entry_price at all in autonomous
                    # mode. Once we have a real entry from the tracker,
                    # we lock it; otherwise we follow the live price so
                    # the shape at least appears at a sensible spot.
                    if entry <= 0:
                        ticks = dash_state.get("ticks") or []
                        if ticks:
                            try:
                                last = list(ticks)[-1]
                                px = float(last.get("p") or 0.0)
                                if px > 0:
                                    fallback_entry = px
                            except Exception:
                                pass
                    effective_entry = entry if entry > 0 else (fallback_entry or 0.0)
                    update_positions(
                        [
                            {
                                "price": effective_entry,
                                "size": size,
                                "side": side,
                                "entry_time": entry_time,
                                "tp_price": float(tp) if tp else None,
                                "original_stop_price": float(original_stop) if original_stop else None,
                            }
                        ]
                    )
                    dash_state["adapter"] = _AdapterShim(_TrackerShim(effective_entry, float(stop) if stop else None))
                else:
                    last_was_flat = True
                    entry_time = None
                    fallback_entry = None
                    update_positions([])
                    dash_state.pop("adapter", None)
            else:
                last_was_flat = True
                entry_time = None
                fallback_entry = None
                update_positions([])
                dash_state.pop("adapter", None)
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            log.debug("position poller: %s", exc)
        except Exception:
            log.exception("position poller iteration failed")
        await asyncio.sleep(30.0)


async def _passive_trades_poller() -> None:
    """Fetch the last 7 days of broker_trades and stash them in
    `dash_state["trades"]` so the TV overlay broadcaster can paint each
    trade on the chart (closed AND open, with entry/exit/stop/tp/timestamps).

    Polls every 30s — historical data is slow-changing; the active position
    has its own faster poller for live updates.
    """
    import json

    import httpx

    from arnold.http_client import tunnel_client
    from src.stocks.dashboard import _state as dash_state

    url = "/api/stocks/broker-trades"
    client = tunnel_client()
    while True:
        try:
            r = await client.get(url, params={"days": 7}, timeout=10.0)
            if r.status_code == 200:
                data = r.json() or {}
                trades = data.get("trades") or []
                dash_state["trades"] = trades
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            log.debug("trades poller: %s", exc)
        except Exception:
            log.exception("trades poller iteration failed")
        await asyncio.sleep(30.0)


async def _passive_dashboard_listener() -> None:
    """Mirror server-side dashboard events into the local dashboard state.

    Used in autonomous mode where the server owns the TopstepX session — the
    local Arnold app's chart WS / REST endpoints still need their _state dict
    populated. We connect to the server's /ws/signals as a passive subscriber
    (X-API-Key auth) and forward zone_update / signal / dqn_inference / tick
    payloads into the local dashboard module. No order execution, no tick
    forwarding back — purely read-only.
    """
    import json
    import os

    import websockets

    from src.stocks.dashboard import (
        bind_loop,
        record_dqn_inference,
        record_signal,
        record_tick,
        update_zones,
    )

    bind_loop(asyncio.get_running_loop())

    api_key = os.environ.get("ARNOLD_API_KEY", "")
    api_base = os.environ.get("ARNOLD_TUNNEL_URL") or os.environ.get(
        "ARNOLDSPORTS_TUNNEL_URL", "http://localhost:18000"
    )
    # /api/* tunnel uses HTTP — derive the matching ws:// URL.
    ws_url = api_base.replace("http://", "ws://").replace("https://", "wss://").rstrip("/")
    ws_url = f"{ws_url}/ws/signals"
    headers = [("X-API-Key", api_key)] if api_key else []

    while True:
        try:
            log.info("Passive dashboard listener: connecting to %s", ws_url)
            async with websockets.connect(
                ws_url,
                ping_interval=30,
                ping_timeout=60,
                additional_headers=headers,
            ) as ws:
                log.info("Passive dashboard listener: connected")
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    t = msg.get("type")
                    if t == "zone_update":
                        update_zones(msg.get("zones", []))
                    elif t == "signal":
                        record_signal(msg)
                    elif t == "dqn_inference":
                        record_dqn_inference(msg)
                    elif t == "tick":
                        record_tick(
                            float(msg.get("price", 0)),
                            int(msg.get("size", 0)),
                            float(msg.get("ts", 0)),
                            msg.get("side", "B"),
                        )
                    elif t == "position_update":
                        # Server pushes the live position snapshot whenever
                        # tracker / pending-trade state changes. Mirror into
                        # dash_state so the TV overlay broadcaster's next
                        # tick sees fresh entry/stop/tp/entry_time without
                        # an HTTP poll.
                        from src.stocks.dashboard import _state as _ds
                        from src.stocks.dashboard import update_positions as _upd

                        if msg.get("flat"):
                            _upd([])
                            _ds.pop("adapter", None)
                        else:
                            _upd(
                                [
                                    {
                                        "price": float(msg.get("entry_price") or 0.0),
                                        "size": int(msg.get("size") or 0),
                                        "side": msg.get("side"),
                                        "entry_time": msg.get("entry_time"),
                                        "tp_price": msg.get("tp_price"),
                                        "halted": bool(msg.get("halted", False)),
                                    }
                                ]
                            )
                            _ds["adapter"] = _AdapterShim(
                                _TrackerShim(
                                    float(msg.get("entry_price") or 0.0),
                                    float(msg.get("stop_price")) if msg.get("stop_price") else None,
                                )
                            )
                    elif t == "level_update":
                        # Server pushes the full individual-level list.
                        # Stored separately from zones so the TV overlay
                        # broadcaster can emit per-dim shapes.
                        from src.stocks.dashboard import _state as _ds

                        _ds["levels"] = list(msg.get("levels") or [])
                    elif t == "trade_closed":
                        # New broker_trade row just landed server-side —
                        # prepend to dash_state["trades"] so the TV overlay
                        # paints the closed shape with no 30s poll lag.
                        from src.stocks.dashboard import _state as _ds

                        trade = msg.get("trade") or {}
                        if trade:
                            existing = list(_ds.get("trades") or [])
                            existing.insert(0, trade)
                            _ds["trades"] = existing[:2000]
                    elif t == "depth":
                        # Server pre-aggregates a top-20 snapshot. Mirror it
                        # directly into _state["depth"] (skip per-level
                        # record_depth since the dict overwrites are
                        # already done server-side). Re-emit so local WS
                        # clients see the same payload shape.
                        from src.stocks.dashboard import _emit
                        from src.stocks.dashboard import _state as _ds

                        bids = msg.get("bids") or []
                        asks = msg.get("asks") or []
                        try:
                            _ds["depth"]["bids"] = {
                                float(lvl["price"]): int(lvl["size"]) for lvl in bids if isinstance(lvl, dict)
                            }
                            _ds["depth"]["asks"] = {
                                float(lvl["price"]): int(lvl["size"]) for lvl in asks if isinstance(lvl, dict)
                            }
                            _ds["depth"]["ts"] = float(msg.get("ts") or 0.0)
                            _emit({"type": "depth", "bids": bids, "asks": asks, "ts": msg.get("ts") or 0.0})
                        except Exception:
                            log.exception("passive depth handler malformed payload")
        except Exception as exc:
            log.warning("Passive dashboard listener: connection lost (%s) — retrying in 5s", exc)
            await asyncio.sleep(5)


async def bootstrap_stocks() -> StocksRuntime | None:
    """Authenticate TopstepX, start stream + relay, wire dashboard callbacks.

    Returns None when TopstepX is not configured (sports-only mode) or
    when STOCKS_AUTONOMOUS=true (server runs the broker; we'd get duplicate
    fills + order conflicts if both sides connected simultaneously).
    """
    import os

    if os.environ.get("STOCKS_AUTONOMOUS", "").lower() == "true":
        log.info(
            "STOCKS_AUTONOMOUS=true — server handles TopstepX. Starting passive "
            "WS listener so the local chart still receives zone/signal/dqn updates."
        )
        # Spawn a passive listener that mirrors server-side dashboard events into
        # the local dashboard state. The chart's WS endpoint is mounted by this
        # Arnold app, so its _state needs to be populated even though all real
        # TopstepX work happens server-side.
        asyncio.create_task(_passive_dashboard_listener(), name="passive-dashboard")
        # Server doesn't broadcast position state (update_positions is unused
        # server-side as of 2026-04-27), so the local broadcaster has no
        # position to draw on TV. Poll /api/stocks/runtime-status instead.
        asyncio.create_task(_passive_position_poller(), name="passive-position-poller")
        # All historical broker_trades (last 7 days) drawn on the chart:
        # entry_time → close_time bounded shapes with stop/tp levels.
        asyncio.create_task(_passive_trades_poller(), name="passive-trades-poller")
        return None

    from arnold.http_client import tunnel_client
    from src.broker.flatten_scheduler import FlattenScheduler
    from src.stocks import broker_adapter as _broker_adapter_mod
    from src.stocks.broker_adapter import TopstepXBrokerAdapter
    from src.stocks.config import TopstepXConfig
    from src.stocks.dashboard import (
        _state as dash_state,
    )
    from src.stocks.dashboard import (
        bind_loop,
        record_depth,
        record_dqn_inference,
        record_fill,
        record_quote,
        record_signal,
        record_tick,
        update_status,
        update_zones,
    )
    from src.stocks.signal_relay import SignalRelayClient
    from src.stocks.topstepx_client import TopstepXClient
    from src.stocks.topstepx_stream import TopstepXStream

    config = TopstepXConfig.from_env()
    if not config.is_configured:
        log.warning("TopstepX not configured — stocks runtime disabled")
        return None

    # Bind the current event loop so sync callbacks can schedule WS broadcasts
    bind_loop(asyncio.get_running_loop())

    log.info("Authenticating with TopstepX...")
    client = TopstepXClient(config)
    if not await client.connect():
        log.error("TopstepX authentication failed — stocks runtime disabled")
        await client.close()
        return None
    log.info("TopstepX authenticated")

    adapter = TopstepXBrokerAdapter(client, config)
    relay = SignalRelayClient(config.server_ws_url, client, adapter=adapter)
    stream = TopstepXStream(
        token=lambda: client._token,
        contract_id=config.contract_id,
        account_id=client._account_id,
        market_hub=config.market_hub_url,
        user_hub=config.user_hub_url,
    )

    dash_state["stats"]["session_start"] = time.time()
    dash_state["topstepx_client"] = client
    dash_state["adapter"] = adapter

    # --- broker_trade persistence: POST every closed round-trip to the server.
    # The server endpoint dedupes on (closed_at, symbol, side, entry_price, size)
    # so retries are safe. Fire-and-forget — failures only show in local logs.
    # Uses the singleton tunnel client (auth headers preset) so we don't open
    # a fresh TCP connection on every closed trade.
    _persist_loop = asyncio.get_running_loop()

    async def _post_trade(payload: dict) -> None:
        try:
            hc = tunnel_client()
            r = await hc.post("/api/stocks/broker-trades", json=payload, timeout=15.0)
            if r.status_code >= 400:
                log.warning("broker-trade POST %d: %s", r.status_code, r.text[:200])
        except Exception:
            log.exception("broker-trade POST failed")

    def _persist_trade(trade_kwargs: dict) -> None:
        # _log_broker_trade runs on the TopstepX stream thread, so schedule
        # the async POST onto the FastAPI loop instead of running it here.
        payload = _serialize_trade_for_post(trade_kwargs)
        asyncio.run_coroutine_threadsafe(_post_trade(payload), _persist_loop)

    _broker_adapter_mod.set_persist_callback(_persist_trade)

    def on_tick(price: float, size: int, ts: float, side: str = "B") -> None:
        asyncio.create_task(relay.forward_tick(price, size, ts, side))
        record_tick(price, size, ts, side)

    def on_fill(fill: dict) -> None:
        side = "long" if fill.get("side", 0) == 0 else "short"
        price = float(fill.get("price", 0))
        size = int(fill.get("size", 1))
        adapter.on_stream_fill(fill)
        asyncio.create_task(relay.forward_fill(side, price, size, 0.0))
        record_fill({"side": side, "price": price, "size": size, "ts": time.time()})

    stream.on_tick = on_tick
    stream.on_fill = on_fill
    stream.on_quote = record_quote
    stream.on_depth = record_depth

    relay.on_signal = record_signal
    relay.on_dqn_inference = record_dqn_inference
    relay.on_zone_update = lambda msg: update_zones(msg.get("zones", []))

    tasks: dict = {
        "relay": asyncio.create_task(relay.connect(), name="stocks-relay-connect"),
    }
    await asyncio.sleep(2)

    log.info("Starting TopstepX stream...")
    await stream.start()

    flatten_scheduler = FlattenScheduler(adapter, config.flatten_et)
    flatten_scheduler.start()
    log.info("FlattenScheduler started (flatten at %s ET)", config.flatten_et)

    async def _heartbeat() -> None:
        """Periodic status update + token refresh + dead-task supervisor.

        relay.connect() and stream._run_hub both have their own forever-loops
        with broad except clauses, so transient WebSocket failures recover on
        their own. The supervisor here catches the rare case where an
        unexpected exception escapes those loops (asyncio.gather semantics,
        token provider crash, etc.) and the task ends — without this, stocks
        would go permanently dark until the user restarts Arnold.
        """
        while True:
            try:
                await asyncio.sleep(30)
                update_status(relay.is_connected, stream._running)
                # Keep client._token fresh so WS reconnects don't 401 after 24h.
                await client._ensure_token()

                if tasks["relay"].done():
                    try:
                        tasks["relay"].result()
                        log.warning("stocks relay task ended cleanly — restarting")
                    except asyncio.CancelledError:
                        return
                    except Exception:
                        log.exception("stocks relay task died — restarting")
                    tasks["relay"] = asyncio.create_task(relay.connect(), name="stocks-relay-connect")

                if stream._running and any(t.done() for t in stream._tasks):
                    log.warning("stocks stream hub task(s) ended while running=True — restarting stream")
                    try:
                        await stream.stop()
                    except Exception:
                        log.exception("stream.stop during supervisor restart failed")
                    try:
                        await stream.start()
                    except Exception:
                        log.exception("stream.start during supervisor restart failed")
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("stocks heartbeat iteration failed")

    tasks["heartbeat"] = asyncio.create_task(_heartbeat(), name="stocks-heartbeat")

    return StocksRuntime(
        client=client,
        adapter=adapter,
        relay=relay,
        stream=stream,
        flatten_scheduler=flatten_scheduler,
        tasks=tasks,
    )
