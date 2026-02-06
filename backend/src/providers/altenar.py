"""
Altenar Retriever - REST API-based extraction

Altenar platform uses REST API for sportsbook data.
Events are fetched via /widget/GetUpcoming and /widget/GetLivenow endpoints.

API Usage:
- GetUpcoming requires 'sportId' parameter for sport-specific events
- Without sportId: Returns football events only (default)
- With sportId=67: Returns basketball events
- Each sport requires separate API call with corresponding sportId

Providers using Altenar:
- Betinia (betinia.se / betinia.com)
- FrankFred (frankfred.com)
"""

from typing import Dict, Any, List, Optional
import json
import logging
from datetime import datetime
import asyncio
import aiohttp

from ..core import Retriever, StandardEvent
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)


class AltenarRetriever(Retriever):
    """
    Altenar platform retriever using REST API.

    Altenar provides events via REST API endpoints:
    - /widget/GetUpcoming - Upcoming events (pre-match)
    - /widget/GetLivenow - Live events

    Architecture:
    1. Call GetUpcoming/GetLivenow endpoint with sportId parameter
    2. Parse response with events, competitors, markets, odds
    3. Resolve relational references by ID
    4. Map to StandardEvent format

    Note: sportId parameter is REQUIRED to get sport-specific events.
    Without it, only football events are returned.
    """

    # Sport mapping from Altenar sportId to our sport keys
    SPORT_MAPPING = {
        66: 'football',
        67: 'basketball',
        68: 'tennis',
        70: 'ice_hockey',
        77: 'table_tennis',
        73: 'handball',
        69: 'volleyball',
        145: 'esports',
        # Add more as discovered
    }

    # Market type mapping from Altenar typeId to our market types
    # Only 1x2/moneyline markets are supported
    MARKET_TYPE_MAPPING = {
        1: '1x2',              # Match result (football)
        219: 'moneyline',      # Winner (basketball, incl. overtime)
    }

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        # Altenar API base
        self.api_base = config.get("api_base", "https://sb2frontend-altenar2.biahosted.com/api")

        # Integration ID (skin)
        self.integration = config.get("integration", "betiniase2")

    def _find_by_id(self, items: List[Dict], target_id: int) -> Optional[Dict]:
        """Find item in list by ID."""
        for item in items:
            if item.get('id') == target_id:
                return item
        return None

    def _standardize_outcome(
        self,
        outcome_name: str,
        market_type: str,
        raw_home: str,
        raw_away: str
    ) -> str:
        """
        Standardize outcome names to platform conventions.

        Args:
            outcome_name: Raw outcome name from API
            market_type: Market type (1x2, moneyline)
            raw_home: Raw home team name (before normalization)
            raw_away: Raw away team name (before normalization)

        Returns:
            Standardized outcome name (home, away, draw)
        """
        outcome_lower = outcome_name.lower().strip()

        # Handle 1x2 and moneyline markets (only difference is draw for 1x2)
        if market_type in ('1x2', 'moneyline'):
            # Check for draw first (1x2 only)
            if market_type == '1x2' and outcome_lower in ['x', 'draw', 'tie', 'x2']:
                return 'draw'

            # Extract team name without parentheses and extra text
            def extract_base_name(team_name):
                import re
                base = re.sub(r'\([^)]*\)', '', team_name).strip()
                return normalize_team_name(base)

            home_base = extract_base_name(raw_home)
            away_base = extract_base_name(raw_away)
            outcome_base = extract_base_name(outcome_name)

            # Try exact match with normalized names
            if outcome_base == home_base:
                return 'home'
            if outcome_base == away_base:
                return 'away'

            # Try partial match - check if any word from team name is in outcome
            home_words = set(home_base.split())
            away_words = set(away_base.split())
            outcome_words = set(outcome_base.split())

            if home_words & outcome_words:
                return 'home'
            if away_words & outcome_words:
                return 'away'

            # Simple numeric markers
            if outcome_lower in ['1', '2']:
                return 'home' if outcome_lower == '1' else 'away'

        # If no match found, return original (will be logged as 'other')
        return outcome_name

    async def _fetch_events(self, endpoint: str, sport_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Fetch events from Altenar API endpoint.

        Args:
            endpoint: API endpoint (e.g., 'widget/GetUpcoming')
            sport_id: Optional sport ID to filter events (e.g., 67 for basketball)

        Returns:
            Response data with events, competitors, markets, odds
        """
        try:
            url = f"{self.api_base}/{endpoint}"

            params = {
                'culture': 'en-GB',
                'timezoneOffset': '0',
                'integration': self.integration,
                'deviceType': '1',
                'numFormat': 'en-GB'
            }

            # Add sport filter if provided
            if sport_id is not None:
                params['sportId'] = str(sport_id)

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data

            logger.warning(f"[{self.provider_id}] {endpoint} returned status {response.status}")
            return {}

        except Exception as e:
            logger.error(f"[{self.provider_id}] Error fetching {endpoint}: {e}")
            return {}

    def _parse_event(
        self,
        event_data: Dict,
        sport: str,
        reference_data: Dict[str, List[Dict]]
    ) -> Optional[StandardEvent]:
        """
        Parse event data from Altenar API.

        Args:
            event_data: Event object from API
            sport: Sport key (e.g., 'football')
            reference_data: Dict with 'competitors', 'champs', 'markets', 'odds' lists

        Returns:
            StandardEvent or None
        """
        try:
            event_id = str(event_data.get('id', ''))
            event_name = event_data.get('name', '')

            if not event_id:
                return None

            # Parse start time
            start_time_str = event_data.get('startDate')
            start_time = None
            if start_time_str:
                try:
                    start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                except Exception as e:
                    logger.debug(f"[{self.provider_id}] Failed to parse start time: {e}")

            # Get competitors (teams)
            competitor_ids = event_data.get('competitorIds', [])
            competitors = [
                self._find_by_id(reference_data.get('competitors', []), comp_id)
                for comp_id in competitor_ids
            ]
            competitors = [c for c in competitors if c]  # Filter out None

            # Determine home/away teams (normalize immediately)
            raw_home = competitors[0]['name'] if len(competitors) > 0 else None
            raw_away = competitors[1]['name'] if len(competitors) > 1 else None

            # For events with only one competitor (e.g., futures), use event name
            if not raw_home and not raw_away:
                # This might be a special market (futures, outright, etc.)
                # Skip for now
                return None

            # Normalize team names
            home_team = normalize_team_name(raw_home) if raw_home else None
            away_team = normalize_team_name(raw_away) if raw_away else None

            # Get championship (league)
            champ_id = event_data.get('champId')
            champ = self._find_by_id(reference_data.get('champs', []), champ_id)
            league = champ['name'] if champ else 'Unknown'

            # Parse markets
            markets = []
            market_ids = event_data.get('marketIds', [])

            for market_id in market_ids:
                market = self._find_by_id(reference_data.get('markets', []), market_id)
                if not market:
                    continue

                # Map market type
                market_type_id = market.get('typeId')
                market_type = self.MARKET_TYPE_MAPPING.get(market_type_id, 'other')
                market_name = market.get('name', 'Unknown')

                # Get odds for this market
                odd_ids = market.get('oddIds', [])
                outcomes = []

                for odd_id in odd_ids:
                    odd = self._find_by_id(reference_data.get('odds', []), odd_id)
                    if odd:
                        raw_outcome = odd.get('name', '')
                        standardized_outcome = self._standardize_outcome(
                            raw_outcome,
                            market_type,
                            raw_home,
                            raw_away
                        )

                        outcomes.append({
                            'name': standardized_outcome,
                            'odds': odd.get('price', 0.0)
                        })

                if outcomes:
                    market_dict = {
                        'type': market_type,
                        'outcomes': outcomes
                    }
                    markets.append(market_dict)

                    # Log unmapped market types for future improvement
                    if market_type == 'other' and market_type_id:
                        logger.debug(
                            f"[{self.provider_id}] {sport}: Unmapped marketTypeId: {market_type_id} "
                            f"({market_name})"
                        )

            # Create StandardEvent
            return StandardEvent(
                id=event_id,
                name=event_name,
                provider=self.provider_id,
                sport=sport,
                league=league,
                home_team=home_team,
                away_team=away_team,
                start_time=start_time,
                markets=markets
            )

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Failed to parse event: {e}")
            return None

    def _get_sport_url(self, sport: str) -> str:
        """
        Get API URL for sport.

        Not used for Altenar since we use the generic GetUpcoming endpoint
        with sportId parameter instead of sport-specific URLs.
        """
        return f"{self.api_base}/widget/GetUpcoming"

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """
        Parse Altenar API response data.

        Not used - we override extract() completely to handle the API call
        and parsing in one method.
        """
        return []

    async def extract(self, sport: str, limit: int = 100, **kwargs) -> List[StandardEvent]:
        """
        Extract events using REST API.

        Args:
            sport: Sport key (e.g., 'football')
            limit: Maximum number of events to return

        Returns:
            List of StandardEvents
        """
        logger.info(f"[{self.provider_id}] Starting extraction for {sport}")

        # Find sport ID
        sport_id = None
        for sid, sport_key in self.SPORT_MAPPING.items():
            if sport_key == sport:
                sport_id = sid
                break

        if not sport_id:
            logger.warning(f"[{self.provider_id}] Sport '{sport}' not supported")
            return []

        try:
            # Fetch upcoming events with sport filter
            logger.info(f"[{self.provider_id}] Fetching upcoming events for {sport} (sportId={sport_id})")
            data = await self._fetch_events('widget/GetUpcoming', sport_id=sport_id)

            if not data or 'events' not in data:
                logger.warning(f"[{self.provider_id}] No data returned from API")
                return []

            # All events should match the requested sport (no client-side filtering needed)
            sport_events = data.get('events', [])

            logger.info(f"[{self.provider_id}] Found {len(sport_events)} {sport} events")

            # Reference data for resolving IDs
            reference_data = {
                'competitors': data.get('competitors', []),
                'champs': data.get('champs', []),
                'markets': data.get('markets', []),
                'odds': data.get('odds', [])
            }

            # Parse events
            events = []
            for event_data in sport_events:
                event = self._parse_event(event_data, sport, reference_data)
                if event:
                    events.append(event)

                # Check limit
                if limit and len(events) >= limit:
                    break

            logger.info(f"[{self.provider_id}] Parsed {len(events)} {sport} events")

            return events

        except Exception as e:
            logger.error(f"[{self.provider_id}] Error extracting {sport}: {e}", exc_info=True)
            return []
