"""
Mirror launcher — local backend with SSH tunnel to production DB.

Opens an SSH tunnel to the production PostgreSQL, then starts a minimal
local backend (no extraction, no trading, no RL) with a headed Chrome
mirror for Polymarket bet firing.

Usage:
    python run_mirror.py

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

SERVER = "204.168.218.18"
LOCAL_PG_PORT = 15432  # local tunnel port (avoids conflict with any local postgres)
DB_PASSWORD = "firev2026secure"


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def main():
    if _port_in_use(LOCAL_PG_PORT):
        print(f"[mirror] Port {LOCAL_PG_PORT} already in use -- tunnel may already be running")
    else:
        print(f"[mirror] Opening SSH tunnel to {SERVER} postgres via localhost:{LOCAL_PG_PORT}...")
        try:
            result = subprocess.run(
                ["ssh", f"root@{SERVER}",
                 "docker inspect firev-postgres-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'"],
                capture_output=True, text=True, timeout=10,
            )
            pg_ip = result.stdout.strip().strip("'")
            if not pg_ip:
                pg_ip = "172.18.0.2"
                print(f"[mirror] Could not resolve postgres IP, using fallback {pg_ip}")
        except Exception:
            pg_ip = "172.18.0.2"
            print(f"[mirror] SSH lookup failed, using fallback {pg_ip}")

        print(f"[mirror] Tunneling to postgres at {pg_ip}:5432")
        subprocess.Popen(
            ["ssh", "-N", "-L", f"{LOCAL_PG_PORT}:{pg_ip}:5432", f"root@{SERVER}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        for _ in range(10):
            time.sleep(0.5)
            if _port_in_use(LOCAL_PG_PORT):
                print(f"[mirror] SSH tunnel established on localhost:{LOCAL_PG_PORT}")
                break
        else:
            print("[mirror] WARNING: Tunnel may not be ready yet, proceeding anyway...")

    # Mirror-only mode: skip extraction scheduler, trading features, RL collector
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

    print("[mirror] Starting local backend on http://127.0.0.1:8000")
    print("[mirror] Open Play tab -> build batch -> fire window -> click Polymarket")

    import uvicorn
    uvicorn.run(
        "src.api:app",
        host="127.0.0.1",
        port=8000,
        timeout_keep_alive=120,
    )


if __name__ == "__main__":
    main()
