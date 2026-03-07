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
import socket
import sys

HOST = "127.0.0.1"
PORT = 8000


def _port_in_use(host: str, port: int) -> bool:
    """Check if a port is already bound by another process."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


if sys.platform == "win32":
    from uvicorn.loops import asyncio as _uvicorn_asyncio

    _original_factory = _uvicorn_asyncio.asyncio_loop_factory

    def _proactor_loop_factory(use_subprocess: bool = False):
        # Always return ProactorEventLoop on Windows — SelectorEventLoop
        # doesn't support subprocess, breaking browser-based extraction.
        return asyncio.ProactorEventLoop

    _uvicorn_asyncio.asyncio_loop_factory = _proactor_loop_factory

if __name__ == "__main__":
    if _port_in_use(HOST, PORT):
        print(f"\n  ERROR: Port {PORT} is already in use.")
        print(f"  Another backend is already running on {HOST}:{PORT}.")
        print(f"  Starting a second instance would wipe the database.\n")
        sys.exit(1)

    import uvicorn

    uvicorn.run("src.api:app", host=HOST, port=PORT)
