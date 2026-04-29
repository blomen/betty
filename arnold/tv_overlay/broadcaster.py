"""Compares dashboard `_state` snapshots to a "world" set, emits typed deltas.

Designed to be transport-agnostic — caller provides an `emit(dict) -> awaitable`.
In production this is `arnold.tv_overlay.router.broadcast`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger("arnold.tv_overlay.broadcaster")

_CET = ZoneInfo("Europe/Stockholm")


def _epoch_to_iso(epoch_seconds: float | None) -> str | None:
    """Reconcile_trades parses entry timestamps via fromisoformat — convert
    the poller's epoch float into the same shape so the active synthetic
    trade flows through unchanged."""
    if epoch_seconds is None:
        return None
    try:
        return datetime.fromtimestamp(float(epoch_seconds), tz=timezone.utc).isoformat()
    except Exception:
        return None


def _vp_windows_now() -> dict[str, dict[str, int]]:
    """Window {start_ms, end_ms} for daily/weekly/monthly VP, in epoch
    milliseconds (TV's `Fixed Range Volume Profile` study uses ms).

    Mirrors `MarketService._get_period_bars` / `get_session_bars`:
      daily   = 00:00 CET today          → now
      weekly  = 00:00 CET Monday this wk → now
      monthly = 00:00 CET 1st this month → now

    Keep these aligned with market_service.py — drift here means the chart's
    VP no longer matches what the model sees.
    """
    now_utc = datetime.now(timezone.utc)
    now_ms = int(now_utc.timestamp() * 1000)
    now_cet = now_utc.astimezone(_CET)
    today = now_cet.date()
    monday = today - timedelta(days=today.weekday())
    first = today.replace(day=1)

    def start_ms(d) -> int:
        return int(datetime(d.year, d.month, d.day, tzinfo=_CET).astimezone(timezone.utc).timestamp() * 1000)

    return {
        "daily": {"start_ms": start_ms(today), "end_ms": now_ms},
        "weekly": {"start_ms": start_ms(monday), "end_ms": now_ms},
        "monthly": {"start_ms": start_ms(first), "end_ms": now_ms},
    }


def _zone_key(z: dict) -> str:
    """Stable key — zone clusters dedup by centroid price (zone_builder picks a single
    centroid per family on each rebuild)."""
    return f"zone:{float(z['price']):.2f}"


def _zone_payload(z: dict) -> dict:
    """Stable payload — fields rounded so that loop-tick jitter doesn't trip
    the diff detector and force an unnecessary safeRemove + redraw on the
    userscript. Strength is quantized to 0.05 (still 20 distinct heat steps).
    Top/bottom rounded to 0.25 (NQ tick size) — sub-tick changes are noise.
    """
    strength = float(z.get("hierarchy") or 0.0)
    top = float(z.get("upper") or z["price"])
    bottom = float(z.get("lower") or z["price"])
    return {
        "key": _zone_key(z),
        "price": round(float(z["price"]) / 0.25) * 0.25,
        "top": round(top / 0.25) * 0.25,
        "bottom": round(bottom / 0.25) * 0.25,
        "members": int(z.get("members", 0)),
        "strength": round(strength / 0.05) * 0.05,
        "kind": str(z.get("name") or "zone"),
    }


class OverlayBroadcaster:
    """Holds the last sent state per topic; emits only deltas."""

    def __init__(self, emit: Callable[[dict], Awaitable[None]]) -> None:
        self._emit = emit
        self._zones: dict[str, dict] = {}  # key → last payload
        self._has_position = False
        self._last_position: dict | None = None
        self._vp_anchors: dict[str, int] = {}  # window → last-sent epoch
        # Per-trade state for `reconcile_trades`. Key = "trade:<id>".
        # Open trades re-emit each tick (because end_time = now changes), so
        # the diff dedup is only meaningful for closed trades.
        self._trades: dict[str, dict] = {}
        # Per-level state for `reconcile_levels`. Key = "level:<name>:<price>".
        self._levels: dict[str, dict] = {}

    async def reconcile_levels(self, levels: list[dict]) -> None:
        """Emit individual dim levels as `level_upsert`. Each level gets a
        stable key from name + quantized price so re-emits dedup against
        last-sent. Userscript draws each via the appropriate primitive
        (horizontal_line / horizontal_ray / rectangle for FVG/OB ranges).
        """
        seen: dict[str, dict] = {}
        for lv in levels:
            try:
                name = str(lv.get("name") or "unknown")
                price = float(lv.get("price"))
                top = lv.get("top")
                bottom = lv.get("bottom")
                key = f"level:{name}:{price:.2f}"
                payload = {
                    "key": key,
                    "name": name,
                    "price": round(price / 0.25) * 0.25,
                    "top": round(float(top) / 0.25) * 0.25 if top is not None else None,
                    "bottom": round(float(bottom) / 0.25) * 0.25 if bottom is not None else None,
                }
                seen[key] = payload
            except Exception:
                log.exception("malformed level %r", lv)

        for key, payload in seen.items():
            prior = self._levels.get(key)
            if prior != payload:
                await self._emit({"type": "level_upsert", **payload})
        for key in list(self._levels.keys()):
            if key not in seen:
                await self._emit({"type": "level_remove", "key": key})
        self._levels = seen

    async def reconcile_trades(self, trades: list[dict]) -> None:
        """Emit a `position_upsert` per trade so the userscript draws every
        historical + live trade on the chart. Closed trades carry close_time
        as `end_time`; open trades use "now" so the shape extends right to
        the live edge and updates each loop tick.

        Stop/tp updates on the active trade flow through naturally — the
        diff detector fires when `stop`/`tp` change in `_trades`.
        """

        def _parse_iso(s: str | None) -> int | None:
            if not s:
                return None
            try:
                # Trades may have a 'Z' or '+00:00' suffix or none — naive UTC fallback.
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                return int(
                    datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()
                    if "+" not in s
                    else datetime.fromisoformat(s).timestamp()
                )
            except Exception:
                return None

        now = int(datetime.now(tz=timezone.utc).timestamp())
        seen: dict[str, dict] = {}
        for t in trades:
            tid = t.get("id")
            if tid is None:
                continue
            key = f"trade:{tid}"
            entry_time = _parse_iso(t.get("ts"))
            close_time = _parse_iso(t.get("closed_at"))
            entry_price = t.get("entry_price")
            if entry_time is None or entry_price is None:
                continue
            side_raw = (t.get("side") or "").lower()
            side = "long" if side_raw in ("long", "buy", "0") else "short"
            # Floor end_time to entry_time + 60s for closed trades that
            # have ts == closed_at (instant stop hits — the same DB row
            # records both timestamps as the close moment). Without this
            # floor, the userscript falls back to 1-point form (since
            # endEpoch <= anchor) and TV auto-extends the long_position
            # shape rightward forever.
            effective_end = close_time if close_time else now
            if close_time is not None and effective_end <= entry_time:
                effective_end = entry_time + 60
            payload = {
                "key": key,
                "side": side,
                "entry": float(entry_price),
                "stop": float(t.get("stop_price")) if t.get("stop_price") is not None else None,
                "tp": float(t.get("tp_price")) if t.get("tp_price") is not None else None,
                "size": int(t.get("size") or 1),
                "entry_time": entry_time,
                # Closed → close timestamp is the right edge. Open → "now"
                # so the shape extends to current candle and follows time.
                "end_time": effective_end,
                "closed": close_time is not None,
                "exit_price": float(t.get("exit_price")) if t.get("exit_price") is not None else None,
                "pnl_dollars": t.get("pnl_dollars"),
            }
            seen[key] = payload

        # Upserts: emit on change (closed trades become stable; open trade
        # changes when stop/tp move OR when "now" rolls past dedup quantize).
        for key, payload in seen.items():
            prior = self._trades.get(key)
            # For open trades we still want frequent re-emit so end_time
            # tracks the live edge — quantize end_time to the minute so we
            # don't spam an upsert every loop tick.
            if not payload.get("closed"):
                payload = {**payload, "end_time": (payload["end_time"] // 60) * 60}
            if prior != payload:
                await self._emit({"type": "position_upsert", **payload})
                self._trades[key] = payload

        # Removes: trades that fell out of the 7-day window
        for key in list(self._trades.keys()):
            if key not in seen:
                await self._emit({"type": "position_remove", "key": key})
                del self._trades[key]

    async def replay_to(self, emit_one: Callable[[dict], Awaitable[None]]) -> None:
        """Re-send the current zone + position picture to a single client.
        Called by the WS endpoint when a new overlay attaches mid-session,
        so the userscript doesn't sit empty until the next zone diff."""
        for payload in self._zones.values():
            try:
                await emit_one({"type": "zone_upsert", **payload})
            except Exception:
                log.exception("replay zone failed")
        if self._has_position and self._last_position is not None:
            try:
                await emit_one({"type": "position_upsert", **self._last_position})
            except Exception:
                log.exception("replay position failed")
        for payload in self._trades.values():
            try:
                await emit_one({"type": "position_upsert", **payload})
            except Exception:
                log.exception("replay trade failed")
        for payload in self._levels.values():
            try:
                await emit_one({"type": "level_upsert", **payload})
            except Exception:
                log.exception("replay level failed")
        for window, w in self._vp_anchors.items():
            try:
                await emit_one(
                    {
                        "type": "vp_anchor",
                        "key": f"vp:{window}",
                        "window": window,
                        "start_ms": w["start_ms"],
                        "end_ms": w["end_ms"],
                    }
                )
            except Exception:
                log.exception("replay vp_anchor failed")

    def state_snapshot(self) -> dict:
        """Debug — what's the broadcaster's last-sent picture? Used by the
        /stocks/api/tv-overlay/debug endpoint to verify position_upsert /
        vp_anchor reach-the-wire without browser-console access."""
        return {
            "zones": len(self._zones),
            "has_position": self._has_position,
            "last_position": self._last_position,
            "trades": len(self._trades),
            "trade_keys": list(self._trades.keys()),
            "vp_anchors": dict(self._vp_anchors),
        }

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

    async def reconcile_vp_anchors(self) -> None:
        """Emit Fixed Range VP window deltas — daily / weekly / monthly.

        Userscript creates one `Fixed Range Volume Profile` study per window
        and drives `first_bar_time` / `last_bar_time` via `setInputValues`.

        Quantize end_ms to 60s buckets so we don't spam updates every loop
        tick — VP recomputes are expensive on TV's side.
        """
        windows = _vp_windows_now()
        for window, w in windows.items():
            quantized = {"start_ms": w["start_ms"], "end_ms": (w["end_ms"] // 60_000) * 60_000}
            prior = self._vp_anchors.get(window)
            if prior != quantized:
                await self._emit(
                    {
                        "type": "vp_anchor",
                        "key": f"vp:{window}",
                        "window": window,
                        "start_ms": quantized["start_ms"],
                        "end_ms": quantized["end_ms"],
                    }
                )
                self._vp_anchors[window] = quantized

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
        # tp_price flows through the poller on `first` since the
        # _TrackerShim doesn't carry it; runtime-status now exposes it from
        # the adapter's pending-trade dict.
        tp = first.get("tp_price") or ms.get("tp_price")

        # Skip emit if we still don't have a real entry price — drawing a
        # shape at price 0 puts it off the chart. The poller fills entry
        # from the last tick when tracker.entry_price is buggy/0; only when
        # both fail do we end up here. Wait silently for the next cycle.
        if entry <= 0:
            return

        # entry_time captured by the local poller on flat→open transition.
        # Userscript anchors the long/short position shape at this exact time
        # so the entry handle on the chart matches when the trade actually
        # filled, not when the broadcaster first emitted.
        entry_time = first.get("entry_time")
        payload: dict[str, Any] = {
            "key": "pos:current",
            "side": side,
            "entry": entry,
            "stop": float(stop) if stop is not None else None,
            "tp": float(tp) if tp is not None else None,
            "size": int(first.get("size", 0)),
            "entry_time": int(entry_time) if entry_time else None,
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
                    trades: list[dict] = list(dash_state.get("trades") or [])
                    # Merge the active position into the trades stream as a
                    # synthetic row keyed by id="active". Reconcile_trades
                    # then handles open + closed in one pass: open trades
                    # auto-extend end_time to "now" each loop tick (already
                    # quantized to the minute), and stop/tp moves trail
                    # naturally through the diff detector → mutate-in-place
                    # on the userscript shape.
                    first = positions[0] if positions else None
                    if first and int(first.get("size", 0)) > 0:
                        entry = float(first.get("price") or 0.0)
                        if entry > 0:
                            trades.insert(
                                0,
                                {
                                    "id": "active",
                                    "ts": _epoch_to_iso(first.get("entry_time")),
                                    "side": first.get("side"),
                                    "size": first.get("size", 1),
                                    "entry_price": entry,
                                    "stop_price": model_status.get("stop_price"),
                                    "tp_price": first.get("tp_price") or model_status.get("tp_price"),
                                    "exit_price": None,
                                    "closed_at": None,
                                    "pnl_dollars": None,
                                },
                            )
                    levels: list[dict] = list(dash_state.get("levels") or [])
                    await self.reconcile_zones(zones)
                    # reconcile_position emits a separate `pos:current` shape
                    # which would visually duplicate the synthetic active
                    # trade — drop it. The trades flow is now the single
                    # source of truth for position shapes.
                    # await self.reconcile_position(positions, model_status)
                    await self.reconcile_trades(trades)
                    await self.reconcile_levels(levels)
                    # Fixed Range Volume Profile (paid plan) — daily/weekly/monthly
                    await self.reconcile_vp_anchors()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("overlay broadcaster iteration failed")
                await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            pass
