"""KambiWorkflow — WS-based guided workflow for Kambi platform providers.

Covers: unibet, leovegas, expekt, 888sport, speedybet, x3000, goldenbull, 1x2, betmgm.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import ProviderWorkflow, WorkflowMode, PlacementResult, HistoryEntry

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

# Known balance API endpoints per Kambi operator
_BALANCE_ENDPOINTS: dict[str, str] = {
    "unibet": "/wallitt/mainbalance",
}


class KambiWorkflow(ProviderWorkflow):
    platform = "kambi"

    def __init__(self, provider_id: str, domain: str, mode: WorkflowMode = WorkflowMode.GUIDED):
        super().__init__(provider_id, domain, mode)

    def _balance_url(self) -> str | None:
        path = _BALANCE_ENDPOINTS.get(self.provider_id)
        if path and self.domain:
            return f"https://{self.domain}{path}"
        return None

    # ------------------------------------------------------------------
    # Login / balance
    # ------------------------------------------------------------------

    async def check_login(self, page: "Page") -> bool:
        """Try known balance endpoint if available, otherwise assume logged in."""
        url = self._balance_url()
        if url is None:
            return True  # No known endpoint — assume logged in if tab is open

        result = await self._evaluate_api(page, url)
        if result is None or "__error" in (result or {}):
            return False
        return True

    async def sync_balance(self, page: "Page") -> float:
        """Try known balance endpoint, otherwise return -1 (unknown)."""
        url = self._balance_url()
        if url is None:
            return -1

        result = await self._evaluate_api(page, url)
        if result is None or "__error" in (result or {}):
            return -1
        try:
            # Unibet returns {mainBalance: {amount: 123.45, ...}}
            if "mainBalance" in result:
                return float(result["mainBalance"]["amount"])
            # Generic fallback: look for common keys
            for key in ("balance", "amount", "cash"):
                if key in result:
                    val = result[key]
                    if isinstance(val, dict):
                        return float(val.get("amount", val.get("total", -1)))
                    return float(val)
            return -1
        except (KeyError, TypeError, ValueError):
            logger.warning(f"[{self.provider_id}] Unexpected balance response: {result}")
            return -1

    # ------------------------------------------------------------------
    # History / navigation / placement — WS-based, needs investigation
    # ------------------------------------------------------------------

    async def sync_history(self, page: "Page") -> list[HistoryEntry]:
        """No-op — WS-based history, needs further investigation."""
        return []

    async def navigate_to_event(self, page: "Page", bet) -> bool:
        """User navigates manually."""
        return True

    async def place_bet(self, page: "Page", bet, stake: float) -> PlacementResult:
        """Manual placement — user places via provider UI."""
        return PlacementResult(
            status="manual",
            bet_id=bet.bet_id,
            actual_stake=stake,
            reason="manual_placement",
        )
