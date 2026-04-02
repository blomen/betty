"""
Mirror launcher — local backend with SSH tunnel to production DB.

Double-click mirror.bat or run `python run_mirror.py` to start.
Auto-opens browser to the Play page once ready.

Security:
    - DB traffic encrypted via SSH tunnel (no public DB port)
    - Local backend binds to 127.0.0.1 only
    - Uses your existing SSH key for auth
"""

import sys
import os
import asyncio
import subprocess
import time
import socket
import threading
import webbrowser

SERVER = "204.168.218.18"
LOCAL_PG_PORT = 15432
DB_PASSWORD = "firev2026secure"
LOCAL_URL = "http://127.0.0.1:8000"


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _open_browser_when_ready():
    """Poll until backend is healthy, then open browser to Play tab."""
    import urllib.request
    for _ in range(60):
        time.sleep(1)
        try:
            urllib.request.urlopen(f"{LOCAL_URL}/health", timeout=2)
            webbrowser.open(LOCAL_URL)
            return
        except Exception:
            pass
    print("[mirror] Backend did not start in 60s -- open manually")


def _start_tunnel() -> bool:
    """Start SSH tunnel to production postgres. Returns True if ready."""
    if _port_in_use(LOCAL_PG_PORT):
        print(f"[mirror] Tunnel already running on localhost:{LOCAL_PG_PORT}")
        return True

    print(f"[mirror] Opening SSH tunnel to {SERVER}...")
    try:
        result = subprocess.run(
            ["ssh", f"root@{SERVER}",
             "docker inspect firev-postgres-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'"],
            capture_output=True, text=True, timeout=10,
        )
        pg_ip = result.stdout.strip().strip("'") or "172.18.0.2"
    except Exception:
        pg_ip = "172.18.0.2"

    subprocess.Popen(
        ["ssh", "-N", "-L", f"{LOCAL_PG_PORT}:{pg_ip}:5432", f"root@{SERVER}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    for _ in range(10):
        time.sleep(0.5)
        if _port_in_use(LOCAL_PG_PORT):
            print(f"[mirror] SSH tunnel ready on localhost:{LOCAL_PG_PORT}")
            return True

    print("[mirror] WARNING: Tunnel may not be ready yet")
    return False


def main():
    _start_tunnel()

    # Mirror-only mode: skip extraction, trading, RL
    os.environ["FIREV_MIRROR_ONLY"] = "1"

    # Point at production DB through the SSH tunnel
    os.environ["DATABASE_URL"] = (
        f"postgresql+asyncpg://firev:{DB_PASSWORD}@127.0.0.1:{LOCAL_PG_PORT}/firev"
    )
    os.environ["MARKET_DATABASE_URL"] = (
        f"postgresql+asyncpg://firev:{DB_PASSWORD}@127.0.0.1:{LOCAL_PG_PORT}/market"
    )

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    # Open browser once backend is ready (background thread)
    threading.Thread(target=_open_browser_when_ready, daemon=True).start()

    print("[mirror] Starting...")

    import uvicorn
    uvicorn.run(
        "src.api:app",
        host="127.0.0.1",
        port=8000,
        timeout_keep_alive=120,
    )


if __name__ == "__main__":
    main()
