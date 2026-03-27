"""
Windows-compatible dev server launcher.

Patches uvicorn's loop factory to use ProactorEventLoop on Windows.
Without this patch, uvicorn defaults to SelectorEventLoop which breaks
patchright's asyncio.create_subprocess_exec (needed for browser-based extraction).

Includes a port guard to prevent duplicate instances — a second backend
would run _startup_purge() and wipe all extracted data before dying.

Usage:
    python run_dev.py
"""

import asyncio
import os
import socket
import subprocess
import sys
import time

HOST = "127.0.0.1"
PORT = 8000
FRONTEND_PORT = 5173


def _port_in_use(host: str, port: int) -> bool:
    """Check if a port is already bound by another process."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def _kill_orphan_servers():
    """Kill orphan backend (port 8000) and frontend (port 5173) processes.

    Uses netstat to find PIDs bound to our dev ports, then kills them.
    This is surgical — only targets processes on specific ports, so RL
    training, VSCode, and other node/python processes are untouched.
    """
    if sys.platform != "win32":
        return

    for port in (PORT, FRONTEND_PORT):
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=5,
            )
            pids_to_kill = set()
            for line in result.stdout.splitlines():
                # Match LISTENING or ESTABLISHED on our ports
                if f":{port}" not in line:
                    continue
                parts = line.split()
                if len(parts) < 5:
                    continue
                local_addr = parts[1]
                state = parts[3]
                pid = parts[4]
                # Only kill processes actually bound to our port (LISTENING)
                if local_addr.endswith(f":{port}") and state == "LISTENING" and pid.isdigit():
                    pid_int = int(pid)
                    if pid_int > 0 and pid_int != os.getpid():
                        pids_to_kill.add(pid_int)

            for pid in pids_to_kill:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(pid)],
                        capture_output=True, timeout=5,
                    )
                    print(f"  Killed orphan process PID {pid} on port {port}")
                except Exception:
                    pass
        except Exception:
            pass


if sys.platform == "win32":
    from uvicorn.loops import asyncio as _uvicorn_asyncio

    _original_factory = _uvicorn_asyncio.asyncio_loop_factory

    def _proactor_loop_factory(use_subprocess: bool = False):
        # Always return ProactorEventLoop on Windows — SelectorEventLoop
        # doesn't support subprocess, breaking browser-based extraction.
        return asyncio.ProactorEventLoop

    _uvicorn_asyncio.asyncio_loop_factory = _proactor_loop_factory

if __name__ == "__main__":
    # Kill any orphan dev servers from previous sessions (safe — only
    # targets processes LISTENING on ports 8000/5173, not training etc.)
    _kill_orphan_servers()

    # Wait for port to be released after killing orphans
    if _port_in_use(HOST, PORT):
        print(f"  Waiting for port {PORT} to be released...")
        for _ in range(10):
            time.sleep(0.5)
            if not _port_in_use(HOST, PORT):
                break
        else:
            print(f"\n  ERROR: Port {PORT} still in use after killing orphans.")
            print(f"  Could not free {HOST}:{PORT}.\n")
            sys.exit(1)

    # On Windows with ProactorEventLoop, Ctrl+C is swallowed.
    # Spawn uvicorn as a child process so the parent can catch Ctrl+C and kill it.
    if sys.platform == "win32" and not os.environ.get("_BBQDEV_CHILD"):
        env = {**os.environ, "_BBQDEV_CHILD": "1"}
        proc = subprocess.Popen([sys.executable, __file__], env=env)
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            proc.wait()
        sys.exit(proc.returncode or 0)

    import uvicorn

    uvicorn.run("src.api:app", host=HOST, port=PORT)
