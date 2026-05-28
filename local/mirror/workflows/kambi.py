"""KambiWorkflow — navigation-only stub for Kambi-platform soft books.

Covers: unibet, leovegas, expekt, 888sport, speedybet, x3000, goldenbull,
1x2, betmgm, mrgreen.

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


def _g(obj, key, default=None):
    """Get attribute from object or dict — handles both play loop dicts and BetProxy objects."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


logger = logging.getLogger(__name__)


class KambiWorkflow(ProviderWorkflow):
    platform = "kambi"

    # Provider-specific betting page paths (some use /betting, others /betting/sports).
    _BETTING_PATHS: dict[str, str] = {
        "leovegas": "/sv-se/betting",
        "unibet": "/betting/sports",
    }

    def __init__(
        self, provider_id: str, domain: str, mode: WorkflowMode = WorkflowMode.GUIDED
    ):
        super().__init__(provider_id, domain, mode)

    def _betting_url(self) -> str:
        path = self._BETTING_PATHS.get(self.provider_id, "/betting/sports")
        return f"https://{self.domain}{path}"

    @property
    def home_url(self) -> str:
        """Open betting page directly — skip casino landing."""
        return self._betting_url()

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
            bet_id=_g(bet, "bet_id", 0) or 0,
            actual_stake=stake,
            reason="soft_provider_manual_only",
        )

    # ------------------------------------------------------------------
    # Navigation — Kambi widget navigateClient API with URL fallback.
    # ------------------------------------------------------------------

    async def navigate_to_event(self, page: Page, bet) -> bool:
        """Navigate to a Kambi event via the widget API, falling back to URL."""
        kambi_eid = _g(bet, "kambi_event_id", "")
        if not kambi_eid:
            meta = _g(bet, "provider_meta") or {}
            kambi_eid = meta.get("event_id", "")
        if not kambi_eid:
            logger.warning(f"[{self.provider_id}] No kambi event_id for navigation")
            return False

        current = page.url or ""
        if kambi_eid in current:
            return True

        betting_url = self._betting_url()
        if "/betting" not in current:
            try:
                await page.goto(
                    betting_url, wait_until="domcontentloaded", timeout=15000
                )
                await asyncio.sleep(2)
            except Exception as e:
                logger.warning(
                    f"[{self.provider_id}] Could not navigate to betting page: {e}"
                )
                return False

        # Prefer the Kambi widget navigateClient API — works across all
        # Kambi white-labels regardless of URL structure.
        try:
            result = await page.evaluate(f"""
                async () => {{
                    if (window.KambiWidget && window.KambiWidget.navigateClient) {{
                        window.KambiWidget.navigateClient('#/event/{kambi_eid}');
                        return 'kambi_widget';
                    }}
                    if (window.location.hash !== undefined) {{
                        window.location.hash = '#/event/{kambi_eid}';
                        return 'hash';
                    }}
                    return null;
                }}
            """)
            if result:
                await asyncio.sleep(2)
                logger.info(
                    f"[{self.provider_id}] Navigated to event {kambi_eid} via {result}"
                )
                return True
        except Exception as e:
            logger.warning(f"[{self.provider_id}] Widget navigation failed: {e}")

        # Fallback: direct URL (works on unibet-style sites)
        url = f"{betting_url}/event/{kambi_eid}"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(1)
            logger.info(
                f"[{self.provider_id}] Navigated to event {kambi_eid} via direct URL"
            )
            return True
        except Exception as e:
            logger.warning(f"[{self.provider_id}] navigate_to_event failed: {e}")
            return False
