"""
Hajper Retriever - Multi-League WebSocket extraction

Hajper (ComeOn Group) uses WebSocket/RSocket for event data.
Extracts events by navigating to individual league pages.
Similar to ComeOn: Multi-league approach for comprehensive coverage.
"""

from typing import Dict, Any, List, Optional
import json
import logging
from datetime import datetime
import asyncio

from ..core import BrowserRetriever, BrowserTransport, StandardEvent
from ..matching.normalizer import normalize_team_name
from .mixins import RSocketMixin

logger = logging.getLogger(__name__)


class HajperRetriever(BrowserRetriever, RSocketMixin):
    """
    Multi-league Hajper retriever for comprehensive event coverage.

    Strategy: Navigate to individual league pages to extract all events
    ComeOn Group platform - similar to ComeOn implementation
    """

    # Sport URL mapping: sports.json keys -> Hajper URL slugs
    SPORT_URL_MAP = {
        'football': '/sportsbook/sport/1-fotboll',
        'basketball': '/sportsbook/sport/2-basket',
        'tennis': '/sportsbook/sport/3-tennis',
        'ice_hockey': '/sportsbook/sport/4-ishockey',
        'american_football': '/sportsbook/sport/5-amerikansk-fotboll',
        'baseball': '/sportsbook/sport/6-baseboll',
        'mma': '/sportsbook/sport/7-mma',
        'esports': '/sportsbook/sport/8-esport',
    }

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)
        raw_site_url = config.get("site_url", f"https://www.{config.get('domain')}")
        self.site_url: str = raw_site_url.rstrip("/")
        self.max_leagues = config.get("max_leagues", 30)  # Reduced default for faster extraction
        # Cache for league list to avoid re-fetching between runs
        self._league_cache: Dict[str, List[Dict[str, str]]] = {}

    async def _extract_league_links(self, page) -> List[Dict[str, str]]:
        """Extract league links from main sport page DOM."""
        # Note: Initial testing showed Hajper loads all leagues immediately (no lazy loading)
        # The page height stabilizes at ~720px regardless of scrolling
        # Keeping wait time for initial render but removing scrolling logic

        await page.wait_for_timeout(2000)  # Wait for initial render

        # Count leagues before extraction for debugging
        initial_count = await page.evaluate('''() => {
            return document.querySelectorAll('a[href*="/leagues/"]').length;
        }''')

        logger.info(f"[{self.provider_id}] Found {initial_count} league link elements on page")

        league_links = await page.evaluate('''() => {
            const links = [];
            const seen = new Set();

            // Find all league links (pattern: /leagues/)
            const allLinks = document.querySelectorAll('a[href*="/leagues/"]');

            allLinks.forEach(link => {
                const href = link.getAttribute('href');
                const text = link.textContent.trim();

                // Filter out event pages (contain "/events/")
                if (href && !href.includes('/events/') && text) {
                    // Normalize href to avoid duplicates
                    const cleanHref = href.split('?')[0];

                    if (!seen.has(cleanHref)) {
                        seen.add(cleanHref);
                        links.push({ href: cleanHref, text });
                    }
                }
            });

            return links;
        }''')

        logger.info(f"[{self.provider_id}] Found {len(league_links)} league links on main page")
        return league_links

    async def _extract_events_from_league(self, page, league_url: str, sport: str) -> List[tuple]:
        """Extract events from a single league page."""
        # Setup per-page WebSocket interception
        ws_messages = self._setup_ws_interception(page)

        # Navigate to league page
        full_url = league_url if league_url.startswith('http') else f"{self.site_url}{league_url}"
        try:
            # Use "networkidle" for reliable WebSocket initialization (reverted from domcontentloaded)
            await asyncio.wait_for(
                page.goto(full_url, wait_until="networkidle", timeout=30000),
                timeout=45.0  # Python-level timeout (45s) - restored for reliability
            )
            await page.wait_for_timeout(2000)  # Allow WebSocket messages to complete
        except asyncio.TimeoutError:
            logger.warning(f"[{self.provider_id}] Timeout loading {league_url}")
            return []
        except Exception as e:
            logger.warning(f"[{self.provider_id}] Failed to load {league_url}: {e}")
            return []

        # Parse WebSocket messages
        events_data = []
        event_ids_seen = set()

        for msg_data in ws_messages:
            if isinstance(msg_data, list):
                for msg in msg_data:
                    if msg.get('type') == 'INITIAL_STATE':
                        payload = msg.get('payload', {})
                        events_list = payload.get('events', [])
                        selections_list = payload.get('selections', [])

                        for event_data in events_list:
                            event_id = str(event_data.get('id', ''))
                            if event_id and event_id not in event_ids_seen:
                                event_ids_seen.add(event_id)
                                # Store event data with selections for later parsing
                                events_data.append((event_id, json.dumps({
                                    'event': event_data,
                                    'selections': selections_list,
                                    'sport': sport
                                })))

        return events_data

    def _normalize_market_type(self, market_name: str) -> str:
        """Normalize Hajper market names to standard types (1x2/moneyline only)."""
        name_lower = market_name.lower()

        # 1x2 / Match result / Moneyline
        if any(kw in name_lower for kw in ['1x2', 'helmatchen', 'match result', 'slutresultat', 'matchresultat', 'vinnare', 'winner', 'moneyline']):
            return '1x2'

        return 'other'

    def _normalize_outcome(self, outcome_name: str, outcome_type: str, market_type: str) -> str:
        """Normalize outcome names for 1x2/moneyline markets."""
        name_lower = outcome_name.lower()
        type_lower = outcome_type.lower() if outcome_type else ''

        # 1x2/moneyline markets
        if market_type in ('1x2', 'moneyline'):
            if 'home' in type_lower or any(kw in name_lower for kw in ['hemma', 'home', '1']):
                return 'home'
            if 'away' in type_lower or any(kw in name_lower for kw in ['borta', 'away', '2']):
                return 'away'
            if 'draw' in type_lower or any(kw in name_lower for kw in ['oavgjort', 'draw', 'x', 'lika']):
                return 'draw'

        return outcome_name

    def _get_sport_market_type_map(self, sport: str) -> Dict[str, str]:
        """Get sport-specific market type mapping (1x2/moneyline only)."""
        # All sports use the same mapping - only 1x2/moneyline is supported
        return {
            '1': '1x2',           # Match result / Match winner
        }

    def _parse_event(self, event_data: Dict, sport: str, all_selections: list) -> Optional[StandardEvent]:
        """Parse event data from WebSocket message."""
        try:
            # Extract teams from primaryParticipants (key is participant ID)
            home_team = None
            away_team = None

            primary_participants = event_data.get('primaryParticipants', {})
            for participant_id, participant in primary_participants.items():
                venue_role = participant.get('venueRole', '')
                if venue_role == 'Home':
                    home_team = participant.get('name')
                elif venue_role == 'Away':
                    away_team = participant.get('name')

            if not home_team or not away_team:
                return None

            # Normalize team names
            home_team = normalize_team_name(home_team)
            away_team = normalize_team_name(away_team)

            # Parse start time
            start_time_str = event_data.get('startingOn')
            start_time = None
            if start_time_str:
                try:
                    start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                except:
                    pass

            # Extract league
            league = event_data.get('leagueName', 'Unknown')

            # Build markets dynamically from selections
            event_id = str(event_data.get('id', ''))
            markets_dict = {}

            # Get sport-specific market type mapping
            market_type_map = self._get_sport_market_type_map(sport)

            # Build markets from selections
            for selection in all_selections:
                selection_event_id = str(selection.get('eventId', ''))
                if selection_event_id != event_id:
                    continue

                # Skip suspended selections
                if selection.get('status') != 'Active':
                    continue

                market_id = str(selection.get('marketId', ''))
                market_type_id = str(selection.get('marketTypeId', ''))

                # Create market if not exists
                if market_id not in markets_dict:
                    # Infer market type from marketTypeId using sport-specific mapping
                    market_type = market_type_map.get(market_type_id, 'other')

                    # Log unmapped market types for future enhancement
                    if market_type == 'other' and market_type_id not in ['8', '18', '52', '60', '342', '2718']:
                        logger.info(f"[{self.provider_id}] {sport}: Unmapped marketTypeId: {market_type_id}")

                    markets_dict[market_id] = {
                        'type': market_type,
                        'outcomes': []
                    }

                outcome_name = selection.get('name', '')
                outcome_type = selection.get('outcomeType', '')
                odds = selection.get('trueOdds', 0.0)

                market_type = markets_dict[market_id]['type']
                normalized_outcome = self._normalize_outcome(outcome_name, outcome_type, market_type)

                outcome_dict = {
                    "name": normalized_outcome,
                    "odds": odds
                }

                markets_dict[market_id]['outcomes'].append(outcome_dict)

            # Convert to list and filter empty markets
            markets_list = [m for m in markets_dict.values() if m['outcomes']]

            # Create StandardEvent
            event_name = f"{home_team} vs {away_team}"

            return StandardEvent(
                id=event_id,
                name=event_name,
                provider=self.provider_id,
                sport=sport,
                league=league,
                home_team=home_team,
                away_team=away_team,
                start_time=start_time,
                markets=markets_list
            )

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Failed to parse event: {e}")
            return None

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """Not used - extract() is overridden."""
        raise NotImplementedError("HajperRetriever uses extract() directly")

    async def extract(self, sport: str, limit: int = 50) -> List[StandardEvent]:
        """Extract events using multi-league approach for comprehensive coverage."""
        sport_url_path = self.SPORT_URL_MAP.get(sport)
        if not sport_url_path:
            logger.warning(f"[{self.provider_id}] Sport '{sport}' not supported")
            return []

        logger.info(f"[{self.provider_id}] Starting multi-league extraction for {sport}")
        logger.info(f"[{self.provider_id}] Max leagues to process: {self.max_leagues}")

        all_events_data = {}  # event_id -> event_json

        try:
            if not isinstance(self.transport, BrowserTransport):
                logger.error(f"[{self.provider_id}] Hajper requires BrowserTransport")
                return []

            await self.transport._ensure_browser()
            page = self.transport.page

            # Step 1: Load main sport page to get league links
            sport_url = f"{self.site_url}{sport_url_path}"
            logger.info(f"[{self.provider_id}] Loading main page: {sport_url}")

            await page.goto(sport_url, wait_until='domcontentloaded', timeout=30000)  # Optimized: domcontentloaded

            # Handle cookie consent
            try:
                await page.click('button:has-text("Accept")', timeout=2000)
                logger.info(f"[{self.provider_id}] Clicked cookie consent")
                await page.wait_for_timeout(1000)
            except:
                pass

            await page.wait_for_timeout(1500)  # Optimized from 3s

            # Extract league links from DOM (with caching)
            cache_key = sport
            if cache_key in self._league_cache:
                league_links = self._league_cache[cache_key]
                logger.info(f"[{self.provider_id}] Using cached league links ({len(league_links)} leagues)")
            else:
                league_links = await self._extract_league_links(page)
                if league_links:
                    self._league_cache[cache_key] = league_links

            if not league_links:
                logger.warning(f"[{self.provider_id}] No league links found")
                return []

            # Limit number of leagues to process
            leagues_to_process = league_links[:self.max_leagues]
            logger.info(f"[{self.provider_id}] Processing {len(leagues_to_process)} of {len(league_links)} leagues")

            # Step 2: Navigate to leagues in parallel (optimized concurrency)
            concurrent_limit = self.config.get('concurrent_leagues', 8)  # Increased from 5 to 8
            sem = asyncio.Semaphore(concurrent_limit)

            async def extract_league_with_limit(league_index: int, league: dict) -> tuple:
                """Extract events from single league with concurrency control."""
                async with sem:
                    league_name = league['text']
                    league_url = league['href']

                    logger.info(f"[{self.provider_id}] [{league_index}/{len(leagues_to_process)}] Processing: {league_name}")

                    # Create dedicated page for this league
                    league_page = await self.transport.new_page()

                    try:
                        league_events = await self._extract_events_from_league(league_page, league_url, sport)
                        logger.info(f"[{self.provider_id}]   -> {len(league_events)} events from {league_name}")
                        return (True, league_events)

                    except Exception as e:
                        logger.warning(f"[{self.provider_id}] Failed to extract {league_name}: {e}")
                        return (False, [])

                    finally:
                        await league_page.close()

            # Create parallel tasks for all leagues
            tasks = [
                extract_league_with_limit(i, league)
                for i, league in enumerate(leagues_to_process, 1)
            ]

            # Execute in parallel with error handling
            logger.info(f"[{self.provider_id}] Extracting {len(leagues_to_process)} leagues in parallel (max {concurrent_limit} concurrent)")
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Merge results
            successful_leagues = 0
            for result in results:
                if isinstance(result, tuple):
                    success, league_events = result
                    if success:
                        successful_leagues += 1
                        for event_id, event_json in league_events:
                            if event_id not in all_events_data:
                                all_events_data[event_id] = event_json

            logger.info(f"[{self.provider_id}] Successfully extracted {successful_leagues}/{len(leagues_to_process)} leagues")
            logger.info(f"[{self.provider_id}] Total unique events: {len(all_events_data)}")

            # Step 3: Parse events
            events = []
            for event_id, event_json in all_events_data.items():
                try:
                    event_obj = json.loads(event_json)
                    event_data = event_obj['event']
                    selections = event_obj['selections']
                    sport_key = event_obj['sport']

                    event = self._parse_event(event_data, sport_key, selections)
                    if event:
                        events.append(event)
                except Exception as e:
                    logger.debug(f"[{self.provider_id}] Failed to parse event {event_id}: {e}")

            logger.info(f"[{self.provider_id}] Parsed {len(events)} events successfully")
            return events[:limit] if limit else events

        except Exception as e:
            logger.error(f"[{self.provider_id}] Error extracting {sport}: {e}", exc_info=True)
            return []
