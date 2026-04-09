# FirevSports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create `firevsports/` — a standalone local client that runs a thin proxy + Playwright mirror browser, with its own frontend (Play, Pending, Dutch, Bankroll, Stats tabs), connecting to the Hetzner server API via SSH tunnel.

**Architecture:** Thin local FastAPI server (proxy + mirror control, ~200 lines), SSH tunnel to server API (port 18000 → server:8000), Playwright browser for bet placement, dedicated React frontend with 5 tabs.

**Tech Stack:** Python 3.10+ / FastAPI / httpx / Playwright | React 19 / TypeScript / Vite / Tailwind / @tanstack/react-query

**Spec:** `docs/superpowers/specs/2026-04-09-firevsports-design.md`

---

## File Structure

### New files
- `firevsports/firevsports.bat` — Windows launcher
- `firevsports/server.py` — Thin FastAPI: API proxy + mirror router + static frontend
- `firevsports/proxy.py` — httpx-based reverse proxy to server tunnel
- `firevsports/mirror/__init__.py`
- `firevsports/mirror/browser.py` — Playwright browser lifecycle
- `firevsports/mirror/interceptor.py` — HTTP/WS interception (extracted from backend)
- `firevsports/mirror/recorder.py` — JSONL traffic recording (copied from backend)
- `firevsports/mirror/router.py` — FastAPI endpoints for /mirror/*
- `firevsports/mirror/workflows/` — Copied from backend (base, registry, all providers)
- `firevsports/requirements.txt` — Minimal deps
- `firevsports/tests/test_proxy.py`
- `firevsports/tests/test_mirror_router.py`
- `firevsports/frontend/` — Dedicated React app (package.json, vite.config.ts, src/)

### Files deleted after migration
- `mirror.bat`
- `backend/run_mirror.py`
- Server-side Play tab hiding logic in `frontend/src/components/Terminal/TabBar.tsx`
- `frontend/src/components/Terminal/pages/play/SyncLane.tsx`
- `frontend/src/components/Terminal/pages/play/BettingLane.tsx`
- `frontend/src/hooks/useSyncStream.ts`, `usePriceStream.ts`, `useBettingLane.ts`, `useProviderQueue.ts`

### Files modified
- `frontend/src/components/Terminal/TabBar.tsx` — remove Play tab entirely (no more isLocalMirror)
- `frontend/src/components/Terminal/TerminalWindow.tsx` — remove PlayPage import and KEEP_ALIVE entry

---

## Task 1: Directory Scaffold + Requirements

**Files:**
- Create: `firevsports/requirements.txt`
- Create: `firevsports/__init__.py`
- Create: `firevsports/mirror/__init__.py`
- Create: `firevsports/mirror/workflows/__init__.py`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p firevsports/mirror/workflows/strategies
mkdir -p firevsports/tests
mkdir -p firevsports/frontend/src
```

- [ ] **Step 2: Create requirements.txt**

```
# firevsports/requirements.txt
fastapi>=0.115.0
uvicorn>=0.32.0
httpx>=0.27.0
playwright>=1.49.0
sse-starlette>=2.1.0
```

- [ ] **Step 3: Create __init__.py files**

```python
# firevsports/__init__.py
# FirevSports — local betting client
```

```python
# firevsports/mirror/__init__.py
```

- [ ] **Step 4: Commit**

```bash
git add firevsports/
git commit -m "feat(firevsports): scaffold directory structure"
```

---

## Task 2: API Proxy

**Files:**
- Create: `firevsports/proxy.py`
- Test: `firevsports/tests/test_proxy.py`

- [ ] **Step 1: Write test for proxy**

```python
# firevsports/tests/test_proxy.py
"""Tests for the API proxy."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI

from firevsports.proxy import create_proxy_router

TUNNEL_URL = "http://localhost:18000"


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(create_proxy_router(TUNNEL_URL))
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_proxy_get_forwards_to_tunnel(client):
    """GET /api/play/batch should proxy to tunnel."""
    with patch("firevsports.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{"batch": []}'
        mock_resp.headers = {"content-type": "application/json"}
        mock_client.request.return_value = mock_resp

        resp = client.get("/api/play/batch")
        assert resp.status_code == 200


def test_proxy_post_forwards_body(client):
    """POST /api/bets/record should forward request body."""
    with patch("firevsports.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{"ok": true}'
        mock_resp.headers = {"content-type": "application/json"}
        mock_client.request.return_value = mock_resp

        resp = client.post("/api/bets/record", json={"bet_id": 1})
        assert resp.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd firevsports && python -m pytest tests/test_proxy.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement proxy**

```python
# firevsports/proxy.py
"""Reverse proxy — forwards /api/* and /health to server via SSH tunnel."""
import logging

import httpx
from fastapi import APIRouter, Request, Response
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)

# Headers to strip when proxying
_HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "te", "trailers",
                "upgrade", "proxy-authorization", "proxy-authenticate"}


def create_proxy_router(tunnel_url: str) -> APIRouter:
    """Create a router that proxies /api/* and /health to the tunnel URL."""
    router = APIRouter()

    async def _proxy(request: Request, path: str) -> Response:
        url = f"{tunnel_url}/{path}"
        if request.query_params:
            url += f"?{request.query_params}"

        headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in _HOP_HEADERS and k.lower() != "host"
        }

        body = await request.body()

        # Check if this is an SSE request (Accept: text/event-stream)
        is_sse = "text/event-stream" in request.headers.get("accept", "")

        if is_sse:
            return await _proxy_sse(request.method, url, headers, body)

        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.request(
                method=request.method,
                url=url,
                content=body,
                headers=headers,
            )
            resp_headers = {
                k: v for k, v in resp.headers.items()
                if k.lower() not in _HOP_HEADERS
            }
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers=resp_headers,
            )

    async def _proxy_sse(method: str, url: str, headers: dict, body: bytes) -> StreamingResponse:
        """Proxy SSE streams with chunked transfer."""
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd firevsports && python -m pytest tests/test_proxy.py -v`
Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add firevsports/proxy.py firevsports/tests/test_proxy.py
git commit -m "feat(firevsports): add API proxy to server tunnel"
```

---

## Task 3: Mirror Browser Manager

**Files:**
- Create: `firevsports/mirror/browser.py`

- [ ] **Step 1: Create browser manager**

Extract the Playwright browser lifecycle from `backend/src/mirror/interceptor.py`. The browser manager handles launching, finding tabs, and cleanup.

```python
# firevsports/mirror/browser.py
"""Playwright browser lifecycle — launch, manage tabs, cleanup."""
import asyncio
import logging
from typing import Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Playwright

logger = logging.getLogger(__name__)


class MirrorBrowser:
    """Manages a single headed Chromium browser for bet placement."""

    def __init__(self):
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    @property
    def context(self) -> Optional[BrowserContext]:
        return self._context

    async def start(self) -> BrowserContext:
        """Launch headed Chromium with persistent context."""
        if self._running:
            return self._context

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
            ],
        )
        self._context = await self._browser.new_context(
            viewport=None,  # Use full window size
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        self._running = True
        logger.info("Mirror browser started")
        return self._context

    async def stop(self):
        """Close browser and cleanup."""
        if not self._running:
            return
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            logger.exception("Error closing mirror browser")
        finally:
            self._running = False
            self._context = None
            self._browser = None
            self._playwright = None
            logger.info("Mirror browser stopped")

    async def open_tab(self, url: str):
        """Open a new tab to the given URL."""
        if not self._context:
            raise RuntimeError("Browser not started")
        page = await self._context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        return page

    def get_status(self) -> dict:
        """Return browser status."""
        pages = []
        if self._context:
            for page in self._context.pages:
                pages.append({"url": page.url, "title": page.url.split("/")[2] if "/" in page.url else ""})
        return {
            "running": self._running,
            "tabs": len(pages),
            "pages": pages,
        }
```

- [ ] **Step 2: Commit**

```bash
git add firevsports/mirror/browser.py
git commit -m "feat(firevsports): add MirrorBrowser — Playwright lifecycle"
```

---

## Task 4: Copy Workflows from Backend

**Files:**
- Copy: `backend/src/mirror/workflows/base.py` → `firevsports/mirror/workflows/base.py`
- Copy: `backend/src/mirror/workflows/__init__.py` → `firevsports/mirror/workflows/__init__.py`
- Copy: all workflow files and strategies

- [ ] **Step 1: Copy workflow files**

```bash
# Copy base and registry
cp backend/src/mirror/workflows/base.py firevsports/mirror/workflows/base.py
cp backend/src/mirror/workflows/__init__.py firevsports/mirror/workflows/__init__.py

# Copy all provider workflows
cp backend/src/mirror/workflows/altenar.py firevsports/mirror/workflows/altenar.py
cp backend/src/mirror/workflows/pinnacle.py firevsports/mirror/workflows/pinnacle.py
cp backend/src/mirror/workflows/gecko.py firevsports/mirror/workflows/gecko.py 2>/dev/null || true
cp backend/src/mirror/workflows/kambi.py firevsports/mirror/workflows/kambi.py 2>/dev/null || true
cp backend/src/mirror/workflows/polymarket.py firevsports/mirror/workflows/polymarket.py 2>/dev/null || true
cp backend/src/mirror/workflows/generic.py firevsports/mirror/workflows/generic.py 2>/dev/null || true

# Copy strategies
cp -r backend/src/mirror/workflows/strategies/ firevsports/mirror/workflows/strategies/ 2>/dev/null || true

# Copy recorder
cp backend/src/mirror/recorder.py firevsports/mirror/recorder.py 2>/dev/null || true
```

- [ ] **Step 2: Fix imports in copied files**

The copied workflow files import from `backend/src/...` paths. Update imports in `firevsports/mirror/workflows/__init__.py`:

Change: `from .base import ProviderWorkflow, WorkflowMode, PlacementResult, HistoryEntry`
(This should already be a relative import — verify and fix any absolute `backend.src.mirror` imports.)

Read each copied file's imports and replace any `from ...mirror.` or `from src.mirror.` with relative imports.

- [ ] **Step 3: Verify imports work**

Run: `cd firevsports && python -c "from mirror.workflows.base import ProviderWorkflow; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add firevsports/mirror/
git commit -m "feat(firevsports): copy workflows from backend"
```

---

## Task 5: Mirror Router (Local Endpoints)

**Files:**
- Create: `firevsports/mirror/router.py`
- Test: `firevsports/tests/test_mirror_router.py`

- [ ] **Step 1: Write test for mirror router**

```python
# firevsports/tests/test_mirror_router.py
"""Tests for mirror router endpoints."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI

from firevsports.mirror.router import create_mirror_router


@pytest.fixture
def app():
    mock_browser = MagicMock()
    mock_browser.running = False
    mock_browser.get_status.return_value = {"running": False, "tabs": 0, "pages": []}
    app = FastAPI()
    app.include_router(create_mirror_router(mock_browser))
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_mirror_status(client):
    """GET /mirror/status should return browser status."""
    resp = client.get("/mirror/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["running"] is False
    assert data["tabs"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd firevsports && python -m pytest tests/test_mirror_router.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement mirror router**

```python
# firevsports/mirror/router.py
"""FastAPI router for local mirror browser control."""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .browser import MirrorBrowser

logger = logging.getLogger(__name__)


class NavigateRequest(BaseModel):
    provider_id: str
    event_id: str
    market: str
    outcome: str
    point: float | None = None
    odds: float
    fair_odds: float
    stake: float
    display_home: str
    display_away: str


class PlaceRequest(BaseModel):
    provider_id: str
    bet_id: int


def create_mirror_router(browser: MirrorBrowser) -> APIRouter:
    """Create router for /mirror/* endpoints."""
    router = APIRouter(prefix="/mirror", tags=["mirror"])

    @router.get("/status")
    async def status():
        return browser.get_status()

    @router.post("/start")
    async def start():
        if browser.running:
            return {"status": "already_running"}
        await browser.start()
        return {"status": "started"}

    @router.post("/stop")
    async def stop():
        if not browser.running:
            return {"status": "not_running"}
        await browser.stop()
        return {"status": "stopped"}

    @router.post("/navigate")
    async def navigate(req: NavigateRequest):
        if not browser.running:
            raise HTTPException(400, "Browser not started")
        from .workflows import get_workflow
        workflow = get_workflow(req.provider_id)
        context = browser.context
        page = await workflow.find_tab(context)
        if not page:
            raise HTTPException(404, f"No tab found for {req.provider_id}")
        navigated = await workflow.navigate_to_event(page, req)
        return {"navigated": navigated, "provider_id": req.provider_id}

    @router.post("/place")
    async def place(req: PlaceRequest):
        if not browser.running:
            raise HTTPException(400, "Browser not started")
        from .workflows import get_workflow
        workflow = get_workflow(req.provider_id)
        context = browser.context
        page = await workflow.find_tab(context)
        if not page:
            raise HTTPException(404, f"No tab found for {req.provider_id}")
        result = await workflow.place_bet(page, req, 0)
        return {"status": result.status, "bet_id": result.bet_id,
                "actual_odds": result.actual_odds, "actual_stake": result.actual_stake}

    @router.post("/open-tab")
    async def open_tab(url: str):
        if not browser.running:
            raise HTTPException(400, "Browser not started")
        page = await browser.open_tab(url)
        return {"url": page.url}

    return router
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd firevsports && python -m pytest tests/test_mirror_router.py -v`
Expected: 1 test PASS

- [ ] **Step 5: Commit**

```bash
git add firevsports/mirror/router.py firevsports/tests/test_mirror_router.py
git commit -m "feat(firevsports): add mirror router — browser control endpoints"
```

---

## Task 6: Main Server (server.py)

**Files:**
- Create: `firevsports/server.py`

- [ ] **Step 1: Create the thin local server**

```python
# firevsports/server.py
"""FirevSports local server — thin proxy + mirror browser control + static frontend."""
import logging
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .proxy import create_proxy_router
from .mirror.browser import MirrorBrowser
from .mirror.router import create_mirror_router

logger = logging.getLogger(__name__)

TUNNEL_URL = os.environ.get("FIREVSPORTS_TUNNEL_URL", "http://localhost:18000")
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend", "dist")

app = FastAPI(title="FirevSports", docs_url=None, redoc_url=None)

# Mirror browser (singleton)
browser = MirrorBrowser()

# Mount mirror control endpoints
app.include_router(create_mirror_router(browser))

# Mount API proxy (forwards /api/* and /health to server via tunnel)
app.include_router(create_proxy_router(TUNNEL_URL))

# Serve frontend static files (must be last — catches all unmatched routes)
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")


@app.on_event("startup")
async def startup():
    logger.info(f"FirevSports starting — tunnel: {TUNNEL_URL}")
    # Auto-start mirror browser
    try:
        await browser.start()
    except Exception:
        logger.warning("Mirror browser auto-start failed — start manually via /mirror/start")


@app.on_event("shutdown")
async def shutdown():
    await browser.stop()
    logger.info("FirevSports stopped")
```

- [ ] **Step 2: Verify it loads**

Run: `cd firevsports && python -c "from server import app; print('Server loads OK')"`
Expected: `Server loads OK`

- [ ] **Step 3: Commit**

```bash
git add firevsports/server.py
git commit -m "feat(firevsports): add main server — proxy + mirror + static"
```

---

## Task 7: Launcher (firevsports.bat)

**Files:**
- Create: `firevsports/firevsports.bat`
- Create: `firevsports/launch.py`

- [ ] **Step 1: Create launcher script**

```batch
@echo off
cd /d "%~dp0"
python launch.py
```

- [ ] **Step 2: Create launch.py**

```python
# firevsports/launch.py
"""FirevSports launcher — SSH tunnel + local server + browser."""
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser

SERVER = "148.251.40.251"
TUNNEL_LOCAL_PORT = 18000
TUNNEL_REMOTE_PORT = 8000
LOCAL_PORT = 8000
LOCAL_URL = f"http://127.0.0.1:{LOCAL_PORT}"


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _kill_port(port: int, label: str):
    """Kill any process on the given port (Windows)."""
    try:
        result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.splitlines():
            if f"127.0.0.1:{port}" in line and "LISTENING" in line:
                pid = line.strip().split()[-1]
                print(f"[firevsports] Killing old {label} (PID {pid}) on port {port}")
                subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, timeout=5)
                time.sleep(0.5)
                return
    except Exception:
        pass


def _start_tunnel() -> bool:
    """Start SSH tunnel: local:18000 → server:8000."""
    if _port_in_use(TUNNEL_LOCAL_PORT):
        # Verify tunnel works
        try:
            import urllib.request
            urllib.request.urlopen(f"http://localhost:{TUNNEL_LOCAL_PORT}/health", timeout=3)
            print(f"[firevsports] Existing tunnel on port {TUNNEL_LOCAL_PORT} is healthy")
            return True
        except Exception:
            print(f"[firevsports] Stale tunnel on port {TUNNEL_LOCAL_PORT} — killing")
            _kill_port(TUNNEL_LOCAL_PORT, "stale tunnel")
            time.sleep(1)

    print(f"[firevsports] Opening SSH tunnel to {SERVER}:{TUNNEL_REMOTE_PORT}...")
    subprocess.Popen(
        ["ssh", "-N", "-L", f"{TUNNEL_LOCAL_PORT}:localhost:{TUNNEL_REMOTE_PORT}", f"root@{SERVER}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    for _ in range(20):
        time.sleep(0.5)
        if _port_in_use(TUNNEL_LOCAL_PORT):
            print(f"[firevsports] SSH tunnel ready on port {TUNNEL_LOCAL_PORT}")
            return True

    print("[firevsports] ERROR: Tunnel failed to start")
    return False


def _open_browser_when_ready():
    """Poll until server is healthy, then open browser."""
    import urllib.request
    for _ in range(30):
        time.sleep(1)
        try:
            urllib.request.urlopen(f"{LOCAL_URL}/mirror/status", timeout=2)
            webbrowser.open(LOCAL_URL)
            return
        except Exception:
            pass
    print("[firevsports] Server did not start in 30s — open manually")


def main():
    print("[firevsports] FirevSports Launcher")
    print(f"[firevsports] Server: {SERVER}")

    # Cleanup
    _kill_port(LOCAL_PORT, "backend")
    _kill_port(TUNNEL_LOCAL_PORT, "tunnel")

    # Start tunnel
    if not _start_tunnel():
        print("[firevsports] Cannot connect to server. Check SSH key.")
        input("Press Enter to exit...")
        return

    # Verify tunnel
    try:
        import urllib.request
        resp = urllib.request.urlopen(f"http://localhost:{TUNNEL_LOCAL_PORT}/health", timeout=5)
        print(f"[firevsports] Server API reachable via tunnel")
    except Exception as e:
        print(f"[firevsports] WARNING: Server API not reachable: {e}")

    # Open browser when ready
    threading.Thread(target=_open_browser_when_ready, daemon=True).start()

    # Start local server
    print("[firevsports] Starting local server...")
    import logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(name)s: %(message)s")
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    import uvicorn
    try:
        uvicorn.run(
            "firevsports.server:app",
            host="127.0.0.1",
            port=LOCAL_PORT,
            timeout_keep_alive=120,
            log_level="info",
        )
    finally:
        print("\n[firevsports] Shutting down...")


if __name__ == "__main__":
    while True:
        try:
            main()
            break
        except KeyboardInterrupt:
            print("\n[firevsports] Restarting in 2s... (Ctrl+C again to exit)")
            try:
                time.sleep(2)
            except KeyboardInterrupt:
                print("\n[firevsports] Exiting.")
                break
```

- [ ] **Step 3: Commit**

```bash
git add firevsports/firevsports.bat firevsports/launch.py
git commit -m "feat(firevsports): add launcher — SSH tunnel + server + browser"
```

---

## Task 8: Frontend Scaffold

**Files:**
- Create: `firevsports/frontend/package.json`
- Create: `firevsports/frontend/vite.config.ts`
- Create: `firevsports/frontend/tsconfig.json`
- Create: `firevsports/frontend/index.html`
- Create: `firevsports/frontend/src/main.tsx`
- Create: `firevsports/frontend/src/App.tsx`
- Create: `firevsports/frontend/tailwind.config.js`
- Create: `firevsports/frontend/postcss.config.js`

- [ ] **Step 1: Create package.json**

```json
{
  "name": "firevsports",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "@tanstack/react-query": "^5.90.0"
  },
  "devDependencies": {
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react-swc": "^4.0.0",
    "autoprefixer": "^10.4.0",
    "postcss": "^8.4.0",
    "tailwindcss": "^3.4.0",
    "typescript": "^5.6.0",
    "vite": "^6.0.0"
  }
}
```

- [ ] **Step 2: Create vite.config.ts**

```typescript
// firevsports/frontend/vite.config.ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react-swc'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5174,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/mirror': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/health': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
  },
})
```

- [ ] **Step 3: Create tsconfig.json, index.html, tailwind config, postcss config**

```json
// firevsports/frontend/tsconfig.json
{
  "compilerOptions": {
    "target": "ES2020",
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "paths": { "@/*": ["./src/*"] }
  },
  "include": ["src"]
}
```

```html
<!-- firevsports/frontend/index.html -->
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>FirevSports</title>
  </head>
  <body class="bg-zinc-950 text-zinc-200">
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

```javascript
// firevsports/frontend/tailwind.config.js
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: { extend: {} },
  plugins: [],
}
```

```javascript
// firevsports/frontend/postcss.config.js
export default {
  plugins: { tailwindcss: {}, autoprefixer: {} },
}
```

- [ ] **Step 4: Create main.tsx and App.tsx**

```tsx
// firevsports/frontend/src/main.tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 5000 } },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
```

```tsx
// firevsports/frontend/src/App.tsx
import { useState } from 'react'

type Tab = 'play' | 'pending' | 'dutch' | 'bankroll' | 'stats'

const TABS: { name: Tab; label: string; color: string }[] = [
  { name: 'play',     label: 'Play',     color: '#22c55e' },
  { name: 'pending',  label: 'Pending',  color: '#f59e0b' },
  { name: 'dutch',    label: 'Dutch',    color: '#10b981' },
  { name: 'bankroll', label: 'Bankroll', color: '#ec4899' },
  { name: 'stats',    label: 'Stats',    color: '#3b82f6' },
]

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('play')

  return (
    <div className="flex flex-col h-screen bg-zinc-950">
      {/* Tab bar */}
      <div className="flex items-center gap-1 px-3 py-1 border-b border-zinc-800 bg-zinc-900">
        <span className="text-sm font-bold text-orange-500 mr-4">FirevSports</span>
        {TABS.map(tab => (
          <button
            key={tab.name}
            onClick={() => setActiveTab(tab.name)}
            className={`px-3 py-1.5 text-xs font-mono uppercase tracking-wider rounded ${
              activeTab === tab.name
                ? 'text-zinc-950 font-bold'
                : 'text-zinc-500 hover:text-zinc-300'
            }`}
            style={activeTab === tab.name ? { backgroundColor: tab.color } : undefined}
          >
            <span style={{ color: activeTab === tab.name ? undefined : tab.color }}>● </span>
            {tab.label}
          </button>
        ))}
      </div>

      {/* Page content */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {activeTab === 'play' && <div className="p-4 text-zinc-500">Play page — coming next</div>}
        {activeTab === 'pending' && <div className="p-4 text-zinc-500">Pending page — coming next</div>}
        {activeTab === 'dutch' && <div className="p-4 text-zinc-500">Dutch page — coming next</div>}
        {activeTab === 'bankroll' && <div className="p-4 text-zinc-500">Bankroll page — coming next</div>}
        {activeTab === 'stats' && <div className="p-4 text-zinc-500">Stats page — coming next</div>}
      </div>
    </div>
  )
}
```

```css
/* firevsports/frontend/src/index.css */
@tailwind base;
@tailwind components;
@tailwind utilities;
```

- [ ] **Step 5: Install dependencies and build**

```bash
cd firevsports/frontend && npm install && npm run build
```

- [ ] **Step 6: Commit**

```bash
git add firevsports/frontend/
git commit -m "feat(firevsports): scaffold frontend — 5-tab shell with Vite + React + Tailwind"
```

---

## Task 9: PlayPage (Bet Queue + Mirror Control)

**Files:**
- Create: `firevsports/frontend/src/pages/PlayPage.tsx`
- Create: `firevsports/frontend/src/hooks/useApi.ts`

- [ ] **Step 1: Create useApi hook**

```tsx
// firevsports/frontend/src/hooks/useApi.ts
/**
 * API wrapper — all requests go through the local proxy which tunnels to server.
 */

