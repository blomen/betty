"""ManualWorkflow — fallback for unwired providers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import ProviderWorkflow, PlacementResult, HistoryEntry

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


class ManualWorkflow(ProviderWorkflow):
    platform = "manual"

    async def check_login(self, page: "Page") -> bool:
        return True  # Assume logged in if page is open

    async def sync_history(self, page: "Page") -> list[HistoryEntry]:
        return []  # Interceptor handles history

    async def sync_balance(self, page: "Page") -> float:
        return -1  # Signal unknown — fire window uses DB balance

    async def navigate_to_event(self, page: "Page", bet) -> bool:
        logger.info(f"[{self.provider_id}] Manual: navigate to {bet.display_home} vs {bet.display_away}")
        return True

    async def place_bet(self, page: "Page", bet, stake: float) -> PlacementResult:
        return PlacementResult(
            status="manual",
            bet_id=bet.bet_id,
            actual_stake=stake,
            reason="manual_placement",
        )
