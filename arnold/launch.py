"""
Arnold launcher -- local server with SSH tunnel to production API.

Double-click arnold.bat or run `python launch.py` to start.
Kills any previous instance, opens SSH tunnel to production API,
starts local server, and auto-opens browser.
"""

import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

SERVER = "148.251.40.251"
TUNNEL_LOCAL_PORT = 18000
TUNNEL_REMOTE_PORT = 8000
LOCAL_PORT = 8000
LOCAL_URL = f"http://127.0.0.1:{LOCAL_PORT}"


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _kill_port(port: int, label: str):
    """Kill any process listening on the given port (Windows only).

    Uses PowerShell Get-NetTCPConnection as primary method (handles ghost PIDs
    that show up in netstat but can't be found by Get-Process), with taskkill
    as fallback for non-SSH processes.
    """
    killed = False
    try:
        # Primary: PowerShell Get-NetTCPConnection — resolves ghost PIDs correctly
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
            # Verify process actually exists before trying to kill
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
                print(f"[arnold] Killing old {label} (PID {pid}) on port {port}")
                subprocess.run(
                    ["powershell.exe", "-Command", f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"],
                    capture_output=True,
                    timeout=5,
                )
                killed = True
            else:
                print(f"[arnold] Ghost socket on port {port} (PID {pid} no longer exists) — skipping kill")
    except Exception:
        pass

    # Fallback: taskkill via netstat (catches cases PowerShell misses)
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
                        print(f"[arnold] Killing old {label} (PID {pid}) on port {port} [fallback]")
                        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, timeout=5)
                        killed = True
                        break
        except Exception:
            pass

    if killed:
        time.sleep(0.5)
    return killed


