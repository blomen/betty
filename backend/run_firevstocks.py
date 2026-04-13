"""
Firev Stocks launcher -- local process connecting TopstepX to the firev server.

Double-click firevstocks.bat or run `python run_firevstocks.py` to start.

What it does:
  1. Kills previous instance on port 8001
  2. Opens SSH tunnels:
       localhost:15432  ->  postgres:5432      (DB reads)
       localhost:18000  ->  localhost:8000     (server /ws/signals)
  3. Authenticates with TopstepX
  4. Connects SignalRelayClient to server via tunnel
  5. Starts TopstepXStream (ticks + fills)
  6. Wires: TopstepX tick -> relay.forward_tick()
           server signal -> execute on TopstepX
  7. Runs keep-alive loop with health checks

Security:
  - DB + WS traffic encrypted via SSH tunnel (no public ports)
  - Uses existing SSH key for auth
"""

import asyncio
import logging
import os
import socket
import subprocess
import sys
import time

from dotenv import load_dotenv

load_dotenv()

# DB URLs are set AFTER SSH tunnel is confirmed up (see _start_tunnels)
# Setting them here would cause import-time DB connections to fail if tunnel isn't ready
DB_PASSWORD = os.environ.get("DB_PASSWORD", "Skf8vRY3L26lAL4IhCge2V0tZBe7mnZn")

SERVER = "148.251.40.251"
LOCAL_PG_PORT = 15432
LOCAL_WS_PORT = 18000
LOCAL_DASHBOARD_PORT = 8001  # local dashboard web UI

log = logging.getLogger("firevstocks")


