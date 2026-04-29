"""FastAPI router exposing overlay WS + status + userscript file."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from arnold.tv_overlay import status as overlay_status

log = logging.getLogger("arnold.tv_overlay")

_USERSCRIPT_PATH = Path(__file__).resolve().parent / "userscript" / "arnold-overlay.user.js"

# Module-level client list — accessed by the broadcaster too.
clients: list[WebSocket] = []
_clients_lock = asyncio.Lock()


async def broadcast(event: dict) -> None:
    if not clients:
        return
    msg = json.dumps(event, default=str)
    dead: list[WebSocket] = []
    async with _clients_lock:
        for ws in list(clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in clients:
                clients.remove(ws)
                overlay_status.client_detached()


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/tv-overlay/status")
    async def status_endpoint() -> dict:
        return overlay_status.get_status()

    @router.get("/api/tv-overlay/debug")
    async def debug_endpoint(request: Request) -> dict:
        bc = getattr(request.app.state, "overlay_broadcaster", None)
        if bc is None:
            return {"error": "broadcaster not initialized"}
        return {**overlay_status.get_status(), "broadcaster": bc.state_snapshot()}

    @router.get("/api/tv-overlay/userscript")
    async def serve_userscript() -> Response:
        if not _USERSCRIPT_PATH.exists():
            return Response(
                content="// arnold-overlay.user.js missing — install pending",
                media_type="application/javascript",
                status_code=404,
            )
        return Response(
            content=_USERSCRIPT_PATH.read_text(encoding="utf-8"),
            media_type="application/javascript; charset=utf-8",
        )

    @router.post("/api/tv-overlay/ping-zone/{zone_key}")
    async def ping_zone(zone_key: str) -> dict:
        await broadcast({"type": "ping_zone", "zone_key": zone_key})
        return {"ok": True}

    @router.post("/api/tv-overlay/force-cleanup")
    async def force_cleanup() -> dict:
        """Tell every attached overlay client to wipe its drawings + run
        cleanupStaleShapes again. Useful when the TV chart has accumulated
        leftover shapes from earlier sessions that the auto-cleanup on
        attach didn't catch.
        """
        await broadcast({"type": "force_cleanup"})
        return {"ok": True}

    @router.websocket("/ws/tv-overlay")
    async def overlay_ws(ws: WebSocket) -> None:
        await ws.accept()
        async with _clients_lock:
            clients.append(ws)
            overlay_status.client_attached()

        # Replay current state to this client only — broadcaster's diff
        # dedup means new clients otherwise see nothing until the next
        # change (could be hours for stable zones).
        bc = getattr(ws.app.state, "overlay_broadcaster", None)
        if bc is not None:

            async def _send_one(event: dict) -> None:
                try:
                    await ws.send_text(json.dumps(event, default=str))
                except Exception:
                    pass

            try:
                await bc.replay_to(_send_one)
            except Exception:
                log.exception("overlay replay failed")
        try:
            while True:
                # Userscript may post ack / paint stats / errors.
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                t = msg.get("type")
                if t == "ack":
                    overlay_status.record_paint(int(msg.get("count", 1)))
                elif t == "error":
                    overlay_status.set_error(str(msg.get("message", ""))[:200])
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("overlay ws error")
        finally:
            async with _clients_lock:
                if ws in clients:
                    clients.remove(ws)
                    overlay_status.client_detached()

    return router
