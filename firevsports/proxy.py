"""Reverse proxy — forwards /api/* and /health to server via SSH tunnel."""
import logging
import httpx
from fastapi import APIRouter, Request, Response
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)

_HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "te", "trailers",
                "upgrade", "proxy-authorization", "proxy-authenticate"}


def create_proxy_router(tunnel_url: str) -> APIRouter:
    router = APIRouter()

    async def _proxy(request: Request, path: str) -> Response:
        url = f"{tunnel_url}/{path}"
        if request.query_params:
            url += f"?{request.query_params}"
        headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in _HOP_HEADERS and k.lower() != "host"}
        body = await request.body()
        is_sse = "text/event-stream" in request.headers.get("accept", "")

        if is_sse:
            return await _proxy_sse(request.method, url, headers, body)

        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.request(method=request.method, url=url, content=body, headers=headers)
            resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in _HOP_HEADERS}
            return Response(content=resp.content, status_code=resp.status_code, headers=resp_headers)

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
