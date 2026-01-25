"""
Coolbet Retriever - Browser-based API Interception

Uses Playwright to bypass Incapsula protection and intercept API calls.
Similar to gecko_v2 pattern but adapted for Coolbet's API structure.
"""

from typing import List, Any, Optional, Dict
import logging
import asyncio
from datetime import datetime
from ..core import BrowserRetriever, StandardEvent, BrowserTransport

logger = logging.getLogger(__name__)


class CoolbetRetriever(BrowserRetriever):
    """
    Retriever for Coolbet using browser automation and API interception.

    Strategy: Load page with Playwright, intercept API calls, parse JSON responses.
    Bypasses Incapsula bot detection with stealth mode.
    """

    SPORT_SLUGS: Dict[str, str] = {
        "football": "football",
        "basketball": "basketball",
        "tennis": "tennis",
        "ice_hockey": "ice-hockey",
        "american_football": "american-football",
        "baseball": "baseball",
        "mma": "mma",
        "esports": "esports",
    }

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)

        raw_site_url = config.get("site_url", f"https://www.{config.get('domain', 'coolbet.com')}")
        self.site_url: str = raw_site_url.rstrip("/")

        # Cache for captured API responses
        self._api_responses: List[Dict] = []

    async def _ensure_sport_init(self, sport: str) -> None:
        """No special initialization needed."""
        pass

    def _get_sport_url(self, sport: str) -> str:
        """Get the sportsbook URL for a given sport."""
        sport_slug = self.SPORT_SLUGS.get(sport, sport)
        return f"{self.site_url}/en/sports/{sport_slug}"

    async def _handle_cookie_consent(self, page):
        """Handle cookie consent dialogs."""
        cookie_selectors = [
            'button:has-text("Accept")',
            'button:has-text("Accept all")',
            '#accept-cookies',
            '.cookie-accept',
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
            # Try to get JSON directly
            try:
                data = await response.json()
            except:
                # Fallback to text parsing
                text = await response.text()
                import json
                data = json.loads(text)

            # Check for errors
            if 'error' in data or 'errorId' in data:
                logger.warning(f"[{self.provider_id}] API returned error: {data}")
            else:
                self._api_responses.append(data)
                logger.info(f"[{self.provider_id}] Captured API response (total: {len(self._api_responses)})")

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Failed to parse response: {e}")

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """Not used - we override extract() completely."""
        return []

    async def extract(self, sport: str, limit: int = 50) -> List[StandardEvent]:
        """
        Extract events by intercepting API calls.

        1. Load sport page with Playwright
        2. Intercept sports/events API responses
        3. Parse JSON directly (no DOM parsing)
        """
        if sport not in self.SPORT_SLUGS:
            logger.warning(f"[{self.provider_id}] Sport '{sport}' not supported")
            return []

        try:
            if not isinstance(self.transport, BrowserTransport):
                logger.error(f"[{self.provider_id}] CoolbetRetriever requires BrowserTransport")
                return []

            # Clear previous responses
            self._api_responses = []

            # Setup response interceptor
            await self.transport._ensure_browser()
            page = self.transport.page

            # Intercept API responses
            pending_tasks = []

            def intercept_response(response):
                """Synchronous handler that schedules async processing."""
                url = response.url

                # Log API calls for debugging
                if '/api/' in url or '/sb-api/' in url:
                    logger.debug(f"[{self.provider_id}] API call: {url.split('?')[0]}")

                # Look for sports/events endpoints
                # Common patterns: /api/events, /api/sports, /sportsbook/events
                if any(pattern in url for pattern in ['/events', '/markets', '/odds', '/sports', '/sportsbook']):
                    logger.debug(f"[{self.provider_id}] Found potential events API: {url}")
                    # Schedule async processing
                    task = asyncio.create_task(self._process_response(response))
                    pending_tasks.append(task)

            page.on('response', intercept_response)

            # Load the sport page
            sport_url = self._get_sport_url(sport)
            logger.info(f"[{self.provider_id}] Loading {sport_url}")
            await page.goto(sport_url, wait_until='load', timeout=60000)

            # Handle cookie consent
            await self._handle_cookie_consent(page)

            # Wait for page to fully render and make API calls
            logger.info(f"[{self.provider_id}] Waiting for page to fully load...")
            await asyncio.sleep(8)

            # Scroll to trigger lazy loading
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            await asyncio.sleep(3)

            # Remove interceptor
            page.remove_listener('response', intercept_response)

            # Wait for all pending response processing tasks
            if pending_tasks:
                logger.debug(f"[{self.provider_id}] Waiting for {len(pending_tasks)} pending response tasks...")
                await asyncio.gather(*pending_tasks, return_exceptions=True)

            # Parse captured responses
            logger.info(f"[{self.provider_id}] Captured {len(self._api_responses)} API responses")

            # Save first response for inspection
            if self._api_responses:
                import json
                try:
                    with open('scrap/coolbet_response_sample.json', 'w', encoding='utf-8') as f:
                        json.dump(self._api_responses[0], f, indent=2, ensure_ascii=False)
                    logger.debug(f"[{self.provider_id}] Saved first response for inspection")
                except Exception as e:
                    logger.debug(f"[{self.provider_id}] Could not save response: {e}")

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
                event_id = getattr(event, 'id', None) or f"{event.home_team}_{event.away_team}"
                if event_id not in seen_ids:
                    seen_ids.add(event_id)
                    unique_events.append(event)

            logger.info(f"[{self.provider_id}] Extracted {len(unique_events)} unique events")
            return unique_events[:limit]

        except Exception as e:
            logger.error(f"[{self.provider_id}] Error extracting {sport}: {e}", exc_info=True)
            return []

    def _parse_api_response(self, api_data: Dict, sport: str) -> List[StandardEvent]:
        """
        Parse Coolbet API response.

        Structure varies - need to inspect actual responses to determine format.
        Common patterns:
        - events: [...]
        - data: { events: [...] }
        - matches: [...]
        """
        events = []

        try:
            # Try different response structures
            events_raw = None

            # Pattern 1: Direct events array
            if isinstance(api_data, list):
                events_raw = api_data

            # Pattern 2: { events: [...] }
            elif 'events' in api_data:
                events_raw = api_data['events']

            # Pattern 3: { data: { events: [...] } }
            elif 'data' in api_data and isinstance(api_data['data'], dict):
                data = api_data['data']
                events_raw = data.get('events') or data.get('matches') or data.get('fixtures')

            # Pattern 4: { matches: [...] }
            elif 'matches' in api_data:
                events_raw = api_data['matches']

            if not events_raw:
                logger.debug(f"[{self.provider_id}] Could not find events in API response")
                return []

            logger.debug(f"[{self.provider_id}] Found {len(events_raw)} events in response")

            # Parse each event
            for event_raw in events_raw:
                try:
                    event = self._parse_event(event_raw, sport)
                    if event:
                        events.append(event)
                except Exception as e:
                    logger.debug(f"[{self.provider_id}] Error parsing event: {e}")
                    continue

        except Exception as e:
            logger.error(f"[{self.provider_id}] Error parsing API response: {e}")

        return events

    def _parse_event(self, event_raw: Dict, sport: str) -> Optional[StandardEvent]:
        """Parse a single event from the API response."""
        try:
            # Extract teams (common field names)
            home_team = (
                event_raw.get('homeTeam') or
                event_raw.get('home_team') or
                event_raw.get('home') or
                event_raw.get('participant1')
            )
            away_team = (
                event_raw.get('awayTeam') or
                event_raw.get('away_team') or
                event_raw.get('away') or
                event_raw.get('participant2')
            )

            # Handle nested team objects
            if isinstance(home_team, dict):
                home_team = home_team.get('name') or home_team.get('label')
            if isinstance(away_team, dict):
                away_team = away_team.get('name') or away_team.get('label')

            if not home_team or not away_team:
                logger.debug(f"[{self.provider_id}] Missing teams in event")
                return None

            # Extract event ID
            event_id = (
                event_raw.get('id') or
                event_raw.get('eventId') or
                event_raw.get('matchId')
            )

            # Parse start time
            start_time_str = (
                event_raw.get('startTime') or
                event_raw.get('start_time') or
                event_raw.get('startDate') or
                event_raw.get('kickoff')
            )
            start_time = None
            if start_time_str:
                try:
                    start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                except Exception:
                    pass

            # Extract league
            league = (
                event_raw.get('league') or
                event_raw.get('leagueName') or
                event_raw.get('tournament') or
                event_raw.get('competition') or
                'Unknown'
            )
            if isinstance(league, dict):
                league = league.get('name') or league.get('label') or 'Unknown'

            # Extract odds
            odds = {}

            # Try different odds structures
            if 'odds' in event_raw:
                odds_data = event_raw['odds']
                # Handle different odds formats
                if isinstance(odds_data, dict):
                    odds = {k: v for k, v in odds_data.items() if isinstance(v, (int, float))}
                elif isinstance(odds_data, list):
                    # Array of outcome objects
                    for outcome in odds_data:
                        if isinstance(outcome, dict):
                            name = outcome.get('name') or outcome.get('label') or outcome.get('type')
                            value = outcome.get('odds') or outcome.get('price') or outcome.get('decimal')
                            if name and value:
                                odds[name.lower()] = value

            # Try markets array
            if 'markets' in event_raw:
                markets = event_raw['markets']
                if isinstance(markets, list) and markets:
                    # Use first market (usually 1x2 or moneyline)
                    market = markets[0]
                    if 'outcomes' in market:
                        for outcome in market['outcomes']:
                            name = outcome.get('name') or outcome.get('label')
                            value = outcome.get('odds') or outcome.get('price')
                            if name and value:
                                odds[name.lower()] = value

            return StandardEvent(
                provider=self.provider_id,
                sport=sport,
                league=str(league),
                home_team=str(home_team),
                away_team=str(away_team),
                start_time=start_time,
                odds=odds,
                raw=event_raw
            )

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Error parsing event: {e}")
            return None
