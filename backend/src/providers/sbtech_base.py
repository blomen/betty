"""
SBTech Base Retriever - Shared logic for SBTech-powered operators

SBTech (acquired by DraftKings 2020) powers multiple white-label sportsbooks
including Bethard, ComeOn, and Hajper. This base class provides common
API interception and parsing logic.

Flow:
1. Load sportsbook page with Playwright
2. Intercept SBTech API calls
3. Parse JSON responses
4. Convert to StandardEvent format
"""

from typing import List, Any, Optional, Dict
import logging
import asyncio
from datetime import datetime
from ..core import BrowserRetriever, StandardEvent, BrowserTransport
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)


class SBTechRetriever(BrowserRetriever):
    """
    Base retriever for SBTech-powered operators.

    Subclasses must define:
    - site_url: Base URL for the operator
    - SPORT_SLUGS: Mapping of sport names to URL slugs
    """

    # Default sport slugs (can be overridden by subclasses)
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

    # API endpoint patterns to intercept (can be extended by subclasses)
    API_PATTERNS: List[str] = [
        '/api/sportsbook/',
        '/api/odds/',
        '/api/sb/',
        '/sportcontent/',
        '/EventMarket/',
        '/sportsbook-api/',  # ComeOn Group custom API (ComeOn, Hajper)
    ]

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)

        raw_site_url = config.get("site_url", f"https://www.{config.get('domain')}")
        self.site_url: str = raw_site_url.rstrip("/")

        # Cache for captured API responses
        self._api_responses: List[Dict] = []

    async def _ensure_sport_init(self, sport: str) -> None:
        """No special initialization needed."""
        pass

    def _get_sport_url(self, sport: str) -> str:
        """
        Get the sportsbook URL for a given sport.

        Override this in subclasses if URL structure differs.
        """
        sport_slug = self.SPORT_SLUGS.get(sport, sport)
        return f"{self.site_url}/sports/{sport_slug}"

    async def _handle_cookie_consent(self, page):
        """Handle cookie consent dialogs."""
        cookie_selectors = [
            'button:has-text("Accept")',
            'button:has-text("Acceptera")',
            'button:has-text("Godkänn")',
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
            if isinstance(data, dict) and ('error' in data or 'Error' in data):
                logger.warning(f"[{self.provider_id}] API returned error: {data}")
            else:
                self._api_responses.append(data)
                logger.info(f"[{self.provider_id}] Captured SBTech API response (total: {len(self._api_responses)})")

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Failed to parse response: {e}")

    def _should_intercept(self, url: str) -> bool:
        """
        Determine if a URL should be intercepted.

        Override this in subclasses for custom filtering logic.
        """
        return any(pattern in url for pattern in self.API_PATTERNS)

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """Not used - extract() is overridden."""
        raise NotImplementedError("SBTechRetriever uses extract() directly")

    async def extract(self, sport: str, limit: int = 50) -> List[StandardEvent]:
        """
        Extract events by intercepting SBTech API calls.

        1. Load sport page with Playwright
        2. Intercept SBTech API responses
        3. Parse JSON directly
        """
        if sport not in self.SPORT_SLUGS:
            logger.warning(f"[{self.provider_id}] Sport '{sport}' not supported")
            return []

        try:
            if not isinstance(self.transport, BrowserTransport):
                logger.error(f"[{self.provider_id}] SBTechRetriever requires BrowserTransport")
                return []

            # Clear previous responses
            self._api_responses = []

            # Setup response interceptor
            await self.transport._ensure_browser()
            page = self.transport.page

            # Intercept API responses
            pending_tasks = []
            all_responses_count = 0

            def intercept_response(response):
                """Synchronous handler that schedules async processing."""
                nonlocal all_responses_count
                all_responses_count += 1
                url = response.url

                # Log API calls for debugging
                if self._should_intercept(url):
                    logger.debug(f"[{self.provider_id}] Found SBTech API call: {url[:100]}")
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
            logger.info(f"[{self.provider_id}] Waiting for API calls...")
            await asyncio.sleep(8)
            logger.debug(f"[{self.provider_id}] Total HTTP responses: {all_responses_count}")

            # Scroll to trigger lazy loading
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            await asyncio.sleep(3)
            logger.debug(f"[{self.provider_id}] Total HTTP responses after scroll: {all_responses_count}")

            # Remove interceptor
            page.remove_listener('response', intercept_response)

            # Wait for all pending response processing tasks
            if pending_tasks:
                logger.debug(f"[{self.provider_id}] Waiting for {len(pending_tasks)} pending tasks...")
                await asyncio.gather(*pending_tasks, return_exceptions=True)

            # Parse captured responses
            logger.info(f"[{self.provider_id}] Captured {len(self._api_responses)} API responses")

            events = []
            for i, api_data in enumerate(self._api_responses):
                logger.debug(f"[{self.provider_id}] Parsing response {i+1}...")
                parsed_events = self._parse_sbtech_response(api_data, sport)
                logger.debug(f"[{self.provider_id}] Response {i+1} yielded {len(parsed_events)} events")
                events.extend(parsed_events)

            # Deduplicate by event ID
            seen_ids = set()
            unique_events = []
            for event in events:
                event_key = f"{event.home_team}:{event.away_team}:{event.start_time}"
                if event_key not in seen_ids:
                    seen_ids.add(event_key)
                    unique_events.append(event)

            logger.info(f"[{self.provider_id}] Extracted {len(unique_events)} unique events")
            return unique_events[:limit]

        except Exception as e:
            logger.error(f"[{self.provider_id}] Error extracting {sport}: {e}", exc_info=True)
            return []

    def _parse_sbtech_response(self, api_data: Dict, sport: str) -> List[StandardEvent]:
        """
        Parse SBTech API response.

        SBTech structure:
        {
          "data": {
            "events": [...],      # Event metadata
            "markets": [...],     # Market metadata
            "selections": [...]   # Outcomes with odds
          }
        }
        """
        events = []

        try:
            # Skip non-dict responses (arrays, null, etc.)
            if not isinstance(api_data, dict):
                return []

            # Check for SBTech data structure
            if 'data' in api_data and isinstance(api_data['data'], dict):
                data = api_data['data']

                # Must have events
                if 'events' not in data:
                    return []

                events_raw = data.get('events', [])
                markets_raw = data.get('markets', [])
                selections_raw = data.get('selections', [])

                logger.debug(f"[{self.provider_id}] Processing {len(events_raw)} events, {len(markets_raw)} markets, {len(selections_raw)} selections")

                # Build market lookup: marketId -> market data
                markets_by_event = {}
                for market in markets_raw:
                    event_id = market.get('eventId')
                    if event_id not in markets_by_event:
                        markets_by_event[event_id] = []
                    markets_by_event[event_id].append(market)

                # Build selection lookup: marketId -> list of selections
                selections_by_market = {}
                for selection in selections_raw:
                    market_id = selection.get('marketId')
                    if market_id not in selections_by_market:
                        selections_by_market[market_id] = []
                    selections_by_market[market_id].append(selection)

                # Parse each event with its markets
                for event_data in events_raw:
                    try:
                        event_id = event_data.get('id')
                        event_markets = markets_by_event.get(event_id, [])

                        event = self._parse_event(event_data, sport, event_markets, selections_by_market)
                        if event:
                            events.append(event)
                    except Exception as e:
                        logger.debug(f"[{self.provider_id}] Failed to parse event {event_id}: {e}")
                        continue

            else:
                # Unknown structure
                logger.debug(f"[{self.provider_id}] Unknown API structure: {list(api_data.keys())[:10]}")
                return []

        except Exception as e:
            logger.warning(f"[{self.provider_id}] Failed to parse SBTech response: {e}")

        return events

    def _parse_event(self, event_data: Dict, sport: str, event_markets: List[Dict],
                     selections_by_market: Dict[str, List[Dict]]) -> Optional[StandardEvent]:
        """
        Parse a single event from SBTech API data.

        SBTech event structure:
        - participants: [{label, side: 1}, {label, side: 2}]  # 1=home, 2=away
        - startDate: ISO datetime
        - competitionName: League name
        - label: "Home Team - Away Team"
        """
        try:
            # Extract teams from participants (side 1 = home, side 2 = away)
            participants = event_data.get('participants', [])
            home_team = None
            away_team = None

            for p in participants:
                if p.get('side') == 1:
                    home_team = p.get('label')
                elif p.get('side') == 2:
                    away_team = p.get('label')

            if not home_team or not away_team:
                return None

            # Normalize team names
            home_team = normalize_team_name(home_team)
            away_team = normalize_team_name(away_team)

            # Parse start time
            start_time_raw = event_data.get('startDate')
            start_time = self._parse_datetime(start_time_raw) if start_time_raw else None

            # Extract league
            league = event_data.get('competitionName', 'Unknown')

            # Parse markets
            markets = []
            for market in event_markets:
                market_id = market.get('id')
                market_label = market.get('marketFriendlyName', market.get('label', ''))

                # Get selections for this market
                market_selections = selections_by_market.get(market_id, [])

                # Normalize market type first (needed for point value logic)
                market_type = self._normalize_market_type(market_label)

                # Build outcomes
                outcomes = []
                for selection in market_selections:
                    if selection.get('status') == 'Open':
                        outcome_dict = {
                            "name": selection.get('label', ''),
                            "odds": selection.get('odds', 0.0)
                        }
                        outcomes.append(outcome_dict)

                if outcomes:
                    markets.append({
                        "type": market_type,
                        "outcomes": outcomes
                    })

            # Generate event ID and name
            event_id = event_data.get('id', event_data.get('globalId', ''))
            event_name = f"{home_team} vs {away_team}"

            return StandardEvent(
                id=event_id,
                name=event_name,
                provider=self.provider_id,
                sport=sport,
                league=str(league),
                home_team=str(home_team),
                away_team=str(away_team),
                start_time=start_time,
                markets=markets
            )

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Failed to parse event: {e}")
            return None

    def _normalize_market_type(self, market_label: str) -> str:
        """Normalize market labels to standard types (1x2/moneyline only)."""
        label_lower = market_label.lower()

        # 1x2/Match result patterns
        if any(kw in label_lower for kw in ['matchresultat', 'match result', '1x2', 'full time result',
                                             'vinnare', 'winner', 'slutresultat', 'matchodds', 'moneyline']):
            return "1x2"

        return "other"

    def _parse_datetime(self, dt_str: Any) -> Optional[datetime]:
        """Parse datetime from various formats."""
        if not dt_str:
            return None

        try:
            # Try ISO format
            if isinstance(dt_str, str):
                return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
            # Try timestamp
            elif isinstance(dt_str, (int, float)):
                return datetime.fromtimestamp(dt_str / 1000 if dt_str > 10**10 else dt_str)
        except Exception as e:
            logger.debug(f"Failed to parse datetime '{dt_str}': {e}")

        return None
