"""
ComeOn Multi-League Retriever

Extracts events by navigating to individual league pages.
Based on testing: 1,044 events achievable vs 33 from main page only.
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


class ComeOnMultiLeagueRetriever(BrowserRetriever, RSocketMixin):
    """
    Multi-league ComeOn retriever for comprehensive event coverage.

    Performance: ~60-120 seconds for 1,000+ events
    Coverage: 1,044 estimated events from 178 leagues
    Strategy: Navigate to individual league pages to extract all events
    """

    # Sport URL mapping: sports.json keys -> ComeOn URL slugs
    SPORT_URL_MAP = {
        'football': '/sportsbook/football',
        'basketball': '/sportsbook/basketball',
        'tennis': '/sportsbook/tennis',
        'ice_hockey': '/sportsbook/icehockey',
        'american_football': '/sportsbook/americanfootball',
        'baseball': '/sportsbook/baseball',
        'cricket': '/sportsbook/cricket',
        'rugby': '/sportsbook/rugby',
        'esports': '/sportsbook/esports',
        'mma': '/sportsbook/mma',
        'boxing': '/sportsbook/boxing',
        'motorsports': '/sportsbook/formula-1',
    }

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)
        raw_site_url = config.get("site_url", f"https://www.{config.get('domain')}")
        self.site_url: str = raw_site_url.rstrip("/")
        self.ws_messages = []
        self.max_leagues = config.get("max_leagues", 30)  # Reduced default for faster extraction
        # Cache for league list to avoid re-fetching between runs
        self._league_cache: Dict[str, List[Dict[str, str]]] = {}

    async def _extract_league_links(self, page) -> List[Dict[str, str]]:
        """Extract league links from main page DOM."""
        league_links = await page.evaluate('''() => {
            const links = [];
            const seen = new Set();

            // Find all league links (not event links)
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

    async def _extract_events_from_league(self, page, league_url: str) -> List[tuple]:
        """Extract events from a single league page."""
        # Setup per-page WebSocket interception
        ws_messages = self._setup_ws_interception(page)

        # Navigate to league page
        full_url = league_url if league_url.startswith('http') else f"{self.site_url}{league_url}"
        try:
            # networkidle needed for WebSocket data capture
            await page.goto(full_url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)  # Allow WebSocket messages
        except Exception as e:
            logger.warning(f"[{self.provider_id}] Failed to load {league_url}: {e}")
            return []

        # Parse WebSocket messages
        events_data = {}
        for msg_data in ws_messages:
            if isinstance(msg_data, list):
                for msg in msg_data:
                    if msg.get('type') == 'INITIAL_STATE':
                        payload = msg.get('payload', {})
                        events = payload.get('events', [])

                        for event_data in events:
                            event_id = event_data.get('id')
                            if event_id:
                                events_data[event_id] = json.dumps(event_data)

        return list(events_data.items())

    def _normalize_market_type(self, market_name: str) -> str:
        """Normalize ComeOn market names (Swedish/English) to standard types."""
        name_lower = market_name.lower()

        # 1x2 / Moneyline
        if any(kw in name_lower for kw in ['1x2', 'helmatchen', 'match result', 'slutresultat']):
            return '1x2'

        # Over/Under / Totals
        if any(kw in name_lower for kw in ['över/under', 'over/under', 'o/u', 'total', 'mål över', 'mål under']):
            return 'over_under'

        # Spread / Handicap
        if any(kw in name_lower for kw in ['handikapp', 'handicap', 'asian', 'europeiskt', 'spread']):
            return 'spread'

        # Both Teams to Score
        if any(kw in name_lower for kw in ['båda lagen', 'both teams', 'btts']):
            return 'both_teams_to_score'

        return 'other'

    def _normalize_outcome(self, outcome_name: str, outcome_type: str, market_type: str) -> str:
        """Normalize outcome names based on market type and context."""
        name_lower = outcome_name.lower()
        type_lower = outcome_type.lower() if outcome_type else ''

        # 1x2 markets
        if market_type == '1x2':
            if 'home' in type_lower or any(kw in name_lower for kw in ['hemma', 'home', '1']):
                return 'home'
            if 'away' in type_lower or any(kw in name_lower for kw in ['borta', 'away', '2']):
                return 'away'
            if 'draw' in type_lower or any(kw in name_lower for kw in ['oavgjort', 'draw', 'x']):
                return 'draw'

        # Over/Under markets
        if market_type == 'over_under':
            if 'over' in type_lower or 'över' in name_lower or 'over' in name_lower:
                return 'over'
            if 'under' in type_lower or 'under' in name_lower:
                return 'under'

        # Spread markets
        if market_type == 'spread':
            if 'home' in type_lower or '1' in outcome_type:
                return 'home'
            if 'away' in type_lower or '2' in outcome_type:
                return 'away'

        # Both teams to score
        if market_type == 'both_teams_to_score':
            if any(kw in name_lower for kw in ['yes', 'ja', 'båda']):
                return 'yes'
            if any(kw in name_lower for kw in ['no', 'nej', 'minst ett']):
                return 'no'

        # Fallback to original name (cleaned)
        return outcome_name.lower().strip()

    def _get_sport_url(self, sport: str) -> str:
        """Get URL for sport page."""
        sport_path = self.SPORT_URL_MAP.get(sport)
        if not sport_path:
            logger.warning(f"[{self.provider_id}] Unknown sport '{sport}', defaulting to football")
            sport_path = self.SPORT_URL_MAP['football']
        return f"{self.site_url}{sport_path}"

    def _construct_event_detail_url(self, event_id: str, home_team: str, away_team: str) -> str:
        """
        Construct URL for event detail page.

        Pattern: /events/{event_id}-{slug}
        Example: /events/2988556-arsenal-manchester-united
        """
        # Generate slug from team names
        slug = f"{home_team}-{away_team}".lower()
        slug = slug.replace(' ', '-')
        slug = ''.join(c for c in slug if c.isalnum() or c == '-')

        return f"{self.site_url}/events/{event_id}-{slug}"

    async def _extract_event_details(self, event_id: str, home_team: str, away_team: str) -> List[Dict]:
        """
        Navigate to event detail page and extract full market data.

        Returns:
            List of market dictionaries with complete data (over/under, spreads, props)
        """
        event_url = self._construct_event_detail_url(event_id, home_team, away_team)

        # Create dedicated page for this event
        page = await self.transport.new_page()
        ws_messages = self._setup_ws_interception(page)

        try:
            # OPTIMIZATION: Use "load" instead of "networkidle" for faster page loads
            await page.goto(event_url, wait_until="load", timeout=10000)

            # OPTIMIZATION: Reduced wait time from 2000ms to 500ms
            # WebSocket data typically arrives within 300-500ms
            await page.wait_for_timeout(500)

            # Parse WebSocket messages for market data
            markets = self._parse_event_detail_markets(ws_messages)

            return markets

        except Exception as e:
            logger.warning(f"[{self.provider_id}] Failed to extract event {event_id} details: {e}")
            return []

        finally:
            await page.close()

    def _parse_event_detail_markets(self, ws_messages: List) -> List[Dict]:
        """
        Parse WebSocket messages from event detail page.

        Similar to existing parsing but captures ALL markets, not just 1x2.
        """
        all_markets = {}
        all_selections = {}

        # Build lookups (similar to existing logic)
        for msg_data in ws_messages:
            if isinstance(msg_data, list):
                for msg in msg_data:
                    if msg.get('type') == 'INITIAL_STATE':
                        payload = msg.get('payload', {})

                        for market in payload.get('markets', []):
                            market_id = market.get('id')
                            if market_id:
                                all_markets[market_id] = market

                        for selection in payload.get('selections', []):
                            selection_id = selection.get('id')
                            if selection_id:
                                all_selections[selection_id] = selection

        # Convert to standardized market format
        markets = []
        for market_id, market_data in all_markets.items():
            market_type = self._normalize_market_type(market_data.get('name', ''))

            # Get selections for this market
            selection_ids = [
                sel_id for sel_id, sel in all_selections.items()
                if sel.get('marketId') == market_id
            ]

            outcomes = []
            for sel_id in selection_ids:
                selection = all_selections[sel_id]

                outcome_name = selection.get('name', '')
                outcome_type = selection.get('outcomeType', '')
                odds = selection.get('odds') or selection.get('decimalOdds') or selection.get('trueOdds')
                point_value = selection.get('points')  # This is what we're after!

                if not outcome_name or not odds:
                    continue

                outcome_normalized = self._normalize_outcome(outcome_name, outcome_type, market_type)

                outcome_dict = {
                    'name': outcome_normalized,
                    'odds': float(odds)
                }

                # Include point value if present
                if point_value is not None and point_value != 0.0:
                    outcome_dict['point'] = float(point_value)

                outcomes.append(outcome_dict)

            if outcomes:
                markets.append({
                    'type': market_type,
                    'outcomes': outcomes
                })

        return markets

    async def _enhance_events_with_details(self, base_events: List[StandardEvent]) -> List[StandardEvent]:
        """
        Enhance events with full market data from event detail pages.

        Args:
            base_events: Events from league pages (basic markets)

        Returns:
            Enhanced events with full market data
        """
        # Check if detail extraction is enabled
        if not self.config.get('extract_full_markets', False):
            logger.info(f"[{self.provider_id}] Event detail extraction disabled, skipping")
            return base_events

        # Filter which events to enhance
        events_to_enhance = [
            e for e in base_events
            if self._should_extract_detail(e)
        ]

        logger.info(f"[{self.provider_id}] Enhancing {len(events_to_enhance)}/{len(base_events)} events with detail extraction")

        if not events_to_enhance:
            return base_events

        # Extract details in parallel
        concurrent_limit = self.config.get('concurrent_event_details', 10)
        sem = asyncio.Semaphore(concurrent_limit)

        async def enhance_single_event(event: StandardEvent) -> StandardEvent:
            async with sem:
                try:
                    detail_markets = await self._extract_event_details(
                        event.id, event.home_team, event.away_team
                    )

                    # Merge markets (keep existing, add new ones)
                    if detail_markets:
                        event.markets = self._merge_markets(event.markets, detail_markets)

                    return event

                except Exception as e:
                    logger.warning(f"[{self.provider_id}] Failed to enhance event {event.id}: {e}")
                    return event  # Return original on error

        # Process in parallel
        enhanced = await asyncio.gather(
            *[enhance_single_event(e) for e in events_to_enhance],
            return_exceptions=True
        )

        # Build result (enhanced + non-enhanced)
        enhanced_map = {e.id: e for e in enhanced if isinstance(e, StandardEvent)}

        result = []
        for event in base_events:
            result.append(enhanced_map.get(event.id, event))

        return result

    def _should_extract_detail(self, event: StandardEvent) -> bool:
        """
        Determine if event should get detail extraction.

        Filters based on configuration.
        """
        # OPTIMIZATION: Skip events that already have over/under markets with point values
        # This avoids unnecessary detail page loads for events with complete data
        has_over_under_with_points = False
        for market in event.markets:
            if market['type'] == 'over_under':
                for outcome in market['outcomes']:
                    if 'point' in outcome:
                        has_over_under_with_points = True
                        break

        if has_over_under_with_points:
            logger.debug(f"[{self.provider_id}] Skipping {event.id} - already has over/under with points")
            return False

        # Apply filter mode
        filter_mode = self.config.get('detail_extraction_filter', 'all')

        if filter_mode == 'none':
            return False

        if filter_mode == 'all':
            return True

        if filter_mode == 'popular':
            # Popular leagues only
            popular_leagues = [
                'Premier League', 'La Liga', 'Bundesliga', 'Serie A', 'Ligue 1',
                'Champions League', 'Europa League', 'NBA', 'NHL', 'NFL'
            ]
            return any(league.lower() in event.league.lower() for league in popular_leagues)

        return True

    def _merge_markets(self, base_markets: List[Dict], detail_markets: List[Dict]) -> List[Dict]:
        """
        Merge markets from league page and event detail page.

        Strategy: Keep all markets, avoid duplicates by market type.
        """
        merged = {}

        # Add base markets
        for market in base_markets:
            market_type = market['type']
            merged[market_type] = market

        # Add detail markets (will override if same type, which is fine - detail is more complete)
        for market in detail_markets:
            market_type = market['type']
            merged[market_type] = market

        return list(merged.values())

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """Not used - we override extract() completely."""
        return []

    def _parse_event(self, event_data: Dict) -> Optional[StandardEvent]:
        """Parse event data into StandardEvent format."""
        try:
            event_id = event_data.get('id')
            if not event_id:
                return None

            # Extract teams
            home_team = event_data.get('homeTeam', {}).get('name', '')
            away_team = event_data.get('awayTeam', {}).get('name', '')

            if not home_team or not away_team:
                return None

            # Extract start time
            start_time_str = event_data.get('startTime')
            start_time = None
            if start_time_str:
                try:
                    start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                except:
                    pass

            # Extract markets (from separate field in payload)
            markets = []

            return StandardEvent(
                provider_id=self.provider_id,
                event_id=str(event_id),
                sport='football',
                league=event_data.get('tournament', {}).get('name', 'Unknown'),
                home_team=home_team,
                away_team=away_team,
                start_time=start_time,
                markets=markets
            )

        except Exception as e:
            logger.warning(f"[{self.provider_id}] Failed to parse event {event_data.get('id')}: {e}")
            return None

    async def extract(self, sport: str | List[str], limit: Optional[int] = None) -> List[StandardEvent]:
        """
        Extract events from one or more sports.

        Args:
            sport: Sport key (e.g., 'football') or list of sports, or "all" for all sports
            limit: Optional limit per sport

        Returns:
            List of StandardEvent objects
        """
        # Resolve which sports to extract
        sports_to_extract = self._resolve_sports(sport)

        logger.info(f"[{self.provider_id}] Extracting from {len(sports_to_extract)} sports: {', '.join(sports_to_extract)}")

        all_events = []

        # Extract each sport sequentially
        for sport_key in sports_to_extract:
            try:
                sport_events = await self._extract_single_sport(sport_key, limit)
                logger.info(f"[{self.provider_id}] {sport_key}: {len(sport_events)} events")
                all_events.extend(sport_events)
            except Exception as e:
                logger.error(f"[{self.provider_id}] Failed to extract {sport_key}: {e}")
                # Continue with other sports even if one fails

        return all_events

    def _resolve_sports(self, sport: str | List[str]) -> List[str]:
        """Resolve sport parameter to list of sport keys."""
        if isinstance(sport, list):
            return sport

        if sport == "all":
            return list(self.SPORT_URL_MAP.keys())

        # Single sport
        return [sport.split('/')[0] if '/' in sport else sport]

    async def _extract_single_sport(self, sport: str, limit: Optional[int] = None) -> List[StandardEvent]:
        """
        Extract events from a single sport.

        Args:
            sport: Sport to extract (e.g., 'football')
            limit: Optional limit on number of events (applies max_leagues limit instead)

        Returns:
            List of StandardEvent objects
        """
        sport_normalized = sport.split('/')[0] if '/' in sport else sport

        logger.info(f"[{self.provider_id}] Starting multi-league extraction for {sport_normalized}")
        logger.info(f"[{self.provider_id}] Max leagues to process: {self.max_leagues}")

        self.ws_messages = []
        all_events_data = {}  # event_id -> event_data

        try:
            await self.transport._ensure_browser()
            page = self.transport.page

            # Setup WebSocket interception
            def on_websocket(ws):
                def on_frame_received(payload):
                    if isinstance(payload, bytes):
                        decoded = self._decode_rsocket_frame(payload)
                        if decoded:
                            self.ws_messages.append(decoded)

                ws.on("framereceived", on_frame_received)

            page.on("websocket", on_websocket)

            # Step 1: Load main page to get league links
            main_url = self._get_sport_url(sport_normalized)
            logger.info(f"[{self.provider_id}] Loading main page for {sport_normalized}: {main_url}")

            # networkidle needed for WebSocket establishment
            await page.goto(main_url, wait_until='networkidle', timeout=45000)
            await page.wait_for_timeout(3000)

            # Extract league links (with caching)
            cache_key = f"{sport_normalized}"
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

            # Step 2: Navigate to leagues in parallel
            # Get concurrency limit from config (default 8 for better performance)
            concurrent_limit = self.config.get('concurrent_leagues', 8)
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
                        league_events = await self._extract_events_from_league(league_page, league_url)
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
                                all_events_data[event_id] = json.loads(event_json)
                elif isinstance(result, Exception):
                    logger.error(f"[{self.provider_id}] League extraction error: {result}")

            logger.info(f"[{self.provider_id}] Successfully extracted from {successful_leagues}/{len(leagues_to_process)} leagues")
            logger.info(f"[{self.provider_id}] Total unique events: {len(all_events_data)}")

            # Step 3: Parse events into StandardEvent format
            # Note: We need to also parse markets and selections from the payload
            # For now, just parse basic event info
            parsed_events = []

            # We need to re-parse the ws_messages to get markets and selections
            # Build lookups
            all_markets = {}
            all_selections = {}

            for msg_data in self.ws_messages:
                if isinstance(msg_data, list):
                    for msg in msg_data:
                        if msg.get('type') == 'INITIAL_STATE':
                            payload = msg.get('payload', {})

                            # Build market lookup
                            for market in payload.get('markets', []):
                                market_id = market.get('id')
                                if market_id:
                                    all_markets[market_id] = market

                            # Build selection lookup
                            for selection in payload.get('selections', []):
                                selection_id = selection.get('id')
                                if selection_id:
                                    all_selections[selection_id] = selection

            # Debug: Check first event structure
            if all_events_data:
                sample_id = list(all_events_data.keys())[0]
                sample_event = all_events_data[sample_id]
                logger.info(f"[{self.provider_id}] Sample event structure: {list(sample_event.keys())}")
                logger.info(f"[{self.provider_id}] Sample event: {str(sample_event)[:200]}")

            logger.info(f"[{self.provider_id}] Markets available: {len(all_markets)}")
            logger.info(f"[{self.provider_id}] Selections available: {len(all_selections)}")

            # Build market -> selections mapping (selections have marketId field)
            market_selections_map = {}
            for selection_id, selection_data in all_selections.items():
                market_id = selection_data.get('marketId')
                if market_id:
                    if market_id not in market_selections_map:
                        market_selections_map[market_id] = []
                    market_selections_map[market_id].append(selection_id)

            # Build event -> markets mapping (markets have eventId field)
            event_markets_map = {}
            for market_id, market_data in all_markets.items():
                market_event_id = market_data.get('eventId')
                if market_event_id:
                    if market_event_id not in event_markets_map:
                        event_markets_map[market_event_id] = []
                    event_markets_map[market_event_id].append(market_id)

            logger.info(f"[{self.provider_id}] Market->selections mapping: {len(market_selections_map)} markets have selections")
            logger.info(f"[{self.provider_id}] Event->markets mapping: {len(event_markets_map)} events have markets")

            # Parse events with markets
            for event_id, event_data in all_events_data.items():
                try:
                    # Get markets for this event using the mapping
                    event_markets = []
                    market_ids = event_markets_map.get(event_id, [])

                    if not market_ids:
                        logger.debug(f"[{self.provider_id}] Event {event_id} has no markets")

                    for market_id in market_ids:
                        market_data = all_markets.get(market_id)
                        if not market_data:
                            continue

                        # Normalize market type
                        market_type_raw = market_data.get('name', 'Unknown')
                        market_type_normalized = self._normalize_market_type(market_type_raw)

                        # Get selections for this market using the mapping
                        selection_ids = market_selections_map.get(market_id, [])

                        # Get odds for each selection
                        outcomes = []
                        for selection_id in selection_ids:
                            selection = all_selections.get(selection_id)
                            if not selection:
                                continue

                            # Extract outcome metadata
                            outcome_name = selection.get('name', '')
                            outcome_type = selection.get('outcomeType', '')  # "Home", "Away", "Over", "Under"
                            decimal_odds = selection.get('odds') or selection.get('decimalOdds') or selection.get('trueOdds')
                            point_value = selection.get('points')  # Line value (e.g., 2.5, -1.5)

                            if not outcome_name or not decimal_odds:
                                continue

                            # Normalize outcome name based on market type
                            outcome_normalized = self._normalize_outcome(
                                outcome_name, outcome_type, market_type_normalized
                            )

                            # Build outcome dict
                            outcome_dict = {
                                'name': outcome_normalized,
                                'odds': float(decimal_odds)
                            }

                            # Add point value if present and non-zero
                            if point_value is not None and point_value != 0.0:
                                outcome_dict['point'] = float(point_value)

                            outcomes.append(outcome_dict)

                        if not outcomes:
                            logger.debug(f"[{self.provider_id}] Market {market_id} ({market_type_normalized}) has no outcomes")

                        if outcomes:
                            market_dict = {
                                'type': market_type_normalized,
                                'outcomes': outcomes
                            }

                            # Skip markets without required data
                            if market_type_normalized in ['over_under', 'spread']:
                                # Verify at least one outcome has a point value
                                has_point = any(o.get('point') is not None for o in outcomes)
                                if not has_point:
                                    logger.debug(f"[{self.provider_id}] Skipping {market_type_normalized} without point values")
                                    continue

                            event_markets.append(market_dict)

                    # Extract teams from event structure
                    # Try different methods to get team names
                    home_team = None
                    away_team = None

                    # Method 1: Parse from eventName (e.g., "Arsenal - Sunderland" or "Team A @ Team B")
                    event_name = event_data.get('eventName', '')

                    # Try different separators
                    if ' - ' in event_name:
                        parts = event_name.split(' - ')
                        if len(parts) == 2:
                            home_team = parts[0].strip()
                            away_team = parts[1].strip()
                    elif ' @ ' in event_name:
                        # Basketball and some American sports use "@" separator
                        parts = event_name.split(' @ ')
                        if len(parts) == 2:
                            away_team = parts[0].strip()  # Note: away @ home
                            home_team = parts[1].strip()
                    elif ' vs ' in event_name.lower():
                        # Some sports use "vs"
                        parts = event_name.split(' vs ' if ' vs ' in event_name else ' VS ')
                        if len(parts) == 2:
                            home_team = parts[0].strip()
                            away_team = parts[1].strip()

                    # Method 2: Use primaryParticipants/secondaryParticipants
                    if not home_team or not away_team:
                        primary = event_data.get('primaryParticipants', [])
                        secondary = event_data.get('secondaryParticipants', [])

                        if primary and len(primary) > 0:
                            home_team = primary[0].get('name', '') if isinstance(primary[0], dict) else str(primary[0])
                        if secondary and len(secondary) > 0:
                            away_team = secondary[0].get('name', '') if isinstance(secondary[0], dict) else str(secondary[0])

                    if not home_team or not away_team:
                        logger.debug(f"[{self.provider_id}] Skipping event {event_id}: missing teams (name: {event_name})")
                        continue

                    # Normalize team names
                    home_team = normalize_team_name(home_team)
                    away_team = normalize_team_name(away_team)

                    # Extract start time
                    start_time_str = event_data.get('startingOn') or event_data.get('startTime')
                    start_time = None
                    if start_time_str:
                        try:
                            start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                        except:
                            pass

                    # Extract league name
                    league_name = event_data.get('leagueName') or event_data.get('tournament', {}).get('name', 'Unknown')

                    if event_markets:
                        logger.debug(f"[{self.provider_id}] Event {event_id} has {len(event_markets)} markets")
                    else:
                        logger.debug(f"[{self.provider_id}] Event {event_id} has NO markets after parsing")

                    event = StandardEvent(
                        id=str(event_id),
                        name=f"{home_team} vs {away_team}",
                        sport=sport_normalized,
                        provider=self.provider_id,
                        markets=event_markets,
                        league=league_name,
                        home_team=home_team,
                        away_team=away_team,
                        start_time=start_time.isoformat() if start_time else ""
                    )

                    parsed_events.append(event)

                except Exception as e:
                    logger.warning(f"[{self.provider_id}] Failed to parse event {event_id}: {e}")

            logger.info(f"[{self.provider_id}] Successfully parsed {len(parsed_events)} events")

            # Enhance with event detail extraction if enabled
            if self.config.get('extract_full_markets', False):
                parsed_events = await self._enhance_events_with_details(parsed_events)
                logger.info(f"[{self.provider_id}] Enhanced {len(parsed_events)} events with detail data")

            return parsed_events

        except Exception as e:
            logger.error(f"[{self.provider_id}] Extraction failed: {e}", exc_info=True)
            return []
