"""Process-wide singleton httpx clients.

Why this exists: every `async with httpx.AsyncClient(...)` opens a fresh TCP
connection and tears down a httpcore pool. Through the SSH tunnel that's a
new SSH channel allocation every time. Reusing one client per base URL keeps
HTTP/1.1 keepalive alive on the tunnel and removes per-call handshake cost.

Two clients are exposed:

- `tunnel_client()` — for talking to the production API directly through the
  SSH tunnel (`http://localhost:18000`). Mirror runners and stocks pollers
  used to call `http://127.0.0.1:8000/api/...` (their own FastAPI's proxy
  route) which then re-issued the request through the tunnel — two HTTP
  round-trips for one logical call. Targeting the tunnel directly removes
  that loopback hop.
- `local_client()` — for in-process calls (e.g. the play-loop autostart
  task hitting `/mirror/play/start` on its own server). Same singleton
  pattern, just a different base URL.

Both clients pre-set the nginx auth header so callers don't have to.
"""

from __future__ import annotations

import os

import httpx

_AUTH_HEADER = "X-Nginx-Authenticated"
_AUTH_VALUE = "arnoldsports"

TUNNEL_URL = (
    os.environ.get("BETTY_TUNNEL_URL")
    or os.environ.get("ARNOLDSPORTS_TUNNEL_URL")
    or "http://localhost:18000"
)
LOCAL_URL = "http://127.0.0.1:8000"

_tunnel: httpx.AsyncClient | None = None
_local: httpx.AsyncClient | None = None


def tunnel_client() -> httpx.AsyncClient:
    """Singleton client for the SSH-tunneled production API."""
    global _tunnel
    if _tunnel is None or _tunnel.is_closed:
        api_key = os.environ.get("BETTY_API_KEY", "")
        headers: dict[str, str] = {_AUTH_HEADER: _AUTH_VALUE}
        if api_key:
            headers["X-API-Key"] = api_key
        _tunnel = httpx.AsyncClient(
            base_url=TUNNEL_URL,
            headers=headers,
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _tunnel


def local_client() -> httpx.AsyncClient:
    """Singleton client for self-calls into the local FastAPI process."""
    global _local
    if _local is None or _local.is_closed:
        _local = httpx.AsyncClient(
            base_url=LOCAL_URL,
            timeout=httpx.Timeout(30.0, connect=5.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _local


async def close_all() -> None:
    """Close both clients on shutdown. Safe to call multiple times."""
    global _tunnel, _local
    if _tunnel is not None and not _tunnel.is_closed:
        await _tunnel.aclose()
    if _local is not None and not _local.is_closed:
        await _local.aclose()
    _tunnel = None
    _local = None