export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  });
  if (!resp.ok) throw new Error(`API ${resp.status}: ${resp.statusText}`);
  return resp.json();
}

export const api = {
  getPlayBatch: () => apiFetch<any>('/api/play/batch'),
  navigateBet: (body: any) => apiFetch<any>('/mirror/navigate', { method: 'POST', body: JSON.stringify(body) }),
  placeBet: (body: any) => apiFetch<any>('/mirror/place', { method: 'POST', body: JSON.stringify(body) }),
  getMirrorStatus: () => apiFetch<any>('/mirror/status'),
  startMirror: () => apiFetch<any>('/mirror/start', { method: 'POST' }),
};
```

- [ ] **Step 2: Create PlayPage**

```tsx
// firevsports/frontend/src/pages/PlayPage.tsx
import { useState, useCallback } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../hooks/useApi'

interface Bet {
  event_id: string
  provider_id: string
  market: string
  outcome: string
  point?: number
  odds: number
  fair_odds: number
  edge_pct: number
  stake: number
  expected_profit: number
  display_home: string
  display_away: string
  start_time?: string
  tier: string
}

export function PlayPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['play-batch'],
    queryFn: api.getPlayBatch,
    refetchInterval: 10_000,
  })

  const [activeBet, setActiveBet] = useState<string | null>(null)
  const [navigating, setNavigating] = useState(false)

  const batch: Bet[] = data?.batch ?? []

  // Group by provider
  const byProvider: Record<string, Bet[]> = {}
  for (const b of batch) {
    if (!byProvider[b.provider_id]) byProvider[b.provider_id] = []
    byProvider[b.provider_id].push(b)
  }

  const handleNavigate = useCallback(async (b: Bet) => {
    const key = `${b.event_id}:${b.market}:${b.outcome}`
    setActiveBet(key)
    setNavigating(true)
    try {
      await api.navigateBet({
        provider_id: b.provider_id,
        event_id: b.event_id,
        market: b.market,
        outcome: b.outcome,
        point: b.point,
        odds: b.odds,
        fair_odds: b.fair_odds,
        stake: b.stake,
        display_home: b.display_home,
        display_away: b.display_away,
      })
    } catch (err) {
      console.error('Navigate failed:', err)
    } finally {
      setNavigating(false)
    }
  }, [])

  if (isLoading) return <div className="p-4 text-zinc-500">Loading batch...</div>
  if (!batch.length) return <div className="p-4 text-zinc-500">No bets in batch.</div>

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      <div className="px-3 py-2 text-xs text-zinc-400 border-b border-zinc-800">
        {batch.length} bets · +{batch.reduce((s, b) => s + b.expected_profit, 0).toFixed(0)} kr EV
      </div>
      {Object.entries(byProvider).map(([pid, bets]) => (
        <div key={pid}>
          <div className="px-3 py-1.5 bg-zinc-900/50 border-b border-zinc-800 text-xs text-zinc-500 uppercase font-medium">
            {pid} · {bets.length} bets
          </div>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-zinc-600 text-[10px]">
                <th className="text-left pl-3 py-1">Event</th>
                <th className="text-left">Outcome</th>
                <th className="text-right">Odds</th>
                <th className="text-right">Edge</th>
                <th className="text-right">Stake</th>
                <th className="text-right pr-3">EV</th>
              </tr>
            </thead>
            <tbody>
              {bets.filter(b => b.edge_pct > 0).sort((a, b) => b.edge_pct - a.edge_pct).map(b => {
                const key = `${b.event_id}:${b.market}:${b.outcome}`
                const isActive = activeBet === key
                return (
                  <tr
                    key={key}
                    onClick={() => handleNavigate(b)}
                    className={`cursor-pointer border-b border-zinc-800/50 ${isActive ? 'bg-zinc-800/60' : 'hover:bg-zinc-800/30'}`}
                  >
                    <td className="pl-3 py-1.5 text-zinc-300 truncate max-w-[200px]">
                      {isActive && navigating && <span className="text-amber-400 mr-1">⟳</span>}
                      {isActive && !navigating && <span className="text-green-400 mr-1">▸</span>}
                      {b.display_home} v {b.display_away}
                    </td>
                    <td className="text-zinc-400">{b.outcome}</td>
                    <td className="text-right text-zinc-300">{b.odds.toFixed(2)}</td>
                    <td className="text-right text-green-400 font-semibold">+{b.edge_pct.toFixed(1)}%</td>
                    <td className="text-right text-zinc-300">{Math.round(b.stake)} kr</td>
                    <td className="text-right pr-3 text-green-400">+{b.expected_profit.toFixed(0)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  )
}
```

- [ ] **Step 3: Wire PlayPage into App.tsx**

Update `App.tsx` to import and render PlayPage:

```tsx
// In App.tsx, add import:
import { PlayPage } from './pages/PlayPage'

// Replace placeholder:
{activeTab === 'play' && <PlayPage />}
```

- [ ] **Step 4: Build and verify**

```bash
cd firevsports/frontend && npm run build
```

- [ ] **Step 5: Commit**

```bash
git add firevsports/frontend/src/
git commit -m "feat(firevsports): add PlayPage with batch table + mirror navigation"
```

---

## Task 10: PendingPage (Open Bets + Settlements)

**Files:**
- Create: `firevsports/frontend/src/pages/PendingPage.tsx`

- [ ] **Step 1: Add API methods to useApi.ts**

```tsx
// Add to firevsports/frontend/src/hooks/useApi.ts:
  getPendingBets: () => apiFetch<any>('/api/pending/bets'),
  getProviderState: (pid: string) => apiFetch<any>(`/api/mirror/state/${pid}`),
  confirmSettlements: (pid: string) => apiFetch<any>('/api/mirror/settlements/confirm-queue', {
    method: 'POST', body: JSON.stringify({ provider_id: pid }),
  }),
```

- [ ] **Step 2: Create PendingPage**

```tsx
// firevsports/frontend/src/pages/PendingPage.tsx
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../hooks/useApi'

export function PendingPage() {
  const queryClient = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['pending-bets'],
    queryFn: api.getPendingBets,
    refetchInterval: 15_000,
  })

  const providers = data?.providers ?? []

  const handleConfirm = async (pid: string) => {
    try {
      await api.confirmSettlements(pid)
      queryClient.invalidateQueries({ queryKey: ['pending-bets'] })
    } catch (err) {
      console.error('Settlement confirm failed:', err)
    }
  }

  if (isLoading) return <div className="p-4 text-zinc-500">Loading...</div>

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      <div className="px-3 py-2 text-xs text-zinc-400 border-b border-zinc-800">
        Pending bets & settlements
      </div>
      {providers.length === 0 ? (
        <div className="p-4 text-zinc-600 text-sm">No pending bets.</div>
      ) : (
        providers.map((p: any) => (
          <div key={p.provider_id} className="border-b border-zinc-800">
            <div className="flex items-center gap-3 px-3 py-2 bg-zinc-900/50">
              <span className="text-xs font-medium text-zinc-300 uppercase">{p.provider_id}</span>
              <span className="text-xs text-zinc-500">{p.bet_count} bets</span>
              {p.settle_count > 0 && (
                <>
                  <span className="text-xs text-amber-400">{p.settle_count} to settle</span>
                  <button
                    onClick={() => handleConfirm(p.provider_id)}
                    className="ml-auto text-xs bg-green-700 hover:bg-green-600 text-white px-2 py-0.5 rounded"
                  >
                    Confirm
                  </button>
                </>
              )}
            </div>
          </div>
        ))
      )}
    </div>
  )
}
```

- [ ] **Step 3: Wire into App.tsx**

```tsx
import { PendingPage } from './pages/PendingPage'
// ...
{activeTab === 'pending' && <PendingPage />}
```

- [ ] **Step 4: Build and commit**

```bash
cd firevsports/frontend && npm run build
git add firevsports/frontend/src/
git commit -m "feat(firevsports): add PendingPage — open bets + settlement confirm"
```

---

## Task 11: DutchPage, BankrollPage, StatsPage (Proxy Views)

**Files:**
- Create: `firevsports/frontend/src/pages/DutchPage.tsx`
- Create: `firevsports/frontend/src/pages/BankrollPage.tsx`
- Create: `firevsports/frontend/src/pages/StatsPage.tsx`

These are read-only pages that fetch from server API. They can start simple and be enhanced later.

- [ ] **Step 1: Create DutchPage**

```tsx
// firevsports/frontend/src/pages/DutchPage.tsx
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../hooks/useApi'

export function DutchPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['dutch'],
    queryFn: () => apiFetch<any>('/api/dutch/opportunities'),
    refetchInterval: 30_000,
  })

  const opps = data?.opportunities ?? data ?? []

  if (isLoading) return <div className="p-4 text-zinc-500">Loading dutch opportunities...</div>
  if (!Array.isArray(opps) || opps.length === 0) return <div className="p-4 text-zinc-600">No dutch opportunities.</div>

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      <div className="px-3 py-2 text-xs text-zinc-400 border-b border-zinc-800">
        {opps.length} dutch opportunities
      </div>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-zinc-600 text-[10px]">
            <th className="text-left pl-3 py-1">Event</th>
            <th className="text-right">Profit %</th>
            <th className="text-right">Stake</th>
            <th className="text-right pr-3">Providers</th>
          </tr>
        </thead>
        <tbody>
          {opps.slice(0, 50).map((o: any, i: number) => (
            <tr key={i} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
              <td className="pl-3 py-1.5 text-zinc-300 truncate max-w-[250px]">{o.event || o.home_team + ' v ' + o.away_team}</td>
              <td className="text-right text-green-400">+{(o.profit_pct ?? o.edge_pct ?? 0).toFixed(1)}%</td>
              <td className="text-right text-zinc-300">{Math.round(o.total_stake ?? 0)} kr</td>
              <td className="text-right pr-3 text-zinc-500">{o.provider_count ?? 2}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
```

- [ ] **Step 2: Create BankrollPage**

```tsx
// firevsports/frontend/src/pages/BankrollPage.tsx
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../hooks/useApi'

export function BankrollPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['bankroll'],
    queryFn: () => apiFetch<any>('/api/bankroll/summary'),
    refetchInterval: 30_000,
  })

  if (isLoading) return <div className="p-4 text-zinc-500">Loading bankroll...</div>
  if (!data) return <div className="p-4 text-zinc-600">No bankroll data.</div>

  const providers = data.providers ?? []

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      <div className="px-3 py-2 text-xs text-zinc-400 border-b border-zinc-800">
        Bankroll · {providers.length} providers
      </div>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-zinc-600 text-[10px]">
            <th className="text-left pl-3 py-1">Provider</th>
            <th className="text-right">Balance</th>
            <th className="text-right">Deposited</th>
            <th className="text-right">P&L</th>
            <th className="text-right pr-3">ROI</th>
          </tr>
        </thead>
        <tbody>
          {providers.map((p: any) => (
            <tr key={p.provider_id} className="border-b border-zinc-800/50">
              <td className="pl-3 py-1.5 text-zinc-300 uppercase">{p.provider_id}</td>
              <td className="text-right text-zinc-300">{(p.balance ?? 0).toFixed(0)} kr</td>
              <td className="text-right text-zinc-500">{(p.deposited ?? 0).toFixed(0)} kr</td>
              <td className={`text-right ${(p.profit ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {(p.profit ?? 0) >= 0 ? '+' : ''}{(p.profit ?? 0).toFixed(0)} kr
              </td>
              <td className="text-right pr-3 text-zinc-400">{(p.roi_pct ?? 0).toFixed(1)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
```

- [ ] **Step 3: Create StatsPage**

```tsx
// firevsports/frontend/src/pages/StatsPage.tsx
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../hooks/useApi'

export function StatsPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['stats'],
    queryFn: () => apiFetch<any>('/api/bets/stats'),
    refetchInterval: 60_000,
  })

  if (isLoading) return <div className="p-4 text-zinc-500">Loading stats...</div>
  if (!data) return <div className="p-4 text-zinc-600">No stats data.</div>

  return (
    <div className="flex flex-col h-full overflow-y-auto p-4">
      <div className="grid grid-cols-4 gap-4 mb-6">
        <div className="bg-zinc-900 rounded p-3">
          <div className="text-[10px] text-zinc-500 uppercase">Total Bets</div>
          <div className="text-xl font-bold text-zinc-200">{data.total_bets ?? 0}</div>
        </div>
        <div className="bg-zinc-900 rounded p-3">
          <div className="text-[10px] text-zinc-500 uppercase">Win Rate</div>
          <div className="text-xl font-bold text-green-400">{(data.win_rate ?? 0).toFixed(1)}%</div>
        </div>
        <div className="bg-zinc-900 rounded p-3">
          <div className="text-[10px] text-zinc-500 uppercase">ROI</div>
          <div className={`text-xl font-bold ${(data.roi ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {(data.roi ?? 0) >= 0 ? '+' : ''}{(data.roi ?? 0).toFixed(1)}%
          </div>
        </div>
        <div className="bg-zinc-900 rounded p-3">
          <div className="text-[10px] text-zinc-500 uppercase">Profit</div>
          <div className={`text-xl font-bold ${(data.total_profit ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {(data.total_profit ?? 0) >= 0 ? '+' : ''}{(data.total_profit ?? 0).toFixed(0)} kr
          </div>
        </div>
      </div>
      <div className="text-xs text-zinc-600">Detailed stats coming soon</div>
    </div>
  )
}
```

- [ ] **Step 4: Wire all pages into App.tsx**

```tsx
// Add imports:
import { DutchPage } from './pages/DutchPage'
import { BankrollPage } from './pages/BankrollPage'
import { StatsPage } from './pages/StatsPage'

// Replace placeholders:
{activeTab === 'dutch' && <DutchPage />}
{activeTab === 'bankroll' && <BankrollPage />}
{activeTab === 'stats' && <StatsPage />}
```

- [ ] **Step 5: Build and commit**

```bash
cd firevsports/frontend && npm run build
git add firevsports/frontend/src/
git commit -m "feat(firevsports): add Dutch, Bankroll, Stats pages"
```

---

## Task 12: Cleanup — Remove Old Mirror Code

**Files:**
- Delete: `mirror.bat`
- Delete: `backend/run_mirror.py`
- Modify: `frontend/src/components/Terminal/TabBar.tsx` — remove isLocalMirror, remove play from SPORTS_TABS
- Modify: `frontend/src/components/Terminal/TerminalWindow.tsx` — remove PlayPage import + KEEP_ALIVE entry

- [ ] **Step 1: Delete old files**

```bash
rm mirror.bat
rm backend/run_mirror.py
```

- [ ] **Step 2: Remove Play from server frontend TabBar.tsx**

In `frontend/src/components/Terminal/TabBar.tsx`, remove the `isLocalMirror` export and the play entry from SPORTS_TABS:

```typescript
const SPORTS_TABS: Tab[] = [
  { name: 'polymarket', label: 'Poly',      color: '#A855F7' },
  { name: 'value',      label: 'Soft',      color: '#FF9800' },
  { name: 'reverse',    label: 'Pinnacle',  color: '#EF5350' },
  { name: 'dutch',      label: 'Dutch',     color: '#10b981' },
  { name: 'bankroll',   label: 'Bankroll',  color: '#EC4899' },
  { name: 'stats',      label: 'Stats',     color: '#1E88E5' },
];

// Remove isLocalMirror export and the ternary in TABS_BY_CATEGORY
export const TABS_BY_CATEGORY: Record<CategoryName, Tab[]> = {
  sports: SPORTS_TABS,
  stocks: STOCKS_TABS,
};
```

- [ ] **Step 3: Remove PlayPage from TerminalWindow.tsx**

Remove `PlayPage` import and its entry in `KEEP_ALIVE_PAGES`. Remove `isLocalMirror` import.

- [ ] **Step 4: Delete play-specific frontend files**

```bash
rm frontend/src/components/Terminal/pages/play/SyncLane.tsx
rm frontend/src/components/Terminal/pages/play/BettingLane.tsx
rm frontend/src/components/Terminal/pages/play/FireWindow.tsx
rm frontend/src/components/Terminal/pages/PlayPage.tsx
rm frontend/src/hooks/useSyncStream.ts
rm frontend/src/hooks/usePriceStream.ts
rm frontend/src/hooks/useBettingLane.ts
rm frontend/src/hooks/useProviderQueue.ts
```

- [ ] **Step 5: Verify server frontend builds**

```bash
cd frontend && npm run build
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: remove old mirror code — replaced by firevsports"
```

---

## Task 13: End-to-End Test

- [ ] **Step 1: Build firevsports frontend**

```bash
cd firevsports/frontend && npm install && npm run build
```

- [ ] **Step 2: Test firevsports.bat launcher**

Run `firevsports/firevsports.bat`. Verify:
- SSH tunnel opens to server
- Local server starts on port 8000
- Browser opens to `http://127.0.0.1:8000`
- 5 tabs visible: Play, Pending, Dutch, Bankroll, Stats
- Play tab loads batch data from server (via proxy)
- Mirror browser launches (Playwright headed window)

- [ ] **Step 3: Test server frontend**

Deploy to server and verify:
- Play tab is gone
- All other tabs work: Poly, Soft, Pinnacle, Dutch, Bankroll, Stats

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: firevsports — standalone local betting client"
```
