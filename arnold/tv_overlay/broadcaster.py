"""Compares dashboard `_state` snapshots to a "world" set, emits typed deltas.

Designed to be transport-agnostic — caller provides an `emit(dict) -> awaitable`.
In production this is `arnold.tv_overlay.router.broadcast`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger("arnold.tv_overlay.broadcaster")


def _zone_key(z: dict) -> str:
    """Stable key — zone clusters dedup by centroid price (zone_builder picks a single
    centroid per family on each rebuild)."""
    return f"zone:{float(z['price']):.2f}"


def _zone_payload(z: dict) -> dict:
    return {
        "key": _zone_key(z),
        "price": float(z["price"]),
        "top": float(z.get("upper") or z["price"]),
        "bottom": float(z.get("lower") or z["price"]),
        "members": int(z.get("members", 0)),
        "strength": float(z.get("hierarchy") or 0.0),
        "kind": str(z.get("name") or "zone"),
    }


class OverlayBroadcaster:
    """Holds the last sent state per topic; emits only deltas."""

    def __init__(self, emit: Callable[[dict], Awaitable[None]]) -> None:
        self._emit = emit
        self._zones: dict[str, dict] = {}  # key → last payload
        self._has_position = False
        self._last_position: dict | None = None

    async def reconcile_zones(self, zones: list[dict]) -> None:
        seen: dict[str, dict] = {}
        for z in zones:
            try:
                payload = _zone_payload(z)
                seen[payload["key"]] = payload
            except Exception:
                log.exception("malformed zone %r", z)

        # Upserts: emit when payload changed (incl. brand-new keys)
        for key, payload in seen.items():
            prior = self._zones.get(key)
            if prior != payload:
                await self._emit({"type": "zone_upsert", **payload})

        # Removes: keys that were known but are no longer present
        for key in list(self._zones.keys()):
            if key not in seen:
                await self._emit({"type": "zone_remove", "key": key})

        self._zones = seen

    async def reconcile_position(self, positions: list[dict], model_status: dict | None) -> None:
        ms = model_status or {}
        first = positions[0] if positions else None
        flat = first is None or int(first.get("size", 0)) == 0
        if flat:
            if self._has_position:
                await self._emit({"type": "position_remove", "key": "pos:current"})
                self._has_position = False
                self._last_position = None
            return

        side_raw = first.get("side", 0)
        side = "long" if side_raw == 0 or side_raw == "long" else "short"
        entry = float(ms.get("entry_price") or first.get("price") or 0.0)
        stop = ms.get("stop_price")
        tp = ms.get("tp_price")

        payload: dict[str, Any] = {
            "key": "pos:current",
            "side": side,
            "entry": entry,
            "stop": float(stop) if stop is not None else None,
            "tp": float(tp) if tp is not None else None,
            "size": int(first.get("size", 0)),
        }
        if payload != self._last_position:
            await self._emit({"type": "position_upsert", **payload})
            self._last_position = payload
            self._has_position = True

    async def loop(self, *, interval_s: float = 2.0) -> None:
        from src.stocks.dashboard import _state as dash_state

        try:
            while True:
                try:
                    zones: list[dict] = dash_state.get("zones") or []
                    positions: list[dict] = dash_state.get("positions") or []
                    adapter_obj = dash_state.get("adapter")
                    model_status: dict[str, Any] = {}
                    if adapter_obj is not None:
                        tracker = getattr(adapter_obj, "tracker", None)
                        if tracker is not None:
                            model_status = {
                                "entry_price": getattr(tracker, "entry_price", None),
                                "stop_price": getattr(tracker, "stop_price", None),
                                "tp_price": None,
                            }
                    await self.reconcile_zones(zones)
                    await self.reconcile_position(positions, model_status)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("overlay broadcaster iteration failed")
                await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            pass