def _kill_old_chromium():
    """Kill orphaned Chromium instances from previous Playwright sessions.

    Targets only Chromium spawned by Playwright (identified by --disable-blink-features
    flag we pass in browser.py). Does NOT kill regular Chrome windows.
    """
    try:
        result = subprocess.run(
            [
                "wmic",
                "process",
                "where",
                "name='chromium.exe' or name='chrome.exe'",
                "get",
                "processid,commandline",
                "/format:csv",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        killed = 0
        for line in result.stdout.splitlines():
            if "browser_profile" in line and "disable-blink-features" in line:
                # This is a Playwright-managed Chromium from our profile
                parts = line.strip().split(",")
                if parts:
                    pid = parts[-1].strip()
                    if pid.isdigit():
                        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, timeout=5)
                        killed += 1
        if killed:
            print(f"[arnold] Killed {killed} orphaned Chromium process(es)")
            time.sleep(1)  # Let profile lock release
    except Exception:
        pass


def _start_tunnel() -> bool:
    """Start SSH tunnel to production API. Returns True if ready."""
    if _port_in_use(TUNNEL_LOCAL_PORT):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{TUNNEL_LOCAL_PORT}/health", timeout=3)
            print(f"[arnold] Existing tunnel on localhost:{TUNNEL_LOCAL_PORT} is healthy")
            return True
        except Exception:
            print(f"[arnold] Stale tunnel on localhost:{TUNNEL_LOCAL_PORT} -- killing it")
            _kill_port(TUNNEL_LOCAL_PORT, "stale tunnel")
            time.sleep(1)

    # Backend publishes 127.0.0.1:8000 on the server — tunnel straight through
    print(f"[arnold] Opening SSH tunnel to {SERVER} -> localhost:{TUNNEL_REMOTE_PORT}...")

    proc = subprocess.Popen(
        [
            "ssh",
            "-N",
            "-o",
            "BatchMode=yes",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "TCPKeepAlive=yes",
            "-L",
            f"{TUNNEL_LOCAL_PORT}:localhost:{TUNNEL_REMOTE_PORT}",
            f"root@{SERVER}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    for i in range(30):
        time.sleep(0.5)
        if _port_in_use(TUNNEL_LOCAL_PORT):
            # Port is open — tunnel is established. Verify API responds.
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{TUNNEL_LOCAL_PORT}/health", timeout=2)
                print(f"[arnold] SSH tunnel ready on localhost:{TUNNEL_LOCAL_PORT}")
                return True
            except Exception:
                if i > 10:
                    # Port open for 5+ seconds but health not responding — accept anyway
                    print(f"[arnold] SSH tunnel open on localhost:{TUNNEL_LOCAL_PORT} (health check skipped)")
                    return True

    # Check if SSH process died
    if proc.poll() is not None:
        stderr = proc.stderr.read().decode() if proc.stderr else ""
        print(f"[arnold] ERROR: SSH tunnel exited: {stderr.strip()}")
    else:
        print("[arnold] ERROR: Tunnel failed to start (port not open after 15s)")
    return False


_LOCK_FILE = os.path.join(os.path.dirname(__file__), "data", ".running")


def _start_mirror():
    """POST /mirror/start once the local server is up.

    Eagerly opens the 4 unlimited counter tabs (pinnacle, polymarket, cloudbet,
    kalshi) so the user can log in once and they stay available as arb counters
    + value-bet sources. Idempotent — safe even if mirror already running.
    """
    try:
        req = urllib.request.Request(f"{LOCAL_URL}/mirror/start", method="POST")
        urllib.request.urlopen(req, timeout=120).read()
        print("[arnold] Mirror started — unlimited tabs opening")
    except Exception as e:
        print(f"[arnold] Mirror start failed (will retry on first UI access): {e}")


def _open_browser_when_ready():
    """Poll until local server is healthy, then open browser on first launch only."""
    is_restart = os.path.exists(_LOCK_FILE)
    for _ in range(60):
        time.sleep(1)
        try:
            urllib.request.urlopen(f"{LOCAL_URL}/health", timeout=2)
            # Write lock file for future restarts
            os.makedirs(os.path.dirname(_LOCK_FILE), exist_ok=True)
            with open(_LOCK_FILE, "w") as f:
                f.write(str(os.getpid()))
            # Eager-open the unlimited counter tabs once the server is up
            _start_mirror()
            if is_restart:
                print("[arnold] Restart detected — skipping browser open")
                return
            webbrowser.open(LOCAL_URL)
            return
        except Exception:
            pass
    print("[arnold] Server did not start in 60s -- open manually")


def main(open_browser: bool = True):
    print("[arnold] Arnold Launcher")
    print(f"[arnold] Server: {SERVER}")

    # Kill any previous instance (server, tunnel, and stale Chromium)
    _kill_port(LOCAL_PORT, "server")
    _kill_port(TUNNEL_LOCAL_PORT, "tunnel")
    _kill_old_chromium()

    # Check SSH connectivity
    if not _port_in_use(TUNNEL_LOCAL_PORT):
        print(f"[arnold] Checking server {SERVER}...")
        try:
            result = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", f"root@{SERVER}", "echo ok"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                print(f"[arnold] WARNING: Cannot SSH to {SERVER} — will retry tunnel")
            else:
                print("[arnold] Server reachable")
        except subprocess.TimeoutExpired:
            print("[arnold] WARNING: SSH timed out — will retry tunnel")
        except FileNotFoundError:
            print("[arnold] FAILED: ssh not found. Install OpenSSH.")
            input("Press Enter to exit...")
            return

    # Start SSH tunnel + watchdog that auto-restarts on drop
    if not _start_tunnel():
        print("[arnold] Cannot connect to production API. Check SSH key and server.")
        input("Press Enter to exit...")
        return

    def _tunnel_watchdog():
        """Check tunnel every 20s — test actual HTTP health, not just port.

        SSH tunnel can be 'zombie': port open, process alive, but forwarded
        connections fail with ReadError/RemoteProtocolError. Port-only checks
        miss this. We do an actual HTTP request through the tunnel.

        Tolerance is generous (6 fails × 15s timeout = ~2 min) because some
        legit backend endpoints (arb-workflow, market/candles) can take
        30-60s and saturate the tunnel, making health probes fail transiently
        while the tunnel itself is perfectly healthy. SSH's own keepalive
        (ServerAliveInterval=15, CountMax=3) kills truly dead tunnels at ~45s
        anyway, so we can afford to be slack here.
        """
        consecutive_fails = 0
        while True:
            time.sleep(20)
            if not _port_in_use(TUNNEL_LOCAL_PORT):
                print("[arnold] Tunnel port closed — restarting...")
                consecutive_fails = 0
                _kill_port(TUNNEL_LOCAL_PORT, "dead tunnel")
                time.sleep(1)
                _start_tunnel()
                continue
            # Port is open — but is the tunnel actually forwarding data?
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{TUNNEL_LOCAL_PORT}/health/live",
                    timeout=15,
                )
                consecutive_fails = 0
            except Exception:
                consecutive_fails += 1
                if consecutive_fails >= 6:
                    print(
                        f"[arnold] Tunnel zombie (port open, {consecutive_fails} health fails) "
                        f"— killing and restarting..."
                    )
                    _kill_port(TUNNEL_LOCAL_PORT, "zombie tunnel")
                    time.sleep(1)
                    consecutive_fails = 0
                    _start_tunnel()

    threading.Thread(target=_tunnel_watchdog, daemon=True).start()

    if sys.platform == "win32":
        import asyncio

        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    # Open browser once server is ready
    if open_browser:
        threading.Thread(target=_open_browser_when_ready, daemon=True).start()

    print("[arnold] Starting local server...")
    print("[arnold] Press Ctrl+C to stop\n")

    import uvicorn

    try:
        uvicorn.run(
            "server:app",
            host="127.0.0.1",
            port=LOCAL_PORT,
            timeout_keep_alive=120,
            log_level="warning",
        )
    finally:
        pass


if __name__ == "__main__":
    _first_start = True
    while True:
        try:
            main(open_browser=_first_start)
            break  # Clean exit (no Ctrl+C) — don't restart
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[arnold] Error: {e}")
            break
