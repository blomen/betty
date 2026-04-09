"""
Firev Stocks launcher -- local process connecting TopstepX to the firev server.

Double-click firevstocks.bat or run `python run_firevstocks.py` to start.

What it does:
  1. Kills previous instance on port 8001
  2. Opens SSH tunnels:
       localhost:15432  →  postgres:5432      (DB reads)
       localhost:18000  →  localhost:8000     (server /ws/signals)
  3. Authenticates with TopstepX
  4. Connects SignalRelayClient to server via tunnel
  5. Starts TopstepXStream (ticks + fills)
  6. Wires: TopstepX tick → relay.forward_tick()
           server signal → execute on TopstepX
  7. Runs keep-alive loop with health checks

Security:
  - DB + WS traffic encrypted via SSH tunnel (no public ports)
  - Uses existing SSH key for auth
"""

import sys
import os
import asyncio
import subprocess
import time
import socket
import logging

SERVER = "148.251.40.251"
LOCAL_PG_PORT = 15432
LOCAL_WS_PORT = 18000
LOCAL_BACKEND_PORT = 8001  # unused directly, reserved for future local API

log = logging.getLogger("firevstocks")


# ---------------------------------------------------------------------------
# Port / process helpers
# ---------------------------------------------------------------------------

def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _kill_port(port: int, label: str) -> bool:
    """Kill any process listening on the given port (Windows only)."""
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if f"127.0.0.1:{port}" in line and "LISTENING" in line:
                pid = line.strip().split()[-1]
                log.info("Killing old %s (PID %s) on port %d", label, pid, port)
                subprocess.run(["taskkill", "/PID", pid, "/F"],
                               capture_output=True, timeout=5)
                time.sleep(0.5)
                return True
    except Exception:
        pass
    return False


def _cleanup_old_instance():
    _kill_port(LOCAL_BACKEND_PORT, "firevstocks")
    # Do NOT kill the PG or WS tunnel ports — mirror may be using them


# ---------------------------------------------------------------------------
# SSH tunnel
# ---------------------------------------------------------------------------

