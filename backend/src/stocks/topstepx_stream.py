"""TopstepX SignalR streaming client (raw websockets).

Connects to two hubs via SignalR JSON protocol over websockets:
  - Market hub: live trade ticks (GatewayTrade), quotes (GatewayQuote), depth (GatewayDepth)
  - User hub:   accounts, fills, positions, orders (GatewayUserAccount/Trade/Position/Order)

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
import contextlib
import json
import logging
from collections.abc import Callable
from datetime import datetime

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
        return 0.0


class TopstepXStream:
    """SignalR streaming client for TopstepX market and user hubs."""

    def __init__(
        self,
        token: str | Callable[[], str],
        contract_id: str,
        account_id: int,
        market_hub: str | None = None,
        user_hub: str | None = None,
    ) -> None:
        # Accept a callable so reconnects pick up rotated tokens; tokens
        # expire ~24h and the WS would otherwise spin on stale-token 401s.
        self._token_provider: Callable[[], str] = token if callable(token) else (lambda t=token: t)
        self._contract_id = contract_id
        self._account_id = account_id
        self._market_hub_url = market_hub or _MARKET_HUB
        self._user_hub_url = user_hub or _USER_HUB

        # Callbacks — set by the launcher
        self.on_tick: Callable[[float, int, float], None] | None = None
        self.on_fill: Callable[[dict], None] | None = None
        self.on_depth: Callable[[dict], None] | None = None
        self.on_quote: Callable[[dict], None] | None = None
        self.on_account: Callable[[dict], None] | None = None
        # 2026-05-08: fired ONCE per successful (re)connect of the user hub.
        # TopstepX doesn't replay missed events on reconnect, so a fill that
        # arrived during the disconnect window is permanently lost from the
        # stream. Wire this to reconcile_tracker_from_broker so the adapter
        # syncs its tracker from REST after every reconnect — caught lost
        # entry fills land within seconds instead of waiting for the
        # update_mark watchdog (10s + 60s cooldown).
        self.on_user_reconnect: Callable[[], None] | None = None

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
        self._tasks.append(
            asyncio.create_task(
                self._run_hub("market", self._market_hub_url, self._on_market_msg, self._market_subs),
            )
        )
        self._tasks.append(
            asyncio.create_task(
                self._run_hub("user", self._user_hub_url, self._on_user_msg, self._user_subs),
            )
        )
        log.info("TopstepXStream started (contract=%s, account=%d)", self._contract_id, self._account_id)

    async def stop(self) -> None:
        """Disconnect both hubs."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        for ws in (self._market_ws, self._user_ws):
            if ws:
                with contextlib.suppress(Exception):
                    await ws.close()
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
                url = f"{hub_url}?access_token={self._token_provider()}"
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
                        # Fire reconnect hook on the USER hub so the adapter can
                        # sync its tracker from REST. Missed-fills during the
                        # disconnect window are not replayed by TopstepX, so
                        # post-reconnect-without-reconcile leaves us with a
                        # stale local tracker until the next 60s reconcile tick.
                        if self.on_user_reconnect is not None:
                            try:
                                self.on_user_reconnect()
                            except Exception:
                                log.exception("TopstepXStream [user]: on_user_reconnect raised")

                    # Subscribe
                    await subscribe_fn(ws)

                    # Listen
                    msg_count = 0
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
                            elif msg_type == 3:  # Completion (subscription response)
                                inv_id = msg.get("invocationId", "?")
                                error = msg.get("error")
                                if error:
                                    log.warning(
                                        "TopstepXStream [%s]: subscription error (id=%s): %s", name, inv_id, error
                                    )
                                else:
                                    log.info("TopstepXStream [%s]: subscription confirmed (id=%s)", name, inv_id)
                            elif msg_type == 6:  # Ping
                                await ws.send(json.dumps({"type": 6}) + _SEPARATOR)
                            # Log first 5 raw messages per hub for diagnostics
                            msg_count += 1
                            if msg_count <= 5 and name == "user":
                                log.info("TopstepXStream [user] raw msg #%d: %s", msg_count, part[:300])

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
            await ws.send(
                json.dumps(
                    {
                        "type": 1,
                        "target": target,
                        "arguments": [self._contract_id],
                    }
                )
                + _SEPARATOR
            )
        log.info("TopstepXStream [market]: subscribed to quotes+trades+depth for %s", self._contract_id)

    async def _user_subs(self, ws) -> None:
        """Subscribe to user event channels."""
        for i, target in enumerate(
            (
                "SubscribeAccounts",  # canTrade=false detection on prop firm violations
                "SubscribePositions",
                "SubscribeOrders",
                "SubscribeTrades",
            )
        ):
            await ws.send(
                json.dumps(
                    {
                        "invocationId": str(i + 1),
                        "type": 1,
                        "target": target,
                        "arguments": [self._account_id],
                    }
                )
                + _SEPARATOR
            )
        log.info(
            "TopstepXStream [user]: subscribed to accounts+positions+orders+trades for account %d",
            self._account_id,
        )

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
        # Log all user hub messages for diagnostics
        if target not in ("GatewayUserPosition",):  # positions are noisy
            log.info("TopstepXStream [user] event: target=%s args_len=%d", target, len(args))
            if args:
                first = args[0] if isinstance(args[0], dict) else str(args[0])[:200]
                log.info("TopstepXStream [user] payload: %s", first)

        if target in ("GatewayUserAccount", "GotUserAccount"):
            self._handle_account(args)
        elif target in ("GatewayUserTrade", "GotUserTrade", "UserTrade"):
            self._handle_user_trade(args)
        elif target in ("GatewayUserPosition", "GotUserPosition"):
            self._handle_position(args)
        elif target in ("GatewayUserOrder", "GotUserOrder", "UserOrder"):
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
                if ts <= 0:
                    # Drop trades with unparseable timestamps so they don't
                    # land in market_trades with epoch-0 ts.
                    log.debug("TopstepXStream: skip trade with bad ts: %r", trade)
                    continue
                # type: 0=bid hit (sell aggressor), 1=ask lift (buy aggressor)
                side = "B" if trade.get("type") == 0 else "A"
                self.on_tick(price, size, ts, side)
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
        log.info("TopstepXStream: FILL received: %r", args[:2] if len(args) > 1 else args)
        if not args or not self.on_fill:
            log.warning("TopstepXStream: fill dropped — on_fill=%s args=%d", self.on_fill is not None, len(args))
            return
        try:
            fill = args[0] if isinstance(args[0], dict) else args[0]
            log.info("TopstepXStream: forwarding fill to adapter: %s", fill)
            self.on_fill(fill)
        except Exception:
            log.exception("TopstepXStream: fill handler error: %r", args)

    def _handle_position(self, args: list) -> None:
        """Log GatewayUserPosition updates."""
        pos = args[0] if args else {}
        qty = pos.get("qty", pos.get("quantity", pos.get("netQuantity", "?")))
        avg_price = pos.get("averagePrice", pos.get("avgPrice", "?"))
        log.info("TopstepXStream: POSITION update: qty=%s avg_price=%s raw=%s", qty, avg_price, pos)

    def _handle_order(self, args: list) -> None:
        """Log GatewayUserOrder updates."""
        order = args[0] if args else {}
        log.debug("TopstepXStream: order update: %s", order)

    def _handle_account(self, args: list) -> None:
        """Parse GatewayUserAccount — fires on balance change or canTrade flip.
        canTrade=false signals a prop-firm-side halt (drawdown / loss limit hit)."""
        if not args:
            return
        payload = args[0] if isinstance(args[0], dict) else {}
        can_trade = payload.get("canTrade")
        balance = payload.get("balance")
        log.info(
            "TopstepXStream [user] ACCOUNT update: canTrade=%s balance=%s",
            can_trade,
            balance,
        )
        if self.on_account is not None:
            try:
                self.on_account(payload)
            except Exception:
                log.exception("on_account handler raised")
