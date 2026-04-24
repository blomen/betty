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
        "ts", "session_date", "symbol", "side", "size",
        "entry_price", "stop_price", "exit_price", "tp_price",
        "pnl_dollars", "pnl_r", "fill_latency_ms", "slippage_ticks",
        "was_stop", "trail_count", "stop_ticks",
        "signal_action", "signal_confidence", "signal_zone",
        "signal_trigger", "signal_cont_p", "signal_rev_p",
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


async def bootstrap_stocks() -> StocksRuntime | None:
    """Authenticate TopstepX, start stream + relay, wire dashboard callbacks.

    Returns None when TopstepX is not configured (sports-only mode) or
    when STOCKS_AUTONOMOUS=true (server runs the broker; we'd get duplicate
    fills + order conflicts if both sides connected simultaneously).
    """
    import os

    import httpx

    if os.environ.get("STOCKS_AUTONOMOUS", "").lower() == "true":
        log.info(
            "STOCKS_AUTONOMOUS=true — server handles TopstepX. Skipping local bootstrap "
            "(sports mirror still runs; dashboard reads via API)."
        )
        return None

    from src.broker.flatten_scheduler import FlattenScheduler
    from src.stocks import broker_adapter as _broker_adapter_mod
    from src.stocks.broker_adapter import TopstepXBrokerAdapter
    from src.stocks.config import TopstepXConfig
    from src.stocks.dashboard import (
        _state as dash_state,
    )
    from src.stocks.dashboard import (
        bind_loop,
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
    _api_base = os.environ.get("ARNOLD_TUNNEL_URL") or os.environ.get(
        "ARNOLDSPORTS_TUNNEL_URL", "http://localhost:18000"
    )
    _api_key = os.environ.get("ARNOLD_API_KEY", "")
    _persist_loop = asyncio.get_running_loop()

    async def _post_trade(payload: dict) -> None:
        try:
            async with httpx.AsyncClient(timeout=15.0) as hc:
                r = await hc.post(
                    f"{_api_base}/api/stocks/broker-trades",
                    json=payload,
                    headers={"X-API-Key": _api_key} if _api_key else {},
                )
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
                    tasks["relay"] = asyncio.create_task(
                        relay.connect(), name="stocks-relay-connect"
                    )

                if stream._running and any(t.done() for t in stream._tasks):
                    log.warning(
                        "stocks stream hub task(s) ended while running=True — restarting stream"
                    )
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
