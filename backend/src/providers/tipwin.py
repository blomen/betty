"""
Tipwin Retriever - Proprietary sportsbook platform

Tipwin is a Malta-licensed bookmaker (MGA) with Spelinspektionen license for Sweden.
Uses a proprietary React SPA with REST API + SignalR WebSocket.

API endpoints:
- GET api-web.tipwin.se/v2/{agencyId}/offer/sport-menu — sport/league navigation
- GET api-web.tipwin.se/v2/{agencyId}/offer/data?filter={encoded} — full odds data
- SignalR at api-web.tipwin.se/v2/sync/signalr/ — live odds updates

Key notes:
- Agency ID for Sweden: 100683
- The `filter` parameter is an opaque, session-generated encoded string
- API rejects direct calls without proper session context
- reCAPTCHA v3 present but doesn't block passive browsing
- Strategy: Navigate to sport pages via Playwright, intercept API responses

URL structure: tipwin.se/sv/sports/full/{sport}
"""

from typing import Dict, Any, List, Optional
import asyncio
import logging
import re
from datetime import datetime

from ..core import StandardEvent
from ..core.browser_retriever import BrowserRetriever
from ..core.transport import BrowserTransport
from ..matching.normalizer import normalize_team_name, normalize_outcome

logger = logging.getLogger(__name__)


