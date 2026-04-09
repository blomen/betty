"""
FirevSports launcher -- local server with SSH tunnel to production API.

Double-click firevsports.bat or run `python launch.py` to start.
Kills any previous instance, opens SSH tunnel to production API,
starts local server, and auto-opens browser.
"""

import sys
import os
import subprocess
import time
import socket
import threading
import webbrowser
import urllib.request

SERVER = "148.251.40.251"
TUNNEL_LOCAL_PORT = 18000
TUNNEL_REMOTE_PORT = 8000
LOCAL_PORT = 8000
LOCAL_URL = f"http://127.0.0.1:{LOCAL_PORT}"


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _kill_port(port: int, label: str):
    """Kill any process listening on the given port (Windows only)."""
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if f"127.0.0.1:{port}" in line and "LISTENING" in line:
                pid = line.strip().split()[-1]
                print(f"[firevsports] Killing old {label} (PID {pid}) on port {port}")
                subprocess.run(["taskkill", "/PID", pid, "/F"],
                               capture_output=True, timeout=5)
                time.sleep(0.5)
                return True
    except Exception:
        pass
    return False


def _start_tunnel() -> bool:
    """Start SSH tunnel to production API. Returns True if ready."""
    if _port_in_use(TUNNEL_LOCAL_PORT):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{TUNNEL_LOCAL_PORT}/health", timeout=3)
            print(f"[firevsports] Existing tunnel on localhost:{TUNNEL_LOCAL_PORT} is healthy")
            return True
        except Exception:
            print(f"[firevsports] Stale tunnel on localhost:{TUNNEL_LOCAL_PORT} -- killing it")
            _kill_port(TUNNEL_LOCAL_PORT, "stale tunnel")
            time.sleep(1)

    print(f"[firevsports] Opening SSH tunnel to {SERVER}:{TUNNEL_REMOTE_PORT}...")
    proc = subprocess.Popen(
        ["ssh", "-N", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=30",
         "-L", f"{TUNNEL_LOCAL_PORT}:localhost:{TUNNEL_REMOTE_PORT}", f"root@{SERVER}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    for i in range(30):
        time.sleep(0.5)
        if _port_in_use(TUNNEL_LOCAL_PORT):
            # Port is open — tunnel is established. Verify API responds.
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{TUNNEL_LOCAL_PORT}/health", timeout=2)
                print(f"[firevsports] SSH tunnel ready on localhost:{TUNNEL_LOCAL_PORT}")
                return True
            except Exception:
                if i > 10:
                    # Port open for 5+ seconds but health not responding — accept anyway
                    print(f"[firevsports] SSH tunnel open on localhost:{TUNNEL_LOCAL_PORT} (health check skipped)")
                    return True

    # Check if SSH process died
    if proc.poll() is not None:
        stderr = proc.stderr.read().decode() if proc.stderr else ""
        print(f"[firevsports] ERROR: SSH tunnel exited: {stderr.strip()}")
    else:
        print("[firevsports] ERROR: Tunnel failed to start (port not open after 15s)")
    return False


def _open_browser_when_ready():
    """Poll until local server is healthy, then open browser."""
    for _ in range(60):
        time.sleep(1)
        try:
            urllib.request.urlopen(f"{LOCAL_URL}/health", timeout=2)
            webbrowser.open(LOCAL_URL)
            return
        except Exception:
            pass
    print("[firevsports] Server did not start in 60s -- open manually")


def main(open_browser: bool = True):
    print("[firevsports] FirevSports Launcher")
    print(f"[firevsports] Server: {SERVER}")

    # Kill any previous instance
    _kill_port(LOCAL_PORT, "server")
    _kill_port(TUNNEL_LOCAL_PORT, "tunnel")

    # Check SSH connectivity
    if not _port_in_use(TUNNEL_LOCAL_PORT):
        print(f"[firevsports] Checking server {SERVER}...")
        try:
            result = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", f"root@{SERVER}", "echo ok"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                print(f"[firevsports] WARNING: Cannot SSH to {SERVER} — will retry tunnel")
            else:
                print(f"[firevsports] Server reachable")
        except subprocess.TimeoutExpired:
            print(f"[firevsports] WARNING: SSH timed out — will retry tunnel")
        except FileNotFoundError:
            print("[firevsports] FAILED: ssh not found. Install OpenSSH.")
            input("Press Enter to exit...")
            return

    # Start SSH tunnel
    if not _start_tunnel():
        print("[firevsports] Cannot connect to production API. Check SSH key and server.")
        input("Press Enter to exit...")
        return

    if sys.platform == "win32":
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    # Open browser once server is ready
    if open_browser:
        threading.Thread(target=_open_browser_when_ready, daemon=True).start()

    print("[firevsports] Starting local server...")
    print("[firevsports] Press Ctrl+C to stop\n")

    import uvicorn

    try:
        uvicorn.run(
            "firevsports.server:app",
            host="127.0.0.1",
            port=LOCAL_PORT,
            timeout_keep_alive=120,
            log_level="info",
        )
    finally:
        print("\n[firevsports] Shutting down...")
        print("[firevsports] Done.")


if __name__ == "__main__":
    _first_start = True
    while True:
        try:
            main(open_browser=_first_start)
            break  # Clean exit (no Ctrl+C) — don't restart
        except KeyboardInterrupt:
            print("\n[firevsports] Restarting in 2s... (Ctrl+C again to exit)")
            _first_start = False
            try:
                time.sleep(2)
            except KeyboardInterrupt:
                print("\n[firevsports] Exiting.")
                break
        except Exception as e:
            print(f"\n[firevsports] Error: {e}")
            input("Press Enter to exit...")
            break
