"""SignalRelayClient — WebSocket bridge between arnold server and TopstepX.

Connects to the server's /ws/signals endpoint, forwards ticks from TopstepX,
and executes orders on TopstepX when the server emits a signal.

Outbound messages go through a bounded outbox drained by a single sender
coroutine: this serializes concurrent sends (the websockets library is not
safe for parallel `send()` calls) and buffers messages across brief
disconnects so we don't silently drop ticks and fills during the 5 s
reconnect window.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections import deque
from collections.abc import Callable

import websockets

log = logging.getLogger(__name__)

_RECONNECT_DELAY = 5  # seconds between reconnect attempts
_OUTBOX_MAX = 2000  # ~60 s at 30 Hz; oldest messages drop if we exceed this


class SignalRelayClient:
    """WebSocket client that relays ticks to the server and executes signals from it."""

    def __init__(self, server_ws_url: str, topstepx_client, adapter=None) -> None:
        self._url = server_ws_url
        self._client = topstepx_client  # TopstepXClient instance
        self._adapter = adapter
        self._ws = None
        self._connected = False
        self._sender_task: asyncio.Task | None = None
        # Outbox: serializes sends and buffers during reconnects.
        self._outbox: deque[dict] = deque(maxlen=_OUTBOX_MAX)
        self._outbox_event = asyncio.Event()
        self._dropped: int = 0  # count messages shed because the outbox was full
        self.on_signal: Callable[[dict], None] | None = None  # UI callback
        self.on_dqn_inference: Callable[[dict], None] | None = None  # DQN viz callback
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
        if self._sender_task is None or self._sender_task.done():
            self._sender_task = asyncio.create_task(self._sender_loop(), name="relay-sender")
        while True:
            try:
                log.info("SignalRelay: connecting to %s", self._url)
                # ping_interval=None disables WebSocket-level keepalive entirely.
                # Both endpoints are localhost on the same machine; TCP itself
                # detects a dead connection. With ping_interval=30/timeout=60
                # the relay was logging 1011 (internal error) keepalive timeouts
                # ~once per minute (1558 occurrences in the day's log) because
                # tick bursts + RL inference stall the FastAPI event loop past
                # 60s, the server's reply ping doesn't return in time, and
                # uvicorn closes the socket. The reconnect storm dropped ticks/
                # fills until the bounded outbox replayed. No actual liveness
                # benefit on loopback — silence the noise.
                # X-API-Key authenticates us to /ws/signals — the server-side
                # loopback trust can't identify us because Docker's port
                # forwarding rewrites our source IP to the bridge gateway,
                # so the API key is required even over the SSH tunnel.
                api_key = os.environ.get("ARNOLD_API_KEY", "")
                headers = [("X-API-Key", api_key)] if api_key else []
                async with websockets.connect(
                    self._url,
                    ping_interval=None,
                    additional_headers=headers,
                ) as ws:
                    self._ws = ws
                    self._connected = True
                    log.info("SignalRelay: connected")
                    if self._outbox:
                        log.info(
                            "SignalRelay: %d messages buffered during outage — replaying",
                            len(self._outbox),
                        )
                        self._outbox_event.set()  # wake sender to drain
                    await self._listen()
            except Exception as exc:
                self._connected = False
                self._ws = None
                log.warning("SignalRelay: connection lost (%s) — retrying in %ds", exc, _RECONNECT_DELAY)
                await asyncio.sleep(_RECONNECT_DELAY)

    async def disconnect(self) -> None:
        """Close the WebSocket connection and cancel the sender task."""
        self._connected = False
        if self._sender_task and not self._sender_task.done():
            self._sender_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sender_task
            self._sender_task = None
        if self._ws:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None

    async def forward_tick(self, price: float, size: int, ts: float, side: str = "B") -> None:
        """Enqueue a tick for forwarding. Buffers during disconnects."""
        self._enqueue(self._tick_msg(price, size, ts, side))

    async def forward_fill(self, side: str, price: float, size: int, stop_price: float) -> None:
        """Enqueue a fill for forwarding. Buffers during disconnects."""
        self._enqueue(self._fill_msg(side, price, size, stop_price))

    # ------------------------------------------------------------------
    # Internal: outbox + sender loop
    # ------------------------------------------------------------------

    def _enqueue(self, msg: dict) -> None:
        """Append to outbox, track eviction, wake sender."""
        if len(self._outbox) >= _OUTBOX_MAX:
            # deque.append will evict the oldest — count it so we can log on recovery.
            self._dropped += 1
        self._outbox.append(msg)
        self._outbox_event.set()

    async def _sender_loop(self) -> None:
        """Drain the outbox through the current WS whenever connected.

        Serializes sends (no concurrent `ws.send()` calls) and parks when the
        outbox is empty or the connection is down. New messages and reconnects
        both signal via `_outbox_event`.
        """
        while True:
            try:
                # Park until there's work AND we're connected.
                while not self._outbox or not self._connected or self._ws is None:
                    self._outbox_event.clear()
                    await self._outbox_event.wait()

                # Drain the outbox. Keep each message at the head of the deque
                # until the send succeeds so a mid-drain disconnect doesn't lose it.
                while self._outbox and self._connected and self._ws is not None:
                    msg = self._outbox[0]
                    try:
                        await self._ws.send(json.dumps(msg))
                    except Exception as exc:
                        # The WS died mid-send. Leave msg at the head; connect()
                        # will flip _connected=False and eventually set the event
                        # again when the new WS is up.
                        log.warning("SignalRelay: sender send failed (%s) — will retry on reconnect", exc)
                        self._connected = False
                        break
                    self._outbox.popleft()

                if self._dropped and not self._outbox:
                    log.warning(
                        "SignalRelay: ring buffer dropped %d messages during the outage",
                        self._dropped,
                    )
                    self._dropped = 0
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("SignalRelay: sender loop iteration failed")
                await asyncio.sleep(0.5)

    # ------------------------------------------------------------------
    # Internal: receive loop
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
                log.info(
                    "SignalRelay: received signal: %s conf=%.3f price=%.2f",
                    msg.get("action"),
                    msg.get("confidence", 0),
                    msg.get("price", 0),
                )
                if self.on_signal:
                    try:
                        self.on_signal(msg)
                    except Exception:
                        log.exception("SignalRelay: on_signal callback failed")
                await self._execute_signal(msg)
            elif msg_type == "dqn_inference":
                log.info(
                    "SignalRelay: received dqn_inference: %s conf=%.3f",
                    msg.get("action"),
                    msg.get("confidence", 0),
                )
                if self.on_dqn_inference:
                    try:
                        self.on_dqn_inference(msg)
                    except Exception:
                        log.exception("SignalRelay: on_dqn_inference callback failed")
            elif msg_type == "zone_update":
                if self.on_zone_update:
                    try:
                        self.on_zone_update(msg)
                    except Exception:
                        log.exception("SignalRelay: on_zone_update callback failed")
            elif msg_type == "command":
                result = await self._handle_command(msg)
                cmd_id = msg.get("cmd_id")
                if cmd_id:
                    # Route through the outbox so we don't race the sender loop.
                    self._enqueue({"type": "command_result", "cmd_id": cmd_id, "result": result})
            elif msg_type == "pong":
                pass  # keepalive response
            else:
                log.info("SignalRelay: unknown message type %r", msg_type)

    async def _execute_signal(self, signal: dict) -> None:
        """Parse signal and execute via adapter (if set) or directly on TopstepX.

        When the adapter handles execution, the real fill is forwarded later by
        the TopstepX stream callback in arnold/stocks_runtime.py — we do NOT
        emit a placeholder fill here, since price=0.0 would corrupt any
        downstream consumer that aggregates by price.
        """
        if self._adapter:
            try:
                await self._adapter.on_signal(signal)
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

    async def _handle_command(self, msg: dict) -> dict:
        """Execute a command from the server (flatten, get_orders, cancel_order)."""
        cmd = msg.get("cmd", "")
        log.info("SignalRelay: command received: %s", cmd)
        try:
            if cmd == "flatten":
                if self._adapter:
                    return await self._adapter.flatten("remote_ui")
                await self._client.liquidate_position()
                return {"action": "flatten", "reason": "remote_ui"}
            elif cmd == "get_orders":
                orders = await self._client.get_orders()
                return {"orders": orders if isinstance(orders, list) else []}
            elif cmd == "cancel_order":
                order_id = msg.get("order_id")
                if order_id:
                    result = await self._client.cancel_order(order_id)
                    return result if isinstance(result, dict) else {"ok": True}
                return {"error": "no order_id"}
            else:
                return {"error": f"unknown command: {cmd}"}
        except Exception as exc:
            log.exception("SignalRelay: command %s failed", cmd)
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Message factories (static — easy to unit-test)
    # ------------------------------------------------------------------

    @staticmethod
    def _tick_msg(price: float, size: int, ts: float, side: str = "B") -> dict:
        return {"type": "tick", "price": price, "size": size, "ts": ts, "side": side}

    @staticmethod
    def _fill_msg(side: str, price: float, size: int, stop_price: float) -> dict:
        return {"type": "fill", "side": side, "price": price, "size": size, "stop_price": stop_price}
