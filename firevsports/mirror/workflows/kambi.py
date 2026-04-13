"""KambiWorkflow — WS-based guided workflow for Kambi platform providers.

Covers: unibet, leovegas, expekt, 888sport, speedybet, x3000, goldenbull, 1x2, betmgm.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .base import HistoryEntry, PlacementResult, ProviderWorkflow, WorkflowMode

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

# REST balance endpoint paths per Kambi operator
_BALANCE_ENDPOINTS: dict[str, str] = {
    "unibet": "/wallitt/mainbalance",
}

# GraphQL relay URLs per Kambi operator (for providers that use relay instead of REST)
_BALANCE_GRAPHQL: dict[str, str] = {
    "leovegas": "https://www.leovegas.com/api?relay",
}


def _parse_graphql_balance(data) -> float:
    """Extract totalAmount from GraphQL relay balance response. Returns -1 on failure."""
    try:
        relay = data
        if isinstance(data, list) and data:
            relay = data[0]
        if not isinstance(relay, dict):
            return -1
        bal = relay.get("data", {}).get("viewer", {}).get("user", {}).get("balance", {})
        if isinstance(bal, dict) and "totalAmount" in bal:
            return float(bal["totalAmount"])
    except (TypeError, ValueError, KeyError):
        pass
    return -1


class KambiWorkflow(ProviderWorkflow):
    platform = "kambi"

    def __init__(self, provider_id: str, domain: str, mode: WorkflowMode = WorkflowMode.GUIDED):
        super().__init__(provider_id, domain, mode)

    def _balance_rest_url(self) -> str | None:
        path = _BALANCE_ENDPOINTS.get(self.provider_id)
        if path and self.domain:
            return f"https://www.{self.domain}{path}"
        return None

    def _balance_graphql_url(self) -> str | None:
        return _BALANCE_GRAPHQL.get(self.provider_id)

    async def _fetch_graphql_balance(self, page: Page) -> float:
        """POST GraphQL relay and return totalAmount, or -1 on failure."""
        url = self._balance_graphql_url()
        if url is None:
            return -1
        try:
            result = await page.evaluate(f"""
                async () => {{
                    try {{
                        const r = await fetch("{url}", {{
                            method: "POST",
                            credentials: "include",
                            headers: {{"Content-Type": "application/json"}},
                            body: JSON.stringify({{
                                query: "{{ viewer {{ user {{ balance {{ totalAmount currency }} }} }} }}"
                            }})
                        }});
                        if (!r.ok) return null;
                        return await r.json();
                    }} catch (e) {{ return null; }}
                }}
            """)
            return _parse_graphql_balance(result)
        except Exception as e:
            logger.warning(f"[{self.provider_id}] GraphQL balance fetch failed: {e}")
            return -1

    # ------------------------------------------------------------------
    # Login / balance
    # ------------------------------------------------------------------

    async def check_login(self, page: Page) -> bool:
        """Try REST balance endpoint (unibet), then GraphQL relay (leovegas)."""
        rest_url = self._balance_rest_url()
        if rest_url:
            result = await self._evaluate_api(page, rest_url)
            return bool(result and "__error" not in result)

        graphql_url = self._balance_graphql_url()
        if graphql_url:
            bal = await self._fetch_graphql_balance(page)
            return bal >= 0

        # No known endpoint — assume logged in if tab is open
        return True

    async def sync_balance(self, page: Page) -> float:
        """Try REST balance endpoint, then GraphQL relay, then return -1."""
        rest_url = self._balance_rest_url()
        if rest_url:
            result = await self._evaluate_api(page, rest_url)
            if result and "__error" not in result:
                try:
                    if "mainBalance" in result:
                        return float(result["mainBalance"]["amount"])
                    for key in ("balance", "amount", "cash"):
                        if key in result:
                            val = result[key]
                            if isinstance(val, dict):
                                return float(val.get("amount", val.get("total", -1)))
                            return float(val)
                except (KeyError, TypeError, ValueError):
                    logger.warning(f"[{self.provider_id}] Unexpected REST balance response")
            return -1

        return await self._fetch_graphql_balance(page)

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    async def sync_history(self, page: Page) -> list[HistoryEntry]:
        """Navigate to bet history page — service.py SSR scraper handles parsing."""
        hist_url = f"https://www.{self.domain}/betting/sports/bethistory"
        if "/bethistory" not in (page.url or ""):
            try:
                await page.goto(hist_url, wait_until="networkidle", timeout=15000)
                await asyncio.sleep(3)
            except Exception as e:
                logger.warning(f"[{self.provider_id}] Could not navigate to bet history: {e}")
        return []

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    async def navigate_to_event(self, page: Page, bet) -> bool:
        """Navigate to Kambi event page using kambi_event_id from provider_meta."""
        kambi_eid = getattr(bet, "kambi_event_id", "") or getattr(bet, "altenar_event_id", "")
        if not kambi_eid:
            return True  # No ID — user navigates manually, still counts as success
        if kambi_eid in (page.url or ""):
            return True  # Already on the right page
        url = f"https://www.{self.domain}/betting/sports/event/{kambi_eid}"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(1)
            logger.info(f"[{self.provider_id}] Navigated to event {kambi_eid}")
            return True
        except Exception as e:
            logger.warning(f"[{self.provider_id}] navigate_to_event failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Placement — Phase 2, filled after live discovery session
    # ------------------------------------------------------------------

    async def prep_betslip(self, page: Page, bet, stake: float) -> PlacementResult:
        """Phase 2 placeholder — implemented after live discovery of Kambi Widget API."""
        return PlacementResult(
            status="no_prep",
            bet_id=getattr(bet, "bet_id", 0),
            reason="phase2_not_implemented",
        )

    async def confirm_bet(self, page: Page) -> PlacementResult:
        """Phase 2 placeholder — implemented after live discovery of Place button selector."""
        return PlacementResult(status="manual", bet_id=0, reason="user_confirms_on_site")

    async def place_bet(self, page: Page, bet, stake: float) -> PlacementResult:
        """Manual placement fallback."""
        return PlacementResult(
            status="manual",
            bet_id=getattr(bet, "bet_id", 0),
            actual_stake=stake,
            reason="manual_placement",
        )
