"""Server-side TopstepX trading service — runs 24/7 inside Docker.

No SSH tunnels, no browser, no local dashboard. Connects directly to
the backend at localhost:8000 and executes trades via TopstepX API.

Usage (inside container):
    python /app/backend/scripts/trading_service.py

Auto-started by the backend's API __init__.py on boot.
"""

import asyncio
import logging
import os
import sys
import time

# Ensure backend is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("trading-service")


async def run():
    from src.stocks import broker_adapter as _broker_adapter_mod
    from src.stocks.broker_adapter import TopstepXBrokerAdapter
    from src.stocks.config import TopstepXConfig
    from src.stocks.server_bootstrap import _persist_broker_trade_direct
    from src.stocks.signal_relay import SignalRelayClient
    from src.stocks.topstepx_client import TopstepXClient
    from src.stocks.topstepx_stream import TopstepXStream

    config = TopstepXConfig.from_env()
    # On server: connect to backend directly (no SSH tunnel)
    config.server_ws_url = os.getenv("TOPSTEPX_SERVER_WS_URL", "ws://127.0.0.1:8000/ws/signals")

    if not config.is_configured:
        log.error("TopstepX not configured — set TOPSTEPX_USERNAME and TOPSTEPX_API_KEY")
        return

    # Authenticate
    log.info("Authenticating with TopstepX...")
    client = TopstepXClient(config)
    ok = await client.connect()
    if not ok:
        log.error("TopstepX authentication failed")
        await client.close()
        return

    log.info("TopstepX authenticated: account=%s", client._account_id)

    # Persist closed trades directly into broker_trades. trading_service runs as a
    # separate subprocess from the FastAPI bootstrap, so the broker_adapter module
    # global is per-process — without this wire-up, _log_broker_trade only writes
    # logs + dashboard and broker_trades stays frozen.
    _broker_adapter_mod.set_persist_callback(_persist_broker_trade_direct)
    log.info("broker_trades persist callback wired")

    # Build adapter + relay
    adapter = TopstepXBrokerAdapter(client, config)

    # Reconcile tracker from TopstepX REST before SignalR stream starts.
    # Mirrors the FastAPI bootstrap path (server_bootstrap.py): without this
    # call, a container restart with an open TopstepX position leaves the
    # subprocess's fresh tracker flat — replayed SignalR fills then drop with
    # "arrived while flat", peak_R never updates, BE-lock never fires.
    # Layer 2 fallback: restore from the disk snapshot embedded in
    # _pending_trade by _set_pending_trade if REST is degraded.
    from src.stocks.tracker_reconciler import reconcile_tracker_from_broker

    reconcile_result = await reconcile_tracker_from_broker(adapter, client, config.contract_id)
    if reconcile_result.degraded and adapter._pending_trade:
        snap = adapter._pending_trade.get("tracker_snapshot")
        if snap:
            log.warning("reconcile: REST failed, falling back to disk snapshot")
            adapter.tracker.restore_from_snapshot(snap)
        else:
            log.error(
                "reconcile: REST failed AND no disk snapshot — broker_adapter is in unknown state; halting trading"
            )
            adapter._halt("reconcile_failed")

    relay = SignalRelayClient(config.server_ws_url, client, adapter=adapter)

    # Build stream
    stream = TopstepXStream(
        token=client._token,
        contract_id=config.contract_id,
        account_id=client._account_id,
        market_hub=config.market_hub_url,
        user_hub=config.user_hub_url,
    )

    # Wire ticks → relay → server → signal → adapter → TopstepX
    # Also tick the adapter's update_mark + BE-lock check so peak_R climbs
    # and a +2R cross moves the stop to entry+small-profit. Without this,
    # BE-lock never fires in the trading_service subprocess (its tracker
    # is independent of the FastAPI process's). Trade orphans on chart
    # showed +2R reaches with no stop-move — exactly this bug.
    def on_tick(price: float, size: int, ts: float, side: str = "B") -> None:
        try:
            adapter.update_mark_and_check_be_lock(price)
        except Exception:
            log.debug("BE-lock tick check failed", exc_info=True)
        asyncio.create_task(relay.forward_tick(price, size, ts, side))

    def on_fill(fill: dict) -> None:
        adapter.on_stream_fill(fill)
        side = "long" if fill.get("side", 0) == 0 else "short"
        price = float(fill.get("price", 0))
        size = int(fill.get("size", 1))
        asyncio.create_task(relay.forward_fill(side, price, size, 0.0))

    stream.on_tick = on_tick
    stream.on_fill = on_fill

    # Start relay (auto-reconnects)
    relay_task = asyncio.create_task(relay.connect(), name="relay-connect")
    await asyncio.sleep(2)

    # Start tick stream
    log.info("Starting TopstepX stream (contract=%s)...", config.contract_id)
    await stream.start()

    # EOD flatten scheduler
    from src.broker.flatten_scheduler import FlattenScheduler

    flatten_scheduler = FlattenScheduler(adapter, config.flatten_et)
    flatten_scheduler.start()
    log.info("Flatten scheduler: %s ET", config.flatten_et)

    log.info("Trading service running — Ctrl+C to stop")

    # Keep-alive loop with health logging + periodic size reconciliation.
    # The size-reconcile mirrors server_bootstrap._reconcile_position_loop:
    # if our tracker.size disagrees with the broker's view of the position,
    # halt + flatten — better wash trade than diverged state.
    while True:
        await asyncio.sleep(60)
        log.info(
            "Heartbeat: relay=%s stream=%s session_pnl=$%.2f",
            relay.is_connected,
            stream._running,
            adapter.tracker.session_pnl,
        )

        if adapter.tracker.is_flat:
            continue
        try:
            positions = await client.search_open_positions()
        except Exception:
            log.warning("reconcile loop: REST query failed; skipping cycle", exc_info=True)
            continue
        matching = [p for p in positions if p.get("contractId") == config.contract_id]
        broker_size = sum(int(p.get("size") or 0) for p in matching)
        local_size = int(adapter.tracker.size or 0)
        if broker_size != local_size:
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


def main():
    while True:
        try:
            asyncio.run(run())
        except KeyboardInterrupt:
            log.info("Shutting down")
            break
        except Exception:
            log.exception("Trading service crashed — restarting in 30s")
            time.sleep(30)


if __name__ == "__main__":
    main()
