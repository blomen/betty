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
    from src.stocks.broker_adapter import TopstepXBrokerAdapter
    from src.stocks.config import TopstepXConfig
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

    # Build adapter + relay
    adapter = TopstepXBrokerAdapter(client, config)
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
    def on_tick(price: float, size: int, ts: float, side: str = "B") -> None:
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

    # Keep-alive loop with health logging
    while True:
        await asyncio.sleep(60)
        log.info(
            "Heartbeat: relay=%s stream=%s session_pnl=$%.2f",
            relay.is_connected,
            stream._running,
            adapter.tracker.session_pnl,
        )


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
