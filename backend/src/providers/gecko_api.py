"""
Gecko API Retriever - Direct API calls without browser.

Uses discovered API endpoints to fetch data directly.
Faster and more reliable than browser-based approach.
"""

from typing import List, Any, Optional, Dict
import logging
import aiohttp
import asyncio
from datetime import datetime
from ..core import Retriever, StandardEvent
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)


class GeckoAPIRetriever(Retriever):
    """
    Direct API retriever for Betsson Group sites.

    Strategy:
    1. Call /widgets/view API to get page configuration
    2. Extract market IDs from configuration
    3. Call /event-market API with market IDs to get events & odds
    """

    SPORT_SLUGS: Dict[str, str] = {
        "football": "fotboll",
        "basketball": "basket",
        "tennis": "tennis",
        "ice_hockey": "ishockey",
    }

    CATEGORY_IDS: Dict[str, str] = {
        "football": "1",
        "basketball": "2",
        "tennis": "3",
        "ice_hockey": "4",
    }

    def __init__(self, config: Dict[str, Any], transport=None):
        super().__init__(config, transport)

        raw_site_url = config.get("site_url", f"https://www.{config.get('domain', 'betsson.com')}")
        self.site_url: str = raw_site_url.rstrip("/")
        self.api_base = f"{self.site_url}/api/sb/v1"

    def _get_sport_url(self, sport: str) -> str:
        """Get the sportsbook page URL (for reference/referer)."""
        sport_slug = self.SPORT_SLUGS.get(sport, sport)
        return f"{self.site_url}/sv/odds/{sport_slug}?tab=liveAndUpcoming"

    async def extract(self, sport: str, limit: int = 50) -> List[StandardEvent]:
        """
        Extract events using direct API calls.

        Flow:
        1. Call widgets/view to get configuration
        2. Extract market IDs from widgets
        3. Call event-market with market IDs to get events
        """
        if sport not in self.CATEGORY_IDS:
            logger.warning(f"[{self.provider_id}] Sport '{sport}' not supported")
            return []

        try:
            async with aiohttp.ClientSession() as session:
                # Step 1: Get widget configuration
                widget_config = await self._get_widget_config(session, sport)
                if not widget_config:
                    logger.warning(f"[{self.provider_id}] Failed to get widget config")
                    return []

                # Step 2: Extract market IDs
                market_ids = self._extract_market_ids(widget_config)
                if not market_ids:
                    logger.warning(f"[{self.provider_id}] No market IDs found in config")
                    return []

                logger.info(f"[{self.provider_id}] Found {len(market_ids)} market IDs")

                # Step 3: Fetch event data
                events = await self._fetch_events(session, market_ids, sport)

                logger.info(f"[{self.provider_id}] Extracted {len(events)} events")
                return events[:limit]

        except Exception as e:
            logger.error(f"[{self.provider_id}] Error extracting {sport}: {e}", exc_info=True)
            return []

    async def _get_widget_config(self, session: aiohttp.ClientSession, sport: str) -> Optional[Dict]:
        """Fetch widget configuration from /widgets/view API."""
        category_id = self.CATEGORY_IDS.get(sport)
        sport_slug = self.SPORT_SLUGS.get(sport, sport)

        url = f"{self.api_base}/widgets/view/v1"
        params = {
            'categoryIds': category_id,
            'configurationKey': 'sportsbook.category',
            'excludedWidgetKeys': 'sportsbook.tournament.carousel',
            'slug': sport_slug,
            'timezoneOffsetMinutes': '60'
        }

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
            'Referer': self._get_sport_url(sport),
        }

        try:
            async with session.get(url, params=params, headers=headers, timeout=30) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.debug(f"[{self.provider_id}] Got widget config")
                    return data
                else:
                    logger.warning(f"[{self.provider_id}] Widget config API returned {resp.status}")
                    return None
        except Exception as e:
            logger.warning(f"[{self.provider_id}] Failed to fetch widget config: {e}")
            return None

    def _extract_market_ids(self, widget_config: Dict) -> List[str]:
        """
        Extract market IDs from widget configuration.

        The widget config contains nested structures with market references.
        We need to find and extract all market ID strings.
        """
        market_ids = set()

        def extract_recursive(obj):
            """Recursively search for market IDs in the config."""
            if isinstance(obj, dict):
                # Check for market ID patterns
                for key, value in obj.items():
                    if key == 'marketIds' and isinstance(value, list):
                        market_ids.update(value)
                    elif key == 'id' and isinstance(value, str) and value.startswith('m-'):
                        market_ids.add(value)
                    else:
                        extract_recursive(value)
            elif isinstance(obj, list):
                for item in obj:
                    extract_recursive(item)

        extract_recursive(widget_config)
        return list(market_ids)

    async def _fetch_events(self, session: aiohttp.ClientSession, market_ids: List[str], sport: str) -> List[StandardEvent]:
        """
        Fetch event data from /event-market API.

        The API accepts multiple market IDs and returns all related events, markets, and selections.
        """
        # Limit market IDs per request (API might have limits)
        batch_size = 50
        all_events = []

        for i in range(0, len(market_ids), batch_size):
            batch = market_ids[i:i+batch_size]

            url = f"{self.api_base}/widgets/event-market/v1"
            params = {
                'includescoreboards': 'true',
                'marketids': ','.join(batch)
            }

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json',
                'Referer': self._get_sport_url(sport),
            }

            try:
                async with session.get(url, params=params, headers=headers, timeout=30) as resp:
                    if resp.status == 200:
                        data = await resp.json()

                        # Check for errors
                        if 'errorId' in data or 'code' in data:
                            logger.warning(f"[{self.provider_id}] API returned error: {data}")
                            continue

                        # Parse events
                        events = self._parse_api_response(data, sport)
                        all_events.extend(events)
                        logger.debug(f"[{self.provider_id}] Batch {i//batch_size + 1}: {len(events)} events")
                    else:
                        logger.warning(f"[{self.provider_id}] Event-market API returned {resp.status}")

            except Exception as e:
                logger.warning(f"[{self.provider_id}] Failed to fetch events batch: {e}")

        return all_events

    def _parse_api_response(self, api_data: Dict, sport: str) -> List[StandardEvent]:
        """Parse event-market API response into StandardEvents."""
        events = []

        try:
            data = api_data.get('data', {})
            events_raw = data.get('events', [])
            markets_raw = data.get('markets', [])
            selections_raw = data.get('marketSelections', [])

            logger.debug(f"[{self.provider_id}] API response: {len(events_raw)} events, {len(markets_raw)} markets, {len(selections_raw)} selections")

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

        except Exception as e:
            logger.error(f"[{self.provider_id}] Error parsing API response: {e}")

        return events

    def _parse_event(self, event_raw: Dict, market_map: Dict, selections_by_market: Dict, sport: str) -> Optional[StandardEvent]:
        """Parse a single event from API response."""
        try:
            event_id = event_raw.get('id')
            if not event_id:
                return None

            # Extract team names
            participants = event_raw.get('participants', [])
            if len(participants) < 2:
                return None

            participants.sort(key=lambda p: p.get('side', 0))
            home_team_raw = participants[0].get('label', '')
            away_team_raw = participants[1].get('label', '')

            if not home_team_raw or not away_team_raw:
                return None

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

            league = event_raw.get('competitionName', 'Unknown')

            # Parse markets
            event_markets = [m for m_id, m in market_map.items() if m.get('eventId') == event_id]
            markets_list = []

            for market in event_markets:
                market_id = market.get('id')
                market_dict = self._parse_market(market, selections_by_market.get(market_id, []))
                if market_dict:
                    markets_list.append(market_dict)

            return StandardEvent(
                provider_id=self.provider_id,
                sport=sport,
                league=league,
                home_team=home_team,
                away_team=away_team,
                commence_time=start_time,
                start_time=start_time,
                event_id=event_id,
                name=f"{home_team_raw} vs {away_team_raw}",
                id=f"{self.provider_id}_{event_id}",
                markets=markets_list,
                url="",
                provider=self.provider_id
            )

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Error parsing event: {e}")
            return None

    def _parse_market(self, market: Dict, selections: List[Dict]) -> Optional[Dict]:
        """Parse market and selections."""
        try:
            market_type = market.get('marketFriendlyName', market.get('label', '')).lower()

            outcomes = []
            for sel in selections:
                label = sel.get('label', '').lower()
                odds_value = sel.get('odds')

                if not odds_value or odds_value <= 1.0:
                    continue

                outcomes.append({
                    "name": label,
                    "odds": round(float(odds_value), 3)
                })

            if not outcomes:
                return None

            market_dict = {"type": market_type, "outcomes": outcomes}

            line_value = market.get('lineValue')
            if line_value:
                try:
                    market_dict["line"] = float(line_value)
                except (ValueError, TypeError):
                    pass

            return market_dict

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Error parsing market: {e}")
            return None

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """Not used - we override extract()."""
        return []
