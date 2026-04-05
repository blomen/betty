"""PinnacleWorkflow — REST API balance/history, manual bet placement."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import ProviderWorkflow, WorkflowMode, PlacementResult, HistoryEntry

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

_WALLET_URL = "https://api.arcadia.pinnacle.se/0.1/wallet/balance"
_BETS_URL = "https://api.arcadia.pinnacle.se/0.1/bets"
_SEARCH_URL = "https://www.pinnacle.se/en/search"


class PinnacleWorkflow(ProviderWorkflow):
    platform = "pinnacle"

    def __init__(self, provider_id: str, domain: str, mode: WorkflowMode = WorkflowMode.GUIDED):
        super().__init__(provider_id, domain, mode)

    # ------------------------------------------------------------------
    # Login / balance
    # ------------------------------------------------------------------

    async def check_login(self, page: "Page") -> bool:
        """Check login via wallet balance API (inherits session cookies)."""
        result = await self._evaluate_api(page, _WALLET_URL)
        if result is None or "__error" in (result or {}):
            return False
        return True

    async def sync_balance(self, page: "Page") -> float:
        """Read balance from Pinnacle wallet API."""
        result = await self._evaluate_api(page, _WALLET_URL)
        if result is None or "__error" in (result or {}):
            return -1
        try:
            return float(result.get("amount", -1))
        except (TypeError, ValueError):
            return -1

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    async def sync_history(self, page: "Page") -> list[HistoryEntry]:
        """Read bet history from Pinnacle bets API."""
        result = await self._evaluate_api(page, _BETS_URL)
        if not result or "__error" in (result or {}):
            return []

        entries: list[HistoryEntry] = []
        bets = result if isinstance(result, list) else result.get("bets", [])
        for b in bets:
            settled_at = b.get("settledAt")
            if not settled_at:
                continue  # Skip unsettled

            risk = float(b.get("riskAmount", 0))
            win = float(b.get("winAmount", 0))
            price = float(b.get("price", 0))

            # Determine status from amounts
            if win > risk:
                status = "won"
            elif win == 0:
                status = "lost"
            elif win == risk:
                status = "void"
            else:
                status = "cashout"

            selections = b.get("selections", [])
            event_name = selections[0].get("eventName", "") if selections else ""
            market = selections[0].get("marketType", "") if selections else ""
            outcome = selections[0].get("outcomeType", "") if selections else ""

            entries.append(HistoryEntry(
                provider_bet_id=str(b.get("id", "")),
                event_name=event_name,
                market=market,
                outcome=outcome,
                odds=price,
                stake=risk,
                status=status,
                payout=win if win > 0 else None,
            ))

        return entries

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    async def navigate_to_event(self, page: "Page", bet) -> bool:
        """Navigate to Pinnacle search page for this event."""
        home = getattr(bet, "display_home", "")
        if not home:
            logger.warning(f"[{self.provider_id}] No display_home on bet {bet.bet_id}")
            return False

        url = f"{_SEARCH_URL}/{home}/"
        logger.info(f"[{self.provider_id}] Navigating to {url}")

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            return True
        except Exception as e:
            logger.warning(f"[{self.provider_id}] navigate_to_event failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Bet placement
    # ------------------------------------------------------------------

    async def place_bet(self, page: "Page", bet, stake: float) -> PlacementResult:
        """Manual placement — interceptor catches POST /0.1/bets/straight."""
        return PlacementResult(
            status="manual",
            bet_id=bet.bet_id,
            actual_stake=stake,
            reason="interceptor_placement",
        )

    # ------------------------------------------------------------------
    # Live price
    # ------------------------------------------------------------------

    async def check_live_price(self, page: "Page", bet) -> float | None:
        """Read live odds from Pinnacle search results DOM and compute edge."""
        from ...analysis.value import compute_edge

        fair_odds = getattr(bet, "fair_odds", None)
        if not fair_odds:
            return None

        home = getattr(bet, "display_home", "").lower()
        away = getattr(bet, "display_away", "").lower()

        try:
            # Read odds from search result rows
            odds_data = await page.evaluate("""() => {
                const rows = document.querySelectorAll('[class*="market-row"], [class*="matchup"], [class*="event-row"]');
                const results = [];
                for (const row of rows) {
                    const text = (row.textContent || '').toLowerCase();
                    const buttons = row.querySelectorAll('button, [class*="price"]');
                    const prices = [];
                    for (const btn of buttons) {
                        const t = (btn.textContent || '').trim();
                        const m = t.match(/([\\d.]+)/);
                        if (m) prices.push(parseFloat(m[1]));
                    }
                    if (prices.length >= 2) {
                        results.push({text, prices});
                    }
                }
                return results;
            }""")

            if not odds_data:
                return None

            # Find the row matching our event
            for row in odds_data:
                text = row.get("text", "")
                prices = row.get("prices", [])
                if (home[:4] in text or away[:4] in text) and len(prices) >= 2:
                    outcome = getattr(bet, "original_outcome", getattr(bet, "outcome", ""))
                    if outcome == "home":
                        live_odds = prices[0]
                    elif outcome == "away":
                        live_odds = prices[1] if len(prices) > 1 else prices[-1]
                    elif outcome == "draw" and len(prices) >= 3:
                        live_odds = prices[1]  # Home/Draw/Away layout
                    else:
                        live_odds = prices[0]

                    if live_odds and live_odds > 1:
                        return compute_edge("pinnacle", live_odds, fair_odds)

            return None
        except Exception as e:
            logger.warning(f"[{self.provider_id}] check_live_price failed: {e}")
            return None
