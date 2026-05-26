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
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from .base import HistoryEntry, PlacementResult, ProviderWorkflow, WorkflowMode

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

_API = "https://api.arcadia.pinnacle.se/0.1"

# Map our canonical outcome to Pinnacle designation
_DESIGNATION_MAP = {"home": "home", "away": "away", "draw": "draw", "over": "over", "under": "under"}

# Map our canonical market to Pinnacle market key prefix
_MARKET_KEY_MAP = {
    "moneyline": "s;0;m",
    "1x2": "s;0;m",
    "spread": "s;0;s",
    "total": "s;0;ou",
}


class PinnacleWorkflow(ProviderWorkflow):
    platform = "pinnacle"

    def __init__(
        self, provider_id: str = "pinnacle", domain: str = "pinnacle.se", mode: WorkflowMode = WorkflowMode.GUIDED
    ):
        super().__init__(provider_id, domain, mode)

    # ------------------------------------------------------------------
    # Login / balance
    # ------------------------------------------------------------------

    async def check_login(self, page: Page) -> bool:
        import asyncio

        # Wait for page to settle after navigation (redirects, cookie setup)
        await asyncio.sleep(3)
        # Retry up to 3 times — auth cookies may need a moment
        for attempt in range(3):
            result = await self._evaluate_api(page, f"{_API}/wallet/balance")
            if result and "__error" not in result:
                logger.info(f"[pinnacle] Logged in — {result.get('amount')} {result.get('currency')}")
                return True
            if attempt < 2:
                await asyncio.sleep(2)
        return False

    async def sync_balance(self, page: Page) -> float:
        result = await self._evaluate_api(page, f"{_API}/wallet/balance")
        if result and "amount" in result:
            return float(result["amount"])
        return -1

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    async def sync_history(self, page: Page) -> list[HistoryEntry]:
        """Scrape bet history from Pinnacle DOM + try API fallback.

        DOM format per bet card:
          "Settled: DATE ... EVENT_NAME ... OUTCOME @ ODDS ...
           SETTLED – LOSS/WIN ... Stake: X ... Payout: Y"

        The "LOSS" label in bet details is the actual result.
        "Win:" and "Payout:" fields show potential, not actual outcome.
        """
        import re

        entries: list[HistoryEntry] = []

        # Try DOM scrape first (always works when logged in)
        try:
            raw = await page.evaluate("() => document.body.innerText")
        except Exception as e:
            logger.warning(f"[pinnacle] Could not read DOM: {e}")
            raw = ""

        if raw:
            # Flatten newlines to spaces for easier regex
            flat = raw.replace("\n", " ").replace("\r", "")

            # Split by "Settled:" or "Rättat:"/"Rattat:" markers (EN/SV, handles encoding)
            cards = re.split(r"(?=(?:Settled|R.ttat):\s)", flat)
            for card in cards:
                # Must have stake field (EN: "Stake:", SV: "Insats:")
                if "Stake:" not in card and "Insats:" not in card:
                    continue

                # Odds: "@ 7.420"
                odds_match = re.search(r"@\s*([\d.]+)", card)
                if not odds_match:
                    continue
                odds = float(odds_match.group(1))

                # Stake: EN "Stake: 90.00" or SV "Insats: 90,00"
                stake_match = re.search(r"(?:Stake|Insats):\s*([\d.,]+)", card)
                if not stake_match:
                    continue
                stake = float(stake_match.group(1).replace(",", "."))

                # Result: EN "SETTLED – LOSS/WIN" or SV "RÄTTAT – FÖRLUST/VINST"
                # Use encoding-safe patterns (.RLUST for FÖRLUST, etc.)
                card_upper = card.upper()
                if "RLUST" in card_upper or "LOSS" in card_upper:
                    status = "lost"
                elif "VOID" in card_upper or "CANCEL" in card_upper or "OGILTIG" in card_upper:
                    status = "void"
                elif "SETTLED" in card_upper or "TTAT" in card_upper:
                    # Has a settled marker but no LOSS/VOID — must be a win
                    status = "won"
                else:
                    continue

                # Event name: "Team A vs Team B" pattern
                event_match = re.search(r"(\w[\w\s.]+?)\s+vs\s+(\w[\w\s.]+?)(?:\s+[A-Z]|\s+@|\s+Bet)", card)
                event_name = f"{event_match.group(1).strip()} vs {event_match.group(2).strip()}" if event_match else ""

                # Outcome name: text before "@ ODDS"
                outcome_match = re.search(r"(?:vs\s+\S.*?)\s+(.+?)\s*@\s*[\d.]+", card)
                outcome_name = outcome_match.group(1).strip() if outcome_match else ""

                # Payout: 0 for losses, actual payout for wins
                if status == "lost":
                    payout = 0.0
                elif status == "won":
                    payout = stake * odds
                else:
                    payout = stake

                # Bet ID: "#2220898232"
                bet_id_match = re.search(r"#(\d+)", card)
                bet_id = bet_id_match.group(1) if bet_id_match else ""

                if stake > 0 and odds > 0:
                    entries.append(
                        HistoryEntry(
                            provider_bet_id=bet_id,
                            event_name=event_name,
                            market="",
                            outcome=outcome_name,
                            odds=odds,
                            stake=stake,
                            status=status,
                            payout=payout,
                        )
                    )

        logger.info(f"[pinnacle] DOM scrape: {len(entries)} bet(s) from history page")

        # API fallback if DOM scrape found nothing
        if not entries:
            now = datetime.now(UTC)
            start = (now - timedelta(days=30)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            end = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
            for status_filter in ("settled",):
                url = f"{_API}/bets?status={status_filter}&startDate={start}&endDate={end}"
                result = await self._evaluate_api(page, url)
                if not result or "__error" in (result or {}):
                    continue
                for b in result.get("bets", []):
                    risk = float(b.get("stake", 0))
                    price = float(b.get("price", 0))
                    outcome_str = b.get("outcome", "none")
                    if outcome_str == "win":
                        st, pay = "won", risk * price
                    elif outcome_str == "loss":
                        st, pay = "lost", 0
                    elif outcome_str == "none":
                        continue
                    else:
                        st, pay = "void", risk
                    sels = b.get("selections", [])
                    sel = sels[0] if sels else {}
                    matchup = sel.get("matchup", {})
                    parts = matchup.get("participants", [])
                    home = next((p["name"] for p in parts if p.get("alignment") == "home"), "")
                    away = next((p["name"] for p in parts if p.get("alignment") == "away"), "")
                    entries.append(
                        HistoryEntry(
                            provider_bet_id=str(b.get("id", "")),
                            event_name=f"{home} vs {away}" if home else "",
                            market=sel.get("market", {}).get("type", ""),
                            outcome=sel.get("designation", ""),
                            odds=price,
                            stake=risk,
                            status=st,
                            payout=pay,
                        )
                    )

        return entries

    # ------------------------------------------------------------------
    # Navigation — show the event on the Pinnacle page
    # ------------------------------------------------------------------

    async def navigate_to_event(self, page: Page, bet) -> bool:
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

    async def place_bet(self, page: Page, bet, stake: float) -> PlacementResult:
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
        target_market = self._find_market(markets, market, point, outcome=outcome)
        if not target_market:
            return PlacementResult(status="failed", bet_id=bet.bet_id, reason=f"market_not_found:{market}")

        if target_market.get("status") != "open":
            return PlacementResult(
                status="skipped", bet_id=bet.bet_id, reason=f"market_closed:{target_market.get('status')}"
            )

        # Step 3: Find the price for our designation
        price_entry = next(
            (p for p in target_market.get("prices", []) if p.get("designation") == designation),
            None,
        )
        if not price_entry:
            return PlacementResult(status="failed", bet_id=bet.bet_id, reason=f"designation_not_found:{designation}")

        american_price = price_entry["price"]
        # Convert American odds to decimal for slippage check
        decimal_odds = 1 + american_price / 100 if american_price > 0 else 1 + 100 / abs(american_price)

        # Step 4: Slippage check — abort if odds dropped > 5% below expected
        if bet.odds > 0 and decimal_odds < bet.odds * 0.95:
            return PlacementResult(
                status="skipped",
                bet_id=bet.bet_id,
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
            "selections": [
                {
                    "marketId": market_id,
                    "matchupId": int(matchup_id),
                    "marketKey": market_key,
                    "designation": designation,
                    "price": round(decimal_odds, 2),
                }
            ],
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

    async def settle_all(self, page: Page) -> dict:
        """Full automated Pinnacle settlement via API.

        1. Fetch unsettled bets → record any missing in DB
        2. Fetch settled bets → match against pending DB bets → auto-settle
        3. Sync balance

        Returns summary with P&L breakdown.
        """
        from ...db.models import Bet, Event, get_session
        from ...repositories.profile_repo import ProfileRepo
        from ...services.bet_service import BetService

        now = datetime.now(UTC)
        start = (now - timedelta(days=30)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        end = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")

        # ---- Step 1: Scrape pending (unsettled) bets from Pinnacle API ----
        recorded_new = 0
        url_unsettled = f"{_API}/bets?status=unsettled&startDate={start}&endDate={end}"
        unsettled_data = await self._evaluate_api(page, url_unsettled)

        db = get_session()
        settled = []
        try:
            profile = ProfileRepo(db).get_active()
            if not profile:
                return {"error": "no active profile"}

            svc = BetService(db)

            if unsettled_data and "__error" not in (unsettled_data if isinstance(unsettled_data, dict) else {}):
                api_bets = unsettled_data if isinstance(unsettled_data, list) else unsettled_data.get("bets", [])
                for b in api_bets:
                    risk = float(b.get("stake", b.get("riskAmount", 0)))
                    price = float(b.get("price", 0))
                    pin_id = str(b.get("id", ""))
                    if risk <= 0 or price <= 0:
                        continue

                    # Check if we already have this bet in DB (by odds + stake + provider)
                    existing = (
                        db.query(Bet)
                        .filter(
                            Bet.profile_id == profile.id,
                            Bet.provider_id == "pinnacle",
                            Bet.result == "pending",
                            Bet.odds == round(price, 3),
                            Bet.stake == round(risk, 2),
                        )
                        .first()
                    )
                    if existing:
                        continue

                    # Parse event info from selections
                    sels = b.get("selections", [])
                    sel = sels[0] if sels else {}
                    matchup = sel.get("matchup", {})
                    parts = matchup.get("participants", [])
                    home = next((p["name"] for p in parts if p.get("alignment") == "home"), "")
                    away = next((p["name"] for p in parts if p.get("alignment") == "away"), "")
                    designation = sel.get("designation", "")
                    market_type = sel.get("market", {}).get("type", "moneyline")
                    matchup.get("id") or sel.get("matchupId")

                    # Try to find matching event in DB
                    event_id = None
                    if home and away:
                        from sqlalchemy import or_

                        from ...db.models import Event as EventModel

                        event = (
                            db.query(EventModel)
                            .filter(
                                or_(
                                    EventModel.home_team.ilike(f"%{home}%"),
                                    EventModel.display_home.ilike(f"%{home}%"),
                                ),
                                or_(
                                    EventModel.away_team.ilike(f"%{away}%"),
                                    EventModel.display_away.ilike(f"%{away}%"),
                                ),
                            )
                            .first()
                        )
                        if event:
                            event_id = event.id

                    svc.create_bet(
                        event_id=event_id,
                        provider_id="pinnacle",
                        market=market_type,
                        outcome=designation,
                        odds=round(price, 3),
                        stake=round(risk, 2),
                        bet_type="mirror",
                    )
                    recorded_new += 1
                    logger.info(
                        f"[pinnacle] Recorded missing bet: {home} vs {away} "
                        f"{designation} @ {price} stake={risk} (pin_id={pin_id})"
                    )
                db.commit()

            logger.info(f"[pinnacle] Scrape pending: {recorded_new} new bet(s) recorded")

            # ---- Step 2: Fetch settled bets → auto-settle matched DB bets ----
            url_settled = f"{_API}/bets?status=settled&startDate={start}&endDate={end}"
            settled_data = await self._evaluate_api(page, url_settled)

            if settled_data and "__error" not in (settled_data if isinstance(settled_data, dict) else {}):
                api_settled = settled_data if isinstance(settled_data, list) else settled_data.get("bets", [])

                # Load all pending Pinnacle bets for matching
                pending = (
                    db.query(Bet, Event)
                    .join(Event, Bet.event_id == Event.id, isouter=True)
                    .filter(
                        Bet.profile_id == profile.id,
                        Bet.provider_id == "pinnacle",
                        Bet.result == "pending",
                    )
                    .all()
                )

                for b in api_settled:
                    risk = float(b.get("stake", b.get("riskAmount", 0)))
                    price = float(b.get("price", 0))
                    outcome_str = b.get("outcome", "none")
                    if outcome_str == "none" or risk <= 0:
                        continue

                    if outcome_str == "win":
                        status, payout = "won", risk * price
                    elif outcome_str == "loss":
                        status, payout = "lost", 0.0
                    else:
                        status, payout = "void", risk

                    # Match by odds + stake against pending bets
                    matched_bet = None
                    matched_event = None
                    for bet, event in pending:
                        if abs(bet.odds - price) < 0.01 and abs(bet.stake - risk) < 0.01 and bet.result == "pending":
                            matched_bet = bet
                            matched_event = event
                            break

                    if not matched_bet:
                        continue

                    event_name = ""
                    if matched_event:
                        h = matched_event.display_home or matched_event.home_team or ""
                        a = matched_event.display_away or matched_event.away_team or ""
                        event_name = f"{h} vs {a}" if h and a else h or a

                    svc.settle_bet(matched_bet.id, status, round(payout, 2))
                    settled.append(
                        {
                            "bet_id": matched_bet.id,
                            "event": event_name,
                            "market": matched_bet.market,
                            "outcome": matched_bet.outcome,
                            "odds": matched_bet.odds,
                            "stake": matched_bet.stake,
                            "result": status,
                            "payout": round(payout, 2),
                            "pl": round(payout - matched_bet.stake, 2),
                        }
                    )
                    # Remove from pending list so we don't double-match
                    pending = [(b, e) for b, e in pending if b.id != matched_bet.id]

                    logger.info(
                        f"[pinnacle] Settled bet #{matched_bet.id} {event_name} → {status} (payout={payout:.2f})"
                    )

                db.commit()

        except Exception as e:
            db.rollback()
            logger.error(f"[pinnacle] settle_all failed: {e}", exc_info=True)
            return {"error": str(e)}
        finally:
            db.close()

        # ---- Step 3: Sync balance ----
        new_balance = await self.sync_balance(page)

        # Summary
        total_staked = sum(s["stake"] for s in settled)
        total_payout = sum(s["payout"] for s in settled)
        wins = [s for s in settled if s["result"] == "won"]
        losses = [s for s in settled if s["result"] == "lost"]

        return {
            "recorded_new": recorded_new,
            "settled": len(settled),
            "settlements": settled,
            "summary": {
                "wins": len(wins),
                "losses": len(losses),
                "total_staked": round(total_staked, 2),
                "total_payout": round(total_payout, 2),
                "net_pl": round(total_payout - total_staked, 2),
            },
            "new_balance": new_balance,
        }

    async def check_live_price(self, page: Page, bet) -> float | None:
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
        outcome = getattr(bet, "outcome", "")
        target = self._find_market(markets, market, point, outcome=outcome)
        if not target:
            return None

        designation = _DESIGNATION_MAP.get(outcome)
        price_entry = next(
            (p for p in target.get("prices", []) if p.get("designation") == designation),
            None,
        )
        if not price_entry:
            return None

        american = price_entry["price"]
        decimal_odds = 1 + american / 100 if american > 0 else 1 + 100 / abs(american)

        edge = compute_edge("pinnacle", decimal_odds, fair_odds)
        logger.info(
            f"[pinnacle] Live: {bet.display_home} vs {bet.display_away} "
            f"{outcome} @ {decimal_odds:.2f} (fair {fair_odds:.2f}) edge={edge:.1f}%"
        )
        return edge

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_market(
        self,
        markets: list[dict],
        market_type: str,
        point: float | None,
        outcome: str = "",
    ) -> dict | None:
        """Find the matching market from the markets/straight response.

        Pinnacle keys a spread market by the HOME-perspective LINE: one market
        `s;0;s;1.5` carries home@+1.5 AND away@-1.5. `bet.point` is the LEG's
        own perspective (home leg.point=+line, away leg.point=-line), so for
        an away spread leg we flip the sign to find the right market.
        """
        # Pinnacle market keys: s;0;m (moneyline), s;0;s;{points} (spread), s;0;ou;{points} (total)
        key_prefix = _MARKET_KEY_MAP.get(market_type)
        if not key_prefix:
            return None

        line_point = -point if (market_type == "spread" and outcome == "away" and point is not None) else point

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
                    if line_point is not None:
                        mk_points = mk.split(";")[-1]
                        try:
                            if abs(float(mk_points) - line_point) < 0.01:
                                return m
                        except ValueError:
                            pass
                    else:
                        return m  # First non-alternate spread
            elif market_type == "total" and mk.startswith("s;0;ou;") and not m.get("isAlternate"):
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

    async def _post_api(self, page: Page, url: str, body: dict) -> dict | None:
        """POST JSON to Pinnacle API from the page's session."""
        try:
            body_json = json.dumps(body)
            js = """
                async ([url, bodyStr]) => {
                    const resp = await fetch(url, {
                        method: "POST",
                        credentials: "include",
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
