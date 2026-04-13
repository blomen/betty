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
    # Placement — two-phase: prep (auto-fill) then confirm (click submit)
    #
    # Discovery (2026-04-14, Unibet/Kambi):
    #   - Add outcome:  window.isolatedBetslip.addOutcomeIds([outcomeId])
    #   - Show betslip: window.isolatedBetslip.showBetslip()
    #   - Stake input:  input.mod-KambiBC-js-stake-input  (main frame DOM, not iframe)
    #   - Place button: button.mod-KambiBC-betslip__place-bet-btn
    #   - KambiWidget.api is EMPTY — isolatedBetslip is the correct API
    # ------------------------------------------------------------------

    _STAKE_SELECTOR = "input.mod-KambiBC-js-stake-input"
    _PLACE_SELECTOR = "button.mod-KambiBC-betslip__place-bet-btn"

    async def prep_betslip(self, page: Page, bet, stake: float) -> PlacementResult:
        """Auto-select outcome + fill stake via Kambi isolatedBetslip API.

        Calls addOutcomeIds([outcomeId]) to add the bet to the betslip, then
        fills the stake input. The betslip renders in the main frame DOM.
        """
        bet_id = getattr(bet, "bet_id", 0) or 0
        target_odds = getattr(bet, "odds", None)
        outcome_id = getattr(bet, "kambi_outcome_id", "")

        if not outcome_id:
            logger.warning(f"[{self.provider_id}] prep_betslip: no kambi_outcome_id — manual placement")
            return PlacementResult(
                status="prepped",
                bet_id=bet_id,
                actual_odds=target_odds,
                actual_stake=stake,
                reason="no_outcome_id_manual",
            )

        # 1. Add outcome to betslip via Kambi JS API
        try:
            await page.evaluate(f"""
                () => {{
                    const ib = window.isolatedBetslip;
                    if (ib) {{
                        ib.addOutcomeIds([{int(outcome_id)}]);
                        ib.showBetslip();
                    }}
                }}
            """)
            logger.info(
                f"[{self.provider_id}] addOutcomeIds({outcome_id}) called — "
                f"{getattr(bet, 'display_home', '?')} v {getattr(bet, 'display_away', '?')} "
                f"{getattr(bet, 'outcome', '')} @ {target_odds}"
            )
        except Exception as e:
            logger.warning(f"[{self.provider_id}] addOutcomeIds failed: {e}")
            return PlacementResult(status="failed", bet_id=bet_id, reason=f"add_outcome_failed: {e}")

        # 2. Wait for stake input to appear in DOM
        try:
            await page.wait_for_selector(self._STAKE_SELECTOR, timeout=10000)
        except Exception:
            logger.warning(f"[{self.provider_id}] prep_betslip: stake input not found after addOutcomeIds")
            return PlacementResult(status="failed", bet_id=bet_id, reason="stake_input_not_found")

        # 3. Fill stake — triple-click to clear existing value then fill
        stake_str = str(int(stake)) if stake == int(stake) else f"{stake:.2f}"
        try:
            stake_el = page.locator(self._STAKE_SELECTOR).first
            await stake_el.click(click_count=3, timeout=5000)
            await stake_el.fill(stake_str, timeout=5000)
            # Dispatch input/change so Kambi JS re-validates the value
            await page.evaluate("""
                () => {
                    const inp = document.querySelector('input.mod-KambiBC-js-stake-input');
                    if (inp) {
                        inp.dispatchEvent(new Event('input', {bubbles: true}));
                        inp.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                }
            """)
        except Exception as e:
            logger.warning(f"[{self.provider_id}] prep_betslip: stake fill failed: {e}")
            return PlacementResult(status="failed", bet_id=bet_id, reason=f"stake_fill_failed: {e}")

        logger.info(f"[{self.provider_id}] prep_betslip done — stake={stake_str}, outcomeId={outcome_id}")
        return PlacementResult(
            status="prepped",
            bet_id=bet_id,
            actual_odds=target_odds,
            actual_stake=stake,
            reason="kambi_selected",
        )

    async def confirm_bet(self, page: Page) -> PlacementResult:
        """Click the Place Bet button and wait for betslip to clear."""
        try:
            # Wait for button to be enabled (odds may need a moment to validate)
            await page.wait_for_selector(f"{self._PLACE_SELECTOR}:not([disabled])", timeout=8000)
            btn = page.locator(self._PLACE_SELECTOR).first
            await btn.click(timeout=5000)
            logger.info(f"[{self.provider_id}] confirm_bet: clicked place button")

            # Wait up to 4s for betslip outcome to be removed (placement success indicator)
            await asyncio.sleep(2)
            outcome_gone = await page.evaluate(
                "() => !document.querySelector('.mod-KambiBC-betslip-outcome__close-btn')"
            )
            if outcome_gone:
                return PlacementResult(status="placed", bet_id=0, reason="betslip_cleared")

            return PlacementResult(status="placed", bet_id=0, reason="clicked_place")

        except Exception as e:
            logger.warning(f"[{self.provider_id}] confirm_bet failed: {e}")
            return PlacementResult(status="manual", bet_id=0, reason=f"confirm_failed: {e}")

    async def place_bet(self, page: Page, bet, stake: float) -> PlacementResult:
        """Full two-phase placement (prep + confirm)."""
        prep = await self.prep_betslip(page, bet, stake)
        if prep.status != "prepped":
            return prep
        return await self.confirm_bet(page)
