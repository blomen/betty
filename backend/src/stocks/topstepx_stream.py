"""TopstepX SignalR streaming client.

Connects to two hubs:
  - Market hub: live trade ticks (GatewayTrade)
  - User hub:   fills, positions, orders (GatewayUserTrade/Position/Order)

Usage::

    stream = TopstepXStream(token=token, contract_id="CON.F.US.NQ.M25", account_id=12345)
    stream.on_tick = lambda price, size, ts: print(price, size, ts)
    stream.on_fill = lambda fill: print(fill)
    stream.start()
    ...
    stream.stop()
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from signalrcore.hub_connection_builder import HubConnectionBuilder

log = logging.getLogger(__name__)

_MARKET_HUB = "wss://rtc.topstepx.com/hubs/market"
_USER_HUB = "wss://rtc.topstepx.com/hubs/user"

_RECONNECT_POLICY = {
    "type": "interval",
    "keep_alive_interval": 10,
    "intervals": [0, 2, 5, 10, 30],
}


def _parse_ts(ts_str: str) -> float:
    """Convert ISO-8601 timestamp string to epoch float."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        log.warning("TopstepXStream: bad timestamp %r", ts_str)
        return 0.0


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

        self.on_tick: Callable[[float, int, float], None] | None = None
        self.on_fill: Callable[[dict], None] | None = None

        self._market_conn = None
        self._user_conn = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Connect both hubs and subscribe to relevant channels."""
        self._market_conn = self._build_conn(self._market_hub_url)
        self._market_conn.on("GatewayTrade", self._handle_trade)
        self._market_conn.on_open(lambda: self._on_market_open())
        self._market_conn.on_close(lambda: log.warning("TopstepXStream: market hub disconnected"))
        self._market_conn.start()

        self._user_conn = self._build_conn(self._user_hub_url)
        self._user_conn.on("GatewayUserTrade", self._handle_user_trade)
        self._user_conn.on("GatewayUserPosition", self._handle_position)
        self._user_conn.on("GatewayUserOrder", self._handle_order)
        self._user_conn.on_open(lambda: self._on_user_open())
        self._user_conn.on_close(lambda: log.warning("TopstepXStream: user hub disconnected"))
        self._user_conn.start()

    def stop(self) -> None:
        """Disconnect both hubs."""
        if self._market_conn:
            try:
                self._market_conn.stop()
            except Exception:
                log.exception("TopstepXStream: error stopping market hub")
            self._market_conn = None

        if self._user_conn:
            try:
                self._user_conn.stop()
            except Exception:
                log.exception("TopstepXStream: error stopping user hub")
            self._user_conn = None

    # ------------------------------------------------------------------
    # Internal: connection factory
    # ------------------------------------------------------------------

    def _build_conn(self, hub_url: str):
        return (
            HubConnectionBuilder()
            .with_url(
                f"{hub_url}?access_token={self._token}",
                options={"skip_negotiation": True, "verify_ssl": True},
            )
            .with_automatic_reconnect(_RECONNECT_POLICY)
            .build()
        )

    # ------------------------------------------------------------------
    # Internal: on_open callbacks (subscribe after connection)
    # ------------------------------------------------------------------

    def _on_market_open(self) -> None:
        log.info("TopstepXStream: market hub connected")
        try:
            self._market_conn.send("SubscribeContractTrades", [self._contract_id])
            log.info("TopstepXStream: subscribed to contract trades for %s", self._contract_id)
        except Exception:
            log.exception("TopstepXStream: failed to subscribe to contract trades")

    def _on_user_open(self) -> None:
        log.info("TopstepXStream: user hub connected")
        try:
            self._user_conn.send("SubscribeToPositions", [self._account_id])
            self._user_conn.send("SubscribeToOrders", [self._account_id])
            self._user_conn.send("SubscribeToUserTrades", [self._account_id])
            log.info("TopstepXStream: subscribed to user events for account %d", self._account_id)
        except Exception:
            log.exception("TopstepXStream: failed to subscribe to user events")

    # ------------------------------------------------------------------
    # Internal: event handlers
    # ------------------------------------------------------------------

    def _handle_trade(self, args: list) -> None:
        """Parse GatewayTrade tick and call on_tick(price, size, ts)."""
        if not args:
            return
        try:
            tick = args[0]
            if not isinstance(tick, dict):
                return
            price = float(tick["price"])
            size = int(tick["size"])
            ts = _parse_ts(tick["timestamp"])
            if self.on_tick is not None:
                self.on_tick(price, size, ts)
        except Exception:
            log.exception("TopstepXStream: error handling trade tick: %r", args)

    def _handle_user_trade(self, args: list) -> None:
        """Parse GatewayUserTrade fill and call on_fill(fill_dict)."""
        if not args:
            return
        try:
            fill = args[0]
            if not isinstance(fill, dict):
                return
            if self.on_fill is not None:
                self.on_fill(fill)
        except Exception:
            log.exception("TopstepXStream: error handling user trade: %r", args)

    def _handle_position(self, args: list) -> None:
        """Log GatewayUserPosition updates."""
        try:
            pos = args[0] if args else {}
            log.info("TopstepXStream: position update: %s", pos)
        except Exception:
            log.exception("TopstepXStream: error handling position: %r", args)

    def _handle_order(self, args: list) -> None:
        """Log GatewayUserOrder updates."""
        try:
            order = args[0] if args else {}
            log.info("TopstepXStream: order update: %s", order)
        except Exception:
            log.exception("TopstepXStream: error handling order: %r", args)
