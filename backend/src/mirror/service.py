"""MirrorService — orchestrates bet interception, parsing, storage, and notification."""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse, unquote

from ..db.models import get_session, BetTrace, Bet
from ..services.bet_service import BetService
from .interceptor import BetInterceptor
from .parsers.gecko import GeckoBetParser

logger = logging.getLogger(__name__)


class MirrorService:
    """Coordinates BetInterceptor + parsing + BetService + Broadcaster."""

    def __init__(self, broadcaster=None, provider_id: str | None = None, discovery: bool = True):
        self.broadcaster = broadcaster
        self.provider_id = provider_id
        self.discovery = discovery
        self.parser = GeckoBetParser()
        # Cache: gecko_event_id → {home_team, away_team, event_name}
        self._event_cache: dict[str, dict[str, str]] = {}
        # Pending settlements awaiting confirmation
        self._pending_settlements: list[dict] = []
        self.interceptor = BetInterceptor(
            on_bet_response=self._handle_bet_response,
            on_event_data=self._handle_event_data,
            on_bet_history=self._handle_bet_history,
            on_financial_data=self._handle_financial_data,
        )

    async def start(self, site_url: str | None = None):
        """Start the mirror browser."""
        await self.interceptor.start()

    async def stop(self):
        """Stop the mirror browser."""
        await self.interceptor.stop()

    def get_status(self) -> dict[str, Any]:
        """Get current mirror status."""
        return self.interceptor.get_status()

    async def _handle_event_data(self, url: str, response_body: str):
        """Cache event participant data from events-table API responses."""
        try:
            data = json.loads(response_body)
            events = data.get("data", {}).get("events", [])
            for event in events:
                event_id = event.get("id", "")
                participants = event.get("participants", [])
                if len(participants) >= 2:
                    participants.sort(key=lambda p: p.get("side", 0))
                    from ..matching.normalizer import normalize_team_name
                    home_label = participants[0].get("label", "")
                    away_label = participants[1].get("label", "")
                    self._event_cache[event_id] = {
                        "home_team": normalize_team_name(home_label),
                        "away_team": normalize_team_name(away_label),
                        "event_name": f"{home_label} vs {away_label}",
                    }
            if events:
                logger.debug(f"[mirror] Cached {len(events)} event(s) from events-table response")
        except (json.JSONDecodeError, Exception) as e:
            logger.debug(f"[mirror] Could not parse event data: {e}")

    async def _handle_bet_history(self, url: str, response_body: str, request_body: str | None = None):
        """Auto-settle pending bets from bet history responses.

        Altenar status codes: 1=won, 2=lost, 3=void/cancelled, 4=cashout.
        """
        try:
            data = json.loads(response_body)
        except json.JSONDecodeError:
            return

        bets = data.get("bets", [])
        if not bets:
            return

        provider_id = self._detect_provider_from_request(request_body) or self._detect_provider(url)
        logger.info(f"[mirror] Bet history intercepted: {len(bets)} bets from {provider_id}")

        staged = await asyncio.to_thread(self._stage_settlements_sync, bets, provider_id)
        if staged:
            self._pending_settlements = staged  # Replace — latest history is most accurate
            wins = [s for s in staged if s["result"] == "won"]
            losses = [s for s in staged if s["result"] == "lost"]
            total_staked = sum(s["stake"] for s in staged)
            total_payout = sum(s["payout"] for s in staged)
            net = total_payout - total_staked
            logger.info(
                f"[mirror] Staged {len(staged)} settlement(s) from {provider_id}: "
                f"{len(wins)}W {len(losses)}L, net={net:+.0f} SEK — confirm via API"
            )
            self._notify("settlements_pending", {
                "provider": provider_id,
                "count": len(staged),
                "wins": len(wins),
                "losses": len(losses),
                "total_staked": total_staked,
                "total_payout": total_payout,
                "net": net,
                "settlements": staged,
            })

        # Also store trace for audit
        await asyncio.to_thread(
            self._store_trace_sync, provider_id, url, request_body, response_body, "history"
        )

    def _stage_settlements_sync(self, history_bets: list[dict], provider_id: str) -> list[dict]:
        """Match bet history against pending bets — stage for confirmation, don't commit."""
        STATUS_MAP = {1: "won", 2: "lost", 3: "void", 4: "cashout"}

        db = get_session()
        staged: list[dict] = []
        try:
            pending = db.query(Bet).filter(
                Bet.result == "pending",
                Bet.provider_id == provider_id,
            ).all()
            if not pending:
                return []

            for hb in history_bets:
                result = STATUS_MAP.get(hb.get("status"))
                if not result:
                    continue

                stake = float(hb.get("totalStake", 0))
                odds = float(hb.get("totalOdds", 0))
                payout = float(hb.get("totalWin", 0))
                event_name = hb.get("eventName", "")

                matched_bet = None
                for bet in pending:
                    if bet.result != "pending":
                        continue
                    if abs(bet.stake - stake) > 0.01:
                        continue
                    if abs(bet.odds - odds) > 0.01:
                        continue
                    matched_bet = bet
                    break

                if not matched_bet:
                    continue

                pending.remove(matched_bet)
                staged.append({
                    "bet_id": matched_bet.id,
                    "provider": provider_id,
                    "event": event_name,
                    "odds": odds,
                    "stake": stake,
                    "result": result,
                    "payout": payout,
                })

        except Exception as e:
            logger.error(f"[mirror] Error matching bets: {e}", exc_info=True)
        finally:
            db.close()
        return staged

    def confirm_settlements(self) -> dict:
        """Apply all pending settlements to the database. Returns summary."""
        if not self._pending_settlements:
            return {"settled": 0, "error": "No pending settlements"}

        db = get_session()
        settled = 0
        try:
            bet_service = BetService(db)
            for s in self._pending_settlements:
                bet_service.settle_bet(s["bet_id"], s["result"], s["payout"])
                settled += 1
                logger.info(
                    f"[mirror] Confirmed: bet #{s['bet_id']} {s['event']} "
                    f"→ {s['result']} (payout={s['payout']})"
                )
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[mirror] Error confirming settlements: {e}", exc_info=True)
            return {"settled": settled, "error": str(e)}
        finally:
            db.close()

        summary = self._pending_settlements.copy()
        self._pending_settlements.clear()
        return {"settled": settled, "settlements": summary}

    def reject_settlements(self) -> dict:
        """Discard all pending settlements."""
        count = len(self._pending_settlements)
        self._pending_settlements.clear()
        return {"rejected": count}

    def get_pending_settlements(self) -> list[dict]:
        """Return current pending settlements for review."""
        return self._pending_settlements

    async def _handle_financial_data(self, url: str, response_body: str):
        """Auto-sync balance from intercepted financial data."""
        try:
            data = json.loads(response_body)
        except json.JSONDecodeError:
            return

        provider_id = self._detect_provider(url)
        if provider_id == "unknown":
            return

        balance = self._extract_balance(provider_id, data)
        if balance is not None:
            await asyncio.to_thread(self._sync_balance, provider_id, balance)

        # Store trace for audit
        await asyncio.to_thread(
            self._store_trace_sync, provider_id, url, None, response_body, "balance"
        )

    def _extract_balance(self, provider_id: str, data: dict) -> float | None:
        """Extract cash balance from provider-specific response format."""
        try:
            # Kambi / Unibet: {"balance": {"cash": 384.10, ...}}
            if "balance" in data and isinstance(data["balance"], dict):
                bal = data["balance"]
                if "cash" in bal:
                    return float(bal["cash"])
                if "total" in bal:
                    return float(bal["total"])

            # Altenar (quickcasino, betinia, etc.):
            # {"result": {"cash": {"total": 243.5, ...}}}
            result = data.get("result", {})
            if isinstance(result, dict) and "cash" in result:
                cash = result["cash"]
                if isinstance(cash, dict):
                    return float(cash.get("total", cash.get("available", 0)))
                return float(cash)

            # Gecko V2 / Spelklubben:
            # {"Balances": {"SEK": {"Real": {"Balance": 1087.14}}}}
            balances = data.get("Balances", {})
            for currency, parts in balances.items():
                if isinstance(parts, dict):
                    real = parts.get("Real", parts.get("Total", {}))
                    if isinstance(real, dict) and "Balance" in real:
                        return float(real["Balance"])

        except (TypeError, ValueError, KeyError) as e:
            logger.debug(f"[mirror] Could not extract balance for {provider_id}: {e}")
        return None

    def _sync_balance(self, provider_id: str, balance: float):
        """Update profile balance for the given provider."""
        from ..repositories.profile_repo import ProfileRepo

        db = get_session()
        try:
            repo = ProfileRepo(db)
            profile = repo.get_active()
            old_balance = repo.get_balance(profile.id, provider_id)
            repo.set_balance(profile.id, provider_id, balance)
            db.commit()
            if abs((old_balance or 0) - balance) > 0.01:
                logger.info(
                    f"[mirror] Balance synced: {provider_id} "
                    f"{old_balance:.2f} → {balance:.2f} SEK"
                )
                self._notify("balance_synced", {
                    "provider": provider_id,
                    "balance": balance,
                    "previous": old_balance,
                })
        except Exception as e:
            db.rollback()
            logger.error(f"[mirror] Balance sync failed for {provider_id}: {e}")
        finally:
            db.close()

    async def _handle_bet_response(
        self, url: str, request_body: str | None, response_body: str, page_url: str | None = None
    ):
        """Process an intercepted bet placement response — any platform."""
        provider_id = (
            self._detect_provider_from_request(request_body)
            or self._detect_provider(url)
        )

        try:
            body = json.loads(response_body)
        except json.JSONDecodeError:
            logger.warning(f"[mirror] Invalid JSON response from {url}")
            await asyncio.to_thread(self._store_trace_sync, provider_id, url, request_body, response_body, "failed")
            return

        # Try to extract basic bet info from any platform
        bet_info = self._extract_bet_info(url, body, request_body)

        # Store the raw trace
        await asyncio.to_thread(
            self._store_trace_sync, provider_id, url, request_body, response_body, "bet_placed"
        )

        # Toast notification with whatever info we could extract
        toast = {
            "status": "ok",
            "provider": provider_id,
            "event": bet_info.get("event_name", "Unknown event"),
            "market": bet_info.get("market"),
            "outcome": bet_info.get("outcome"),
            "odds": bet_info.get("odds"),
            "stake": bet_info.get("stake"),
            "matched": False,
        }
        logger.info(
            f"[mirror] Bet recorded: {provider_id} — {toast['event']} "
            f"@ {toast['odds']} × {toast['stake']}"
        )
        self._notify("bet_mirrored", toast)

    def _extract_bet_info(self, url: str, body: dict, request_body: str | None) -> dict:
        """Best-effort extraction of bet info from any platform response.

        Returns dict with whatever fields could be extracted:
        event_name, odds, stake, market, outcome, confirmation_id
        """
        info: dict[str, Any] = {}
        req: dict = {}
        if request_body:
            try:
                req = json.loads(request_body) if isinstance(request_body, str) else request_body
            except (json.JSONDecodeError, TypeError):
                pass

        url_lower = url.lower()

        # --- Altenar (placeWidget) ---
        if "placewidget" in url_lower:
            bets = body.get("bets", [])
            if bets:
                b = bets[0]
                info["confirmation_id"] = str(b.get("id", ""))
                info["odds"] = b.get("totalOdds")
                info["stake"] = b.get("totalStake")
                sels = b.get("selections", [])
                if sels:
                    s = sels[0]
                    info["event_name"] = s.get("eventName", "")
                    info["outcome"] = s.get("name", "")
                    info["market"] = s.get("marketName", "")
            # Request has richer data
            markets = req.get("betMarkets", [])
            if markets and not info.get("event_name"):
                m = markets[0]
                info["event_name"] = m.get("eventName", "")
                odds_list = m.get("odds", [])
                if odds_list:
                    info["outcome"] = odds_list[0].get("selectionName", "")
                    info["market"] = odds_list[0].get("marketName", "")
            stakes = req.get("stakes", [])
            if stakes and not info.get("stake"):
                info["stake"] = stakes[0]
            return info

        # --- Gecko V2 (coupons) ---
        if "/api/sb/" in url_lower and "coupon" in url_lower:
            coupon = body.get("couponStatus", {})
            info["confirmation_id"] = str(coupon.get("couponId", ""))
            # Odds/stake/market from request
            bets = req.get("bets", [])
            if bets:
                bet = bets[0]
                info["stake"] = bet.get("stake")
                sels = bet.get("betSelections", [])
                if sels:
                    info["odds"] = sels[0].get("odds")
            return info

        # --- Kambi (coupon.json) ---
        if "coupon" in url_lower and "kambi" in url_lower:
            coupon = body.get("coupon", body)
            info["confirmation_id"] = str(coupon.get("couponRef", ""))
            # Kambi uses integer milliodds (1840 = 1.84) and centistake (140000 = 1400.00)
            bets = coupon.get("bets", [])
            if bets:
                info["odds"] = bets[0].get("betOdds", 0) / 1000
                info["stake"] = bets[0].get("stake", 0) / 100
            events = coupon.get("events", [])
            if events:
                e = events[0]
                info["event_name"] = e.get("eventName", "")
                info["home_team"] = e.get("homeName")
                info["away_team"] = e.get("awayName")
            outcomes = coupon.get("outcomes", [])
            if outcomes:
                info["outcome"] = outcomes[0].get("label", "")
            bet_offers = coupon.get("betOffers", [])
            if bet_offers:
                info["market"] = bet_offers[0].get("criterion", "")
            return info

        # --- Generic fallback: scan for common field names ---
        for key in ("totalStake", "stake", "amount"):
            if key in body:
                info["stake"] = body[key]
                break
            if key in req:
                info["stake"] = req[key]
                break
        for key in ("totalOdds", "odds", "price"):
            if key in body:
                info["odds"] = body[key]
                break
        for key in ("eventName", "event_name", "matchName"):
            if key in body:
                info["event_name"] = body[key]
                break

        return info

    def _extract_teams_from_page_url(self, page_url: str, parsed: dict):
        """Extract team names from Gecko V2 event page URL slug.

        Gecko event page URLs typically look like:
          /sport/fotboll/.../team1-vs-team2-{eventId}
          /sport/football/.../team1-v-team2-{eventId}
        """
        try:
            path = unquote(urlparse(page_url).path)
            # Match "team1-vs-team2" or "team1-v-team2" patterns in the URL
            match = re.search(r'/([^/]+?)(?:-vs?-|%20vs?%20)([^/]+?)(?:-[a-zA-Z0-9_]{10,})?/?$', path, re.IGNORECASE)
            if match:
                from ..matching.normalizer import normalize_team_name
                home = match.group(1).replace("-", " ").strip()
                away = match.group(2).replace("-", " ").strip()
                if len(home) > 2 and len(away) > 2:
                    parsed["home_team"] = normalize_team_name(home)
                    parsed["away_team"] = normalize_team_name(away)
                    parsed["event_name"] = f"{home} vs {away}"
                    logger.info(f"[mirror] Resolved from page URL: {parsed['event_name']}")
        except Exception as e:
            logger.debug(f"[mirror] Could not parse teams from page URL: {e}")

    async def _enrich_from_page_title(self, parsed: dict):
        """Extract team names from the browser page title as last resort."""
        context = self.interceptor.context
        if not context or not context.pages:
            logger.debug("[mirror] No browser pages available for title extraction")
            return

        try:
            page = context.pages[0]
            title = await page.title()
            if not title:
                return

            # Page titles often look like "Team1 - Team2 | Spelklubben" or "Team1 vs Team2"
            for sep in [" - ", " vs ", " vs. ", " v "]:
                if sep in title:
                    parts = title.split(sep, 1)
                    home = parts[0].strip()
                    # Strip trailing site name after | or –
                    away = re.split(r'\s*[|–—]\s*', parts[1])[0].strip()
                    if len(home) > 2 and len(away) > 2:
                        from ..matching.normalizer import normalize_team_name
                        parsed["home_team"] = normalize_team_name(home)
                        parsed["away_team"] = normalize_team_name(away)
                        parsed["event_name"] = f"{home} vs {away}"
                        logger.info(f"[mirror] Resolved from page title: {parsed['event_name']}")
                        return
        except Exception as e:
            logger.debug(f"[mirror] Could not read page title: {e}")

    async def _enrich_from_gecko_api(self, bet_url: str, parsed: dict):
        """Fetch event details from Gecko API to resolve team names."""
        gecko_id = parsed.get("gecko_event_id", "")
        if not gecko_id:
            return

        # Derive API base from the bet URL domain
        origin = urlparse(bet_url)
        api_base = f"{origin.scheme}://{origin.netloc}"

        # Use the interceptor's browser context to make the API call
        context = self.interceptor.context
        if not context:
            logger.warning("[mirror] No browser context for Gecko API enrichment")
            return
        if not context.pages:
            logger.warning("[mirror] No browser pages open for Gecko API enrichment")
            return

        try:
            # Include required params that the real site uses
            api_url = (
                f"{api_base}/api/sb/v1/widgets/events-table/v2"
                f"?categoryIds=1&eventIds=f-{gecko_id}"
                f"&eventPhase=Prematch&eventSortBy=Popularity"
                f"&maxMarketCount=2&priceFormats=1"
            )
            logger.debug(f"[mirror] Enrichment API call: {api_url}")
            resp = await context.request.get(api_url, timeout=5000)
            if resp.status != 200:
                logger.warning(f"[mirror] Gecko event lookup returned HTTP {resp.status} for gecko_id={gecko_id}")
                return

            data = await resp.json()
            events = data.get("data", {}).get("events", [])
            if not events:
                logger.warning(f"[mirror] No events returned for gecko_id={gecko_id} (empty response)")
                return

            event = events[0]
            participants = event.get("participants", [])
            if len(participants) < 2:
                logger.warning(f"[mirror] Event {gecko_id} has <2 participants: {participants}")
                return

            participants.sort(key=lambda p: p.get("side", 0))
            from ..matching.normalizer import normalize_team_name
            parsed["home_team"] = normalize_team_name(participants[0].get("label", ""))
            parsed["away_team"] = normalize_team_name(participants[1].get("label", ""))
            parsed["event_name"] = f"{participants[0].get('label', '')} vs {participants[1].get('label', '')}"
            logger.info(f"[mirror] Resolved via Gecko API: {parsed['event_name']}")

            # Also cache for future bets on same event
            self._event_cache[gecko_id] = {
                "home_team": parsed["home_team"],
                "away_team": parsed["away_team"],
                "event_name": parsed["event_name"],
            }

        except Exception as e:
            logger.error(f"[mirror] Gecko API enrichment failed for gecko_id={gecko_id}: {e}", exc_info=True)

    # Altenar integration codes → our provider IDs
    _ALTENAR_INTEGRATION_MAP = {
        "campose": "campobet",
        "quickcasinose": "quickcasino",
        "betiniase2": "betinia",
        "lodurse": "lodur",
        "dbetse": "dbet",
        "swiperse": "swiper",
    }

    def _detect_provider_from_request(self, request_body: str | None) -> str | None:
        """Extract provider from Altenar integration field in request body."""
        if not request_body:
            return None
        try:
            req = json.loads(request_body)
            integration = req.get("integration", "").lower()
            if integration:
                # Exact match first
                if integration in self._ALTENAR_INTEGRATION_MAP:
                    return self._ALTENAR_INTEGRATION_MAP[integration]
                # Fuzzy fallback — check if any known provider name is a substring
                for keyword in self._ALTENAR_INTEGRATION_MAP.values():
                    if keyword in integration:
                        return keyword
        except (json.JSONDecodeError, Exception):
            pass
        return None

    # Kambi operator codes in API paths → provider ID
    _KAMBI_OPERATOR_MAP = {
        "ubse": "unibet",
        "ubdk": "unibet",
        "ubno": "unibet",
        "ubfi": "unibet",
        "888se": "888sport",
        "888dk": "888sport",
        "leose": "leovegas",
        "expse": "expekt",
        "speedyse": "speedybet",
        "x3000se": "x3000",
        "gbse": "goldenbull",
        "1x2se": "1x2",
    }

    def _detect_provider(self, url: str) -> str:
        """Best-effort provider detection from URL domain or path."""
        url_lower = url.lower()
        # Direct domain matches
        domain_map = {
            "spelklubben": "spelklubben",
            "betsson": "betsson",
            "betsafe": "betsafe",
            "nordicbet": "nordicbet",
            "hajper": "hajper",
            "quickcasino": "quickcasino",
            "comeon": "comeon",
            "pinnacle": "pinnacle",
            "unibet": "unibet",
            "888sport": "888sport",
            "leovegas": "leovegas",
            "expekt": "expekt",
            "campobet": "campobet",
            "betinia": "betinia",
            "lodur": "lodur",
            "swiper": "swiper",
            "dbet": "dbet",
        }
        for keyword, provider_id in domain_map.items():
            if keyword in url_lower:
                return provider_id

        # Kambi operator code in URL path (e.g. /ubse/coupon.json)
        if "kambi" in url_lower:
            for code, provider_id in self._KAMBI_OPERATOR_MAP.items():
                if f"/{code}/" in url_lower:
                    return provider_id

        # Altenar shared gateway — check for integration in URL params
        if "altenar" in url_lower or "biahosted" in url_lower:
            for keyword in self._KNOWN_PROVIDERS:
                if keyword in url_lower:
                    return keyword

        return "unknown"

    def _process_bet_sync(
        self, provider_id: str, url: str, request_body: str | None, response_body: str, parsed: dict
    ) -> dict[str, Any]:
        """Synchronous: create bet + store trace (runs in thread)."""
        db = get_session()
        try:
            confirmation_id = parsed["confirmation_id"]

            # Dedup
            existing = db.query(Bet).filter(Bet.confirmation_id == confirmation_id).first()
            if existing:
                logger.info(f"[mirror] Bet {confirmation_id} already logged (dedup)")
                return {"status": "duplicate", "confirmation_id": confirmation_id, "provider": provider_id}

            # Match event
            event_id = self._match_event(db, parsed)

            # Create bet
            bet_service = BetService(db)
            bet_result = bet_service.create_bet(
                event_id=event_id,
                provider_id=provider_id,
                market=parsed.get("market"),
                outcome=parsed.get("outcome"),
                odds=parsed["odds"],
                stake=parsed["stake"],
                point=parsed.get("point"),
                bet_type="mirror",
            )

            if "error" not in bet_result:
                bet_obj = db.get(Bet, bet_result["bet_id"])
                if bet_obj:
                    bet_obj.confirmation_id = confirmation_id

            db.commit()

            bet_id = bet_result.get("bet_id")
            parse_status = "ok" if event_id else "unmatched"
            if "error" in bet_result:
                parse_status = "failed"

            self._store_trace(
                db=db, provider_id=provider_id, url=url,
                request_body=request_body, response_body=response_body,
                parse_status=parse_status, provider_bet_id=confirmation_id, bet_id=bet_id,
            )
            db.commit()

            return {
                "status": "ok" if "error" not in bet_result else "error",
                "confirmation_id": confirmation_id,
                "provider": provider_id,
                "event": parsed.get("event_name", "Unknown event"),
                "market": parsed.get("market"),
                "outcome": parsed.get("outcome"),
                "odds": parsed["odds"],
                "stake": parsed["stake"],
                "matched": event_id is not None,
                "error": bet_result.get("error"),
            }
        except Exception as e:
            db.rollback()
            logger.error(f"[mirror] Error processing bet: {e}", exc_info=True)
            return {"status": "error", "error": str(e), "provider": provider_id}
        finally:
            db.close()

    def _store_trace_sync(
        self, provider_id: str, url: str, request_body: str | None, response_body: str, parse_status: str
    ):
        """Store trace in a new DB session (for rejected/failed bets)."""
        db = get_session()
        try:
            self._store_trace(db, provider_id, url, request_body, response_body, parse_status)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[mirror] Error storing trace: {e}")
        finally:
            db.close()

    def _store_trace(
        self, db, provider_id: str, url: str, request_body: str | None, response_body: str,
        parse_status: str, provider_bet_id: str | None = None, bet_id: int | None = None,
    ) -> BetTrace:
        """Insert a BetTrace record."""
        trace = BetTrace(
            timestamp=datetime.now(timezone.utc),
            provider_id=provider_id,
            request_url=url,
            request_body=request_body,
            response_body=response_body,
            bet_id=bet_id,
            provider_bet_id=provider_bet_id,
            parse_status=parse_status,
        )
        db.add(trace)
        return trace

    def _match_event(self, db, parsed: dict) -> str | None:
        """Try to match intercepted bet to an internal Event."""
        from ..db.models import Event
        from rapidfuzz import fuzz
        from datetime import timedelta

        home = parsed.get("home_team")
        away = parsed.get("away_team")
        if not home or not away:
            logger.warning(f"[mirror] Cannot match — no team names resolved")
            return None

        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=7)
        events = db.query(Event).filter(
            Event.home_team.isnot(None),
            Event.away_team.isnot(None),
            Event.start_time >= now - timedelta(hours=3),
            Event.start_time <= cutoff,
        ).all()

        best_match = None
        best_score = 0.0

        for event in events:
            home_score = fuzz.ratio(home, event.home_team or "")
            away_score = fuzz.ratio(away, event.away_team or "")
            combined = (home_score + away_score) / 2
            if combined > best_score:
                best_score = combined
                best_match = event

        if best_match and best_score >= 75:
            logger.info(f"[mirror] Matched to event {best_match.id} (score={best_score:.0f})")
            return best_match.id

        logger.warning(f"[mirror] No match for {home} vs {away} (best={best_score:.0f})")
        return None

    def _notify(self, event_type: str, data: dict):
        """Publish SSE event if broadcaster is available."""
        if self.broadcaster:
            self.broadcaster.publish(event_type, data)
