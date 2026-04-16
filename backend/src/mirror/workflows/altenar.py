"""AltenarWorkflow — API-based balance for Altenar-platform providers.

Covers: campobet, quickcasino, betinia, swiper, lodur, dbet.

Navigation: sportRoutingParams query param on /sv/sport
Price reading: cached from GetEventDetails intercepted responses
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from .base import HistoryEntry, PlacementResult, PositionEntry, ProviderWorkflow, WorkflowMode

if TYPE_CHECKING:
    from playwright.async_api import Page


def _g(obj, key, default=None):
    """Get attribute from object or dict — handles both play loop dicts and BetProxy objects."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


logger = logging.getLogger(__name__)

# Altenar market typeId → our canonical market type
_MARKET_TYPE_MAP = {
    1: "1x2",
    186: "moneyline",
    219: "moneyline",
    251: "moneyline",
    406: "moneyline",
    30001: "moneyline",
    18: "total",
    189: "total",
    225: "total",
    238: "total",
    258: "total",
    412: "total",
    16: "spread",
    187: "spread",
    223: "spread",
    237: "spread",
    256: "spread",
    410: "spread",
}

# Altenar odd typeId → our outcome
_ODD_TYPE_MAP = {
    1: "home",
    2: "draw",
    3: "away",
    1714: "home",
    1715: "away",
    12: "over",
    13: "under",
}

