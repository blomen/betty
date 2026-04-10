"""SignalRelayClient — WebSocket bridge between firev server and TopstepX.

Connects to the server's /ws/signals endpoint, forwards ticks from TopstepX,
and executes orders on TopstepX when the server emits a signal.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable

import websockets

log = logging.getLogger(__name__)

_RECONNECT_DELAY = 5  # seconds between reconnect attempts


class SignalRelayClient:
    """WebSocket client that relays ticks to the server and executes signals from it."""

    def __init__(self, server_ws_url: str, topstepx_client, adapter=None) -> None:
        self._url = server_ws_url
        self._client = topstepx_client  # TopstepXClient instance
        self._adapter = adapter
        self._ws = None
        self._connected = False
        self._listen_task: asyncio.Task | None = None
        self.on_signal: Callable[[dict], None] | None = None  # UI callback
        self.on_zone_update: Callable[[dict], None] | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect with retry loop (5 s between attempts). Runs forever."""
        while True:
            try:
                log.info("SignalRelay: connecting to %s", self._url)
                async with websockets.connect(self._url, ping_interval=20) as ws:
                    self._ws = ws
                    self._connected = True
                    log.info("SignalRelay: connected")
                    await self._listen()
            except Exception as exc:
                self._connected = False
                self._ws = None
                log.warning("SignalRelay: connection lost (%s) — retrying in %ds", exc, _RECONNECT_DELAY)
                await asyncio.sleep(_RECONNECT_DELAY)

    async def disconnect(self) -> None:
        """Close the WebSocket connection and cancel the listen task."""
        self._connected = False
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def forward_tick(self, price: float, size: int, ts: float, side: str = "B") -> None:
        """Send a tick message to the server."""
        if not self._connected or self._ws is None:
            return
        try:
            await self._ws.send(json.dumps(self._tick_msg(price, size, ts, side)))
        except Exception as exc:
            log.warning("SignalRelay: failed to forward tick: %s", exc)
            self._connected = False

    async def forward_fill(self, side: str, price: float, size: int, stop_price: float) -> None:
        """Send a fill message to the server."""
        if not self._connected or self._ws is None:
            return
        try:
            await self._ws.send(json.dumps(self._fill_msg(side, price, size, stop_price)))
        except Exception as exc:
            log.warning("SignalRelay: failed to forward fill: %s", exc)
            self._connected = False

    # ------------------------------------------------------------------
    # Internal: message loop
    # ------------------------------------------------------------------

    async def _listen(self) -> None:
        """Listen for messages from the server."""
        assert self._ws is not None
        async for raw in self._ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("SignalRelay: non-JSON message: %r", raw)
                continue

            msg_type = msg.get("type")
            if msg_type == "signal":
                if self.on_signal:
                    try:
                        self.on_signal(msg)
                    except Exception:
                        log.exception("SignalRelay: on_signal callback failed")
                await self._execute_signal(msg)
            elif msg_type == "zone_update":
                if self.on_zone_update:
                    try:
                        self.on_zone_update(msg)
                    except Exception:
                        log.exception("SignalRelay: on_zone_update callback failed")
            else:
                log.debug("SignalRelay: unknown message type %r", msg_type)

    async def _execute_signal(self, signal: dict) -> None:
        """Parse signal and execute via adapter (if set) or directly on TopstepX."""
        if self._adapter:
            try:
                result = await self._adapter.on_signal(signal)
                if result and not result.get("rejected"):
                    side = result.get("side", "long")
                    price = 0.0  # real price comes via stream fill
                    size = result.get("size", 1)
                    stop_price = result.get("stop_price", 0)
                    await self.forward_fill(side, price, size, stop_price)
            except Exception:
                log.exception("SignalRelay: adapter execution failed for signal %r", signal)
            return

        # Legacy direct path (no adapter)
        action = signal.get("action", "")
        is_long = "long" in action.lower()
        order_action = "Buy" if is_long else "Sell"
        stop_action = "Sell" if is_long else "Buy"
        size = int(signal.get("size", 1) or 1)
        stop_price = float(signal.get("stop_price", 0) or 0)

        log.info("SignalRelay: executing signal: %s size=%d stop=%.2f", action, size, stop_price)

        try:
            result = await self._client.place_market_order(order_action, size)
            fill_price = float(result.get("price", 0) if isinstance(result, dict) else 0)

            if stop_price > 0:
                await self._client.place_stop_order(stop_action, size, stop_price)
        except Exception:
            log.exception("SignalRelay: order execution failed for signal %r", signal)
            return

        await self.forward_fill(order_action, fill_price, size, stop_price)

    # ------------------------------------------------------------------
    # Message factories (static — easy to unit-test)
    # ------------------------------------------------------------------

    @staticmethod
    def _tick_msg(price: float, size: int, ts: float, side: str = "B") -> dict:
        return {"type": "tick", "price": price, "size": size, "ts": ts, "side": side}

    @staticmethod
    def _fill_msg(side: str, price: float, size: int, stop_price: float) -> dict:
        return {"type": "fill", "side": side, "price": price, "size": size, "stop_price": stop_price}