# ---------------------------------------------------------------------------
# Port / process helpers
# ---------------------------------------------------------------------------


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _kill_port(port: int, label: str) -> bool:
    """Kill any process listening on the given port (Windows only).

    Uses PowerShell Get-NetTCPConnection as primary method (handles ghost PIDs
    that show up in netstat but can't be found by Get-Process), with taskkill
    as fallback for non-SSH processes.
    """
    killed = False
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-Command",
                f"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue"
                f" | Select-Object -ExpandProperty OwningProcess",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in result.stdout.splitlines():
            pid = line.strip()
            if not pid.isdigit():
                continue
            check = subprocess.run(
                [
                    "powershell.exe",
                    "-Command",
                    f"Get-Process -Id {pid} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if check.stdout.strip() == pid:
                log.info("Killing old %s (PID %s) on port %d", label, pid, port)
                subprocess.run(
                    ["powershell.exe", "-Command", f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"],
                    capture_output=True,
                    timeout=5,
                )
                killed = True
            else:
                log.info("Ghost socket on port %d (PID %s no longer exists) — skipping kill", port, pid)
    except Exception:
        pass

    if not killed:
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in result.stdout.splitlines():
                if f"127.0.0.1:{port}" in line and "LISTENING" in line:
                    pid = line.strip().split()[-1]
                    if pid.isdigit() and pid != "0":
                        log.info("Killing old %s (PID %s) on port %d [fallback]", label, pid, port)
                        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, timeout=5)
                        killed = True
                        break
        except Exception:
            pass

    if killed:
        time.sleep(0.5)
    return killed


_had_previous_instance = False


def _cleanup_old_instance():
    global _had_previous_instance
    _had_previous_instance = _port_in_use(LOCAL_DASHBOARD_PORT)
    _kill_port(LOCAL_DASHBOARD_PORT, "firevstocks-dashboard")
    # Do NOT kill the PG or WS tunnel ports -- mirror may be using them


# ---------------------------------------------------------------------------
# SSH tunnel
# ---------------------------------------------------------------------------


def _start_tunnels() -> bool:
    """Open SSH tunnels for postgres and the server backend WS. Returns True if ready."""
    # Check if tunnels are up AND healthy (not just port in use — stale tunnels may point to old container IPs)
    pg_up = _port_in_use(LOCAL_PG_PORT)
    ws_up = _port_in_use(LOCAL_WS_PORT)

    if pg_up and ws_up:
        # Verify the WS tunnel actually works by hitting /health
        import urllib.request

        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{LOCAL_WS_PORT}/health", timeout=5) as resp:
                if resp.status == 200:
                    log.info("SSH tunnels already up and healthy (pg=%d, ws=%d)", LOCAL_PG_PORT, LOCAL_WS_PORT)
                    os.environ["DATABASE_URL"] = f"postgresql://firev:{DB_PASSWORD}@127.0.0.1:{LOCAL_PG_PORT}/firev"
                    os.environ["MARKET_DATABASE_URL"] = (
                        f"postgresql://firev:{DB_PASSWORD}@127.0.0.1:{LOCAL_PG_PORT}/market"
                    )
                    return True
        except Exception:
            log.warning("SSH tunnels bound but unhealthy — killing and recreating")
            _kill_port(LOCAL_PG_PORT, "stale-pg-tunnel")
            _kill_port(LOCAL_WS_PORT, "stale-ws-tunnel")
            time.sleep(1)

    # Backend publishes on host localhost:8000 (127.0.0.1:8000->8000/tcp in docker-compose).
    # Postgres container IP is needed since postgres has no host port binding.
    log.info("Opening SSH tunnels to %s...", SERVER)
    try:
        result = subprocess.run(
            [
                "ssh",
                f"root@{SERVER}",
                "docker inspect firev-postgres-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        pg_ip = result.stdout.strip().strip("'") or "172.18.0.2"
    except Exception:
        pg_ip = "172.18.0.2"

    log.info(
        "Tunneling: pg=%s:5432 -> localhost:%d, backend=localhost:8000 -> localhost:%d",
        pg_ip,
        LOCAL_PG_PORT,
        LOCAL_WS_PORT,
    )

    subprocess.Popen(
        [
            "ssh",
            "-N",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=3",
            "-L",
            f"{LOCAL_PG_PORT}:{pg_ip}:5432",
            "-L",
            f"{LOCAL_WS_PORT}:localhost:8000",
            f"root@{SERVER}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    # Wait for both ports to become available (up to 30s)
    for _ in range(60):
        time.sleep(0.5)
        if _port_in_use(LOCAL_PG_PORT) and _port_in_use(LOCAL_WS_PORT):
            log.info("SSH tunnels ready (pg=%d, ws=%d)", LOCAL_PG_PORT, LOCAL_WS_PORT)
            os.environ["DATABASE_URL"] = f"postgresql://firev:{DB_PASSWORD}@127.0.0.1:{LOCAL_PG_PORT}/firev"
            os.environ["MARKET_DATABASE_URL"] = f"postgresql://firev:{DB_PASSWORD}@127.0.0.1:{LOCAL_PG_PORT}/market"
            return True

    log.error("SSH tunnels failed to start within 30s")
    return False


# ---------------------------------------------------------------------------
# Main async loop
# ---------------------------------------------------------------------------


async def _run(config, topstepx_client, relay, stream, adapter):
    """Wire everything together and run until interrupted."""

    # ------------------------------------------------------------------
    # Start local dashboard server (port 8001)
    # ------------------------------------------------------------------
    import threading

    import uvicorn

    from src.stocks.dashboard import (
        _state as dash_state,
    )
    from src.stocks.dashboard import (
        create_dashboard_app,
        update_status,
        update_zones,
    )
    from src.stocks.dashboard import (
        record_dqn_inference as dash_dqn_inference,
    )
    from src.stocks.dashboard import (
        record_fill as dash_fill,
    )
    from src.stocks.dashboard import (
        record_quote as dash_quote,
    )
    from src.stocks.dashboard import (
        record_signal as dash_signal,
    )
    from src.stocks.dashboard import (
        record_tick as dash_tick,
    )

    dash_state["stats"]["session_start"] = time.time()
    dash_state["topstepx_client"] = topstepx_client
    dash_state["adapter"] = adapter
    dash_app = create_dashboard_app()

    def _run_dashboard():
        try:
            uvicorn.run(
                dash_app,
                host="127.0.0.1",
                port=LOCAL_DASHBOARD_PORT,
                log_level="warning",
            )
        except Exception:
            log.exception("Dashboard thread crashed")

    threading.Thread(target=_run_dashboard, daemon=True, name="dashboard").start()
    log.info("Dashboard starting at http://127.0.0.1:%d", LOCAL_DASHBOARD_PORT)

    def _open_browser():
        import webbrowser

        if _had_previous_instance:
            # Browser tab already exists — boot_id WS message will trigger reload
            log.info("Previous instance detected — skipping browser open (existing tab will auto-reload)")
            return

        time.sleep(3)
        webbrowser.open(f"http://127.0.0.1:{LOCAL_DASHBOARD_PORT}")

    threading.Thread(target=_open_browser, daemon=True, name="browser-open").start()

    # ------------------------------------------------------------------
    # Start relay in background -- it will keep reconnecting
    # ------------------------------------------------------------------
    relay_task = asyncio.create_task(relay.connect(), name="relay-connect")

    # Give relay a moment to connect before starting stream
    await asyncio.sleep(2)

    # 2.5. Start market data recorder (optional — pipeline works without DB)
    recorder = None
    try:
        from src.db.models import get_market_session
        from src.stocks.recorder import MarketRecorder
        from src.stocks.schema import ensure_recording_tables

        ensure_recording_tables(get_market_session)
        recorder = MarketRecorder(get_market_session)
        recorder.start()
    except Exception as exc:
        log.warning("MarketRecorder failed to start (DB not reachable?): %s", exc)
        log.warning("Continuing without tick recording — signals still work")

    # Wire: TopstepX tick -> relay.forward_tick + dashboard
    def on_tick(price: float, size: int, ts: float, side: str = "B") -> None:
        asyncio.create_task(relay.forward_tick(price, size, ts, side))
        if recorder:
            recorder.record_tick(price, size, ts)
        dash_tick(price, size, ts, side)

    def _on_fill(fill: dict) -> None:
        side = "long" if fill.get("side", 0) == 0 else "short"
        price = float(fill.get("price", 0))
        size = int(fill.get("size", 1))
        # Update adapter tracker with real fill price
        adapter.on_stream_fill(fill)
        # Forward to server and dashboard
        asyncio.create_task(relay.forward_fill(side, price, size, 0.0))
        dash_fill({"side": side, "price": price, "size": size, "ts": time.time()})

    stream.on_tick = on_tick
    stream.on_fill = _on_fill
    stream.on_quote = dash_quote  # forward quotes to dashboard
    if recorder:
        stream.on_depth = recorder.record_depth

    # Wire relay callbacks -> dashboard
    relay.on_signal = dash_signal
    relay.on_dqn_inference = dash_dqn_inference
    relay.on_zone_update = lambda msg: update_zones(msg.get("zones", []))

    # Start stream (async websockets)
    log.info("Starting TopstepX stream...")
    await stream.start()

    # Start EOD flatten scheduler (15:55 ET by default)
    from src.broker.flatten_scheduler import FlattenScheduler

    flatten_scheduler = FlattenScheduler(adapter, config.flatten_et)
    flatten_scheduler.start()
    log.info("FlattenScheduler started (flatten at %s ET)", config.flatten_et)

    # Keep-alive loop
    try:
        while True:
            await asyncio.sleep(30)
            update_status(relay.is_connected, stream._running)
            log.info("Relay connected=%s | stream running=%s", relay.is_connected, stream._running)
    except asyncio.CancelledError:
        pass
    finally:
        log.info("Shutting down...")
        flatten_scheduler.stop()
        await stream.stop()
        if recorder:
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
    from src.stocks.signal_relay import SignalRelayClient
    from src.stocks.topstepx_client import TopstepXClient
    from src.stocks.topstepx_stream import TopstepXStream

    config = TopstepXConfig.from_env()

    if not config.is_configured:
        print("[firevstocks] TopstepX not configured -- set TOPSTEPX_USERNAME and TOPSTEPX_API_KEY")
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
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            log.warning("Cannot SSH to %s -- proceeding anyway", SERVER)
        else:
            log.info("Server reachable")
    except subprocess.TimeoutExpired:
        log.warning("SSH timed out -- proceeding anyway")

    if not _start_tunnels():
        print("[firevstocks] Cannot open SSH tunnels. Check SSH key and server.")
        input("Press Enter to exit...")
        return

    # Authenticate with TopstepX
    log.info("Authenticating with TopstepX...")
    client = TopstepXClient(config)
    ok = await client.connect()
    if not ok:
        print("[firevstocks] TopstepX authentication failed -- check credentials")
        await client.close()
        input("Press Enter to exit...")
        return

    log.info("TopstepX authenticated")

    # Build adapter with risk checks, then relay with adapter
    from src.stocks.broker_adapter import TopstepXBrokerAdapter

    adapter = TopstepXBrokerAdapter(client, config)

    relay = SignalRelayClient(config.server_ws_url, client, adapter=adapter)
    stream = TopstepXStream(
        token=client._token,
        contract_id=config.contract_id,
        account_id=client._account_id,
        market_hub=config.market_hub_url,
        user_hub=config.user_hub_url,
    )

    print(f"[firevstocks] Running -- relay -> {config.server_ws_url}")
    print("[firevstocks] Press Ctrl+C to stop\n")

    await _run(config, client, relay, stream, adapter)


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
            import traceback

            traceback.print_exc()
            try:
                input("Press Enter to exit...")
            except EOFError:
                pass
            break
