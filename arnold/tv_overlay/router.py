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
        # Also surface a per-family member-count summary so we can diagnose
        # missing swings without modifying the userscript: if "swing" isn't
        # in the family list, the brush lines literally cannot be drawn.
        try:
            from src.stocks.dashboard import _state as _dash_state

            zones = _dash_state.get("zones") or []
            fam_counts: dict[str, int] = {}
            sample_members: list[dict] = []
            for z in zones:
                for m in z.get("members_detail") or []:
                    fam = str(m.get("family") or "unknown")
                    fam_counts[fam] = fam_counts.get(fam, 0) + 1
                    if len(sample_members) < 10:
                        sample_members.append({"family": fam, "name": m.get("name"), "price": m.get("price")})
            zones_summary = {
                "zone_count": len(zones),
                "family_member_counts": fam_counts,
                "sample_members": sample_members,
            }
        except Exception as e:
            zones_summary = {"error": f"zones probe failed: {e}"}
        return {
            **overlay_status.get_status(),
            "broadcaster": bc.state_snapshot(),
            "zones_probe": zones_summary,
        }

    async def _serve_userscript_impl() -> Response:
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

    @router.get("/api/tv-overlay/userscript")
    async def serve_userscript() -> Response:
        # Legacy path — kept so existing Tampermonkey installs with
        # @updateURL pointing here still receive updates.
        return await _serve_userscript_impl()

    @router.get("/api/tv-overlay/arnold-overlay.user.js")
    async def serve_userscript_userjs() -> Response:
        # .user.js suffix — Tampermonkey only triggers its install/update
        # prompt for URLs ending in this extension. Visiting the bare
        # /userscript path just renders the script as text in the browser
        # (no install dialog), forcing manual copy-paste. This route
        # serves identical content at a URL Tampermonkey recognizes.
        return await _serve_userscript_impl()

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

        # Auto-cleanup on every connect. Each `arnold.bat` start (or any
        # browser reload of the TV tab) reopens this WS, so sending a
        # force_cleanup before the state replay guarantees the chart
        # starts fresh — wipes leftover long_position handles / rectangles
        # / member lines from earlier sessions before repainting current
        # zones + trades.
        #
        # Wait for the userscript's ack before replaying. The page-side
        # force_cleanup handler awaits multiple times (removeAllShapes +
        # cleanupStaleShapes), and if replay messages arrive while it's
        # mid-flight, the position/zone shapes drawn from replay get wiped
        # by the trailing removeAllShapes. After that, broadcaster diff-
        # dedup never re-emits the same closed trades, so they stay missing
        # on the chart for the rest of the session.
        try:
            await ws.send_text(json.dumps({"type": "force_cleanup"}))
        except Exception:
            log.exception("overlay force_cleanup-on-connect failed")

        async def _wait_for_cleanup_ack(timeout_s: float = 3.0) -> None:
            """Drain incoming messages until we see an ack or hit the timeout.
            Errors / non-ack frames are logged through the normal path so we
            don't lose paint stats. Falls through silently on timeout — the
            replay still happens, just without ordering guarantees."""
            import asyncio as _asyncio

            deadline = _asyncio.get_event_loop().time() + timeout_s
            while True:
                remaining = deadline - _asyncio.get_event_loop().time()
                if remaining <= 0:
                    return
                try:
                    raw = await _asyncio.wait_for(ws.receive_text(), timeout=remaining)
                except _asyncio.TimeoutError:
                    return
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                t = msg.get("type")
                if t == "ack":
                    overlay_status.record_paint(int(msg.get("count", 1)))
                    return
                if t == "error":
                    overlay_status.set_error(str(msg.get("message", ""))[:200])

        try:
            await _wait_for_cleanup_ack()
        except Exception:
            log.exception("overlay cleanup-ack wait failed")

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
