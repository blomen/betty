"""TopstepX SignalR streaming client (raw websockets).

Connects to two hubs via SignalR JSON protocol over websockets:
  - Market hub: live trade ticks (GatewayTrade), quotes (GatewayQuote), depth (GatewayDepth)
  - User hub:   fills, positions, orders (GatewayUserTrade/Position/Order)

Uses raw websockets because signalrcore drops connections on Windows/TopstepX.

Usage::

    stream = TopstepXStream(token=token, contract_id="CON.F.US.ENQ.M26", account_id=12345)
    stream.on_tick = lambda price, size, ts: print(price, size, ts)
    stream.on_fill = lambda fill: print(fill)
    await stream.start()
    ...
    await stream.stop()
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Callable

import websockets

log = logging.getLogger(__name__)

_MARKET_HUB = "wss://rtc.topstepx.com/hubs/market"
_USER_HUB = "wss://rtc.topstepx.com/hubs/user"
_SEPARATOR = "\x1e"  # SignalR message separator
_RECONNECT_DELAYS = [0, 2, 5, 10, 30]


def _parse_ts(ts_str: str) -> float:
    """Convert ISO-8601 timestamp string to epoch float."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return time.time()


class TopstepXStream:
    """SignalR streaming client for TopstepX market and user hubs."""

    def __init__(
        self,
        token: str,
        contract_id: str,
        account_id: int,
        market_hub: str | None = None,
        user_hub: str | None = None,
    ) -> None:
        self._token = token
        self._contract_id = contract_id
        self._account_id = account_id
        self._market_hub_url = market_hub or _MARKET_HUB
        self._user_hub_url = user_hub or _USER_HUB

        # Callbacks — set by the launcher
        self.on_tick: Callable[[float, int, float], None] | None = None
        self.on_fill: Callable[[dict], None] | None = None
        self.on_depth: Callable[[dict], None] | None = None
        self.on_quote: Callable[[dict], None] | None = None

        self._market_ws = None
        self._user_ws = None
        self._tasks: list[asyncio.Task] = []
        self._running = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Connect both hubs and subscribe to relevant channels."""
        self._running = True
        self._tasks.append(asyncio.create_task(
            self._run_hub("market", self._market_hub_url, self._on_market_msg, self._market_subs),
        ))
        self._tasks.append(asyncio.create_task(
            self._run_hub("user", self._user_hub_url, self._on_user_msg, self._user_subs),
        ))
        log.info("TopstepXStream started (contract=%s, account=%d)", self._contract_id, self._account_id)

    async def stop(self) -> None:
        """Disconnect both hubs."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        for ws in (self._market_ws, self._user_ws):
            if ws:
                try:
                    await ws.close()
                except Exception:
                    pass
        self._tasks.clear()
        log.info("TopstepXStream stopped")

    # ------------------------------------------------------------------
    # Internal: hub connection + reconnect loop
    # ------------------------------------------------------------------

    async def _run_hub(self, name: str, hub_url: str, msg_handler, subscribe_fn) -> None:
        """Connect to a hub with auto-reconnect."""
        attempt = 0
        while self._running:
            try:
                url = f"{hub_url}?access_token={self._token}"
                async with websockets.connect(url, ping_interval=20) as ws:
                    # SignalR handshake
                    await ws.send(json.dumps({"protocol": "json", "version": 1}) + _SEPARATOR)
                    handshake = await ws.recv()
                    if "{}" not in handshake:
                        log.error("TopstepXStream [%s]: bad handshake: %r", name, handshake)
                        continue

                    log.info("TopstepXStream [%s]: connected", name)
                    attempt = 0  # reset on success

                    # Store reference
                    if name == "market":
                        self._market_ws = ws
                    else:
                        self._user_ws = ws

                    # Subscribe
                    await subscribe_fn(ws)

                    # Listen
                    async for raw in ws:
                        for part in raw.split(_SEPARATOR):
                            part = part.strip()
                            if not part:
                                continue
                            try:
                                msg = json.loads(part)
                            except json.JSONDecodeError:
                                continue
                            msg_type = msg.get("type")
                            if msg_type == 1:  # Invocation
                                msg_handler(msg.get("target", ""), msg.get("arguments", []))
                            elif msg_type == 6:  # Ping
                                await ws.send(json.dumps({"type": 6}) + _SEPARATOR)

            except asyncio.CancelledError:
                return
            except Exception as e:
                if not self._running:
                    return
                delay = _RECONNECT_DELAYS[min(attempt, len(_RECONNECT_DELAYS) - 1)]
                log.warning("TopstepXStream [%s]: disconnected (%s), reconnecting in %ds", name, e, delay)
                attempt += 1
                await asyncio.sleep(delay)

    async def _market_subs(self, ws) -> None:
        """Subscribe to market data channels."""
        for target in ("SubscribeContractQuotes", "SubscribeContractTrades", "SubscribeContractDepth"):
            await ws.send(json.dumps({
                "type": 1,
                "target": target,
                "arguments": [self._contract_id],
            }) + _SEPARATOR)
        log.info("TopstepXStream [market]: subscribed to quotes+trades+depth for %s", self._contract_id)

    async def _user_subs(self, ws) -> None:
        """Subscribe to user event channels."""
        for target in ("SubscribeToPositions", "SubscribeToOrders", "SubscribeToUserTrades"):
            await ws.send(json.dumps({
                "type": 1,
                "target": target,
                "arguments": [self._account_id],
            }) + _SEPARATOR)
        log.info("TopstepXStream [user]: subscribed to positions+orders+trades for account %d", self._account_id)

    # ------------------------------------------------------------------
    # Internal: message dispatch
    # ------------------------------------------------------------------

    def _on_market_msg(self, target: str, args: list) -> None:
        """Dispatch market hub events."""
        if target == "GatewayTrade":
            self._handle_trades(args)
        elif target == "GatewayQuote":
            self._handle_quote(args)
        elif target == "GatewayDepth":
            self._handle_depth(args)

    def _on_user_msg(self, target: str, args: list) -> None:
        """Dispatch user hub events."""
        if target == "GatewayUserTrade":
            self._handle_user_trade(args)
        elif target == "GatewayUserPosition":
            self._handle_position(args)
        elif target == "GatewayUserOrder":
            self._handle_order(args)

    # ------------------------------------------------------------------
    # Internal: event handlers
    # ------------------------------------------------------------------

    def _handle_trades(self, args: list) -> None:
        """Parse GatewayTrade — args = [contractId, [trade, trade, ...]]."""
        if not args or len(args) < 2 or not self.on_tick:
            return
        trades = args[1]
        if not isinstance(trades, list):
            return
        for trade in trades:
            try:
                price = float(trade["price"])
                size = int(trade.get("volume", 1))
                ts = _parse_ts(trade.get("timestamp", ""))
                self.on_tick(price, size, ts)
            except Exception:
                log.debug("TopstepXStream: bad trade: %r", trade)

    def _handle_quote(self, args: list) -> None:
        """Parse GatewayQuote — args = [contractId, quoteDict]."""
        if not args or len(args) < 2 or not self.on_quote:
            return
        try:
            self.on_quote(args[1])
        except Exception:
            log.debug("TopstepXStream: bad quote: %r", args)

    def _handle_depth(self, args: list) -> None:
        """Parse GatewayDepth — args = [contractId, [level, level, ...]]."""
        if not args or len(args) < 2 or not self.on_depth:
            return
        levels = args[1]
        if not isinstance(levels, list):
            return
        for level in levels:
            try:
                if level.get("price", 0) == 0:
                    continue  # skip heartbeat/reset messages
                self.on_depth(level)
            except Exception:
                log.debug("TopstepXStream: bad depth level: %r", level)

    def _handle_user_trade(self, args: list) -> None:
        """Parse GatewayUserTrade fill."""
        if not args or not self.on_fill:
            return
        try:
            fill = args[0] if isinstance(args[0], dict) else args[0]
            self.on_fill(fill)
        except Exception:
            log.debug("TopstepXStream: bad fill: %r", args)

    def _handle_position(self, args: list) -> None:
        """Log GatewayUserPosition updates."""
        pos = args[0] if args else {}
        log.info("TopstepXStream: position update: %s", pos)

    def _handle_order(self, args: list) -> None:
        """Log GatewayUserOrder updates."""
        order = args[0] if args else {}
        log.debug("TopstepXStream: order update: %s", order)
