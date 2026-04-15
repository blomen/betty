"""KambiWorkflow — WS-based guided workflow for Kambi platform providers.

Covers: unibet, leovegas, expekt, 888sport, speedybet, x3000, goldenbull, 1x2, betmgm.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .base import HistoryEntry, PlacementResult, PositionEntry, ProviderWorkflow, WorkflowMode

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


def _g(obj, key, default=None):
    """Get attribute from object or dict — handles both play loop dicts and BetProxy objects."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


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
            return f"https://{self.domain}{path}"
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
    # History — KSP betting API (api/v1/betting/bet-history)
    #
    # Discovery (2026-04-14): endpoint is sportsbook-feeds/betting-api/api/v1/betting/bet-history
    # Returns 400 without auth, 200 with session cookies (logged-in user).
    # betStatus param filters by OPEN / WON / LOST / VOID / CASHOUT.
    # ------------------------------------------------------------------

    _BET_HISTORY_BASE = "sportsbook-feeds/betting-api/api/v1/betting/bet-history"

    def _ksp_history_url(self, status: str, size: int = 50) -> str:
        return f"https://{self.domain}/{self._BET_HISTORY_BASE}?betStatus={status}&page=0&size={size}"

    def _parse_ksp_bets(self, data: dict, status_hint: str) -> list[HistoryEntry]:
        """Parse KSP bet-history API response into HistoryEntry list."""
        status_map = {
            "WON": "won",
            "WIN": "won",
            "LOST": "lost",
            "LOSS": "lost",
            "VOID": "void",
            "VOIDED": "void",
            "CASHOUT": "cashout",
            "CASH_OUT": "cashout",
            "OPEN": "pending",
            "PENDING": "pending",
        }
        bets = (
            data.get("bets") or data.get("content") or data.get("items") or (data.get("data") or {}).get("bets") or []
        )
        if not isinstance(bets, list):
            return []

        entries: list[HistoryEntry] = []
        for bet in bets:
            if not isinstance(bet, dict):
                continue
            try:
                raw_status = str(bet.get("status") or bet.get("betStatus") or status_hint).upper()
                mapped = status_map.get(raw_status, "pending")

                bet_id = str(bet.get("id") or bet.get("betId") or bet.get("couponId") or "")

                selections = bet.get("selections") or bet.get("legs") or bet.get("betOffers") or []
                event_name = outcome_name = market = ""
                odds = 0.0
                if isinstance(selections, list) and selections:
                    sel = selections[0]
                    home = sel.get("homeName") or sel.get("homeTeam") or ""
                    away = sel.get("awayName") or sel.get("awayTeam") or ""
                    event_name = (
                        sel.get("eventName")
                        or sel.get("event")
                        or (f"{home} v {away}".strip(" v ") if home or away else "")
                    )
                    outcome_name = sel.get("outcomeName") or sel.get("outcome") or ""
                    market = sel.get("marketName") or sel.get("market") or ""
                    odds = float(sel.get("odds") or sel.get("price") or sel.get("oddsDecimal") or 0)

                if not event_name:
                    event_name = str(bet.get("eventName") or bet.get("description") or "")
                if not odds:
                    odds = float(bet.get("odds") or bet.get("totalOdds") or 0)

                stake_raw = bet.get("stake") or {}
                stake = float(
                    (stake_raw.get("amount") if isinstance(stake_raw, dict) else stake_raw)
                    or bet.get("stakeAmount")
                    or 0
                )

                payout_raw = bet.get("payout") or bet.get("winnings") or {}
                payout = (
                    float(
                        (payout_raw.get("amount") if isinstance(payout_raw, dict) else payout_raw)
                        or bet.get("payoutAmount")
                        or 0
                    )
                    or None
                )

                entries.append(
                    HistoryEntry(
                        provider_bet_id=bet_id,
                        event_name=event_name,
                        market=market,
                        outcome=outcome_name,
                        odds=odds,
                        stake=stake,
                        status=mapped,
                        payout=payout,
                    )
                )
            except Exception as e:
                logger.debug(f"[{self.provider_id}] _parse_ksp_bets: skipped bet: {e}")

        return entries

    async def sync_history(self, page: Page) -> list[HistoryEntry]:
        """Fetch all bets (open + settled) from KSP betting API.

        Tries the REST endpoint first; falls back to navigating bet history page
        if the API is unreachable (no session / provider not wired).
        """
        all_entries: list[HistoryEntry] = []
        for status in ("OPEN", "WON", "LOST", "VOID", "CASHOUT"):
            try:
                result = await self._evaluate_api(page, self._ksp_history_url(status))
                if result and "__error" not in result:
                    entries = self._parse_ksp_bets(result, status)
                    all_entries.extend(entries)
            except Exception as e:
                logger.debug(f"[{self.provider_id}] sync_history {status}: {e}")

        if all_entries:
            logger.info(f"[{self.provider_id}] sync_history: {len(all_entries)} bets from API")
            return all_entries

        # Fallback: navigate to history page (pending_loop SSR path picks it up)
        hist_url = f"https://{self.domain}/betting/sports/bethistory"
        if "/bethistory" not in (page.url or ""):
            try:
                await page.goto(hist_url, wait_until="networkidle", timeout=15000)
                await asyncio.sleep(3)
            except Exception as e:
                logger.warning(f"[{self.provider_id}] Could not navigate to bet history: {e}")
        return []

    async def fetch_positions(self, page: Page) -> list[PositionEntry]:
        """Fetch open bets from KSP betting API."""
        try:
            result = await self._evaluate_api(page, self._ksp_history_url("OPEN"))
            if not result or "__error" in result:
                return []
            history = self._parse_ksp_bets(result, "OPEN")
            positions = [
                PositionEntry(
                    provider_bet_id=e.provider_bet_id,
                    event_name=e.event_name,
                    market=e.market,
                    outcome=e.outcome,
                    odds=e.odds,
                    stake=e.stake,
                )
                for e in history
            ]
            logger.info(f"[{self.provider_id}] fetch_positions: {len(positions)} open bets")
            return positions
        except Exception as e:
            logger.warning(f"[{self.provider_id}] fetch_positions failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    # Provider-specific betting page paths (some use /betting, others /betting/sports)
    _BETTING_PATHS: dict[str, str] = {
        "leovegas": "/sv-se/betting",
        "unibet": "/betting/sports",
    }

    def _betting_url(self) -> str:
        path = self._BETTING_PATHS.get(self.provider_id, "/betting/sports")
        return f"https://{self.domain}{path}"

    async def navigate_to_event(self, page: Page, bet) -> bool:
        """Navigate to Kambi event via widget navigateClient API.

        First ensures we're on the betting page, then uses the Kambi widget
        JS API to navigate to the event. This works across all white-labels
        regardless of their URL structure.
        """
        kambi_eid = _g(bet, "kambi_event_id", "")
        if not kambi_eid:
            meta = _g(bet, "provider_meta") or {}
            kambi_eid = meta.get("event_id", "")
        if not kambi_eid:
            logger.warning(f"[{self.provider_id}] No kambi event_id for navigation")
            return False

        # Check if already on this event (URL or widget state)
        current = page.url or ""
        if kambi_eid in current:
            return True

        # Ensure we're on the betting page first
        betting_url = self._betting_url()
        if "/betting" not in current:
            try:
                await page.goto(betting_url, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(2)
            except Exception as e:
                logger.warning(f"[{self.provider_id}] Could not navigate to betting page: {e}")
                return False

        # Use Kambi widget API to navigate to the event
        try:
            result = await page.evaluate(f"""
                async () => {{
                    // Try navigateClient (standard Kambi widget API)
                    if (window.KambiWidget && window.KambiWidget.navigateClient) {{
                        window.KambiWidget.navigateClient('#/event/{kambi_eid}');
                        return 'kambi_widget';
                    }}
                    // Try hash navigation (some sites use hash-based routing)
                    if (window.location.hash !== undefined) {{
                        window.location.hash = '#/event/{kambi_eid}';
                        return 'hash';
                    }}
                    return null;
                }}
            """)
            if result:
                await asyncio.sleep(2)
                logger.info(f"[{self.provider_id}] Navigated to event {kambi_eid} via {result}")
                return True
        except Exception as e:
            logger.warning(f"[{self.provider_id}] Widget navigation failed: {e}")

        # Fallback: try direct URL (works on unibet-style sites)
        url = f"{betting_url}/event/{kambi_eid}"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(1)
            logger.info(f"[{self.provider_id}] Navigated to event {kambi_eid} via direct URL")
            return True
        except Exception as e:
            logger.warning(f"[{self.provider_id}] navigate_to_event failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Live price — read odds from Kambi betslip DOM after outcome is selected
    # ------------------------------------------------------------------

    async def check_live_price(self, page: Page, bet) -> tuple[float | None, float | None]:
        """Read live odds from Kambi betslip DOM.

        Uses .mod-KambiBC-betslip-outcome__odds selector (confirmed working).
        Returns (live_odds, live_edge) or (None, None).
        """
        fair_odds = _g(bet, "fair_odds", None)
        try:
            odds_text = await page.evaluate(
                "() => document.querySelector('.mod-KambiBC-betslip-outcome__odds')?.textContent?.trim()"
            )
            if not odds_text:
                return None, None
            live_odds = float(odds_text.replace(",", "."))
            if live_odds <= 1:
                return None, None
            edge = None
            if fair_odds and fair_odds > 1:
                edge = round((live_odds / fair_odds - 1) * 100, 1)
            logger.info(
                f"[{self.provider_id}] Live: {_g(bet, 'display_home', '?')} v "
                f"{_g(bet, 'display_away', '?')} {_g(bet, 'outcome', '')} "
                f"@ {live_odds:.2f} (fair {fair_odds or 0:.2f}) edge={edge}%"
            )
            return live_odds, edge
        except Exception as e:
            logger.debug(f"[{self.provider_id}] check_live_price failed: {e}")
            return None, None

    # ------------------------------------------------------------------
    # Placement — semi-auto: navigate + auto-select outcome, user confirms
    #
    # Discovery (2026-04-14, Unibet/Kambi):
    #   - Add outcome:  window.isolatedBetslip.addOutcomeIds([outcomeId])
    #   - Show betslip: window.isolatedBetslip.showBetslip()
    #   - User fills stake and clicks Place Bet manually
    #   - Placement detected via network interception (placebet/coupons)
    # ------------------------------------------------------------------

    async def prep_betslip(self, page: Page, bet, stake: float) -> PlacementResult:
        """Auto-select outcome via Kambi isolatedBetslip API.

        Calls addOutcomeIds([outcomeId]) to add the bet to the betslip.
        User fills stake and clicks Place Bet manually (semi-auto).
        """
        bet_id = _g(bet, "bet_id", 0) or 0
        target_odds = _g(bet, "odds", None)
        outcome_id = _g(bet, "kambi_outcome_id", "")
        if not outcome_id:
            meta = _g(bet, "provider_meta") or {}
            outcome_id = meta.get("outcome_id", "")

        if not outcome_id:
            logger.info(
                f"[{self.provider_id}] Event page ready (no auto-select) — "
                f"{_g(bet, 'display_home', '?')} v {_g(bet, 'display_away', '?')} "
                f"{_g(bet, 'outcome', '')} @ {target_odds}"
            )
            return PlacementResult(
                status="prepped",
                bet_id=bet_id,
                actual_odds=target_odds,
                actual_stake=stake,
                reason="manual_placement",
            )

        # Add outcome to betslip via Kambi JS API
        try:
            result = await page.evaluate(f"""
                async () => {{
                    const ib = window.isolatedBetslip;
                    if (!ib) {{
                        await new Promise(r => setTimeout(r, 3000));
                    }}
                    if (!window.isolatedBetslip) return false;
                    window.isolatedBetslip.addOutcomeIds([{int(outcome_id)}]);
                    window.isolatedBetslip.showBetslip();
                    return true;
                }}
            """)
            if result:
                logger.info(
                    f"[{self.provider_id}] addOutcomeIds({outcome_id}) — "
                    f"{_g(bet, 'display_home', '?')} v {_g(bet, 'display_away', '?')} "
                    f"{_g(bet, 'outcome', '')} @ {target_odds}"
                )
            else:
                logger.warning(f"[{self.provider_id}] isolatedBetslip not available")
        except Exception as e:
            logger.warning(f"[{self.provider_id}] addOutcomeIds failed: {e}")

        return PlacementResult(
            status="prepped",
            bet_id=bet_id,
            actual_odds=target_odds,
            actual_stake=stake,
            reason="kambi_selected" if outcome_id else "manual_placement",
        )

    async def confirm_bet(self, page: Page) -> PlacementResult:
        """User places bet manually on the Kambi betslip."""
        logger.info(f"[{self.provider_id}] confirm_bet: user places on site")
        return PlacementResult(status="manual", bet_id=0, reason="user_confirms_on_site")

    async def place_bet(self, page: Page, bet, stake: float) -> PlacementResult:
        """Semi-auto: prep (auto-select) + manual confirm."""
        prep = await self.prep_betslip(page, bet, stake)
        if prep.status != "prepped":
            return prep
        return PlacementResult(
            status="manual",
            bet_id=prep.bet_id,
            actual_odds=prep.actual_odds,
            actual_stake=prep.actual_stake,
            reason="auto_selected_user_confirms",
        )
