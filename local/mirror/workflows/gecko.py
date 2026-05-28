"""GeckoWorkflow — navigation-only stub for Gecko V2 soft books.

Covers: spelklubben, betsson, betsafe, nordicbet, bethard.

Soft providers are fully manual since the DOM/API automation rewrite.
This workflow exists only to:
  1. Tell the browser which tab to open (home_url)
  2. Navigate that tab to a specific event when the user clicks an arb row
     (navigate_to_event)

Everything else is done manually via PlayPage inline controls.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .base import HistoryEntry, PlacementResult, ProviderWorkflow, WorkflowMode

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

# Betting page path per provider (default: /sv/odds for betsson/betsafe/nordicbet)
_INIT_PATHS: dict[str, str] = {
    "spelklubben": "/sv/betting",
    "bethard": "/sv/sports",
}


class GeckoWorkflow(ProviderWorkflow):
    platform = "gecko_v2"

    def __init__(
        self, provider_id: str, domain: str, mode: WorkflowMode = WorkflowMode.GUIDED
    ):
        super().__init__(provider_id, domain, mode)

    @property
    def home_url(self) -> str:
        """Open the sportsbook landing — skip casino lobby."""
        path = _INIT_PATHS.get(self.provider_id, "/sv/odds")
        return f"https://www.{self.domain}{path}"

    # ------------------------------------------------------------------
    # No-op stubs for abstract methods. Soft providers are fully manual.
    # ------------------------------------------------------------------

    async def check_login(self, page: Page) -> bool:
        return False

    async def sync_balance(self, page: Page) -> float:
        return -1

    async def sync_history(self, page: Page) -> list[HistoryEntry]:
        return []

    async def place_bet(self, page: Page, bet, stake: float) -> PlacementResult:
        return PlacementResult(
            status="manual",
            bet_id=getattr(bet, "bet_id", 0) or 0,
            actual_stake=stake,
            reason="soft_provider_manual_only",
        )

    # ------------------------------------------------------------------
    # Navigation — Gecko V2 event URL via eventId query param.
    # ------------------------------------------------------------------

    async def navigate_to_event(self, page: Page, bet) -> bool:
        gecko_eid = getattr(bet, "gecko_event_id", "")
        if not gecko_eid:
            logger.info(
                f"[{self.provider_id}] No gecko_event_id — user navigates manually"
            )
            return True

        if f"eventId={gecko_eid}" in (page.url or "") or f"eventId=f-{gecko_eid}" in (
            page.url or ""
        ):
            return True  # Already on this event

        init_path = _INIT_PATHS.get(self.provider_id, "/sv/odds")
        # Event IDs from the Gecko API already include the f- prefix
        eid_param = gecko_eid if gecko_eid.startswith("f-") else f"f-{gecko_eid}"
        url = f"https://www.{self.domain}{init_path}?eventId={eid_param}"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(1)
            logger.info(f"[{self.provider_id}] Navigated to event {gecko_eid}")
            return True
        except Exception as e:
            logger.warning(f"[{self.provider_id}] navigate_to_event failed: {e}")
            return False