class TipwinRetriever(BrowserRetriever):
    """Retriever for Tipwin sportsbook (proprietary platform)."""

    SPORT_SLUGS: Dict[str, str] = {
        "football":          "football",
        "basketball":        "basketball",
        "tennis":            "tennis",
        "ice_hockey":        "ice-hockey",
        "american_football": "american-football",
        "baseball":          "baseball",
        "mma":               "mma",
        "esports":           "esports",
        "handball":          "handball",
        "volleyball":        "volleyball",
        "table_tennis":      "table-tennis",
    }

    # Individual sports where name reversal may be needed
    INDIVIDUAL_SPORTS = {"tennis", "mma", "table_tennis"}

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)
        self.site_url = config.get("site_url", "https://www.tipwin.se")

    async def extract(self, sport: str, limit: int = 500, **kwargs) -> List[StandardEvent]:
        """
        Extract events by navigating to Tipwin sport page and intercepting API responses.

        Flow:
        1. Navigate to /sv/sports/full/{sport}
        2. Intercept api-web.tipwin.se responses containing offer/event data
        3. Parse JSON into StandardEvents
        """
        slug = self.SPORT_SLUGS.get(sport)
        if not slug:
            logger.warning(f"[{self.provider_id}] Sport '{sport}' not supported")
            return []

        try:
            if not isinstance(self.transport, BrowserTransport):
                logger.error(f"[{self.provider_id}] TipwinRetriever requires BrowserTransport")
                return []

            await self.transport._ensure_browser()
            page = self.transport.page

            # Storage for intercepted API data
            api_responses: List[Dict] = []
            pending_tasks = []

            async def process_response(response):
                """Process intercepted API responses."""
                try:
                    data = await response.json()
                    if isinstance(data, dict):
                        api_responses.append(data)
                        logger.info(
                            f"[{self.provider_id}] Captured Tipwin API response "
                            f"(keys: {list(data.keys())[:5]})"
                        )
                except Exception as e:
                    logger.debug(f"[{self.provider_id}] Failed to parse response: {e}")

            def intercept_response(response):
                url = response.url
                if 'api-web.tipwin' in url or 'api-web-rest.tipwin' in url:
                    if response.status == 200 and 'offer' in url:
                        task = asyncio.create_task(process_response(response))
                        pending_tasks.append(task)

            page.on('response', intercept_response)

            # Navigate to sport page
            sport_url = f"{self.site_url}/sv/sports/full/{slug}"
            logger.info(f"[{self.provider_id}] Loading {sport_url}")

            # Handle cookie consent first on initial load
            if not self._session_ready:
                await page.goto(self.site_url, wait_until='load', timeout=30000)
                await self._handle_cookie_consent(page)
                await asyncio.sleep(2)
                self._session_ready = True

            await page.goto(sport_url, wait_until='load', timeout=60000)

            # Wait for SPA to render and API calls to fire
            await asyncio.sleep(8)

            # Scroll to load more content
            prev_count = len(api_responses)
            for scroll_i in range(5):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)
                if len(api_responses) == prev_count:
                    break
                prev_count = len(api_responses)

            page.remove_listener('response', intercept_response)

            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)

            logger.info(
                f"[{self.provider_id}] Intercepted {len(api_responses)} API responses"
            )

            # Parse all intercepted responses
            events = []
            for resp_data in api_responses:
                parsed = self._parse_api_response(resp_data, sport)
                events.extend(parsed)

            # If API interception got no events, fall back to DOM scraping
            if not events:
                logger.info(f"[{self.provider_id}] No API events, falling back to DOM scrape")
                events = await self._scrape_events_from_dom(page, sport)

            # Deduplicate
            seen = set()
            unique = []
            for e in events:
                key = f"{e.home_team}:{e.away_team}:{e.start_time}"
                if key not in seen:
                    seen.add(key)
                    unique.append(e)

            logger.info(f"[{self.provider_id}] {sport}: {len(unique)} events extracted")
            return unique[:limit]

        except Exception as e:
            logger.error(f"[{self.provider_id}] Error extracting {sport}: {e}", exc_info=True)
            return []

    async def _handle_cookie_consent(self, page):
        """Handle cookie consent dialogs."""
        selectors = [
            'button:has-text("Accept")',
            'button:has-text("Acceptera")',
            'button:has-text("Godkänn")',
            'button:has-text("OK")',
            '[class*="cookie"] button',
            '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll',
        ]
        for sel in selectors:
            try:
                await page.click(sel, timeout=3000)
                logger.info(f"[{self.provider_id}] Clicked cookie consent")
                await asyncio.sleep(1)
                return
            except Exception:
                continue

    # Tipwin bettingType abbreviation → our standard market type
    MARKET_ABRV_MAP = {
        "3way":          "1x2",
        "over-under":    "total",
        "handicap-hcp":  "spread",
    }

    # Tipwin outcome tip → our standard outcome name
    TIP_MAP = {
        "1": "home",
        "2": "away",
        "X": "draw",
        "+": "over",
        "-": "under",
    }

    def _parse_api_response(self, data: Dict, sport: str) -> List[StandardEvent]:
        """
        Parse Tipwin API response into StandardEvents.

        Tipwin structure:
        - data.offer: list of event objects
        - data.lookup.teams: {id: {name, ...}}
        - data.lookup.tournaments: {id: {name, ...}}
        - data.lookup.bettingTypes: {id: {abrv, name, ...}}
        - Each event: {eventId, event: {teamOneId, teamTwoId, startTime, tournamentId},
                       offers: [{bettingTypeId, offers: [{tip, value}]}]}
        """
        offer = data.get("offer")
        if not isinstance(offer, list) or not offer:
            return []

        lookup = data.get("lookup", {})
        teams_lookup = lookup.get("teams", {})
        tournaments_lookup = lookup.get("tournaments", {})
        btypes_lookup = lookup.get("bettingTypes", {})

        events = []
        for ev_data in offer:
            try:
                event = self._parse_tipwin_event(
                    ev_data, sport, teams_lookup, tournaments_lookup, btypes_lookup
                )
                if event:
                    events.append(event)
            except Exception as e:
                logger.debug(f"[{self.provider_id}] Failed to parse event: {e}")

        return events

    def _parse_tipwin_event(
        self,
        ev_data: Dict,
        sport: str,
        teams: Dict,
        tournaments: Dict,
        btypes: Dict,
    ) -> Optional[StandardEvent]:
        """Parse a single Tipwin event."""
        event_id = ev_data.get("eventId", "")
        ev = ev_data.get("event", {})

        # Resolve team names from lookup
        team1_id = ev.get("teamOneId", "")
        team2_id = ev.get("teamTwoId", "")
        team1 = teams.get(team1_id, {})
        team2 = teams.get(team2_id, {})
        home_raw = team1.get("name", "")
        away_raw = team2.get("name", "")

        if not home_raw or not away_raw:
            return None

        # Skip live events (bettingStatus != 1 means live or closed)
        if ev.get("bettingStatus") not in (1, None):
            return None
        if not ev.get("isUpcoming", True):
            return None

        home_team = normalize_team_name(home_raw)
        away_team = normalize_team_name(away_raw)

        # Parse start time
        start_time = self._parse_datetime(ev.get("startTime"))

        # Resolve league from tournament lookup
        tournament_id = ev.get("tournamentId", "")
        tournament = tournaments.get(tournament_id, {})
        league = tournament.get("name", "Unknown")

        # Parse markets from offers
        markets = []
        seen_types = set()

        for market_offer in ev_data.get("offers", []):
            btype_id = market_offer.get("bettingTypeId", "")
            btype = btypes.get(btype_id, {})
            abrv = btype.get("abrv", "")

            market_type = self.MARKET_ABRV_MAP.get(abrv)
            if not market_type or market_type in seen_types:
                continue

            # Parse outcomes from inner offers
            outcomes = []
            inner_offers = market_offer.get("offers", [])

            # Extract point value from market key if present
            point = None
            key = market_offer.get("key", {})
            if isinstance(key, dict):
                for pkey in ("total", "hcp", "handicap", "line"):
                    pval = key.get(pkey)
                    if pval is not None:
                        try:
                            point = float(pval)
                            break
                        except (ValueError, TypeError):
                            continue

            for offer in inner_offers:
                tip = offer.get("tip", "")
                value = offer.get("value")
                if value is None:
                    continue
                try:
                    odds = float(value)
                except (ValueError, TypeError):
                    continue
                if odds <= 1.0:
                    continue

                outcome_name = self.TIP_MAP.get(tip)
                if not outcome_name:
                    continue

                outcome_dict = {"name": outcome_name, "odds": odds}
                if point is not None:
                    outcome_dict["point"] = point
                outcomes.append(outcome_dict)

            if outcomes:
                markets.append({"type": market_type, "outcomes": outcomes})
                seen_types.add(market_type)

        # Dedup: prefer 1x2 over moneyline
        types = {m["type"] for m in markets}
        if "1x2" in types and "moneyline" in types:
            markets = [m for m in markets if m["type"] != "moneyline"]

        if not markets:
            return None

        return StandardEvent(
            id=f"tipwin_{event_id}",
            name=f"{home_raw} vs {away_raw}",
            provider=self.provider_id,
            sport=sport,
            league=str(league),
            home_team=home_team,
            away_team=away_team,
            start_time=start_time,
            markets=markets,
        )

    async def _scrape_events_from_dom(self, page, sport: str) -> List[StandardEvent]:
        """
        Fallback: scrape events directly from the rendered DOM.

        Tipwin renders events as list items with team names and odds buttons.
        """
        try:
            raw_events = await page.evaluate("""() => {
                const events = [];
                // Find event containers - Tipwin uses various class patterns
                const containers = document.querySelectorAll(
                    '[class*="event-row"], [class*="match-row"], [class*="EventRow"], ' +
                    '[class*="event-item"], [class*="match-item"], [data-event-id]'
                );

                for (const el of containers) {
                    try {
                        // Extract team names
                        const teamEls = el.querySelectorAll(
                            '[class*="team"], [class*="participant"], [class*="player"]'
                        );
                        if (teamEls.length < 2) continue;

                        const home = teamEls[0].textContent.trim();
                        const away = teamEls[1].textContent.trim();
                        if (!home || !away) continue;

                        // Extract odds values
                        const oddsEls = el.querySelectorAll(
                            '[class*="odd-value"], [class*="odds-value"], ' +
                            '[class*="price"], [class*="coefficient"], button[class*="odd"]'
                        );
                        const odds = [];
                        for (const oe of oddsEls) {
                            const text = oe.textContent.trim();
                            const val = parseFloat(text.replace(',', '.'));
                            if (!isNaN(val) && val > 1.0) {
                                odds.push(val);
                            }
                        }

                        // Extract time
                        const timeEl = el.querySelector('[class*="time"], [class*="date"], time');
                        const time = timeEl ? timeEl.textContent.trim() : '';

                        // Extract event ID
                        const eventId = el.getAttribute('data-event-id') ||
                                         el.getAttribute('data-id') || '';

                        if (odds.length >= 2) {
                            events.push({
                                id: eventId,
                                home: home,
                                away: away,
                                odds: odds,
                                time: time,
                            });
                        }
                    } catch(e) {}
                }
                return events;
            }""")

            events = []
            for raw in raw_events:
                event = self._parse_dom_event(raw, sport)
                if event:
                    events.append(event)

            logger.info(f"[{self.provider_id}] DOM scrape: {len(events)} events")
            return events

        except Exception as e:
            logger.debug(f"[{self.provider_id}] DOM scrape failed: {e}")
            return []

    def _parse_dom_event(self, raw: Dict, sport: str) -> Optional[StandardEvent]:
        """Parse a DOM-scraped event."""
        home_raw = raw.get("home", "")
        away_raw = raw.get("away", "")
        if not home_raw or not away_raw:
            return None

        home_team = normalize_team_name(home_raw)
        away_team = normalize_team_name(away_raw)

        odds_list = raw.get("odds", [])
        if len(odds_list) < 2:
            return None

        # Build market from odds count
        outcomes = []
        if len(odds_list) == 3:
            # 1x2 market
            outcomes = [
                {"name": "home", "odds": odds_list[0]},
                {"name": "draw", "odds": odds_list[1]},
                {"name": "away", "odds": odds_list[2]},
            ]
            market_type = "1x2"
        elif len(odds_list) == 2:
            outcomes = [
                {"name": "home", "odds": odds_list[0]},
                {"name": "away", "odds": odds_list[1]},
            ]
            market_type = "moneyline"
        else:
            return None

        event_id = raw.get("id") or f"{home_team}_{away_team}"

        return StandardEvent(
            id=f"tipwin_{event_id}",
            name=f"{home_raw} vs {away_raw}",
            provider=self.provider_id,
            sport=sport,
            league="Unknown",
            home_team=home_team,
            away_team=away_team,
            start_time=None,
            markets=[{"type": market_type, "outcomes": outcomes}],
        )

    @staticmethod
    def _parse_datetime(dt_val: Any) -> Optional[datetime]:
        """Parse datetime from various formats."""
        if not dt_val:
            return None
        try:
            if isinstance(dt_val, str):
                return datetime.fromisoformat(dt_val.replace('Z', '+00:00'))
            elif isinstance(dt_val, (int, float)):
                ts = dt_val / 1000 if dt_val > 10**10 else dt_val
                return datetime.fromtimestamp(ts)
        except Exception:
            pass
        return None

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """Not used — browser-based extraction."""
        return []