def _start_tunnels() -> bool:
    """Open SSH tunnels for postgres and the server backend WS. Returns True if ready."""
    # Check if PG tunnel already healthy
    pg_up = _port_in_use(LOCAL_PG_PORT)
    ws_up = _port_in_use(LOCAL_WS_PORT)

    if pg_up and ws_up:
        log.info("SSH tunnels already up (pg=%d, ws=%d)", LOCAL_PG_PORT, LOCAL_WS_PORT)
        return True

    # Discover postgres container IP on server
    log.info("Opening SSH tunnels to %s...", SERVER)
    try:
        result = subprocess.run(
            ["ssh", f"root@{SERVER}",
             "docker inspect firev-postgres-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'"],
            capture_output=True, text=True, timeout=10,
        )
        pg_ip = result.stdout.strip().strip("'") or "172.18.0.2"
    except Exception:
        pg_ip = "172.18.0.2"

    log.info("Tunneling: pg=%s:5432 → localhost:%d, ws=127.0.0.1:8000 → localhost:%d",
             pg_ip, LOCAL_PG_PORT, LOCAL_WS_PORT)

    subprocess.Popen(
        [
            "ssh", "-N",
            "-L", f"{LOCAL_PG_PORT}:{pg_ip}:5432",
            "-L", f"{LOCAL_WS_PORT}:127.0.0.1:8000",
            f"root@{SERVER}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    # Wait for both ports to become available
    for _ in range(30):
        time.sleep(0.5)
        if _port_in_use(LOCAL_PG_PORT) and _port_in_use(LOCAL_WS_PORT):
            log.info("SSH tunnels ready (pg=%d, ws=%d)", LOCAL_PG_PORT, LOCAL_WS_PORT)
            return True

    log.error("SSH tunnels failed to start within 15s")
    return False


# ---------------------------------------------------------------------------
# Main async loop
# ---------------------------------------------------------------------------

async def _run(config, topstepx_client, relay, stream):
    """Wire everything together and run until interrupted."""

    # Start relay in background — it will keep reconnecting
    relay_task = asyncio.create_task(relay.connect(), name="relay-connect")

    # Give relay a moment to connect before starting stream
    await asyncio.sleep(2)

    # 2.5. Start market data recorder
    from src.stocks.schema import ensure_recording_tables
    from src.stocks.recorder import MarketRecorder
    from src.db.models import get_market_session

    ensure_recording_tables(get_market_session)
    recorder = MarketRecorder(get_market_session)
    recorder.start()

    # Wire: TopstepX tick → relay.forward_tick (thread-safe bridge)
    loop = asyncio.get_event_loop()

    def on_tick(price: float, size: int, ts: float) -> None:
        asyncio.run_coroutine_threadsafe(relay.forward_tick(price, size, ts), loop)
        recorder.record_tick(price, size, ts)

    def _on_fill(fill: dict) -> None:
        side = "Buy" if fill.get("side", 0) == 0 else "Sell"
        price = float(fill.get("price", 0))
        size = int(fill.get("size", 1))
        stop_price = 0.0
        asyncio.run_coroutine_threadsafe(
            relay.forward_fill(side, price, size, stop_price), loop
        )

    stream.on_tick = on_tick
    stream.on_fill = _on_fill
    stream.on_depth = recorder.record_depth

    # Start SignalR stream (runs in its own threads)
    log.info("Starting TopstepX stream...")
    stream.start()

    # Keep-alive loop
    try:
        while True:
            await asyncio.sleep(30)
            log.info("Relay connected=%s | stream market=%s user=%s",
                     relay.is_connected,
                     stream._market_conn is not None,
                     stream._user_conn is not None)
    except asyncio.CancelledError:
        pass
    finally:
        log.info("Shutting down...")
        stream.stop()
        recorder.stop()
        relay_task.cancel()
        try:
            await relay_task
        except asyncio.CancelledError:
            pass
        await topstepx_client.close()
        log.info("Shutdown complete.")


async def main():
    from src.stocks.config import TopstepXConfig
    from src.stocks.topstepx_client import TopstepXClient
    from src.stocks.topstepx_stream import TopstepXStream
    from src.stocks.signal_relay import SignalRelayClient

    config = TopstepXConfig.from_env()

    if not config.is_configured:
        print("[firevstocks] TopstepX not configured — set TOPSTEPX_USERNAME and TOPSTEPX_API_KEY")
        return

    _cleanup_old_instance()

    # Check SSH is available
    try:
        subprocess.run(["ssh", "-V"], capture_output=True, timeout=5)
    except FileNotFoundError:
        print("[firevstocks] FAILED: ssh not found. Install OpenSSH.")
        input("Press Enter to exit...")
        return

    # Check server reachable
    log.info("Checking server %s...", SERVER)
    try:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", f"root@{SERVER}", "echo ok"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            log.warning("Cannot SSH to %s — proceeding anyway", SERVER)
        else:
            log.info("Server reachable")
    except subprocess.TimeoutExpired:
        log.warning("SSH timed out — proceeding anyway")

    if not _start_tunnels():
        print("[firevstocks] Cannot open SSH tunnels. Check SSH key and server.")
        input("Press Enter to exit...")
        return

    # Authenticate with TopstepX
    log.info("Authenticating with TopstepX...")
    client = TopstepXClient(config)
    ok = await client.connect()
    if not ok:
        print("[firevstocks] TopstepX authentication failed — check credentials")
        await client.close()
        input("Press Enter to exit...")
        return

    log.info("TopstepX authenticated")

    # Build relay and stream
    relay = SignalRelayClient(config.server_ws_url, client)
    stream = TopstepXStream(
        token=client._token,
        contract_id=config.contract_id,
        account_id=client._account_id,
        market_hub=config.market_hub_url,
        user_hub=config.user_hub_url,
    )

    print(f"[firevstocks] Running — relay → {config.server_ws_url}")
    print("[firevstocks] Press Ctrl+C to stop\n")

    await _run(config, client, relay, stream)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("websockets").setLevel(logging.WARNING)

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    _first_start = True
    while True:
        try:
            asyncio.run(main())
            break
        except KeyboardInterrupt:
            print("\n[firevstocks] Restarting in 2s... (Ctrl+C again to exit)")
            try:
                time.sleep(2)
            except KeyboardInterrupt:
                print("\n[firevstocks] Exiting.")
                break
        except Exception as exc:
            print(f"\n[firevstocks] Error: {exc}")
            input("Press Enter to exit...")
            break
