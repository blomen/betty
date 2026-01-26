"""
Hajper Retriever - WebSocket-based extraction

Hajper (ComeOn Group) uses WebSocket/RSocket for event data.
Extracts from main sport page only - no league navigation needed.
Based on testing: Main page WebSocket messages include markets.
"""

from typing import Dict, Any, List, Optional
import json
import logging
from datetime import datetime

from ..core import BrowserRetriever, BrowserTransport, StandardEvent
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)


class HajperRetriever(BrowserRetriever):
    """Retriever for Hajper sportsbook using WebSocket interception."""

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

    def _decode_rsocket_frame(self, frame_bytes: bytes) -> Optional[List[Dict]]:
        """Decode RSocket binary frame to extract JSON payload."""
        try:
            frame_str = frame_bytes.decode('utf-8', errors='ignore')

            # Find JSON start
            if '[{' in frame_str:
                json_start = frame_str.index('[{')
                json_str = frame_str[json_start:]
                return json.loads(json_str)

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Failed to decode frame: {e}")

        return None

    def _setup_ws_interception(self, page) -> list:
        """Setup WebSocket interception and return message storage list."""
        messages = []

        def on_websocket(ws):
            def on_frame_received(payload):
                if isinstance(payload, bytes):
                    decoded = self._decode_rsocket_frame(payload)
                    if decoded:
                        messages.append(decoded)

            ws.on("framereceived", on_frame_received)

        page.on("websocket", on_websocket)
        return messages

    def _normalize_market_type(self, market_name: str) -> str:
        """Normalize Hajper market names to standard types."""
        name_lower = market_name.lower()

        # 1x2 / Match result
        if any(kw in name_lower for kw in ['1x2', 'helmatchen', 'match result', 'slutresultat', 'matchresultat']):
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
        """Normalize outcome names."""
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

        # Over/Under
        if market_type == 'over_under':
            if 'over' in type_lower or 'över' in name_lower:
                return 'over'
            if 'under' in type_lower or 'under' in name_lower:
                return 'under'

        return outcome_name

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
                    # Infer market type from marketTypeId
                    # 1 = 1x2, 8 = first goal, etc.
                    market_type_map = {
                        '1': '1x2',
                        '8': 'other',  # first goal
                        '103': 'over_under',
                        '1781': 'spread',
                    }
                    market_type = market_type_map.get(market_type_id, 'other')

                    markets_dict[market_id] = {
                        'type': market_type,
                        'outcomes': []
                    }

                outcome_name = selection.get('name', '')
                outcome_type = selection.get('outcomeType', '')
                odds = selection.get('trueOdds', 0.0)

                market_type = markets_dict[market_id]['type']
                normalized_outcome = self._normalize_outcome(outcome_name, outcome_type, market_type)

                markets_dict[market_id]['outcomes'].append({
                    "name": normalized_outcome,
                    "odds": odds
                })

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
        """Not used - we override extract() completely."""
        return []

    async def extract(self, sport: str, limit: int = 50) -> List[StandardEvent]:
        """Extract events by intercepting WebSocket messages from main sport page."""
        sport_url_path = self.SPORT_URL_MAP.get(sport)
        if not sport_url_path:
            logger.warning(f"[{self.provider_id}] Sport '{sport}' not supported")
            return []

        try:
            if not isinstance(self.transport, BrowserTransport):
                logger.error(f"[{self.provider_id}] Hajper requires BrowserTransport")
                return []

            # Ensure browser is ready
            await self.transport._ensure_browser()
            page = self.transport.page

            # Setup WebSocket interception
            ws_messages = self._setup_ws_interception(page)

            # Load main sport page
            sport_url = f"{self.site_url}{sport_url_path}"
            logger.info(f"[{self.provider_id}] Loading {sport_url}")

            await page.goto(sport_url, wait_until="networkidle", timeout=60000)

            # Handle cookie consent
            try:
                await page.click('button:has-text("Accept")', timeout=2000)
                logger.info(f"[{self.provider_id}] Clicked cookie consent")
                await page.wait_for_timeout(1000)
            except:
                pass

            # Wait for WebSocket messages
            logger.info(f"[{self.provider_id}] Waiting for WebSocket messages...")
            await page.wait_for_timeout(5000)

            # Scroll down multiple times to trigger lazy loading of all markets
            for i in range(3):
                await page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {(i+1)/4})")
                await page.wait_for_timeout(2000)
                logger.info(f"[{self.provider_id}] Scroll {i+1}/3 - received {len(ws_messages)} messages so far")

            # Parse WebSocket messages
            logger.info(f"[{self.provider_id}] Received {len(ws_messages)} WebSocket messages")

            events = []
            event_ids_seen = set()

            for msg_data in ws_messages:
                if isinstance(msg_data, list):
                    for msg in msg_data:
                        # Look for INITIAL_STATE messages
                        if msg.get('type') == 'INITIAL_STATE':
                            payload = msg.get('payload', {})
                            events_list = payload.get('events', [])
                            selections_list = payload.get('selections', [])

                            logger.info(f"[{self.provider_id}] Found INITIAL_STATE with {len(events_list)} events, {len(selections_list)} selections")

                            for event_data in events_list:
                                event_id = str(event_data.get('id', ''))
                                if event_id and event_id not in event_ids_seen:
                                    event_ids_seen.add(event_id)
                                    event = self._parse_event(event_data, sport, selections_list)
                                    if event:
                                        events.append(event)

            logger.info(f"[{self.provider_id}] Extracted {len(events)} unique events")
            return events[:limit]

        except Exception as e:
            logger.error(f"[{self.provider_id}] Error extracting {sport}: {e}", exc_info=True)
            return []
