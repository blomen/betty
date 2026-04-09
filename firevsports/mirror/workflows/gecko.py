"""GeckoWorkflow — API-based balance for Gecko V2 platform providers.

Covers: spelklubben, betsson, betsafe, nordicbet, bethard.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import ProviderWorkflow, WorkflowMode, PlacementResult, HistoryEntry

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


class GeckoWorkflow(ProviderWorkflow):
    platform = "gecko_v2"

    def __init__(self, provider_id: str, domain: str, mode: WorkflowMode = WorkflowMode.GUIDED):
        super().__init__(provider_id, domain, mode)

    def _wallets_url(self) -> str:
        return f"https://cloud-api.{self.domain}/wallets"

    # ------------------------------------------------------------------
    # Login / balance
    # ------------------------------------------------------------------

    async def check_login(self, page: "Page") -> bool:
        """Check login via Gecko wallets API."""
        result = await self._evaluate_api(page, self._wallets_url())
        if result is None or "__error" in (result or {}):
            return False
        return True

    async def sync_balance(self, page: "Page") -> float:
        """Read balance from Gecko wallets API — Balances.SEK.Real.Balance."""
        result = await self._evaluate_api(page, self._wallets_url())
        if result is None or "__error" in (result or {}):
            return -1
        try:
            return float(result["Balances"]["SEK"]["Real"]["Balance"])
        except (KeyError, TypeError, ValueError):
            logger.warning(f"[{self.provider_id}] Unexpected wallets response: {result}")
            return -1

    # ------------------------------------------------------------------
    # History / navigation / placement — interceptor handles
    # ------------------------------------------------------------------

    async def sync_history(self, page: "Page") -> list[HistoryEntry]:
        """No-op — interceptor handles history."""
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
