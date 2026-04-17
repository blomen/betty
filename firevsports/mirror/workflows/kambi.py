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
        # Kambi event IDs of open bets — populated by sync_history, checked by runner
        self._open_kambi_eids: set[str] = set()

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
    # History — Kambi CDN player API + KSP fallback
    #
    # Discovery (2026-04-15): LeoVegas uses Kambi CDN player API at
    # cf-mt-auth-api.kambicdn.com/player/api/v2019/{brand}/coupon/history.json
    # Auth: Bearer token from Kambi widget (captured by intercepting fetch).
    # Odds/stake in millis (divide by 1000). KSP API works for Unibet but
    # returns HTML on LeoVegas.
    # ------------------------------------------------------------------

    # Kambi brand IDs (from providers.yaml)
    _KAMBI_BRANDS: dict[str, str] = {
        "leovegas": "leose",
        "unibet": "ubse",
        "expekt": "expektse",
        "betmgm": "betmgmse",
        "speedybet": "speedybetse",
        "x3000": "speedyspelse",
        "goldenbull": "pafgoldense",
        "1x2": "pafpre1x2se",
        "888sport": "888se",
        "mrgreen": "mrgreense",
    }

    _BET_HISTORY_BASE = "sportsbook-feeds/betting-api/api/v1/betting/bet-history"

    _STATUS_MAP = {
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

    # Kambi betOffer type → market name
    _BO_TYPE_MAP: dict[str, str] = {
        "Match": "1x2",
        "Handicap": "spread",
        "Over/Under": "total",
    }

    def _kambi_brand(self) -> str:
        return self._KAMBI_BRANDS.get(self.provider_id, self.provider_id)

    def _ksp_history_url(self, status: str, size: int = 50) -> str:
        domain = f"www.{self.domain}" if not self.domain.startswith("www.") else self.domain
        return f"https://{domain}/{self._BET_HISTORY_BASE}?betStatus={status}&page=0&size={size}"

    def _parse_kambi_cdn_coupons(self, coupons: list[dict]) -> list[HistoryEntry]:
        """Parse Kambi CDN coupon/history.json response into HistoryEntry list.

        Kambi CDN format: odds and stake in millis (÷1000).
        """
        entries: list[HistoryEntry] = []
        for coupon in coupons:
            if not isinstance(coupon, dict):
                continue
            try:
                bets = coupon.get("bets") or []
                if not bets:
                    continue
                bet = bets[0]

                raw_status = str(bet.get("betStatus") or "OPEN").upper()
                mapped = self._STATUS_MAP.get(raw_status, "pending")

                # playedOdds persists after settlement; betOdds gets zeroed
                odds_millis = bet.get("playedOdds") or bet.get("betOdds") or 0
                odds = odds_millis / 1000.0 if odds_millis else 0.0
                stake = (bet.get("stake") or 0) / 1000.0
                payout_millis = bet.get("payout") or 0
                payout = payout_millis / 1000.0 if payout_millis else None

                events = coupon.get("events") or []
                event = events[0] if events else {}
                event_name = event.get("eventName") or ""
                if not event_name:
                    home = event.get("homeName") or ""
                    away = event.get("awayName") or ""
                    if home and away:
                        event_name = f"{home} v {away}"

                outcomes = coupon.get("outcomes") or []
                outcome_label = outcomes[0].get("label", "") if outcomes else ""

                bet_offers = coupon.get("betOffers") or []
                bo_type = bet_offers[0].get("boType", "") if bet_offers else ""
                market = self._BO_TYPE_MAP.get(bo_type, bo_type.lower() if bo_type else "")

                entries.append(
                    HistoryEntry(
                        provider_bet_id=str(coupon.get("couponRef") or ""),
                        event_name=event_name,
                        market=market,
                        outcome=outcome_label,
                        odds=odds,
                        stake=stake,
                        status=mapped,
                        payout=payout,
                    )
                )
            except Exception as e:
                logger.debug(f"[{self.provider_id}] _parse_kambi_cdn_coupons: skipped coupon: {e}")

        return entries

    async def _sync_history_kambi_cdn(self, page: Page) -> list[HistoryEntry]:
        """Fetch bet history via Kambi CDN player API.

        Intercepts the auth token from the Kambi widget's own fetch calls,
        then uses it to query coupon/history.json directly.
        """
        brand = self._kambi_brand()

        # Step 1: Navigate to #bethistory and intercept the Bearer token
        result = await page.evaluate(
            """async (brand) => {
            const orig = window.fetch;
            let token = null;
            let historyData = null;
            window.fetch = async function(input, init) {
                const url = typeof input === "string" ? input : (input && input.url ? input.url : "");
                if (url.indexOf("kambicdn") > -1 && url.indexOf("player") > -1 && !token) {
                    if (init && init.headers) {
                        const h = init.headers;
                        if (typeof h.get === "function") {
                            token = h.get("Authorization") || h.get("authorization");
                        } else if (typeof h === "object") {
                            token = h["Authorization"] || h["authorization"];
                        }
                    }
                }
                const resp = await orig.apply(this, arguments);
                if (url.indexOf("coupon/history") > -1 && !historyData) {
                    try {
                        const clone = resp.clone();
                        historyData = await clone.json();
                    } catch(e) {}
                }
                return resp;
            };

            // Navigate to bet history to trigger the widget's fetch
            const prevHash = location.hash;
            if (location.hash !== "#bethistory") {
                location.hash = "#bethistory";
            } else {
                location.hash = "#featured";
                await new Promise(r => setTimeout(r, 1000));
                location.hash = "#bethistory";
            }

            // Wait for the response (up to 8s)
            for (let i = 0; i < 16; i++) {
                await new Promise(r => setTimeout(r, 500));
                if (historyData) break;
            }

            // Restore fetch
            window.fetch = orig;

            if (historyData) {
                return { ok: true, data: historyData, token: token };
            }

            // If interception missed it (widget cached?), try direct fetch with token
            if (token) {
                const base = "https://cf-mt-auth-api.kambicdn.com/player/api/v2019/" + brand;
                const params = "lang=sv_SE&market=SE&client_id=200&channel_id=1";
                try {
                    const r = await orig(
                        base + "/coupon/history.json?" + params + "&range_size=100&range_start=0",
                        { headers: { "Authorization": token, "Accept": "application/json" } }
                    );
                    if (r.ok) {
                        const d = await r.json();
                        return { ok: true, data: d, token: token, method: "direct" };
                    }
                } catch(e) {}
            }

            return { ok: false, token: token };
        }""",
            brand,
        )

        if not result or not result.get("ok"):
            logger.warning(
                f"[{self.provider_id}] Kambi CDN history fetch failed (token={bool(result and result.get('token'))})"
            )
            return []

        data = result.get("data", {})
        coupons = data.get("historyCoupons") or []
        entries = self._parse_kambi_cdn_coupons(coupons)

        # Cache Kambi event IDs of open bets — runner checks before navigating
        self._open_kambi_eids.clear()
        for coupon in coupons:
            bet = (coupon.get("bets") or [{}])[0]
            if (bet.get("betStatus") or "").upper() == "OPEN":
                for event in coupon.get("events") or []:
                    eid = event.get("eventId")
                    if eid:
                        self._open_kambi_eids.add(str(eid))

        method = result.get("method", "intercept")
        logger.info(
            f"[{self.provider_id}] Kambi CDN history: {len(entries)} bets via {method}"
            f" | {len(self._open_kambi_eids)} open positions"
        )
        return entries

    def _parse_ksp_bets(self, data: dict, status_hint: str) -> list[HistoryEntry]:
        """Parse KSP bet-history API response into HistoryEntry list."""
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
                mapped = self._STATUS_MAP.get(raw_status, "pending")

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

    async def _sync_history_ksp(self, page: Page) -> list[HistoryEntry]:
        """Fetch bet history via KSP betting API (works on Unibet)."""
        all_entries: list[HistoryEntry] = []
        for status in ("OPEN", "WON", "LOST", "VOID", "CASHOUT"):
            try:
                result = await self._evaluate_api(page, self._ksp_history_url(status))
                if result and "__error" not in result:
                    entries = self._parse_ksp_bets(result, status)
                    all_entries.extend(entries)
            except Exception as e:
                logger.debug(f"[{self.provider_id}] _sync_history_ksp {status}: {e}")
        return all_entries

    async def sync_history(self, page: Page) -> list[HistoryEntry]:
        """Fetch all bets (open + settled) from Kambi.

        Tries Kambi CDN player API first (intercepts widget's auth token),
        then KSP betting API fallback, then DOM navigation fallback.
        """
        # Try Kambi CDN (works for LeoVegas and other Kambi operators)
        entries = await self._sync_history_kambi_cdn(page)
        if entries:
            return entries

        # Fallback: KSP betting API (works for Unibet)
        entries = await self._sync_history_ksp(page)
        if entries:
            logger.info(f"[{self.provider_id}] sync_history: {len(entries)} bets from KSP API")
            return entries

        # Last resort: navigate to bet history page
        betting_path = self._BETTING_PATHS.get(self.provider_id, "/betting/sports")
        hist_url = f"https://www.{self.domain}{betting_path}#bethistory"
        if "#bethistory" not in (page.url or ""):
            try:
                await page.goto(hist_url, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(3)
            except Exception as e:
                logger.warning(f"[{self.provider_id}] Could not navigate to bet history: {e}")
        return []

    async def fetch_positions(self, page: Page) -> list[PositionEntry]:
        """Fetch open bets — from Kambi CDN history (filter OPEN) or KSP API."""
        try:
            entries = await self.sync_history(page)
            positions = [
                PositionEntry(
                    provider_bet_id=e.provider_bet_id,
                    event_name=e.event_name,
                    market=e.market,
                    outcome=e.outcome,
                    odds=e.odds,
                    stake=e.stake,
                )
                for e in entries
                if e.status == "pending"
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

    @property
    def home_url(self) -> str:
        """Open betting page directly — skip casino landing."""
        return self._betting_url()

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

    async def _select_outcome_via_api(self, page: Page, outcome_id: str) -> bool:
        """Try isolatedBetslip JS API (works on Unibet, not on LeoVegas micro-frontend)."""
        try:
            result = await page.evaluate(f"""
                async () => {{
                    const ib = window.isolatedBetslip;
                    if (!ib) await new Promise(r => setTimeout(r, 2000));
                    if (!window.isolatedBetslip) return false;
                    window.isolatedBetslip.addOutcomeIds([{int(outcome_id)}]);
                    window.isolatedBetslip.showBetslip();
                    return true;
                }}
            """)
            return bool(result)
        except Exception as e:
            logger.debug(f"[{self.provider_id}] isolatedBetslip API failed: {e}")
            return False

    async def _select_outcome_via_dom(self, page: Page, bet) -> bool:
        """Click the Kambi outcome button by matching label text.

        Fallback for micro-frontend sites (LeoVegas) where isolatedBetslip
        is not exposed. Matches .KambiBC-betty-outcome buttons by outcome
        label (team name / Over / Under / Draw).
        """
        outcome = _g(bet, "outcome", "")
        market = _g(bet, "market", "")
        home = _g(bet, "display_home", "")
        away = _g(bet, "display_away", "")
        point = _g(bet, "point", None)

        # Build search terms: the button text is "LabelOdds" (e.g. "Pontedera5.60")
        # We match on the label portion (case-insensitive substring)
        search_terms: list[str] = []
        if outcome == "home":
            if home:
                search_terms.append(home)
            search_terms.append("1")
        elif outcome == "away":
            if away:
                search_terms.append(away)
            search_terms.append("2")
        elif outcome == "draw":
            search_terms.extend(["Oavgjort", "Draw", "draw"])
        elif outcome == "over":
            label = f"Över {point}" if point else "Över"
            search_terms.append(label)
            label_en = f"Over {point}" if point else "Over"
            search_terms.append(label_en)
        elif outcome == "under":
            label = f"Under {point}" if point else "Under"
            search_terms.append(label)

        if not search_terms:
            return False

        try:
            result = await page.evaluate(
                """async (terms) => {
                // Clear betslip: expand if minimized, then click clear button
                const toggle = document.querySelector(".mod-KambiBC-betslip__header-toggle-betslip-icon");
                if (toggle) { toggle.click(); await new Promise(r => setTimeout(r, 300)); }
                // Click "Rensa kupongen" clear button
                const clearBtn = document.querySelector(".mod-KambiBC-betslip__clear-btn");
                if (clearBtn) { clearBtn.click(); await new Promise(r => setTimeout(r, 300)); }
                // Fallback: click individual close buttons
                const closeBtns = document.querySelectorAll(".mod-KambiBC-betslip-outcome__close-btn");
                for (const c of closeBtns) c.click();
                if (closeBtns.length > 0) await new Promise(r => setTimeout(r, 300));

                const btns = document.querySelectorAll(".KambiBC-betty-outcome");
                for (const term of terms) {
                    const lower = term.toLowerCase();
                    for (const btn of btns) {
                        const txt = (btn.textContent || "").toLowerCase();
                        if (txt.startsWith(lower) || txt.includes(lower)) {
                            btn.click();
                            return btn.textContent.trim();
                        }
                    }
                }
                return null;
            }""",
                search_terms,
            )
            if result:
                logger.info(f"[{self.provider_id}] DOM click fallback: clicked '{result}'")
                return True
        except Exception as e:
            logger.debug(f"[{self.provider_id}] DOM click fallback failed: {e}")
        return False

    async def prep_betslip(self, page: Page, bet, stake: float) -> PlacementResult:
        """Auto-select outcome via Kambi JS API or DOM click fallback.

        Tries isolatedBetslip.addOutcomeIds first (Unibet), then falls back
        to clicking .KambiBC-betty-outcome DOM buttons (LeoVegas micro-frontend).
        User fills stake and clicks Place Bet manually (semi-auto).
        """
        bet_id = _g(bet, "bet_id", 0) or 0
        target_odds = _g(bet, "odds", None)
        outcome_id = _g(bet, "kambi_outcome_id", "")
        if not outcome_id:
            meta = _g(bet, "provider_meta") or {}
            outcome_id = meta.get("outcome_id", "")

        selected = False
        method = "manual_placement"

        # Strategy 1: isolatedBetslip JS API
        if outcome_id:
            selected = await self._select_outcome_via_api(page, outcome_id)
            if selected:
                method = "kambi_api"

        # Strategy 2: DOM click fallback (micro-frontend sites like LeoVegas)
        if not selected:
            await asyncio.sleep(1)  # Let event page render
            selected = await self._select_outcome_via_dom(page, bet)
            if selected:
                method = "dom_click"

        if selected:
            logger.info(
                f"[{self.provider_id}] prep_betslip ({method}): "
                f"{_g(bet, 'display_home', '?')} v {_g(bet, 'display_away', '?')} "
                f"{_g(bet, 'outcome', '')} @ {target_odds}"
            )
        else:
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
            reason=method,
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

    # ------------------------------------------------------------------
    # Placement parsing — for intercepted HTTP/WS responses
    # ------------------------------------------------------------------

    @staticmethod
    def parse_placement_response(body: dict) -> str | None:
        """Extract provider_bet_id from Kambi placement confirmation."""
        # WS frame: {"couponId": "...", ...} or nested in data/result
        for key in ("couponId", "betId", "id", "receiptId"):
            val = body.get(key)
            if val:
                return str(val)
        # Nested: {data: {couponId: ...}} or {result: {couponId: ...}}
        for wrapper in ("data", "result", "coupon", "placeBetResult"):
            inner = body.get(wrapper)
            if isinstance(inner, dict):
                for key in ("couponId", "betId", "id"):
                    val = inner.get(key)
                    if val:
                        return str(val)
        return None

    @staticmethod
    def parse_placement_status(body: dict) -> dict:
        """Check if Kambi placement response indicates success."""
        # Kambi WS uses status field or couponStatus
        status = body.get("status") or body.get("couponStatus") or ""
        if isinstance(status, str):
            status_upper = status.upper()
            if status_upper in ("REJECTED", "FAILED", "ERROR", "DECLINED"):
                error = body.get("error") or body.get("message") or body.get("reason") or status
                return {"success": False, "error": str(error), "max_stake": None}
        # Nested errors
        for wrapper in ("data", "result", "placeBetResult"):
            inner = body.get(wrapper)
            if isinstance(inner, dict):
                inner_status = (inner.get("status") or inner.get("couponStatus") or "").upper()
                if inner_status in ("REJECTED", "FAILED", "ERROR", "DECLINED"):
                    error = inner.get("error") or inner.get("message") or inner_status
                    return {"success": False, "error": str(error), "max_stake": None}
        return {"success": True, "error": None, "max_stake": None}

    @staticmethod
    def parse_placement_details(body: dict) -> dict:
        """Extract actual odds/stake from Kambi placement response."""
        details: dict = {}
        # Look in top-level and nested structures
        for src in (body, body.get("data", {}), body.get("result", {}), body.get("coupon", {})):
            if not isinstance(src, dict):
                continue
            if not details.get("actual_stake"):
                for key in ("stake", "totalStake", "amount"):
                    val = src.get(key)
                    if val:
                        try:
                            details["actual_stake"] = float(val)
                        except (TypeError, ValueError):
                            pass
            if not details.get("actual_odds"):
                for key in ("odds", "totalOdds", "price", "oddsDecimal"):
                    val = src.get(key)
                    if val:
                        try:
                            details["actual_odds"] = float(val)
                        except (TypeError, ValueError):
                            pass
        return details
