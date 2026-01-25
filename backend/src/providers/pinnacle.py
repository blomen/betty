from typing import List, Dict, Optional, Any
import logging
from datetime import datetime

from ..core import Retriever, StandardEvent

logger = logging.getLogger(__name__)


class PinnacleRetriever(Retriever):
    """
    Pinnacle Guest API Retriever
    Uses guest.api.arcadia.pinnacle.com which requires NO authentication
    """

    # Sport ID mapping (Pinnacle -> OddOpp canonical)
    SPORT_MAP = {
        "football": 29,      # Soccer
        "basketball": 4,
        "american_football": 15,
        "ice_hockey": 19,
        "tennis": 33,
        "baseball": 3,
        "mma": 22,
        "esports": 12,
    }

    def __init__(self, config: dict, transport=None):
        super().__init__(config, transport)
        self.base_url = config.get("api_base", "https://guest.api.arcadia.pinnacle.com/0.1")

    def _get_sport_url(self, sport: str) -> str:
        """Not used - we implement custom extract logic"""
        return ""

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """Not used - we override extract() completely"""
        return []

    async def extract(self, sport: str, limit: int = 50) -> List[StandardEvent]:
        """
        Extract events and odds for a sport

        Flow:
        1. Get sport ID
        2. Get active leagues for that sport
        3. Get matchups for each league
        4. Get odds for each league
        5. Combine matchups + odds into StandardEvent
        """
        # Get Pinnacle sport ID
        sport_id = self.SPORT_MAP.get(sport)
        if not sport_id:
            logger.warning(f"[{self.provider_id}] Sport '{sport}' not mapped for Pinnacle")
            return []

        logger.info(f"[{self.provider_id}] Fetching {sport} (sport_id={sport_id})")

        # Get active leagues
        leagues_url = f"{self.base_url}/sports/{sport_id}/leagues"
        leagues_data = await self.transport.get(leagues_url, params={"all": "false"})

        if not leagues_data:
            logger.warning(f"[{self.provider_id}] No leagues data for sport {sport_id}")
            return []

        # Filter leagues with active matchups
        active_leagues = [l for l in leagues_data if l.get("matchupCount", 0) > 0]

        if not active_leagues:
            logger.info(f"[{self.provider_id}] No active leagues for {sport}")
            return []

        logger.info(f"[{self.provider_id}] Found {len(active_leagues)} active leagues")

        # Limit leagues if needed
        if limit and len(active_leagues) > limit:
            active_leagues = active_leagues[:limit]

        # Fetch matchups and odds for all leagues
        all_events = []

        for league in active_leagues:
            league_id = league["id"]
            league_name = league.get("name", "Unknown")

            try:
                # Fetch matchups (events)
                matchups_url = f"{self.base_url}/leagues/{league_id}/matchups"
                matchups = await self.transport.get(matchups_url)

                if not matchups:
                    continue

                # Fetch odds (markets)
                markets_url = f"{self.base_url}/leagues/{league_id}/markets/straight"
                markets = await self.transport.get(markets_url)

                if not markets:
                    markets = []

                # Build matchup -> markets mapping
                markets_by_matchup = {}
                for market in markets:
                    matchup_id = market.get("matchupId")
                    if matchup_id:
                        if matchup_id not in markets_by_matchup:
                            markets_by_matchup[matchup_id] = []
                        markets_by_matchup[matchup_id].append(market)

                # Convert to StandardEvent
                for matchup in matchups:
                    event = self._parse_matchup(matchup, sport, league_name, markets_by_matchup)
                    if event:
                        all_events.append(event)

            except Exception as e:
                logger.error(f"[{self.provider_id}] Error fetching league {league_name}: {e}")
                continue

        logger.info(f"[{self.provider_id}] Extracted {len(all_events)} events for {sport}")
        return all_events

    def _parse_matchup(
        self,
        matchup: dict,
        sport: str,
        league_name: str,
        markets_by_matchup: Dict[int, List[dict]]
    ) -> Optional[StandardEvent]:
        """Parse a Pinnacle matchup + markets into StandardEvent"""
        try:
            # Check if this is a special/derivative matchup with parent
            # Parent contains the actual home/away participants
            if "parent" in matchup and "participants" in matchup["parent"]:
                parent = matchup["parent"]
                matchup_id = parent.get("id")  # Use parent ID for market matching
                participants = parent.get("participants", [])
                start_time_str = parent.get("startTime")
            else:
                matchup_id = matchup.get("id")
                participants = matchup.get("participants", [])
                start_time_str = matchup.get("startTime")

            if len(participants) < 2:
                return None

            # Extract teams
            home_participant = next((p for p in participants if p.get("alignment") == "home"), None)
            away_participant = next((p for p in participants if p.get("alignment") == "away"), None)

            if not home_participant or not away_participant:
                return None

            home_team = home_participant.get("name", "")
            away_team = away_participant.get("name", "")

            # Parse start time (already extracted above)
            start_time = None
            if start_time_str:
                try:
                    start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                except Exception:
                    pass

            # Get markets for this matchup
            markets = markets_by_matchup.get(matchup_id, [])

            # Find moneyline market (period 0 = full game)
            moneyline = next(
                (m for m in markets if m.get("type") == "moneyline" and m.get("period") == 0),
                None
            )

            # Parse odds into markets format
            markets = []
            if moneyline and moneyline.get("status") == "open":
                prices = moneyline.get("prices", [])
                outcomes = []
                for price_obj in prices:
                    designation = price_obj.get("designation")
                    american_odds = price_obj.get("price")

                    if designation and american_odds is not None:
                        decimal_odds = self._american_to_decimal(american_odds)
                        outcomes.append({
                            "name": designation,
                            "odds": decimal_odds
                        })

                if outcomes:
                    # Determine market type (moneyline vs 1x2)
                    has_draw = any(o["name"] == "draw" for o in outcomes)
                    market_type = "1x2" if has_draw else "moneyline"

                    markets.append({
                        "type": market_type,
                        "outcomes": outcomes
                    })

            # Build StandardEvent
            event = StandardEvent(
                id=f"{self.provider_id}_{matchup_id}",
                name=f"{home_team} vs {away_team}",
                provider=self.provider_id,
                sport=sport,
                league=league_name,
                home_team=home_team,
                away_team=away_team,
                start_time=start_time.isoformat() if start_time else "",
                markets=markets,
                url=""
            )

            return event

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Error parsing matchup: {e}")
            return None

    def _american_to_decimal(self, american_odds: int) -> float:
        """Convert American odds to decimal format"""
        if american_odds > 0:
            return (american_odds / 100) + 1
        else:
            return (-100 / american_odds) + 1
