"""
Gecko V2 Retriever - Hybrid Approach

Uses Playwright to load the page and intercept API calls, then uses the REST API
to fetch clean JSON data. Much faster than DOM parsing.

Flow:
1. Load page with Playwright
2. Intercept event-market API calls to capture market IDs
3. Parse the JSON responses (no DOM parsing needed)
"""

from typing import List, Any, Optional, Dict
import logging
import asyncio
from datetime import datetime
from ..core import BrowserRetriever, StandardEvent, BrowserTransport
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)


class GeckoV2Retriever(BrowserRetriever):
    """
    Retriever for Betsson Group sites using API interception.

    Strategy: Load page with browser, intercept event-market API calls,
    parse JSON responses instead of HTML.
    """

    SPORT_SLUGS: Dict[str, str] = {
        "football": "fotboll",
        "basketball": "basket",
        "tennis": "tennis",
        "ice_hockey": "ishockey",
        "american_football": "amerikansk-fotboll",
        "baseball": "baseboll",
        "mma": "mma",
        "esports": "esports",
        "rugby": "rugby",
        "cricket": "cricket",
        "boxing": "boxning",
        "handball": "handboll",
    }

    # League keywords to validate sport matches (for filtering wrong-sport events)
    # Intentionally broad to avoid filtering valid events while still catching obvious mismatches
    SPORT_LEAGUE_KEYWORDS: Dict[str, List[str]] = {
        "football": ["fotboll", "premier league", "la liga", "liga", "bundesliga", "serie a", "ligue", "champions", "europa", "allsvenskan", "eredivisie", "primeira", "championship", "cup"],
        "basketball": ["basket", "nba", "euroleague", "ncaa", "college", "liga acb", "bbl", "cba", "turkish airlines"],
        "tennis": ["tennis", "atp", "wta", "grand slam", "open", "masters", "wimbledon"],
        "ice_hockey": ["hockey", "hock", "nhl", "shl", "khl", "liiga", "del", "swiss league"],
        "american_football": ["football", "nfl", "ncaa", "college"],
        "baseball": ["baseball", "baseboll", "mlb", "npb", "kbo"],
        "mma": ["mma", "ufc", "bellator", "pfl", "mixed martial"],
        "esports": ["esports", "esport", "cs:go", "counter-strike", "league of legends", "dota", "valorant", "call of duty"],
        "rugby": ["rugby", "six nations", "championship", "premiership", "top 14"],
        "cricket": ["cricket", "test", "odi", "t20", "ipl", "big bash"],
        "boxing": ["boxing", "boxning", "wbc", "wba", "ibf", "wbo", "heavyweight", "welterweight"],
        "handball": ["handboll", "handball", "ehf"],
    }

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)

        raw_site_url = config.get("site_url", f"https://www.{config.get('domain', 'betsson.com')}")
        self.site_url: str = raw_site_url.rstrip("/")

        # Cache for captured API responses
        self._api_responses: List[Dict] = []

    async def _ensure_sport_init(self, sport: str) -> None:
        """No special initialization needed."""
        pass

    def _get_sport_url(self, sport: str) -> str:
        """Get the sportsbook URL for a given sport."""
        sport_slug = self.SPORT_SLUGS.get(sport, sport)
        # Don't filter by tab - get all events
        return f"{self.site_url}/sv/odds/{sport_slug}"

    def _validate_event_sport(self, sport: str, league: str) -> bool:
        """
        Validate that an event's league matches the requested sport.

        Some sites (e.g., Betsafe) may load wrong sport events on certain pages.
        This filters out obviously wrong events by checking league names.
        """
        if not league:
            return True  # Can't validate without league info

        league_lower = league.lower()
        keywords = self.SPORT_LEAGUE_KEYWORDS.get(sport, [])

        if not keywords:
            return True  # No validation keywords for this sport

        # Check if ANY keyword matches the league
        return any(keyword in league_lower for keyword in keywords)

    async def _handle_cookie_consent(self, page):
        """Handle cookie consent dialogs."""
        cookie_selectors = [
            'button:has-text("Acceptera")',
            'button:has-text("Accept")',
            '#accept-cookies',
        ]

        for selector in cookie_selectors:
            try:
                await page.click(selector, timeout=2000)
                logger.info(f"[{self.provider_id}] Clicked cookie consent")
                await asyncio.sleep(1)
                return
            except Exception:
                continue

        logger.debug(f"[{self.provider_id}] No cookie consent needed")

    async def _process_response(self, response):
        """Process an API response asynchronously."""
        try:
            # Try to get JSON directly first
            try:
                data = await response.json()
            except:
                # Fallback to text parsing
                text = await response.text()
                import json
                data = json.loads(text)

            # Check for errors
            if 'errorId' in data or 'code' in data:
                logger.warning(f"[{self.provider_id}] API returned error: {data}")
                # Don't add error responses
            else:
                self._api_responses.append(data)
                logger.info(f"[{self.provider_id}] Captured event-market response (total: {len(self._api_responses)})")

        except Exception as e:
            logger.warning(f"[{self.provider_id}] Failed to parse response: {e}")

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """Not used - extract() is overridden."""
        raise NotImplementedError("GeckoV2Retriever uses extract() directly")

    async def extract(self, sport: str, limit: int = 50, **kwargs) -> List[StandardEvent]:
        """
        Extract events by intercepting API calls.

        1. Load sport page with Playwright
        2. Intercept event-market API responses
        3. Parse JSON directly (no DOM parsing)
        """
        if sport not in self.SPORT_SLUGS:
            logger.warning(f"[{self.provider_id}] Sport '{sport}' not supported")
            return []

        try:
            if not isinstance(self.transport, BrowserTransport):
                logger.error(f"[{self.provider_id}] GeckoV2Retriever requires BrowserTransport")
                return []

            # Clear previous responses
            self._api_responses = []

            # Setup response interceptor
            await self.transport._ensure_browser()
            page = self.transport.page

            # Intercept API responses (use list to track pending tasks)
            pending_tasks = []

            def intercept_response(response):
                """Synchronous handler that schedules async processing."""
                url = response.url

                # Log ALL API calls for debugging
                if '/api/sb/' in url:
                    logger.debug(f"[{self.provider_id}] API call: {url.split('?')[0]}")

                if '/api/sb/v1/widgets/event-market' in url:
                    # Log full URL to check parameters
                    has_params = '?' in url
                    logger.debug(f"[{self.provider_id}] Found event-market response (has_params={has_params})")
                    if has_params:
                        params_start = url.index('?')
                        logger.debug(f"[{self.provider_id}] Params: {url[params_start:params_start+200]}")

                    # Schedule async processing and track the task
                    task = asyncio.create_task(self._process_response(response))
                    pending_tasks.append(task)

            page.on('response', intercept_response)

            # Load the sport page
            sport_url = self._get_sport_url(sport)
            logger.info(f"[{self.provider_id}] Loading {sport_url}")
            # Use 'load' instead of 'networkidle' to not block API calls
            await page.goto(sport_url, wait_until='load', timeout=60000)

            # Handle cookie consent
            await self._handle_cookie_consent(page)

            # Wait for page to fully render and make API calls
            logger.info(f"[{self.provider_id}] Waiting for page to fully load...")
            await asyncio.sleep(7)  # Reduced from 10s for better performance

            # Scroll to trigger lazy loading
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            await asyncio.sleep(2)  # Reduced from 3s for better performance

            # Remove interceptor
            page.remove_listener('response', intercept_response)

            # Wait for all pending response processing tasks to complete
            if pending_tasks:
                logger.debug(f"[{self.provider_id}] Waiting for {len(pending_tasks)} pending response tasks...")
                await asyncio.gather(*pending_tasks, return_exceptions=True)

            # Parse captured responses
            logger.info(f"[{self.provider_id}] Captured {len(self._api_responses)} API responses")

            events = []
            for i, api_data in enumerate(self._api_responses):
                logger.debug(f"[{self.provider_id}] Parsing response {i+1}...")
                parsed_events = self._parse_api_response(api_data, sport)
                logger.debug(f"[{self.provider_id}] Response {i+1} yielded {len(parsed_events)} events")
                events.extend(parsed_events)

            # Deduplicate by event ID
            seen_ids = set()
            unique_events = []
            for event in events:
                if event.id not in seen_ids:
                    seen_ids.add(event.id)
                    unique_events.append(event)

            logger.info(f"[{self.provider_id}] Extracted {len(unique_events)} unique events")
            return unique_events[:limit]

        except Exception as e:
            logger.error(f"[{self.provider_id}] Error extracting {sport}: {e}", exc_info=True)
            return []

    def _parse_api_response(self, api_data: Dict, sport: str) -> List[StandardEvent]:
        """
        Parse event-market API response.

        Response structure:
        {
          "data": {
            "events": [...],
            "markets": [...],
            "marketSelections": [...]
          }
        }
        """
        events = []

        try:
            data = api_data.get('data', {})
            events_raw = data.get('events', [])
            markets_raw = data.get('markets', [])
            selections_raw = data.get('marketSelections', [])

            logger.debug(f"[{self.provider_id}] API response has {len(events_raw)} events, {len(markets_raw)} markets, {len(selections_raw)} selections")

            # Build lookup maps
            market_map = {m['id']: m for m in markets_raw}
            selections_by_market = {}
            for sel in selections_raw:
                market_id = sel.get('marketId')
                if market_id not in selections_by_market:
                    selections_by_market[market_id] = []
                selections_by_market[market_id].append(sel)

            # Parse each event
            for event_raw in events_raw:
                try:
                    event = self._parse_event(event_raw, market_map, selections_by_market, sport)
                    if event:
                        events.append(event)
                except Exception as e:
                    logger.debug(f"[{self.provider_id}] Error parsing event: {e}")
                    continue

        except Exception as e:
            logger.error(f"[{self.provider_id}] Error parsing API response: {e}")

        return events

    def _parse_event(self, event_raw: Dict, market_map: Dict, selections_by_market: Dict, sport: str) -> Optional[StandardEvent]:
        """Parse a single event from the API response."""
        try:
            # Event ID can be either 'id' or 'globalId'
            event_id_full = event_raw.get('id') or event_raw.get('globalId')
            if not event_id_full:
                return None

            # Extract short event ID (globalId format: "event.X.Y.Z.f-XXXXX", we need "f-XXXXX")
            if 'globalId' in event_raw and '.' in event_id_full:
                # Extract the last part after the last dot
                event_id = event_id_full.split('.')[-1]
            else:
                event_id = event_id_full

            # Extract team names from participants
            participants = event_raw.get('participants', [])
            if len(participants) < 2:
                return None

            # Sort by side (1=home, 2=away)
            participants.sort(key=lambda p: p.get('side', 0))
            home_team_raw = participants[0].get('label', '')
            away_team_raw = participants[1].get('label', '')

            if not home_team_raw or not away_team_raw:
                return None

            # Normalize team names
            home_team = normalize_team_name(home_team_raw)
            away_team = normalize_team_name(away_team_raw)

            # Parse start time
            start_date_str = event_raw.get('startDate')
            start_time = None
            if start_date_str:
                try:
                    start_time = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
                except Exception:
                    pass

            # Get competition/league
            league = event_raw.get('competitionName', 'Unknown')

            # Parse markets (note: we only see markets that were loaded for this event)
            markets_list = []
            # We don't have direct event->market mapping in the response
            # Markets are loaded separately and linked by eventId
            # For now, we'll create a simple market structure

            # Find markets for this event (would need to check eventId in market_map)
            event_markets = [m for m_id, m in market_map.items() if m.get('eventId') == event_id]

            for market in event_markets:
                market_id = market.get('id')
                market_dict = self._parse_market(market, selections_by_market.get(market_id, []))
                if market_dict:
                    markets_list.append(market_dict)

            # Skip event if no markets (likely wrong sport or incomplete data)
            if not markets_list:
                logger.debug(f"[{self.provider_id}] Event {event_id} has no markets, skipping")
                return None

            # Validate that the league matches the requested sport
            # (Some sites like Betsafe may load wrong sport events)
            if not self._validate_event_sport(sport, league):
                logger.debug(f"[{self.provider_id}] Event {event_id} league '{league}' doesn't match sport '{sport}', skipping")
                return None

            return StandardEvent(
                id=f"{self.provider_id}_{event_id}",
                name=f"{home_team_raw} vs {away_team_raw}",
                sport=sport,
                markets=markets_list,
                provider=self.provider_id,
                url="",
                start_time=start_time.isoformat() if start_time else "",
                home_team=home_team,
                away_team=away_team,
                league=league
            )

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Error parsing event: {e}")
            return None

    def _parse_market(self, market: Dict, selections: List[Dict]) -> Optional[Dict]:
        """Parse a market and its selections."""
        try:
            # Use marketFriendlyName, fallback to marketTemplateId for better detection
            market_type = market.get('marketFriendlyName') or market.get('label') or market.get('marketTemplateId', '')

            # Normalize market type
            market_type_normalized = self._normalize_market_type(market_type)

            outcomes = []
            for sel in selections:
                outcome = self._parse_selection(sel)
                if outcome:
                    outcomes.append(outcome)

            if not outcomes:
                return None

            market_dict = {
                "type": market_type_normalized,
                "outcomes": outcomes
            }

            return market_dict

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Error parsing market: {e}")
            return None

    def _parse_selection(self, selection: Dict) -> Optional[Dict]:
        """Parse a selection (outcome)."""
        try:
            label = selection.get('label', '')
            odds_value = selection.get('odds')

            if not odds_value or odds_value <= 1.0:
                return None

            # Normalize outcome label
            outcome_name = self._normalize_outcome_label(label)

            return {
                "name": outcome_name,
                "odds": round(float(odds_value), 3)
            }

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Error parsing selection: {e}")
            return None

    def _normalize_market_type(self, market_type: str) -> str:
        """Normalize market type to standard names (1x2/moneyline only)."""
        mt_lower = market_type.lower()

        # 1x2 / Three-way moneyline (Swedish: matchodds)
        if any(x in mt_lower for x in ['1x2', 'full time result', 'matchodds', 'match odds', 'helresultat', 'ftcsr']):
            return '1x2'

        # Two-way moneyline (Swedish: vinnare)
        if any(x in mt_lower for x in ['moneyline', 'match winner', 'vinnare', 'matchwinner', 'mgt']):
            return 'moneyline'

        return 'other'

    def _normalize_outcome_label(self, label: str) -> str:
        """Normalize outcome label to standard names (1x2/moneyline only)."""
        label_lower = label.lower()

        # Home outcomes
        if label in ['1', 'home', 'hemma'] or label_lower in ['1', 'home', 'hemma']:
            return 'home'

        # Draw outcomes
        if label in ['X', 'x', 'draw', 'oavgjort'] or label_lower in ['draw', 'oavgjort']:
            return 'draw'

        # Away outcomes
        if label in ['2', 'away', 'borta'] or label_lower in ['2', 'away', 'borta']:
            return 'away'

        return label.lower()
