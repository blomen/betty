"""GeckoWorkflow — API-based balance for Gecko V2 platform providers.

Covers: spelklubben, betsson, betsafe, nordicbet, bethard.
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

    def __init__(self, provider_id: str, domain: str, mode: WorkflowMode = WorkflowMode.GUIDED):
        super().__init__(provider_id, domain, mode)

    def _wallets_url(self) -> str:
        return f"https://cloud-api.{self.domain}/wallets"

    # ------------------------------------------------------------------
    # Login / balance
    # ------------------------------------------------------------------

    async def check_login(self, page: Page) -> bool:
        """Check login via Gecko wallets API."""
        result = await self._evaluate_api(page, self._wallets_url())
        if result is None or "__error" in (result or {}):
            return False
        return True

    async def sync_balance(self, page: Page) -> float:
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

    async def sync_history(self, page: Page) -> list[HistoryEntry]:
        """No-op — interceptor handles history."""
        return []

    async def navigate_to_event(self, page: Page, bet) -> bool:
        """Navigate to Gecko V2 event page using gecko_event_id from provider_meta.

        URL pattern: {site_url}{init_path}?eventId=f-{gecko_event_id}
        Verified: the main site passes eventId to the sportsbook iframe automatically.
        """
        gecko_eid = getattr(bet, "gecko_event_id", "")
        if not gecko_eid:
            return True  # No ID — user navigates manually

        if f"eventId={gecko_eid}" in (page.url or "") or f"eventId=f-{gecko_eid}" in (page.url or ""):
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

    async def place_bet(self, page: Page, bet, stake: float) -> PlacementResult:
        """Manual placement — user places via provider UI."""
        return PlacementResult(
            status="manual",
            bet_id=bet.bet_id,
            actual_stake=stake,
            reason="manual_placement",
        )
