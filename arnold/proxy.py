"""Reverse proxy — forwards /api/* and /health to server via SSH tunnel."""

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


def create_proxy_router(tunnel_url: str) -> APIRouter:
    router = APIRouter()

    async def _proxy(request: Request, path: str) -> Response:
        global _last_tunnel_warn
        url = f"{tunnel_url}/{path}"
        if request.query_params:
            url += f"?{request.query_params}"
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_HEADERS and k.lower() != "host"}
        # Authenticate with server — mimic nginx auth header
        headers["X-Nginx-Authenticated"] = "arnoldsports"
        body = await request.body()
        is_sse = "text/event-stream" in request.headers.get("accept", "")

        if is_sse:
            return await _proxy_sse(request.method, url, headers, body)

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
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

    return router
