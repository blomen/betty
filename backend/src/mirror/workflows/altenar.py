"""AltenarWorkflow — API-based balance for Altenar-platform providers.

Covers: campobet, quickcasino, betinia, swiper, lodur, dbet.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import ProviderWorkflow, WorkflowMode, PlacementResult, HistoryEntry

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


class AltenarWorkflow(ProviderWorkflow):
    platform = "altenar"

    def __init__(self, provider_id: str, domain: str, mode: WorkflowMode = WorkflowMode.GUIDED):
        super().__init__(provider_id, domain, mode)

    def _balance_url(self) -> str:
        return f"https://{self.domain}/sv/api/v3/account/balance"

    # ------------------------------------------------------------------
    # Login / balance
    # ------------------------------------------------------------------

    async def check_login(self, page: "Page") -> bool:
        """Check login via Altenar balance API."""
        result = await self._evaluate_api(page, self._balance_url())
        if result is None or "__error" in (result or {}):
            return False
        return True

    async def sync_balance(self, page: "Page") -> float:
        """Read balance from Altenar account API — result.cash.total."""
        result = await self._evaluate_api(page, self._balance_url())
        if result is None or "__error" in (result or {}):
            return -1
        try:
            return float(result["cash"]["total"])
        except (KeyError, TypeError, ValueError):
            logger.warning(f"[{self.provider_id}] Unexpected balance response: {result}")
            return -1

    # ------------------------------------------------------------------
    # History / navigation / placement — interceptor handles
    # ------------------------------------------------------------------

    async def sync_history(self, page: "Page") -> list[HistoryEntry]:
        """No-op — interceptor handles via widgetBetHistory."""
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
