"""TopstepX REST API client.

Handles auth token management, order placement, position queries.
All methods are async. Token auto-refreshes before expiry (~23.5h lifetime).
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from .config import TopstepXConfig

log = logging.getLogger(__name__)

# TopstepX order side constants
SIDE_BUY = 0
SIDE_SELL = 1

# TopstepX order type constants
ORDER_TYPE_MARKET = 2
ORDER_TYPE_STOP = 3


class TopstepXClient:
    """Async HTTP client for TopstepX REST API."""

    # Tokens last ~23.5h; refresh 30 min before expiry
    TOKEN_LIFETIME_S = 23.5 * 3600
    TOKEN_REFRESH_BUFFER_S = 1800

    def __init__(self, config: TopstepXConfig) -> None:
        self._config = config
        self._http = httpx.AsyncClient(timeout=10.0)
        self._token: str | None = None
        self._token_expiry: float = 0
        self._account_id: int | None = None
        self._account_name: str | None = None

    # ------------------------------------------------------------------
    # Connection / auth
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """Authenticate and discover account. Returns True on success."""
        try:
            token = await self._auth()
            if not token:
                return False
            self._token = token
            self._token_expiry = time.time() + self.TOKEN_LIFETIME_S - self.TOKEN_REFRESH_BUFFER_S

            data = await self._post("/api/Account/search", {"onlyActiveAccounts": True})
            accounts = data.get("accounts", [])
            if not accounts:
                log.error("TopstepX: no active accounts found")
                return False

            self._account_id = accounts[0]["id"]
            self._account_name = accounts[0].get("name", "")
            log.info("TopstepX connected: account=%s (id=%d)", self._account_name, self._account_id)
            return True
        except Exception:
            log.exception("TopstepX connect failed")
            return False

    async def _auth(self) -> str | None:
        """POST loginKey and return the bearer token string."""
        payload = {
            "userName": self._config.username,
            "apiKey": self._config.api_key,
        }
        resp = await self._http.post(
            f"{self._config.base_url}/api/Auth/loginKey",
            json=payload,
        )
        if resp.status_code == 200:
            body = resp.json()
            if body.get("success"):
                return body.get("token")
            log.error("TopstepX auth rejected: %s", body.get("errorMessage"))
            return None
        log.error("TopstepX auth HTTP %d: %s", resp.status_code, resp.text[:200])
        return None

    async def _ensure_token(self) -> None:
        """Refresh token if near expiry using /api/Auth/validate."""
        if time.time() < self._token_expiry:
            return
        log.info("Refreshing TopstepX token...")
        try:
            resp = await self._http.post(
                f"{self._config.base_url}/api/Auth/validate",
                headers={"Authorization": f"Bearer {self._token}"},
                json={},
            )
            if resp.status_code == 200:
                body = resp.json()
                new_token = body.get("token")
                if new_token:
                    self._token = new_token
                    self._token_expiry = time.time() + self.TOKEN_LIFETIME_S - self.TOKEN_REFRESH_BUFFER_S
                    return
            # Fallback: full re-auth
            token = await self._auth()
            if token:
                self._token = token
                self._token_expiry = time.time() + self.TOKEN_LIFETIME_S - self.TOKEN_REFRESH_BUFFER_S
        except Exception:
            log.exception("TopstepX token refresh failed")

    async def _post(self, path: str, payload: dict) -> Any:
        await self._ensure_token()
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        resp = await self._http.post(
            f"{self._config.base_url}{path}",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Order operations
    # ------------------------------------------------------------------

    def _side(self, action: str) -> int:
        """Convert 'Buy'/'Sell' to TopstepX side integer."""
        return SIDE_BUY if action == "Buy" else SIDE_SELL

    async def place_market_order(self, action: str, quantity: int) -> dict:
        """Place a market order. action: 'Buy' or 'Sell'."""
        payload = {
            "accountId": self._account_id,
            "contractId": self._config.contract_id,
            "type": ORDER_TYPE_MARKET,
            "side": self._side(action),
            "size": quantity,
        }
        result = await self._post("/api/Order/place", payload)
        log.info("Market order placed: %s %d %s → %s",
                 action, quantity, self._config.contract_id, result)
        return result

    async def place_stop_order(self, action: str, quantity: int, stop_price: float) -> dict:
        """Place a stop order (for stop-loss). action: 'Buy' or 'Sell'."""
        payload = {
            "accountId": self._account_id,
            "contractId": self._config.contract_id,
            "type": ORDER_TYPE_STOP,
            "side": self._side(action),
            "size": quantity,
            "stopPrice": stop_price,
        }
        result = await self._post("/api/Order/place", payload)
        log.info("Stop order placed: %s %d @ %.2f → %s",
                 action, quantity, stop_price, result)
        return result

    async def modify_order(self, order_id: int, new_stop_price: float) -> dict:
        """Modify an existing order's stop price."""
        payload = {
            "accountId": self._account_id,
            "orderId": order_id,
            "stopPrice": new_stop_price,
        }
        result = await self._post("/api/Order/modify", payload)
        log.info("Order %d modified: stop=%.2f", order_id, new_stop_price)
        return result

    async def cancel_order(self, order_id: int) -> dict:
        """Cancel a pending order."""
        payload = {
            "accountId": self._account_id,
            "orderId": order_id,
        }
        result = await self._post("/api/Order/cancel", payload)
        log.info("Order %d cancelled", order_id)
        return result

    async def liquidate_position(self) -> dict:
        """Flatten all positions for the configured contract."""
        payload = {
            "accountId": self._account_id,
            "contractId": self._config.contract_id,
        }
        result = await self._post("/api/Position/closeContract", payload)
        log.info("Position liquidated for %s", self._config.contract_id)
        return result

    async def get_positions(self) -> list[dict]:
        """Get all open positions."""
        data = await self._post("/api/Position/searchOpen", {"accountId": self._account_id})
        return data if isinstance(data, list) else data.get("positions", [])

    async def get_orders(self) -> list[dict]:
        """Get all working orders."""
        data = await self._post("/api/Order/searchOpen", {"accountId": self._account_id})
        return data if isinstance(data, list) else data.get("orders", [])

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()