# Altenar API sport_id → our canonical sport string (mirrors AltenarRetriever.SPORT_MAPPING)
# 40 is a legacy Altenar sport_id for volleyball (used before 69 replaced it)
_SPORT_ID_TO_SPORT: dict[int, str] = {
    40: "volleyball",
    66: "football",
    67: "basketball",
    68: "tennis",
    69: "volleyball",
    70: "ice_hockey",
    71: "boxing",
    73: "handball",
    74: "cricket",
    75: "american_football",
    76: "baseball",
    77: "table_tennis",
    84: "mma",
    101: "rugby",
    102: "rugby",
    145: "esports",
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

    @property
    def home_url(self) -> str:
        # Must land on /sv/sport so the Altenar WSDK initialises and sets
        # localStorage.token — without this, _authed_fetch() has no auth header.
        return f"https://{self.domain}/sv/sport"

    def _balance_url(self) -> str:
        return f"https://{self.domain}/sv/api/v3/account/balance"

    def _history_url(self, page: int = 1, limit: int = 50) -> str:
        return f"https://{self.domain}/sv/api/v3/history?page={page}&limit={limit}&type=sport"

    async def _authed_fetch(self, page: Page, url: str) -> dict | None:
        """Fetch with Authorization header read from localStorage token."""
        try:
            return await page.evaluate(
                f"""async () => {{
                try {{
                    const raw = localStorage.getItem("token");
                    const headers = {{}};
                    if (raw) {{
                        const t = JSON.parse(raw);
                        if (t.type && t.value) headers["Authorization"] = t.type + " " + t.value;
                    }}
                    const r = await fetch("{url}", {{ credentials: "include", headers }});
                    if (!r.ok) return {{ __error: r.status }};
                    return await r.json();
                }} catch(e) {{ return {{ __error: e.message }}; }}
            }}"""
            )
        except Exception as e:
            logger.warning(f"[{self.provider_id}] authed_fetch failed: {url} — {e}")
            return None

    # ------------------------------------------------------------------
    # Login / balance
    # ------------------------------------------------------------------

    async def check_login(self, page: Page) -> bool:
        result = await self._authed_fetch(page, self._balance_url())
        if result is None or "__error" in (result or {}):
            return False
        # Navigate into result wrapper if present
        data = result.get("result", result) if isinstance(result, dict) else result
        if not isinstance(data, dict):
            return False
        # Logged in if any wallet has balance data (cash, bonus, sport)
        for wallet in ("cash", "bonus", "sport"):
            try:
                float(data[wallet]["total"])
                return True
            except (KeyError, TypeError, ValueError):
                continue
        return False

    async def sync_balance(self, page: Page) -> float:
        """Read balance from the Altenar balance API (sum of cash + bonus wallets)."""
        result = await self._authed_fetch(page, self._balance_url())
        if result is None or "__error" in (result or {}):
            return -1
        data = result.get("result", result) if isinstance(result, dict) else result
        if not isinstance(data, dict):
            return -1
        # Sum all wallet totals (cash, bonus, sport)
        total = 0.0
        for wallet in ("cash", "bonus", "sport"):
            try:
                total += float(data[wallet]["total"])
            except (KeyError, TypeError, ValueError):
                continue
        return total if total > 0 else -1

    # ------------------------------------------------------------------
    # History + Positions — via account history API
    # ------------------------------------------------------------------

    def _is_bet_history_response(self, url: str) -> bool:
        u = url.lower()
        return "bethistory" in u or "widgetreports" in u or "/api/v3/history" in u

    def _parse_bets_data(self, result: dict) -> list[dict]:
        """Extract the bets array from various response shapes.

        Betinia /api/v3/history: {result: {total, limit, offset, node: [...]}}
        Altenar widgetBetHistory: {data: {bets: [...]}} or {bets: [...]}
        """
        inner = result.get("result", result.get("data", result.get("bets", [])))
        if isinstance(inner, dict):
            inner = inner.get("node", inner.get("bets", inner.get("items", [])))
        return inner if isinstance(inner, list) else []

    def _parse_history_entry(self, bet: dict) -> HistoryEntry | None:
        try:
            status_raw = str(bet.get("status") or bet.get("betStatus") or "").lower()
            status_map = {
                "won": "won",
                "win": "won",
                "lost": "lost",
                "lose": "lost",
                "void": "void",
                "voided": "void",
                "cancelled": "void",
                "refund": "void",
                "cashout": "cashout",
                "cashed_out": "cashout",
                # Altenar widgetBetHistory returns numeric status codes
                "1": "won",
                "2": "lost",
                "3": "void",
                "4": "cashout",
            }
            status = status_map.get(status_raw)
            if not status:
                return None

            odds = float(bet.get("totalOdds") or bet.get("odds") or 0)
            stake = float(bet.get("totalStake") or bet.get("stake") or bet.get("amount") or 0)
            payout = float(bet.get("totalWin") or bet.get("payout") or bet.get("winAmount") or bet.get("returns") or 0)

            # Extract event info — try events[] (v3 API), then selections[] (widget API)
            events = bet.get("events") or bet.get("selections") or bet.get("legs") or bet.get("betLegs") or []
            event_name = bet.get("title") or ""
            market = outcome = ""
            if events and isinstance(events, list):
                ev = events[0]
                if not event_name:
                    event_name = ev.get("eventName") or ev.get("matchName") or ev.get("title") or ""
                market = ev.get("marketName") or ev.get("marketTypeName") or ""
                outcome = ev.get("outcomeName") or ev.get("selectionName") or ev.get("outcome") or ""

            return HistoryEntry(
                provider_bet_id=str(bet.get("id") or bet.get("betId") or ""),
                event_name=event_name,
                market=market,
                outcome=outcome,
                odds=odds,
                stake=stake,
                status=status,
                payout=payout,
            )
        except (ValueError, TypeError, KeyError):
            return None

    def _parse_position_entry(self, bet: dict) -> PositionEntry | None:
        try:
            odds = float(bet.get("totalOdds") or bet.get("odds") or 0)
            stake = float(bet.get("totalStake") or bet.get("stake") or bet.get("amount") or 0)
            payout = float(bet.get("totalWin") or bet.get("potentialWin") or 0)

            events = bet.get("events") or bet.get("selections") or bet.get("legs") or bet.get("betLegs") or []
            event_name = bet.get("title") or ""
            market = outcome = ""
            if events and isinstance(events, list):
                ev = events[0]
                if not event_name:
                    event_name = ev.get("eventName") or ev.get("matchName") or ev.get("title") or ""
                market = ev.get("marketName") or ev.get("marketTypeName") or ""
                outcome = ev.get("outcomeName") or ev.get("selectionName") or ev.get("outcome") or ""

            return PositionEntry(
                provider_bet_id=str(bet.get("id") or bet.get("betId") or ""),
                event_name=event_name,
                market=market,
                outcome=outcome,
                odds=odds,
                stake=stake,
                placed_at=bet.get("dateCreated") or bet.get("createdAt") or bet.get("dateTs"),
                potential_payout=payout if payout > 0 else None,
            )
        except (ValueError, TypeError, KeyError):
            return None

    async def sync_history(self, page: Page) -> list[HistoryEntry]:
        """Fetch settled bet history via on-site v3 history API."""
        try:
            logger.info(f"[{self.provider_id}] sync_history: starting")
            result = await self._authed_fetch(page, self._history_url(page=1, limit=100))
            if not result or "__error" in (result or {}):
                logger.warning(f"[{self.provider_id}] sync_history API failed: {result}")
                return []

            bets_data = self._parse_bets_data(result)
            entries = []
            for bet in bets_data:
                entry = self._parse_history_entry(bet)
                if entry:
                    entries.append(entry)

            logger.info(f"[{self.provider_id}] sync_history: {len(entries)} settled bets")
            return entries
        except Exception as e:
            logger.warning(f"[{self.provider_id}] sync_history failed: {e}")
            return []

    async def fetch_positions(self, page: Page) -> list[PositionEntry]:
        """Fetch open bets via on-site v3 history API (filter pending in code)."""
        try:
            result = await self._authed_fetch(page, self._history_url(page=1, limit=50))
            if not result or "__error" in (result or {}):
                logger.warning(f"[{self.provider_id}] fetch_positions API failed: {result}")
                return []

            bets_data = self._parse_bets_data(result)
            _SETTLED = {
                "won",
                "win",
                "lost",
                "lose",
                "void",
                "voided",
                "cancelled",
                "refund",
                "cashout",
                "cashed_out",
            }
            positions = []
            for bet in bets_data:
                status_raw = str(bet.get("status") or "").lower()
                if status_raw in _SETTLED:
                    continue  # Skip settled bets — only want open/pending
                entry = self._parse_position_entry(bet)
                if entry:
                    positions.append(entry)

            logger.info(f"[{self.provider_id}] fetch_positions: {len(positions)} open bets (API)")
            return positions
        except Exception as e:
            logger.warning(f"[{self.provider_id}] fetch_positions failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Placement response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def parse_placement_response(body: dict) -> str | None:
        """Extract provider_bet_id from Altenar placeWidget response."""
        try:
            return str(body["data"]["betId"])
        except (KeyError, TypeError):
            pass
        try:
            return str(body["bets"][0]["id"])
        except (KeyError, TypeError, IndexError):
            pass
        return None

    @staticmethod
    def parse_placement_status(body: dict) -> dict:
        """Check if placeWidget response indicates success, error, or stake limit.

        Returns dict with:
          - success: bool
          - error: str | None (error message if failed)
          - max_stake: float | None (bookmaker's max stake if limited)
        """
        result: dict = {"success": False, "error": None, "max_stake": None}

        # Check for explicit error indicators
        error = body.get("error") or body.get("errorMessage") or body.get("message")
        if isinstance(body.get("data"), dict):
            error = error or body["data"].get("error") or body["data"].get("errorMessage")
        if error and isinstance(error, str):
            result["error"] = error

        # Check for max stake / stake limit indicators
        for key in ("maxStake", "max_stake", "maximumStake", "maxBetAmount", "stakeLimit"):
            val = body.get(key)
            if val is None and isinstance(body.get("data"), dict):
                val = body["data"].get(key)
            if val is not None:
                try:
                    result["max_stake"] = float(val)
                except (TypeError, ValueError):
                    pass

        # Check status fields
        status = body.get("status") or body.get("code")
        if isinstance(body.get("data"), dict):
            status = status or body["data"].get("status") or body["data"].get("code")
        if isinstance(status, str) and status.lower() in ("error", "failed", "rejected", "declined"):
            result["error"] = result["error"] or f"Placement {status}"
            return result

        # Success: must have a bet ID somewhere
        bet_id = AltenarWorkflow.parse_placement_response(body)
        if bet_id:
            result["success"] = True
        elif not result["error"]:
            # No bet ID and no explicit error — suspicious, treat as failed
            result["error"] = "No bet ID in response"

        return result

    @staticmethod
    def parse_placement_details(body: dict) -> dict:
        """Extract actual stake/odds from Altenar placeWidget response."""
        details: dict = {}
        # Shape 1: {data: {betId, stake, odds, ...}}
        data = body.get("data", {})
        if isinstance(data, dict):
            if "stake" in data:
                details["actual_stake"] = float(data["stake"])
            if "odds" in data:
                details["actual_odds"] = float(data["odds"])
            if "totalOdds" in data:
                details["actual_odds"] = float(data["totalOdds"])
        # Shape 2: {bets: [{id, stake, selections: [{odds}]}]}
        bets = body.get("bets", [])
        if bets and isinstance(bets, list):
            bet = bets[0]
            if "stake" in bet:
                details["actual_stake"] = float(bet["stake"])
            sels = bet.get("selections", [])
            if sels and "odds" in sels[0]:
                details["actual_odds"] = float(sels[0]["odds"])
        return details

    @staticmethod
    def parse_placement_request_stake(request_body: dict) -> float | None:
        """Extract the submitted stake from the placeWidget POST request body."""
        # Top-level stake field
        for key in ("Stake", "stake", "Amount", "amount"):
            val = request_body.get(key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
        # Nested in selections/coupons array: [{Stake: X}]
        for arr_key in ("Coupons", "coupons", "Selections", "selections", "bets", "Bets"):
            arr = request_body.get(arr_key)
            if arr and isinstance(arr, list):
                item = arr[0]
                if isinstance(item, dict):
                    for key in ("Stake", "stake", "Amount", "amount"):
                        val = item.get(key)
                        if val is not None:
                            try:
                                return float(val)
                            except (TypeError, ValueError):
                                pass
        return None

    # ------------------------------------------------------------------
    # Navigation — sportRoutingParams URL pattern
    # ------------------------------------------------------------------

    async def navigate_to_event(self, page: Page, bet) -> bool:
        """Navigate via sportRoutingParams query param.

        URL pattern: {domain}/sv/sport?sportRoutingParams=page~event__sportId~{s}__categoryIds~{c}__championshipIds~{ch}__eventId~{e}
        All IDs come from provider_meta stored during extraction.
        """
        eid = _g(bet, "altenar_event_id", None)
        if not eid:
            meta = _g(bet, "provider_meta") or {}
            eid = meta.get("event_id")
        if not eid:
            logger.warning(f"[{self.provider_id}] No altenar_event_id for navigation")
            return False

        meta = _g(bet, "provider_meta") or {}
        sid = _g(bet, "altenar_sport_id", "") or meta.get("sport_id", "")
        cid = _g(bet, "altenar_category_id", "") or meta.get("category_id", "")
        chid = _g(bet, "altenar_championship_id", "") or meta.get("championship_id", "")

        # Sport consistency check
        if sid:
            try:
                inferred_sport = _SPORT_ID_TO_SPORT.get(int(sid))
                bet_sport = _g(bet, "sport", "")
                if inferred_sport and bet_sport and inferred_sport != bet_sport:
                    logger.warning(
                        f"[{self.provider_id}] Sport mismatch — skipping: "
                        f"event sport_id={sid} ({inferred_sport}) != bet sport={bet_sport} "
                        f"event_id={eid}"
                    )
                    return False
            except (ValueError, TypeError):
                pass

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

    async def check_live_price(self, page: Page, bet) -> tuple[float | None, float | None]:
        """Read live odds from cached GetEventDetails response.

        Returns (live_odds, live_edge) or (None, None).
        """
        eid = _g(bet, "altenar_event_id", None) or (_g(bet, "provider_meta") or {}).get("event_id")
        fair_odds = _g(bet, "fair_odds", None)
        if not eid or not fair_odds:
            return None, None

        # Check cache (max 60s old)
        cached = self._event_details_cache.get(str(eid))
        if not cached:
            await asyncio.sleep(2)
            cached = self._event_details_cache.get(str(eid))

        if not cached:
            logger.debug(f"[{self.provider_id}] No cached event details for {eid}")
            return None, None

        data, ts = cached
        if time.time() - ts > 60:
            logger.debug(f"[{self.provider_id}] Cached event details too old for {eid}")
            return None, None

        return self._match_price(data, bet, fair_odds)

    @staticmethod
    def _compute_edge(provider_odds: float, fair_odds: float) -> float | None:
        if fair_odds <= 1 or provider_odds <= 1:
            return None
        return (provider_odds / fair_odds - 1) * 100

    def _match_price(self, data: dict, bet, fair_odds: float) -> tuple[float | None, float | None]:
        """Match bet market/outcome against GetEventDetails response.

        Returns (live_odds, live_edge) or (None, None).
        """
        target_market = _g(bet, "market", "")
        target_outcome = _g(bet, "outcome", "")
        target_point = _g(bet, "point", None)

        markets = data.get("markets", [])
        odds_list = data.get("odds", [])
        odds_by_id = {o["id"]: o for o in odds_list}

        for m in markets:
            our_market = _MARKET_TYPE_MAP.get(m.get("typeId"))
            if not our_market:
                continue
            if our_market != target_market and not ({our_market, target_market} <= {"1x2", "moneyline"}):
                continue

            flat_ids = [
                oid for group in m.get("desktopOddIds", []) for oid in (group if isinstance(group, list) else [group])
            ]

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

                edge = self._compute_edge(live_price, fair_odds)
                logger.info(
                    f"[{self.provider_id}] Live: {_g(bet, 'display_home', '?')} vs "
                    f"{_g(bet, 'display_away', '?')} {target_outcome} @ {live_price:.2f} "
                    f"(fair {fair_odds:.2f}) edge={edge:.1f}%"
                )
                return live_price, edge

        return None, None

    @staticmethod
    def _extract_point(odd_name: str) -> float | None:
        """Extract point from odd name like 'Team (+1.5)' or 'Over 4.5'."""
        import re

        match = re.search(r"[(\s]([+-]?\d+\.?\d*)\)?$", odd_name.strip())
        if match:
            return abs(float(match.group(1)))
        return None

    # ------------------------------------------------------------------
    # Placement — two-phase: prep (auto-fill) then confirm (click submit)
    # ------------------------------------------------------------------

    async def prep_betslip(self, page: Page, bet, stake: float) -> PlacementResult:
        """Auto-select outcome via altenarWSDK.toggleSelections([oddId])."""
        bet_id = _g(bet, "bet_id", 0) or 0
        target_odds = _g(bet, "odds", 0)

        meta = _g(bet, "provider_meta") or {}
        eid = meta.get("event_id", "")
        current_url = page.url or ""
        on_event = f"eventId~{eid}" in current_url if eid else True

        if not on_event:
            logger.warning(f"[{self.provider_id}] prep_betslip: not on event page")
            return PlacementResult(status="failed", bet_id=bet_id, actual_stake=stake, reason="wrong_page")

        outcome_id = meta.get("outcome_id")
        selected = False
        if outcome_id:
            try:
                result = await page.evaluate(
                    f"""async () => {{
                        if (typeof window.altenarWSDK === 'undefined') {{
                            await new Promise(r => setTimeout(r, 3000));
                        }}
                        if (typeof window.altenarWSDK === 'undefined') return false;
                        window.altenarWSDK.toggleSelections([{int(outcome_id)}]);
                        return true;
                    }}"""
                )
                selected = bool(result)
                if selected:
                    logger.info(
                        f"[{self.provider_id}] WSDK selected oddId={outcome_id} — "
                        f"{_g(bet, 'display_home', '?')} v {_g(bet, 'display_away', '?')} "
                        f"{_g(bet, 'outcome', '')} @ {target_odds}"
                    )
                else:
                    logger.warning(f"[{self.provider_id}] WSDK not available for oddId={outcome_id}")
            except Exception as e:
                logger.warning(f"[{self.provider_id}] toggleSelections failed: {e}")

        if not selected:
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
            reason="wsdk_selected" if selected else "manual_placement",
        )

    async def confirm_bet(self, page: Page) -> PlacementResult:
        """User places bet manually on the WASM betslip."""
        logger.info(f"[{self.provider_id}] confirm_bet: user places on site")
        return PlacementResult(status="manual", bet_id=0, reason="user_confirms_on_site")

    async def place_bet(self, page: Page, bet, stake: float) -> PlacementResult:
        """Full placement (prep + confirm) — used when two-phase is not available."""
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
