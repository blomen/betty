"""
Mirror launcher -- local backend with SSH tunnel to production DB.

Double-click mirror.bat or run `python run_mirror.py` to start.
Kills any previous mirror instance, opens SSH tunnel, starts backend,
and auto-opens browser to the Play page.

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

SERVER = "148.251.40.251"
LOCAL_PG_PORT = 15432
LOCAL_BACKEND_PORT = 8000
DB_PASSWORD = os.environ.get("DB_PASSWORD", "Skf8vRY3L26lAL4IhCge2V0tZBe7mnZn")
LOCAL_URL = f"http://127.0.0.1:{LOCAL_BACKEND_PORT}"


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
                print(f"[mirror] Killing old {label} (PID {pid}) on port {port}")
                subprocess.run(["taskkill", "/PID", pid, "/F"],
                               capture_output=True, timeout=5)
                time.sleep(0.5)
                return True
    except Exception:
        pass
    return False


def _cleanup_old_instance():
    """Kill any previous mirror backend and tunnel."""
    _kill_port(LOCAL_BACKEND_PORT, "backend")
    _kill_port(LOCAL_PG_PORT, "tunnel")


def _start_tunnel() -> bool:
    """Start SSH tunnel to production postgres. Returns True if ready."""
    if _port_in_use(LOCAL_PG_PORT):
        # Verify tunnel actually works (old tunnel to wrong server = stale)
        try:
            import psycopg2
            conn = psycopg2.connect(
                host="127.0.0.1", port=LOCAL_PG_PORT,
                dbname="firev", user="firev", password=DB_PASSWORD,
                connect_timeout=3,
            )
            conn.close()
            print(f"[mirror] Existing tunnel on localhost:{LOCAL_PG_PORT} is healthy")
            return True
        except Exception:
            print(f"[mirror] Stale tunnel on localhost:{LOCAL_PG_PORT} -- killing it")
            _kill_port(LOCAL_PG_PORT, "stale tunnel")
            time.sleep(1)

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

    print(f"[mirror] Tunneling to postgres at {pg_ip}:5432")
    subprocess.Popen(
        ["ssh", "-N", "-L", f"{LOCAL_PG_PORT}:{pg_ip}:5432", f"root@{SERVER}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    for _ in range(20):
        time.sleep(0.5)
        if _port_in_use(LOCAL_PG_PORT):
            print(f"[mirror] SSH tunnel ready on localhost:{LOCAL_PG_PORT}")
            return True

    print("[mirror] ERROR: Tunnel failed to start")
    return False


def _open_browser_when_ready():
    """Poll until backend is healthy, then open browser."""
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


def main(open_browser: bool = True):
    print("[mirror] Firev Mirror Launcher")
    print(f"[mirror] Server: {SERVER}")

    # Kill any previous mirror instance
    _cleanup_old_instance()

    # Check if tunnel already exists (skip SSH checks if so)
    if _port_in_use(LOCAL_PG_PORT):
        print(f"[mirror] Existing tunnel on localhost:{LOCAL_PG_PORT} — skipping SSH checks")
    else:
        print(f"[mirror] Checking server {SERVER}...")
        try:
            result = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=5", f"root@{SERVER}", "echo ok"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                print(f"[mirror] WARNING: Cannot SSH to {SERVER} — will retry tunnel")
            else:
                print(f"[mirror] Server reachable")
        except subprocess.TimeoutExpired:
            print(f"[mirror] WARNING: SSH timed out — will retry tunnel")
        except FileNotFoundError:
            print("[mirror] FAILED: ssh not found. Install OpenSSH.")
            input("Press Enter to exit...")
            return

    # Start SSH tunnel
    if not _start_tunnel():
        print("[mirror] Cannot connect to production DB. Check SSH key and server.")
        input("Press Enter to exit...")
        return

    # Verify DB connection through tunnel
    try:
        import psycopg2
        conn = psycopg2.connect(
            host="127.0.0.1", port=LOCAL_PG_PORT,
            dbname="firev", user="firev", password=DB_PASSWORD,
            connect_timeout=5,
        )
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM events")
        count = cur.fetchone()[0]
        cur.close()
        conn.close()
        print(f"[mirror] DB connected -- {count} events")
    except Exception as e:
        print(f"[mirror] FAILED: DB connection through tunnel: {e}")
        input("Press Enter to exit...")
        return

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

    # Open browser once backend is ready
    if open_browser:
        threading.Thread(target=_open_browser_when_ready, daemon=True).start()

    print("[mirror] Starting local API server...")
    print("[mirror] Press Ctrl+C to stop\n")

    import uvicorn

    # Configure root logger so fire_window etc. show INFO
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(name)s: %(message)s")
    # Suppress noisy loggers
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("src.services.market_service").setLevel(logging.WARNING)
    logging.getLogger("src.market_data").setLevel(logging.WARNING)
    logging.getLogger("src.services.trading_service").setLevel(logging.WARNING)
    logging.getLogger("src.rl").setLevel(logging.WARNING)

    try:
        uvicorn.run(
            "src.api:app",
            host="127.0.0.1",
            port=LOCAL_BACKEND_PORT,
            timeout_keep_alive=120,
            log_level="info",
        )
    finally:
        print("\n[mirror] Shutting down...")
        print("[mirror] Done.")


if __name__ == "__main__":
    import logging
    _first_start = True
    while True:
        try:
            main(open_browser=_first_start)
            break  # Clean exit (no Ctrl+C) — don't restart
        except KeyboardInterrupt:
            _first_start = False
            print("\n[mirror] Restarting in 2s... (Ctrl+C again to exit)")
            try:
                time.sleep(2)
            except KeyboardInterrupt:
                print("\n[mirror] Exiting.")
                break
        except Exception as e:
            print(f"\n[mirror] Error: {e}")
            input("Press Enter to exit...")
            break
