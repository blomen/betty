"""Rithmic order execution client — replaces TradovateClient.

Same interface as TradovateClient so BrokerAdapter doesn't change.
Uses async-rithmic's OrderPlant for order management.
"""

from __future__ import annotations

import logging
import uuid

from async_rithmic import (
    ExchangeOrderNotificationType,
    OrderType,
    RithmicClient,
    TransactionType,
)

from .config import RithmicConfig

log = logging.getLogger(__name__)


class RithmicBrokerClient:
    """Order execution via Rithmic — drop-in replacement for TradovateClient."""

    def __init__(self, client: RithmicClient, config: RithmicConfig) -> None:
        self._client = client
        self._config = config
        self._account_id: str | None = None
        self._fills: list[dict] = []

        # Register fill callback
        self._client.on_exchange_order_notification += self._on_order_notification

    async def connect(self) -> bool:
        """Get account info (client should already be connected via RithmicStream)."""
        try:
            accounts = self._client.accounts
            if accounts:
                self._account_id = accounts[0].account_id if hasattr(accounts[0], "account_id") else str(accounts[0])
                log.info("Rithmic broker: account=%s", self._account_id)
                return True
            # Try listing accounts
            account_list = await self._client.list_accounts()
            if account_list:
                self._account_id = str(account_list[0])
                log.info("Rithmic broker: account=%s", self._account_id)
                return True
            log.error("No Rithmic accounts found")
            return False
        except Exception:
            log.exception("Rithmic broker connect failed")
            return False

    async def place_market_order(self, action: str, quantity: int) -> dict:
        """Place a market order. action: 'Buy' or 'Sell'."""
        tx_type = TransactionType.BUY if action == "Buy" else TransactionType.SELL
        order_id = f"arnold-{uuid.uuid4().hex[:8]}"

        await self._client.submit_order(
            order_id=order_id,
            symbol=self._config.symbol,
            exchange=self._config.exchange,
            qty=quantity,
            transaction_type=tx_type,
            order_type=OrderType.MARKET,
        )
        log.info("Market order: %s %d %s (id=%s)", action, quantity, self._config.symbol, order_id)
        return {"orderId": order_id}

    async def place_stop_order(self, action: str, quantity: int, stop_price: float) -> dict:
        """Place a stop-market order (for stop-loss)."""
        tx_type = TransactionType.BUY if action == "Buy" else TransactionType.SELL
        order_id = f"arnold-stop-{uuid.uuid4().hex[:8]}"

        await self._client.submit_order(
            order_id=order_id,
            symbol=self._config.symbol,
            exchange=self._config.exchange,
            qty=quantity,
            transaction_type=tx_type,
            order_type=OrderType.STOP_MARKET,
            trigger_price=stop_price,
        )
        log.info("Stop order: %s %d @ %.2f (id=%s)", action, quantity, stop_price, order_id)
        return {"orderId": order_id}

    async def modify_order(self, order_id: str, new_stop_price: float) -> dict:
        """Modify an existing order's stop price."""
        await self._client.modify_order(
            order_id=order_id,
            trigger_price=new_stop_price,
        )
        log.info("Order %s modified: stop=%.2f", order_id, new_stop_price)
        return {"orderId": order_id}

    async def cancel_order(self, order_id: str) -> dict:
        """Cancel a pending order."""
        await self._client.cancel_order(order_id=order_id)
        log.info("Order %s cancelled", order_id)
        return {"orderId": order_id}

    async def liquidate_position(self) -> dict:
        """Flatten all positions for the configured symbol."""
        await self._client.exit_position(
            symbol=self._config.symbol,
            exchange=self._config.exchange,
        )
        log.info("Position liquidated for %s", self._config.symbol)
        return {"status": "liquidated"}

    async def get_positions(self) -> list[dict]:
        """Get open positions (via PnL plant)."""
        return []  # TODO: implement via pnl plant if needed

    async def get_orders(self) -> list[dict]:
        """Get working orders."""
        result = await self._client.list_orders()
        return result if result else []

    async def close(self) -> None:
        """No-op — client lifecycle managed by RithmicStream."""
        pass

    def _on_order_notification(self, notification) -> None:
        """Handle exchange order notifications (fills, rejects, etc)."""
        notify_type = getattr(notification, "notify_type", None)
        if notify_type == ExchangeOrderNotificationType.FILL:
            fill = {
                "order_id": getattr(notification, "order_id", ""),
                "price": float(getattr(notification, "fill_price", 0)),
                "size": int(getattr(notification, "fill_size", 0)),
                "side": getattr(notification, "transaction_type", ""),
            }
            self._fills.append(fill)
            log.info("Fill: %s %d @ %.2f", fill["side"], fill["size"], fill["price"])
        elif notify_type == ExchangeOrderNotificationType.REJECT:
            log.warning("Order rejected: %s", getattr(notification, "text", "unknown"))
