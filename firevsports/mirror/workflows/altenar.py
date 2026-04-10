"""AltenarWorkflow — API-based balance for Altenar-platform providers.

Covers: campobet, quickcasino, betinia, swiper, lodur, dbet.

Navigation: sportRoutingParams query param on /sv/sport
Price reading: cached from GetEventDetails intercepted responses
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from .base import ProviderWorkflow, WorkflowMode, PlacementResult, HistoryEntry

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

# Altenar market typeId → our canonical market type
_MARKET_TYPE_MAP = {
    1: "1x2", 186: "moneyline", 219: "moneyline", 251: "moneyline",
    406: "moneyline", 30001: "moneyline",
    18: "total", 189: "total", 225: "total", 238: "total",
    258: "total", 412: "total",
    16: "spread", 187: "spread", 223: "spread", 237: "spread",
    256: "spread", 410: "spread",
}

# Altenar odd typeId → our outcome
_ODD_TYPE_MAP = {
    1: "home", 2: "draw", 3: "away",
    1714: "home", 1715: "away",
    12: "over", 13: "under",
}


class AltenarWorkflow(ProviderWorkflow):
    platform = "altenar"

    # Integration IDs for Altenar API calls
    _INTEGRATION_MAP = {
        "campobet": "campose",
        "quickcasino": "quickcasinose",
        "betinia": "betiniase2",
        "swiper": "swiperse",
        "lodur": "lodurse",
        "dbet": "dbetse",
    }

    def __init__(self, provider_id: str, domain: str, mode: WorkflowMode = WorkflowMode.GUIDED):
        super().__init__(provider_id, domain, mode)
        self._integration = self._INTEGRATION_MAP.get(provider_id, provider_id)
        # Cache for GetEventDetails responses (set by interceptor via service)
        self._event_details_cache: dict[str, tuple[dict, float]] = {}  # event_id → (data, timestamp)

    def _balance_url(self) -> str:
        return f"https://{self.domain}/sv/api/v3/account/balance"

    # ------------------------------------------------------------------
    # Login / balance
    # ------------------------------------------------------------------

    async def check_login(self, page: "Page") -> bool:
        result = await self._evaluate_api(page, self._balance_url())
        if result is None or "__error" in (result or {}):
            return False
        # Must have actual balance data — not just a non-error response
        try:
            float(result["cash"]["total"])
            return True
        except (KeyError, TypeError, ValueError):
            return False

    async def sync_balance(self, page: "Page") -> float:
        result = await self._evaluate_api(page, self._balance_url())
        if result is None or "__error" in (result or {}):
            return -1
        try:
            return float(result["cash"]["total"])
        except (KeyError, TypeError, ValueError):
            logger.warning(f"[{self.provider_id}] Unexpected balance response: {result}")
            return -1

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    async def sync_history(self, page: "Page") -> list[HistoryEntry]:
        """Navigate to bet history and click RÄTTATS tab to trigger settled bets API."""
        import asyncio
        history_url = f"https://{self.domain}/sv/sport?sportRoutingParams=page~betHistory"
        try:
            current = page.url or ""
            if "betHistory" not in current:
                await page.goto(history_url, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(2)

            # Click RÄTTATS tab to load settled bets (shadow DOM)
            clicked = await page.evaluate("""
                () => {
                    const stb = document.querySelector('STB-SPORTSBOOK');
                    if (!stb || !stb.firstElementChild) return false;
                    const sr = stb.firstElementChild.shadowRoot;
                    if (!sr) return false;
                    const tabs = sr.querySelectorAll('button[class*="BetHistoryTab"]');
                    for (const tab of tabs) {
                        const text = (tab.textContent || '').trim().toLowerCase();
                        if (text === 'rättats' || text === 'settled') {
                            tab.click();
                            return true;
                        }
                    }
                    return false;
                }
            """)
            if clicked:
                await asyncio.sleep(2)  # Wait for settled bets API response

            logger.info(f"[{self.provider_id}] Scanned bet history for settlements")
        except Exception as e:
            logger.warning(f"[{self.provider_id}] Failed to navigate to bet history: {e}")
        return []  # Actual settlement handled by interceptor

    # ------------------------------------------------------------------
    # Navigation — sportRoutingParams URL pattern
    # ------------------------------------------------------------------

    async def navigate_to_event(self, page: "Page", bet) -> bool:
        """Navigate via sportRoutingParams query param.

        URL pattern: {domain}/sv/sport?sportRoutingParams=page~event__sportId~{s}__categoryIds~{c}__championshipIds~{ch}__eventId~{e}
        All IDs come from provider_meta stored during extraction.
        """
        eid = getattr(bet, "altenar_event_id", None)
        if not eid:
            logger.warning(f"[{self.provider_id}] No altenar_event_id for navigation")
            return False

        sid = getattr(bet, "altenar_sport_id", "")
        cid = getattr(bet, "altenar_category_id", "")
        chid = getattr(bet, "altenar_championship_id", "")

        # Build sportRoutingParams
        params = f"page~event__eventId~{eid}"
        if sid:
            params = f"page~event__sportId~{sid}__categoryIds~{cid}__championshipIds~{chid}__eventId~{eid}"

        url = f"https://{self.domain}/sv/sport?sportRoutingParams={params}"

        try:
            current = page.url or ""
            if f"eventId~{eid}" in current:
                return True  # Already there

            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            logger.info(f"[{self.provider_id}] Navigated to event {eid}")
            return True
        except Exception as e:
            logger.warning(f"[{self.provider_id}] Navigate failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Price reading — from intercepted GetEventDetails cache
    # ------------------------------------------------------------------

    def cache_event_details(self, event_id: str, data: dict):
        """Called by service when GetEventDetails is intercepted."""
        self._event_details_cache[event_id] = (data, time.time())

    async def check_live_price(self, page: "Page", bet) -> float | None:
        """Read live odds from cached GetEventDetails response.

        The interceptor caches GetEventDetails responses when they flow through.
        After navigate_to_event, the widget auto-fetches event details.
        """
        from ...analysis.value import compute_edge

        eid = getattr(bet, "altenar_event_id", None)
        fair_odds = getattr(bet, "fair_odds", None)
        if not eid or not fair_odds:
            return None

        # Check cache (max 60s old)
        cached = self._event_details_cache.get(str(eid))
        if not cached:
            # Wait briefly for the widget to load event details after navigation
            import asyncio
            await asyncio.sleep(2)
            cached = self._event_details_cache.get(str(eid))

        if not cached:
            logger.debug(f"[{self.provider_id}] No cached event details for {eid}")
            return None

        data, ts = cached
        if time.time() - ts > 60:
            logger.debug(f"[{self.provider_id}] Cached event details too old for {eid}")
            return None

        return self._match_price(data, bet, fair_odds)

    def _match_price(self, data: dict, bet, fair_odds: float) -> float | None:
        """Match bet market/outcome against GetEventDetails response."""
        from ...analysis.value import compute_edge

        target_market = getattr(bet, "market", "")
        target_outcome = getattr(bet, "outcome", "")
        target_point = getattr(bet, "point", None)

        markets = data.get("markets", [])
        odds_list = data.get("odds", [])
        odds_by_id = {o["id"]: o for o in odds_list}

        for m in markets:
            our_market = _MARKET_TYPE_MAP.get(m.get("typeId"))
            if not our_market:
                continue
            if our_market != target_market and not (
                {our_market, target_market} <= {"1x2", "moneyline"}
            ):
                continue

            flat_ids = [oid for group in m.get("desktopOddIds", [])
                        for oid in (group if isinstance(group, list) else [group])]

            for oid in flat_ids:
                odd = odds_by_id.get(oid)
                if not odd or odd.get("oddStatus") != 0:
                    continue

                our_outcome = _ODD_TYPE_MAP.get(odd.get("typeId"))
                if our_outcome != target_outcome:
                    continue

                if target_market in ("spread", "total") and target_point is not None:
                    odd_point = self._extract_point(odd.get("name", ""))
                    if odd_point is None or abs(odd_point - abs(target_point)) > 0.01:
                        continue

                live_price = odd.get("price")
                if not live_price or live_price <= 1:
                    continue

                edge = compute_edge(self.provider_id, live_price, fair_odds)
                logger.info(
                    f"[{self.provider_id}] Live: {getattr(bet, 'display_home', '?')} vs "
                    f"{getattr(bet, 'display_away', '?')} {target_outcome} @ {live_price:.2f} "
                    f"(fair {fair_odds:.2f}) edge={edge:.1f}%"
                )
                return edge

        return None

    @staticmethod
    def _extract_point(odd_name: str) -> float | None:
        """Extract point from odd name like 'Team (+1.5)' or 'Over 4.5'."""
        import re
        match = re.search(r'[(\s]([+-]?\d+\.?\d*)\)?$', odd_name.strip())
        if match:
            return abs(float(match.group(1)))
        return None

    # ------------------------------------------------------------------
    # Placement — auto-select outcome + fill stake, user confirms
    # ------------------------------------------------------------------

    async def place_bet(self, page: "Page", bet, stake: float) -> PlacementResult:
        """Auto-select outcome and fill stake in the Altenar betslip.

        1. Find the matching odds button by price in shadow DOM
        2. Click it to add to betslip
        3. Clear and fill the stake input
        4. Return 'manual' — user confirms by clicking 'Placera spel'
        """
        import asyncio

        target_odds = getattr(bet, "odds", 0)
        stake_str = f"{stake:.2f}"

        target_outcome = getattr(bet, "outcome", "")
        display_home = (getattr(bet, "display_home", "") or "").lower()
        display_away = (getattr(bet, "display_away", "") or "").lower()
        target_market = getattr(bet, "market", "")
        target_point = getattr(bet, "point", None)

        # Step 1: Click the matching odds button by outcome position
        # Altenar layout: first market group has home/away buttons in order
        # For totals: "Över X.5" / "Under X.5"
        clicked = await page.evaluate("""
            (args) => {
                const stb = document.querySelector('STB-SPORTSBOOK');
                if (!stb || !stb.firstElementChild) return { error: 'no_stb' };
                const sr = stb.firstElementChild.shadowRoot;
                if (!sr) return { error: 'no_shadow' };

                // Find all clickable odd containers (parent of OddValue divs)
                const oddValues = sr.querySelectorAll('div[class*="OddValue"]');
                const outcome = args.outcome;
                const market = args.market;
                const home = args.home;
                const away = args.away;

                for (const odd of oddValues) {
                    const container = odd.parentElement;
                    const text = (container.textContent || '').toLowerCase();
                    const price = parseFloat(odd.textContent.trim());
                    if (!price || price <= 1) continue;

                    let match = false;

                    if (market === 'total' || market === 'totals') {
                        // Match "över X.5" or "under X.5"
                        if (outcome === 'over' && text.includes('över')) match = true;
                        if (outcome === 'under' && text.includes('under')) match = true;
                    } else {
                        // moneyline/1x2: match by team name
                        if (outcome === 'home' && home && text.includes(home.substring(0, 6))) match = true;
                        if (outcome === 'away' && away && text.includes(away.substring(0, 6))) match = true;
                        if (outcome === 'draw' && (text.includes('oavgjort') || text.includes('draw'))) match = true;
                    }

                    if (match) {
                        container.click();
                        return { clicked: true, text: text.substring(0, 50), price };
                    }
                }
                return { clicked: false, count: oddValues.length };
            }
        """, {"outcome": target_outcome, "market": target_market,
              "home": display_home, "away": display_away})

        if not clicked or clicked.get("error"):
            reason = clicked.get("error", "unknown") if clicked else "eval_failed"
            logger.warning(f"[{self.provider_id}] Cannot auto-select: {reason}")
            return PlacementResult(status="manual", bet_id=bet.bet_id, actual_stake=stake, reason=reason)

        if not clicked.get("clicked"):
            logger.warning(f"[{self.provider_id}] Odds {target_odds} not found ({clicked.get('count', 0)} odds on page)")
            return PlacementResult(status="manual", bet_id=bet.bet_id, actual_stake=stake, reason="odds_not_found")

        logger.info(f"[{self.provider_id}] Clicked: {clicked.get('text', '?')} @ {clicked.get('price')}")
        await asyncio.sleep(1.5)  # Wait for betslip to appear

        # Step 2: Fill stake input
        filled = await page.evaluate(f"""
            () => {{
                const stb = document.querySelector('STB-SPORTSBOOK');
                const sr = stb && stb.firstElementChild && stb.firstElementChild.shadowRoot;
                if (!sr) return false;
                const inputs = sr.querySelectorAll('input[type="tel"]');
                for (const input of inputs) {{
                    if (input.offsetHeight > 0) {{
                        input.focus();
                        // Use native setter to trigger React/Preact state update
                        const setter = Object.getOwnPropertyDescriptor(
                            HTMLInputElement.prototype, 'value'
                        ).set;
                        setter.call(input, '');
                        input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        setter.call(input, '{stake_str}');
                        input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        return true;
                    }}
                }}
                return false;
            }}
        """)

        if filled:
            logger.info(f"[{self.provider_id}] Stake set to {stake_str} — user confirms")
        else:
            logger.warning(f"[{self.provider_id}] Could not fill stake input")

        return PlacementResult(
            status="manual",
            bet_id=bet.bet_id,
            actual_stake=stake,
            reason="auto_selected_user_confirms",
        )
