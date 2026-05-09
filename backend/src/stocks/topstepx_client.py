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
ORDER_TYPE_LIMIT = 1
ORDER_TYPE_MARKET = 2
ORDER_TYPE_STOP_MARKET = 4  # stop-loss (triggers market order at stop price)
ORDER_TYPE_TRAILING_STOP = 5  # trailing stop (needs trailDistance)
ORDER_TYPE_JOIN_BID = 6  # join bid/ask
ORDER_TYPE_STOP = 4  # alias for backward compat


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

            # Select account: explicit ID > PRAC account > first
            target = self._config.account_id
            if target:
                acct = next((a for a in accounts if a["id"] == target), None)
                if not acct:
                    log.error("TopstepX: account_id %d not found in %s", target, [a["id"] for a in accounts])
                    return False
            else:
                acct = next((a for a in accounts if "PRAC" in a.get("name", "")), accounts[0])

            self._account_id = acct["id"]
            self._account_name = acct.get("name", "")
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
                # Auth/validate response uses "newToken" (NOT "token" — Auth/loginKey uses
                # "token"). Asymmetric per the official Swagger schema. Fetching "token"
                # silently returned None and forced a fallback full re-auth every cycle.
                new_token = body.get("newToken")
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

    # Prefix stamped on every Order/place so arnold's own orders are
    # distinguishable from any concurrent session on the account.
    # TopstepX rejects duplicate customTag values (account permanent
    # violation), so each order gets a unique suffix while preserving the
    # arnold-prod prefix for fingerprinting in Order/search results.
    _ORDER_TAG_PREFIX = "arnold-prod"

    @classmethod
    def _new_order_tag(cls) -> str:
        # nanosecond epoch is monotonically increasing within a process and
        # across container restarts; collisions across two arnold instances
        # on the same account would still be vanishingly rare.
        return f"{cls._ORDER_TAG_PREFIX}-{time.time_ns()}"

    async def place_market_order(self, action: str, quantity: int) -> dict:
        """Place a market order. action: 'Buy' or 'Sell'."""
        payload = {
            "accountId": self._account_id,
            "contractId": self._config.contract_id,
            "type": ORDER_TYPE_MARKET,
            "side": self._side(action),
            "size": quantity,
            "customTag": self._new_order_tag(),
        }
        result = await self._post("/api/Order/place", payload)
        log.info("Market order placed: %s %d %s → %s", action, quantity, self._config.contract_id, result)
        return result

    async def place_stop_order(self, action: str, quantity: int, stop_price: float) -> dict:
        """Place a stop-market order (stop-loss). action: 'Buy' or 'Sell'."""
        payload = {
            "accountId": self._account_id,
            "contractId": self._config.contract_id,
            "type": ORDER_TYPE_STOP_MARKET,
            "side": self._side(action),
            "size": quantity,
            "stopPrice": stop_price,
            "customTag": self._new_order_tag(),
        }
        result = await self._post("/api/Order/place", payload)
        log.info("Stop order placed: %s %d @ %.2f → %s", action, quantity, stop_price, result)
        return result

    async def place_limit_order(self, action: str, quantity: int, limit_price: float) -> dict:
        """Place a limit order (profit target). action: 'Buy' or 'Sell'."""
        payload = {
            "accountId": self._account_id,
            "contractId": self._config.contract_id,
            "type": ORDER_TYPE_LIMIT,
            "side": self._side(action),
            "size": quantity,
            "limitPrice": limit_price,
            "customTag": self._new_order_tag(),
        }
        result = await self._post("/api/Order/place", payload)
        log.info("Limit order placed: %s %d @ %.2f → %s", action, quantity, limit_price, result)
        return result

    async def modify_order(self, order_id: int, **kwargs) -> dict:
        """Modify an existing order. Pass stopPrice= and/or limitPrice=."""
        payload = {
            "accountId": self._account_id,
            "orderId": order_id,
        }
        if "stop_price" in kwargs:
            payload["stopPrice"] = kwargs["stop_price"]
        if "limit_price" in kwargs:
            payload["limitPrice"] = kwargs["limit_price"]
        result = await self._post("/api/Order/modify", payload)
        log.info("Order %d modified: %s → %s", order_id, kwargs, result)
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

    async def search_open_positions(self) -> list[dict]:
        """Alias for get_positions — used by tracker_reconciler on bootstrap."""
        return await self.get_positions()

    async def get_orders(self) -> list[dict]:
        """Get all working orders."""
        data = await self._post("/api/Order/searchOpen", {"accountId": self._account_id})
        return data if isinstance(data, list) else data.get("orders", [])

    async def search_open_orders(self) -> list[dict]:
        """Alias for get_orders — used by tracker_reconciler on bootstrap."""
        return await self.get_orders()

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()
