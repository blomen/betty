"""Polymarket mirror parser — balance, orders, deposits, price verification."""

import json
import logging

logger = logging.getLogger(__name__)


class PolymarketParser:
    """Parses Polymarket-specific browser traffic responses."""

    def parse_balance(self, url: str, body: str) -> float | None:
        """Extract USDC portfolio value from data-api.polymarket.com/value response.

        Response format: [{"user": "0x...", "value": 123.45}]
        Returns value in USDC, or None if unparseable.
        """
        try:
            data = json.loads(body)
            if isinstance(data, list) and data and "value" in data[0]:
                return float(data[0]["value"])
        except (json.JSONDecodeError, TypeError, ValueError, IndexError) as e:
            logger.debug(f"[polymarket] Could not parse balance: {e}")
        return None

    def parse_orders(self, body: str) -> list[dict]:
        """Parse open orders from clob.polymarket.com/data/orders response.

        Returns normalized order list with: id, side, price, size, filled, outcome, market, status.
        """
        try:
            data = json.loads(body)
            if not isinstance(data, list):
                return []
            orders = []
            for o in data:
                orders.append(
                    {
                        "id": o.get("id", ""),
                        "status": o.get("status", ""),
                        "token_id": o.get("asset_id", ""),
                        "side": o.get("side", ""),
                        "price": float(o.get("price", 0)),
                        "size": float(o.get("original_size", 0)),
                        "filled": float(o.get("size_matched", 0)),
                        "outcome": o.get("outcome", ""),
                        "market": o.get("market", ""),
                    }
                )
            return orders
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            logger.debug(f"[polymarket] Could not parse orders: {e}")
            return []

    def parse_deposit(self, url: str, body: str) -> dict | None:
        """Parse deposit initiation from Swapped widget create_order response.

        Returns {"order_id": "...", "amount": 100, "currency": "USD"} or None.
        """
        if "create_order" not in url:
            return None
        try:
            data = json.loads(body)
            order_id = data.get("orderId")
            amount = data.get("amount")
            if order_id and amount is not None:
                return {
                    "order_id": str(order_id),
                    "amount": float(amount),
                    "currency": data.get("currency", "USD"),
                    "status": data.get("status", "unknown"),
                }
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            logger.debug(f"[polymarket] Could not parse deposit: {e}")
        return None

    def check_slippage(self, expected: float, actual: float, max_pct: float) -> bool:
        """Check if price slippage is within acceptable range.

        Returns True if acceptable, False if exceeds max_pct.
        """
        if expected <= 0:
            return False
        slippage_pct = abs(actual - expected) / expected * 100
        return slippage_pct <= max_pct

    def parse_best_ask(self, body: str) -> float | None:
        """Extract best ask price from CLOB order book response.

        Response format: {"asks": [{"price": "0.63", "size": "150"}, ...], "bids": [...]}
        """
        try:
            data = json.loads(body)
            asks = data.get("asks", [])
            if asks:
                return float(asks[0]["price"])
        except (json.JSONDecodeError, TypeError, ValueError, IndexError, KeyError) as e:
            logger.debug(f"[polymarket] Could not parse order book: {e}")
        return None
