"""Tests for the reverse proxy module."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from firevsports.proxy import create_proxy_router


TUNNEL_URL = "http://localhost:18000"


def make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(create_proxy_router(TUNNEL_URL))
    return app


def _mock_response(status_code: int = 200, content: bytes = b"ok", headers: dict | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.headers = headers or {"content-type": "application/json"}
    return resp


@pytest.mark.asyncio
async def test_get_proxy_forwards_request():
    """GET /api/opportunities is forwarded to tunnel and response returned."""
    mock_resp = _mock_response(200, b'{"data": []}', {"content-type": "application/json"})

    with patch("firevsports.proxy.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        app = make_app()
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/opportunities")

    assert response.status_code == 200
    mock_client.request.assert_called_once()
    call_kwargs = mock_client.request.call_args
    assert call_kwargs.kwargs["method"] == "GET"
    assert call_kwargs.kwargs["url"] == f"{TUNNEL_URL}/api/opportunities"


@pytest.mark.asyncio
async def test_post_proxy_forwards_body():
    """POST /api/bets/place forwards request body to tunnel."""
    payload = b'{"bet_id": 42}'
    mock_resp = _mock_response(201, b'{"placed": true}', {"content-type": "application/json"})

    with patch("firevsports.proxy.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        app = make_app()
        client = TestClient(app, raise_server_exceptions=True)
        response = client.post("/api/bets/place", content=payload,
                               headers={"content-type": "application/json"})

    assert response.status_code == 201
    mock_client.request.assert_called_once()
    call_kwargs = mock_client.request.call_args
    assert call_kwargs.kwargs["method"] == "POST"
    assert call_kwargs.kwargs["url"] == f"{TUNNEL_URL}/api/bets/place"
    assert call_kwargs.kwargs["content"] == payload


@pytest.mark.asyncio
async def test_health_proxy_forwarded():
    """/health is forwarded to tunnel health endpoint."""
    mock_resp = _mock_response(200, b'{"status": "ok"}', {"content-type": "application/json"})

    with patch("firevsports.proxy.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        app = make_app()
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/health")

    assert response.status_code == 200
    call_kwargs = mock_client.request.call_args
    assert call_kwargs.kwargs["url"] == f"{TUNNEL_URL}/health"


@pytest.mark.asyncio
async def test_hop_headers_stripped():
    """Hop-by-hop headers are not forwarded to the tunnel."""
    mock_resp = _mock_response(200, b"ok", {"content-type": "text/plain", "connection": "keep-alive"})

    with patch("firevsports.proxy.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        app = make_app()
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/test", headers={"connection": "keep-alive", "x-custom": "value"})

    forwarded_headers = mock_client.request.call_args.kwargs["headers"]
    assert "connection" not in {k.lower() for k in forwarded_headers}
    assert "host" not in {k.lower() for k in forwarded_headers}
    # Response should also strip hop headers
    assert "connection" not in response.headers


@pytest.mark.asyncio
async def test_query_params_forwarded():
    """Query parameters are appended to the forwarded URL."""
    mock_resp = _mock_response(200, b"[]", {"content-type": "application/json"})

    with patch("firevsports.proxy.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        app = make_app()
        client = TestClient(app, raise_server_exceptions=True)
        client.get("/api/opportunities?sport=soccer&min_edge=5")

    forwarded_url = mock_client.request.call_args.kwargs["url"]
    assert "sport=soccer" in forwarded_url
    assert "min_edge=5" in forwarded_url
