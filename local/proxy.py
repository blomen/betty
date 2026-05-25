"""Reverse proxy — forwards /api/* and /health to server via SSH tunnel.

Uses a process-singleton httpx.AsyncClient so HTTP/1.1 keepalive is preserved
across requests. Previously each /api/* call rebuilt a TCP connection through
the SSH tunnel and tore down a httpcore pool — measurable per-call overhead
that compounded under bursts (page loads firing 5-10 calls in parallel).
"""

import logging
import time

import httpx
from fastapi import APIRouter, Request, Response
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)

_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "transfer-encoding",
    "te",
    "trailers",
    "upgrade",
    "proxy-authorization",
    "proxy-authenticate",
    "content-length",
    "content-encoding",
}

# Rate-limit tunnel-down warnings: log once per 30s, not every request
_last_tunnel_warn: float = 0.0
_TUNNEL_WARN_INTERVAL = 30.0

# Singleton client per tunnel URL. The proxy can't share `tunnel_client()` from
# local.http_client because that one pre-sets the nginx auth header for our
# own internal callers — but the proxy must forward the *request's* headers
# verbatim so backend auth/role checks see the real caller. So a separate
# client without canned headers, but still pooled.
_proxy_clients: dict[str, httpx.AsyncClient] = {}


def _get_proxy_client(base_url: str) -> httpx.AsyncClient:
    client = _proxy_clients.get(base_url)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(300.0, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        _proxy_clients[base_url] = client
    return client


async def close_proxy_clients() -> None:
    """Close pooled proxy clients (called from server.py shutdown)."""
    for client in list(_proxy_clients.values()):
        if not client.is_closed:
            await client.aclose()
    _proxy_clients.clear()


def create_proxy_router(tunnel_url: str) -> APIRouter:
    router = APIRouter()
    base = tunnel_url.rstrip("/")

    async def _proxy(request: Request, path: str) -> Response:
        global _last_tunnel_warn
        url = f"/{path}"
        if request.query_params:
            url += f"?{request.query_params}"
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_HEADERS and k.lower() != "host"}
        # Authenticate with server — mimic nginx auth header
        headers["X-Nginx-Authenticated"] = "arnoldsports"
        body = await request.body()
        is_sse = "text/event-stream" in request.headers.get("accept", "")

        if is_sse:
            return await _proxy_sse(request.method, base + url, headers, body)

        try:
            client = _get_proxy_client(base)
            resp = await client.request(method=request.method, url=url, content=body, headers=headers)
            ct = resp.headers.get("content-type", "application/json")
            return Response(content=resp.content, status_code=resp.status_code, headers={"content-type": ct})
        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as e:
            now = time.monotonic()
            if now - _last_tunnel_warn >= _TUNNEL_WARN_INTERVAL:
                logger.warning(f"[proxy] Tunnel down: {e.__class__.__name__} for {path}")
                _last_tunnel_warn = now
            return Response(
                content=f'{{"error": "tunnel_down", "detail": "{e.__class__.__name__}"}}',
                status_code=502,
                headers={"content-type": "application/json"},
            )

    async def _proxy_sse(method: str, url: str, headers: dict, body: bytes) -> StreamingResponse:
        # SSE keeps the response open indefinitely, so it can't share the pooled
        # client (which would tie up a connection slot). Dedicated client per
        # SSE stream, closed when the stream ends.
        client = httpx.AsyncClient(timeout=None)

        async def stream():
            try:
                async with client.stream(method, url, headers=headers, content=body) as resp:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
            finally:
                await client.aclose()

        return StreamingResponse(stream(), media_type="text/event-stream")

    @router.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def proxy_api(request: Request, path: str):
        return await _proxy(request, f"api/{path}")

    @router.get("/health")
    async def proxy_health(request: Request):
        return await _proxy(request, "health")

    @router.get("/health/{path:path}")
    async def proxy_health_subpath(request: Request, path: str):
        return await _proxy(request, f"health/{path}")

    return router
