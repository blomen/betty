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
- Strategy: Navigate to /sv/sports/full/, intercept paginated API responses

Data structure (full listing page):
- Response has 'items' key (list of sport/category groups)
- items[].items[] = tournament groups with 'events' array
- events[].event = {teamOneId, teamTwoId, startTime, ...}
- events[].offers = [{bettingTypeId, key, offers: [{tip, value}]}]
- lookup = {teams, tournaments, bettingTypes, sports, categories}

URL structure: tipwin.se/sv/sports/full/ (all sports, paginated)
"""

from typing import Dict, Any, List, Optional
import asyncio
import logging
from datetime import datetime

from ..core import StandardEvent
from ..core.browser_retriever import BrowserRetriever
from ..core.transport import BrowserTransport
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)


class TipwinRetriever(BrowserRetriever):
    """Retriever for Tipwin sportsbook (proprietary platform)."""

    # Tipwin sport abbreviation → our canonical sport name
    SPORT_ABRV_MAP: Dict[str, str] = {
        "soccer":            "football",
        "basketball":        "basketball",
        "tennis":            "tennis",
        "ice-hockey":        "ice_hockey",
        "american-football": "american_football",
        "baseball":          "baseball",
        "mma":               "mma",
        "esports":           "esports",
        "handball":          "handball",
        "volleyball":        "volleyball",
        "table-tennis":      "table_tennis",
        "rugby":             "rugby",
        "darts":             "darts",
        "biathlon":          "biathlon",
        "boxing":            "boxing",
    }

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

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)
        self.site_url = config.get("site_url", "https://www.tipwin.se")
        # Cache all events on first extraction, then filter by sport
        self._all_events: Optional[Dict[str, List[StandardEvent]]] = None

    async def extract(self, sport: str, limit: int = 500, **kwargs) -> List[StandardEvent]:
        """
        Extract events for a given sport.

        On first call, loads all events from /sv/sports/full/ with pagination.
        Subsequent calls return cached results filtered by sport.
        Health check (limit=1) returns quickly without full extraction.
        """
        # Health check — return quickly without full pagination
        if limit <= 1 and self._all_events is None:
            return await self._quick_health_check()

        # Extract all sports on first call
        if self._all_events is None:
            self._all_events = await self._extract_all()

        events = self._all_events.get(sport, [])
        logger.debug(f"[{self.provider_id}] {sport}: {len(events)} events")
        return events[:limit]

    async def _quick_health_check(self) -> List[StandardEvent]:
        """Quick health check: load first page only to verify site is accessible."""
        try:
            if not isinstance(self.transport, BrowserTransport):
                return []
            await self.transport._ensure_browser()
            page = self.transport.page

            if not self._session_ready:
                await page.goto(self.site_url, wait_until='load', timeout=30000)
                await self._handle_cookie_consent(page)
                await asyncio.sleep(2)
                self._session_ready = True

            # Just verify the page loads — return empty list (health check only cares about no exception)
            return []
        except Exception as e:
            logger.error(f"[{self.provider_id}] Health check failed: {e}")
            raise

    async def _extract_all(self) -> Dict[str, List[StandardEvent]]:
        """
        Navigate to /sv/sports/full/ and paginate through all pages
        to collect events for every sport.

        Optimized: uses direct ?page=N URL navigation instead of clicking
        pagination buttons. Each page navigation triggers the API call with
        the correct opaque filter parameter automatically.
        """
        try:
            if not isinstance(self.transport, BrowserTransport):
                logger.error(f"[{self.provider_id}] TipwinRetriever requires BrowserTransport")
                return {}

            await self.transport._ensure_browser()
            page = self.transport.page

            # Storage for intercepted API data
            api_responses: List[Dict] = []
            pending_tasks: List[asyncio.Task] = []

            async def process_response(response):
                try:
                    data = await response.json()
                    if isinstance(data, dict):
                        has_items = 'items' in data and isinstance(data.get('items'), list)
                        has_offer = 'offer' in data and isinstance(data.get('offer'), list) and len(data['offer']) > 0
                        if has_items or has_offer:
                            api_responses.append(data)
                except Exception:
                    pass

            def intercept_response(response):
                url = response.url
                if ('api-web.tipwin' in url or 'api-web-rest.tipwin' in url) \
                        and response.status == 200 and 'offer' in url:
                    task = asyncio.create_task(process_response(response))
                    pending_tasks.append(task)

            page.on('response', intercept_response)

            # Handle cookie consent on initial load
            if not self._session_ready:
                await page.goto(self.site_url, wait_until='load', timeout=30000)
                await self._handle_cookie_consent(page)
                await asyncio.sleep(2)
                self._session_ready = True

            # Navigate to full sports listing (page 1)
            full_url = f"{self.site_url}/sv/sports/full/"
            logger.debug(f"[{self.provider_id}] Loading {full_url}")
            await page.goto(full_url, wait_until='domcontentloaded', timeout=30000)
            await asyncio.sleep(2)

            # Wait for pending tasks from initial load
            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)

            # Get total pages from the items-format response
            total_pages = 1
            for resp in api_responses:
                if 'items' in resp and isinstance(resp.get('items'), list):
                    total = resp.get('totalNumberOfItems', 0)
                    ps = resp.get('pageSize', 5)
                    if total and ps:
                        total_pages = (total + ps - 1) // ps
                        break

            max_pages = min(total_pages, 120)  # Safety cap
            logger.debug(
                f"[{self.provider_id}] Full listing: {max_pages} pages "
                f"(initial captured: {len(api_responses)} responses)"
            )

            # Paginate via direct ?page=N URL navigation (faster than button clicks)
            for pg in range(2, max_pages + 1):
                try:
                    prev_count = len(api_responses)
                    page_url = f"{full_url}?page={pg}"
                    await page.goto(page_url, wait_until='domcontentloaded', timeout=10000)
                    await asyncio.sleep(0.5)

                    # Wait for intercepted response
                    if pending_tasks:
                        await asyncio.gather(*pending_tasks, return_exceptions=True)

                    if len(api_responses) == prev_count:
                        # No response yet — short retry
                        await asyncio.sleep(0.5)
                        if pending_tasks:
                            await asyncio.gather(*pending_tasks, return_exceptions=True)
                        if len(api_responses) == prev_count:
                            logger.debug(
                                f"[{self.provider_id}] No data at page {pg}, stopping"
                            )
                            break

                    if pg % 20 == 0:
                        logger.debug(
                            f"[{self.provider_id}] Page {pg}/{max_pages}, "
                            f"{len(api_responses)} responses captured"
                        )

                except Exception as e:
                    logger.debug(f"[{self.provider_id}] Page {pg} error: {e}")
                    break

            page.remove_listener('response', intercept_response)
            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)

            logger.debug(
                f"[{self.provider_id}] Collected {len(api_responses)} API responses "
                f"across {max_pages} pages"
            )

            # Parse all responses into events grouped by sport
            events_by_sport: Dict[str, List[StandardEvent]] = {}
            seen: set = set()

            for resp_data in api_responses:
                lookup = resp_data.get('lookup', {})
                teams = lookup.get('teams', {})
                tournaments = lookup.get('tournaments', {})
                btypes = lookup.get('bettingTypes', {})
                sports_lookup = lookup.get('sports', {})

                # Parse items format (full listing page)
                for category in resp_data.get('items', []):
                    sport_id = category.get('sportId', '')
                    sport_info = sports_lookup.get(sport_id, {})
                    sport_abrv = sport_info.get('abrv', '')
                    canonical_sport = self.SPORT_ABRV_MAP.get(sport_abrv)
                    if not canonical_sport:
                        continue

                    for tournament_group in category.get('items', []):
                        tid = tournament_group.get('tournamentId', '')
                        tinfo = tournaments.get(tid, {})
                        tname = tinfo.get('name', 'Unknown')

                        # Skip special/prop markets
                        if tournament_group.get('isSpecial'):
                            continue

                        for ev_data in tournament_group.get('events', []):
                            event = self._parse_full_event(
                                ev_data, canonical_sport, tname, teams, btypes
                            )
                            if event:
                                key = f"{event.home_team}:{event.away_team}:{event.start_time}"
                                if key not in seen:
                                    seen.add(key)
                                    events_by_sport.setdefault(canonical_sport, []).append(event)

                # Parse offer format (highlights page — from initial site load)
                for ev_data in resp_data.get('offer', []):
                    event = self._parse_offer_event(ev_data, teams, tournaments, btypes, sports_lookup)
                    if event:
                        key = f"{event.home_team}:{event.away_team}:{event.start_time}"
                        if key not in seen:
                            seen.add(key)
                            events_by_sport.setdefault(event.sport, []).append(event)

            total = sum(len(v) for v in events_by_sport.values())
            sport_summary = ", ".join(f"{k}: {len(v)}" for k, v in sorted(events_by_sport.items()))
            logger.debug(f"[{self.provider_id}] Total: {total} events ({sport_summary})")

            return events_by_sport

        except Exception as e:
            logger.error(f"[{self.provider_id}] Error extracting all sports: {e}", exc_info=True)
            return {}

    def _parse_full_event(
        self,
        ev_data: Dict,
        sport: str,
        league: str,
        teams: Dict,
        btypes: Dict,
    ) -> Optional[StandardEvent]:
        """Parse event from the full listing page format (items[].items[].events[])."""
        ev = ev_data.get('event', {})

        # Resolve team names
        team1_id = ev.get('teamOneId', '')
        team2_id = ev.get('teamTwoId', '')
        team1 = teams.get(team1_id) or teams.get(str(team1_id), {})
        team2 = teams.get(team2_id) or teams.get(str(team2_id), {})
        home_raw = team1.get('name', '')
        away_raw = team2.get('name', '')

        if not home_raw or not away_raw:
            return None

        # Skip live events
        if ev.get('bettingStatus') not in (1, None):
            return None
        if not ev.get('isUpcoming', True):
            return None

        event_id = ev.get('id', '')
        home_team = normalize_team_name(home_raw)
        away_team = normalize_team_name(away_raw)
        start_time = self._parse_datetime(ev.get('startTime'))

        # Parse markets
        markets = self._parse_markets(ev_data.get('offers', []), btypes)
        if not markets:
            return None

        return StandardEvent(
            id=f"tipwin_{event_id}",
            name=f"{home_raw} vs {away_raw}",
            provider=self.provider_id,
            sport=sport,
            league=league,
            home_team=home_team,
            away_team=away_team,
            start_time=start_time,
            markets=markets,
        )

    def _parse_offer_event(
        self,
        ev_data: Dict,
        teams: Dict,
        tournaments: Dict,
        btypes: Dict,
        sports_lookup: Dict,
    ) -> Optional[StandardEvent]:
        """Parse event from highlights/offer format."""
        ev = ev_data.get('event', {})

        team1_id = ev.get('teamOneId', '')
        team2_id = ev.get('teamTwoId', '')
        team1 = teams.get(team1_id) or teams.get(str(team1_id), {})
        team2 = teams.get(team2_id) or teams.get(str(team2_id), {})
        home_raw = team1.get('name', '')
        away_raw = team2.get('name', '')

        if not home_raw or not away_raw:
            return None

        if ev.get('bettingStatus') not in (1, None):
            return None
        if not ev.get('isUpcoming', True):
            return None

        # Resolve sport
        sport_id = ev.get('sportId', '')
        sport_info = sports_lookup.get(sport_id, {})
        sport_abrv = sport_info.get('abrv', '')
        canonical_sport = self.SPORT_ABRV_MAP.get(sport_abrv)
        if not canonical_sport:
            return None

        event_id = ev_data.get('eventId', ev.get('id', ''))
        tournament_id = ev.get('tournamentId', '')
        tournament = tournaments.get(tournament_id) or tournaments.get(str(tournament_id), {})
        league = tournament.get('name', 'Unknown')

        home_team = normalize_team_name(home_raw)
        away_team = normalize_team_name(away_raw)
        start_time = self._parse_datetime(ev.get('startTime'))

        markets = self._parse_markets(ev_data.get('offers', []), btypes)
        if not markets:
            return None

        return StandardEvent(
            id=f"tipwin_{event_id}",
            name=f"{home_raw} vs {away_raw}",
            provider=self.provider_id,
            sport=canonical_sport,
            league=league,
            home_team=home_team,
            away_team=away_team,
            start_time=start_time,
            markets=markets,
        )

    def _parse_markets(self, offers: List[Dict], btypes: Dict) -> List[Dict]:
        """Parse market offers into standardized market list."""
        markets = []
        seen_types: set = set()

        for market_offer in offers:
            btype_id = market_offer.get('bettingTypeId', '')
            btype = btypes.get(btype_id) or btypes.get(str(btype_id), {})
            abrv = btype.get('abrv', '')

            market_type = self.MARKET_ABRV_MAP.get(abrv)
            if not market_type or market_type in seen_types:
                continue

            outcomes = []
            inner_offers = market_offer.get('offers', [])

            # Extract point value from market key
            # Tipwin nests point values in key.specifier (not directly in key)
            point = None
            key = market_offer.get('key', {})
            if isinstance(key, dict):
                specifier = key.get('specifier', {})
                if isinstance(specifier, dict):
                    # Total: specifier.total = "3.5"
                    total_val = specifier.get('total')
                    if total_val is not None:
                        try:
                            point = float(total_val)
                        except (ValueError, TypeError):
                            pass

                    # Handicap: specifier.hcp = "1:0" (home:away European format)
                    # Convert to Asian handicap: hcp "1:0" means home -1 → point = -1.0
                    if point is None:
                        hcp_val = specifier.get('hcp')
                        if hcp_val and isinstance(hcp_val, str) and ':' in hcp_val:
                            parts = hcp_val.split(':')
                            try:
                                home_hcp = int(parts[0])
                                away_hcp = int(parts[1])
                                # "1:0" = home gives 1 goal start = home -1 handicap
                                point = float(away_hcp - home_hcp)
                            except (ValueError, IndexError):
                                pass

                # Fallback: check direct key fields
                if point is None:
                    for pkey in ('total', 'hcp', 'handicap', 'line'):
                        pval = key.get(pkey)
                        if pval is not None:
                            try:
                                point = float(pval)
                                break
                            except (ValueError, TypeError):
                                continue

            for offer in inner_offers:
                tip = offer.get('tip', '')
                value = offer.get('value')
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

                outcome_dict: Dict[str, Any] = {"name": outcome_name, "odds": odds}
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

        return markets

    async def _handle_cookie_consent(self, page):
        """Handle cookie consent dialogs."""
        selectors = [
            'button:has-text("Acceptera")',
            'button:has-text("Accept")',
            'button:has-text("Godkänn")',
            'button:has-text("OK")',
            '[class*="cookie"] button',
            '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll',
        ]
        for sel in selectors:
            try:
                await page.click(sel, timeout=3000)
                logger.debug(f"[{self.provider_id}] Clicked cookie consent")
                await asyncio.sleep(1)
                return
            except Exception:
                continue

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
