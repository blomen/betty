"""Low-level Tradovate REST API client.

Handles auth token management, order placement, position queries.
All methods are async. Token auto-refreshes before expiry.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from .config import BrokerConfig

log = logging.getLogger(__name__)

# Tradovate order action constants
ACTION_BUY = "Buy"
ACTION_SELL = "Sell"

# Order types
ORDER_TYPE_MARKET = "Market"
ORDER_TYPE_STOP = "Stop"
ORDER_TYPE_LIMIT = "Limit"


class TradovateClient:
    """Async HTTP client for Tradovate REST API."""

    TOKEN_REFRESH_BUFFER_S = 300  # refresh 5 min before expiry

    def __init__(self, config: BrokerConfig) -> None:
        self._config = config
        self._http = httpx.AsyncClient(timeout=10.0)
        self._token: str | None = None
        self._token_expiry: float = 0
        self._account_id: int | None = None
        self._account_spec: str | None = None  # e.g. "DEMO12345"

    async def connect(self) -> bool:
        """Authenticate and get account info."""
        try:
            token_data = await self._auth()
            if not token_data:
                return False

            self._token = token_data["accessToken"]
            # Tradovate tokens last ~24h, but we refresh early
            self._token_expiry = time.time() + token_data.get("expirationTime", 86400) - self.TOKEN_REFRESH_BUFFER_S

            # Get account ID
            accounts = await self._get("/account/list")
            if accounts:
                self._account_id = accounts[0]["id"]
                self._account_spec = accounts[0].get("name", "")
                log.info("Tradovate connected: account=%s (id=%d)", self._account_spec, self._account_id)
                return True

            log.error("No accounts found")
            return False
        except Exception:
            log.exception("Tradovate connect failed")
            return False

    async def _auth(self) -> dict | None:
        """POST auth token request."""
        payload = {
            "name": self._config.username,
            "password": self._config.password,
            "appId": self._config.app_id,
            "appVersion": "1.0",
            "cid": self._config.cid,
            "deviceId": self._config.device_id,
        }
        resp = await self._http.post(
            f"{self._config.base_url}/auth/accesstokenrequest",
            json=payload,
        )
        if resp.status_code == 200:
            return resp.json()
        log.error("Auth failed: %d %s", resp.status_code, resp.text[:200])
        return None

    async def _ensure_token(self) -> None:
        """Refresh token if near expiry."""
        if time.time() >= self._token_expiry:
            log.info("Refreshing Tradovate token...")
            token_data = await self._auth()
            if token_data:
                self._token = token_data["accessToken"]
                self._token_expiry = time.time() + token_data.get("expirationTime", 86400) - self.TOKEN_REFRESH_BUFFER_S

    async def _get(self, path: str) -> Any:
        await self._ensure_token()
        resp = await self._http.get(
            f"{self._config.base_url}{path}",
            headers={"Authorization": f"Bearer {self._token}"},
        )
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, payload: dict) -> Any:
        await self._ensure_token()
        resp = await self._http.post(
            f"{self._config.base_url}{path}",
            json=payload,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        resp.raise_for_status()
        return resp.json()

    # --- Order Operations ---

    async def place_market_order(self, action: str, quantity: int) -> dict:
        """Place a market order. action: 'Buy' or 'Sell'."""
        payload = {
            "accountSpec": self._account_spec,
            "accountId": self._account_id,
            "action": action,
            "symbol": self._config.symbol,
            "orderQty": quantity,
            "orderType": ORDER_TYPE_MARKET,
            "isAutomated": True,
        }
        result = await self._post("/order/placeorder", payload)
        log.info(
            "Market order placed: %s %d %s → orderId=%s", action, quantity, self._config.symbol, result.get("orderId")
        )
        return result

    async def place_stop_order(self, action: str, quantity: int, stop_price: float) -> dict:
        """Place a stop order (for stop-loss)."""
        payload = {
            "accountSpec": self._account_spec,
            "accountId": self._account_id,
            "action": action,
            "symbol": self._config.symbol,
            "orderQty": quantity,
            "orderType": ORDER_TYPE_STOP,
            "stopPrice": stop_price,
            "isAutomated": True,
        }
        result = await self._post("/order/placeorder", payload)
        log.info("Stop order placed: %s %d @ %.2f → orderId=%s", action, quantity, stop_price, result.get("orderId"))
        return result

    async def modify_order(self, order_id: int, new_stop_price: float) -> dict:
        """Modify an existing order's stop price."""
        payload = {
            "orderId": order_id,
            "stopPrice": new_stop_price,
        }
        result = await self._post("/order/modifyorder", payload)
        log.info("Order %d modified: stop=%.2f", order_id, new_stop_price)
        return result

    async def cancel_order(self, order_id: int) -> dict:
        """Cancel a pending order."""
        payload = {"orderId": order_id}
        result = await self._post("/order/cancelorder", payload)
        log.info("Order %d cancelled", order_id)
        return result

    async def liquidate_position(self) -> dict:
        """Flatten all positions for the configured symbol."""
        payload = {
            "accountId": self._account_id,
            "symbol": self._config.symbol,
        }
        result = await self._post("/order/liquidateposition", payload)
        log.info("Position liquidated for %s", self._config.symbol)
        return result

    async def get_positions(self) -> list[dict]:
        """Get all open positions."""
        return await self._get("/position/list")

    async def get_orders(self) -> list[dict]:
        """Get all working orders."""
        return await self._get("/order/list")

    async def get_fills(self) -> list[dict]:
        """Get recent fills."""
        return await self._get("/fill/list")

    async def close(self) -> None:
        """Close HTTP client."""
        await self._http.aclose()
