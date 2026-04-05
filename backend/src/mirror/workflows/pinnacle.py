"""PinnacleWorkflow — full API-based autonomous betting via page fetch().

API flow discovered from mirror traffic (2026-04-05):
  1. GET  /0.1/wallet/balance                        → {amount, currency}
  2. GET  /0.1/bets?status=unsettled&startDate=...   → {bets: [...]}
  3. GET  /0.1/matchups/{id}/markets/straight         → [{key, prices, type, version}]
  4. POST /0.1/bets/straight/quote                    → {classes, limits} (pre-check)
  5. POST /0.1/bets/straight                          → {id, price, stake} (placement)
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from .base import ProviderWorkflow, WorkflowMode, PlacementResult, HistoryEntry

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

_API = "https://api.arcadia.pinnacle.se/0.1"

# Map our canonical outcome to Pinnacle designation
_DESIGNATION_MAP = {"home": "home", "away": "away", "draw": "draw",
                    "over": "over", "under": "under"}

# Map our canonical market to Pinnacle market key prefix
_MARKET_KEY_MAP = {
    "moneyline": "s;0;m",
    "1x2": "s;0;m",
    "spread": "s;0;s",
    "total": "s;0;ou",
}


class PinnacleWorkflow(ProviderWorkflow):
    platform = "pinnacle"

    def __init__(self, provider_id: str = "pinnacle", domain: str = "pinnacle.se",
                 mode: WorkflowMode = WorkflowMode.GUIDED):
        super().__init__(provider_id, domain, mode)

    # ------------------------------------------------------------------
    # Login / balance
    # ------------------------------------------------------------------

    async def check_login(self, page: "Page") -> bool:
        result = await self._evaluate_api(page, f"{_API}/wallet/balance")
        if result and "__error" not in result:
            logger.info(f"[pinnacle] Logged in — {result.get('amount')} {result.get('currency')}")
            return True
        return False

    async def sync_balance(self, page: "Page") -> float:
        result = await self._evaluate_api(page, f"{_API}/wallet/balance")
        if result and "amount" in result:
            return float(result["amount"])
        return -1

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    async def sync_history(self, page: "Page") -> list[HistoryEntry]:
        """Fetch settled + unsettled bets from Pinnacle API."""
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=30)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        end = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")

        entries: list[HistoryEntry] = []
        for status_filter in ("settled", "unsettled"):
            url = f"{_API}/bets?status={status_filter}&startDate={start}&endDate={end}"
            result = await self._evaluate_api(page, url)
            if not result or "__error" in (result or {}):
                continue
            bets = result.get("bets", [])
            for b in bets:
                risk = float(b.get("stake", 0))
                price = float(b.get("price", 0))
                outcome_str = b.get("outcome", "none")
                sels = b.get("selections", [])

                if outcome_str == "none":
                    status = "pending"
                    payout = None
                elif outcome_str == "win":
                    status = "won"
                    payout = risk * price
                elif outcome_str == "loss":
                    status = "lost"
                    payout = 0
                else:
                    status = "void"
                    payout = risk

                sel = sels[0] if sels else {}
                matchup = sel.get("matchup", {})
                participants = matchup.get("participants", [])
                home_name = next((p["name"] for p in participants if p.get("alignment") == "home"), "")
                away_name = next((p["name"] for p in participants if p.get("alignment") == "away"), "")
                event_name = f"{home_name} vs {away_name}" if home_name else str(matchup.get("id", ""))
                market_info = sel.get("market", {})

                entries.append(HistoryEntry(
                    provider_bet_id=str(b.get("id", "")),
                    event_name=event_name,
                    market=market_info.get("type", ""),
                    outcome=sel.get("designation", ""),
                    odds=price,
                    stake=risk,
                    status=status,
                    payout=payout,
                ))
        return entries

    # ------------------------------------------------------------------
    # Navigation — show the event on the Pinnacle page
    # ------------------------------------------------------------------

    async def navigate_to_event(self, page: "Page", bet) -> bool:
        """Navigate to the event page so the user can see it before confirming."""
        matchup_id = getattr(bet, "matchup_id", None)
        if not matchup_id:
            return False
        # Pinnacle event URL: /sv/matchup/{matchupId}
        url = f"https://www.pinnacle.se/sv/matchup/{matchup_id}"
        try:
            current = page.url or ""
            if str(matchup_id) in current:
                return True  # Already on this event
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            logger.info(f"[pinnacle] Navigated to matchup {matchup_id}")
            return True
        except Exception as e:
            logger.warning(f"[pinnacle] navigate failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Bet placement — full API automation
    # ------------------------------------------------------------------

    async def place_bet(self, page: "Page", bet, stake: float) -> PlacementResult:
        """Place bet via Pinnacle REST API: fetch markets → quote → place."""
        matchup_id = getattr(bet, "matchup_id", None)
        if not matchup_id:
            return PlacementResult(status="failed", bet_id=bet.bet_id, reason="no_matchup_id")

        outcome = getattr(bet, "outcome", "")
        market = getattr(bet, "market", "")
        point = getattr(bet, "point", None)
        designation = _DESIGNATION_MAP.get(outcome)
        if not designation:
            return PlacementResult(status="failed", bet_id=bet.bet_id, reason=f"unknown_outcome:{outcome}")

        # Step 1: Fetch current markets for this matchup
        markets = await self._evaluate_api(page, f"{_API}/matchups/{matchup_id}/markets/straight")
        if not markets or "__error" in (markets if isinstance(markets, dict) else {}):
            return PlacementResult(status="failed", bet_id=bet.bet_id, reason="markets_fetch_failed")

        # Step 2: Find the right market line
        target_market = self._find_market(markets, market, point)
        if not target_market:
            return PlacementResult(status="failed", bet_id=bet.bet_id,
                                   reason=f"market_not_found:{market}")

        if target_market.get("status") != "open":
            return PlacementResult(status="skipped", bet_id=bet.bet_id,
                                   reason=f"market_closed:{target_market.get('status')}")

        # Step 3: Find the price for our designation
        price_entry = next(
            (p for p in target_market.get("prices", []) if p.get("designation") == designation),
            None,
        )
        if not price_entry:
            return PlacementResult(status="failed", bet_id=bet.bet_id,
                                   reason=f"designation_not_found:{designation}")

        american_price = price_entry["price"]
        # Convert American odds to decimal for slippage check
        if american_price > 0:
            decimal_odds = 1 + american_price / 100
        else:
            decimal_odds = 1 + 100 / abs(american_price)

        # Step 4: Slippage check — abort if odds dropped > 5% below expected
        if bet.odds > 0 and decimal_odds < bet.odds * 0.95:
            return PlacementResult(
                status="skipped", bet_id=bet.bet_id,
                actual_odds=round(decimal_odds, 3),
                reason=f"slippage:{decimal_odds:.2f}_vs_{bet.odds:.2f}",
            )

        market_key = target_market["key"]
        market_id = target_market["version"]

        # Step 5: Place the bet
        request_id = str(uuid.uuid4())
        body = {
            "oddsFormat": "decimal",
            "requestId": request_id,
            "acceptBetterPrices": True,
            "acceptBetterPrice": True,
            "class": "Straight",
            "selections": [{
                "marketId": market_id,
                "matchupId": int(matchup_id),
                "marketKey": market_key,
                "designation": designation,
                "price": round(decimal_odds, 2),
            }],
            "stake": round(stake, 2),
            "originTag": "ps:bsd",
        }

        result = await self._post_api(page, f"{_API}/bets/straight", body)
        if not result:
            return PlacementResult(status="failed", bet_id=bet.bet_id, reason="api_call_failed")
        if "__error" in result:
            error_detail = result.get("detail", result.get("title", str(result["__error"])))
            return PlacementResult(status="failed", bet_id=bet.bet_id, reason=f"api_error:{error_detail}")

        # Success
        confirmed_price = float(result.get("price", decimal_odds))
        confirmed_stake = float(result.get("stake", stake))
        pinnacle_bet_id = result.get("id")

        logger.info(
            f"[pinnacle] PLACED bet {pinnacle_bet_id}: "
            f"{bet.display_home} vs {bet.display_away} {market} {outcome} "
            f"@ {confirmed_price} stake={confirmed_stake}"
        )

        return PlacementResult(
            status="placed",
            bet_id=bet.bet_id,
            actual_odds=confirmed_price,
            actual_stake=confirmed_stake,
            raw_response=result,
        )

    async def check_live_price(self, page: "Page", bet) -> float | None:
        """Read live odds from Pinnacle markets API and compute edge."""
        from ...analysis.value import compute_edge

        matchup_id = getattr(bet, "matchup_id", None)
        fair_odds = getattr(bet, "fair_odds", None)
        if not matchup_id or not fair_odds:
            return None

        markets = await self._evaluate_api(page, f"{_API}/matchups/{matchup_id}/markets/straight")
        if not markets or not isinstance(markets, list):
            return None

        market = getattr(bet, "market", "")
        point = getattr(bet, "point", None)
        target = self._find_market(markets, market, point)
        if not target:
            return None

        outcome = getattr(bet, "outcome", "")
        designation = _DESIGNATION_MAP.get(outcome)
        price_entry = next(
            (p for p in target.get("prices", []) if p.get("designation") == designation),
            None,
        )
        if not price_entry:
            return None

        american = price_entry["price"]
        if american > 0:
            decimal_odds = 1 + american / 100
        else:
            decimal_odds = 1 + 100 / abs(american)

        edge = compute_edge("pinnacle", decimal_odds, fair_odds)
        logger.info(
            f"[pinnacle] Live: {bet.display_home} vs {bet.display_away} "
            f"{outcome} @ {decimal_odds:.2f} (fair {fair_odds:.2f}) edge={edge:.1f}%"
        )
        return edge

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_market(self, markets: list[dict], market_type: str, point: float | None) -> dict | None:
        """Find the matching market from the markets/straight response."""
        # Pinnacle market keys: s;0;m (moneyline), s;0;s;{points} (spread), s;0;ou;{points} (total)
        key_prefix = _MARKET_KEY_MAP.get(market_type)
        if not key_prefix:
            return None

        for m in markets:
            if m.get("isAlternate"):
                continue  # Skip alternate lines
            mk = m.get("key", "")
            if market_type in ("moneyline", "1x2"):
                if mk == key_prefix:
                    return m
            elif market_type == "spread":
                # Match spread with correct points: s;0;s;{points}
                if mk.startswith("s;0;s;") and not m.get("isAlternate"):
                    if point is not None:
                        mk_points = mk.split(";")[-1]
                        try:
                            if abs(float(mk_points) - point) < 0.01:
                                return m
                        except ValueError:
                            pass
                    else:
                        return m  # First non-alternate spread
            elif market_type == "total":
                if mk.startswith("s;0;ou;") and not m.get("isAlternate"):
                    if point is not None:
                        mk_points = mk.split(";")[-1]
                        try:
                            if abs(float(mk_points) - point) < 0.01:
                                return m
                        except ValueError:
                            pass
                    else:
                        return m  # First non-alternate total
        return None

    async def _post_api(self, page: "Page", url: str, body: dict) -> dict | None:
        """POST JSON to Pinnacle API from the page's session."""
        try:
            body_json = json.dumps(body)
            js = """
                async ([url, bodyStr]) => {
                    const resp = await fetch(url, {
                        method: "POST",
                        headers: {"Content-Type": "application/json"},
                        body: bodyStr,
                    });
                    const data = await resp.json();
                    if (!resp.ok) return { __error: resp.status, ...data };
                    return data;
                }
            """
            return await page.evaluate(js, [url, body_json])
        except Exception as e:
            logger.warning(f"[pinnacle] POST {url} failed: {e}")
            return None
