from typing import List, Dict, Optional, Any
import asyncio
import logging
from datetime import datetime

from ..core import Retriever, StandardEvent
from ..matching.normalizer import normalize_team_name
from .shared.metrics import ExtractionMetrics

logger = logging.getLogger(__name__)

# Max concurrent league fetches (Pinnacle API handles high concurrency well)
MAX_CONCURRENT_LEAGUES = 50


class PinnacleRetriever(Retriever):
    """
    Pinnacle Guest API Retriever
    Uses guest.api.arcadia.pinnacle.com which requires NO authentication
    """

    def __init__(self, config: dict, transport=None):
        super().__init__(config, transport)
        self.base_url = config.get("api_base", "https://guest.api.arcadia.pinnacle.com/0.1")

        # Build sport ID map from config
        from ..config import ConfigLoader
        config_loader = ConfigLoader.get_instance()
        self._sport_map = {
            s.key: s.pinnacle_sport_id
            for s in config_loader.sports
            if s.pinnacle_sport_id
        }

    def _get_sport_url(self, sport: str) -> str:
        """Not used - we implement custom extract logic"""
        return ""

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """Not used - we override extract() completely"""
        return []

    async def extract(self, sport: str, limit: int = None) -> List[StandardEvent]:
        """
        Extract events and odds for a sport using parallel fetching.

        Flow:
        1. Get sport ID
        2. Get active leagues for that sport
        3. Parallel fetch matchups + markets for all leagues
        4. Parse and deduplicate into StandardEvents

        Args:
            sport: Sport key (e.g., 'football', 'basketball')
            limit: Max events to return (None for all)
        """
        metrics = ExtractionMetrics()

        # Get Pinnacle sport ID
        sport_id = self._sport_map.get(sport)
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

        # Check for pagination indicators in leagues response
        self._check_pagination(leagues_data, "leagues", metrics)

        # Filter leagues with active matchups
        active_leagues = [l for l in leagues_data if l.get("matchupCount", 0) > 0]

        if not active_leagues:
            logger.info(f"[{self.provider_id}] No active leagues for {sport}")
            return []

        logger.info(f"[{self.provider_id}] Found {len(active_leagues)} active leagues")
        metrics.leagues_fetched = len(active_leagues)

        # Parallel fetch all leagues with semaphore to limit concurrency
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_LEAGUES)
        league_results = await asyncio.gather(
            *[self._fetch_league(league, semaphore, metrics) for league in active_leagues],
            return_exceptions=True
        )

        # Parse all results and deduplicate
        seen_ids: set = set()
        all_events: List[StandardEvent] = []

        for result in league_results:
            if isinstance(result, Exception):
                logger.debug(f"[{self.provider_id}] League fetch failed: {result}")
                metrics.leagues_failed += 1
                continue
            if not result:
                metrics.leagues_failed += 1
                continue

            league_name, matchups, markets = result

            # Build matchup -> markets mapping
            markets_by_matchup: Dict[int, List[dict]] = {}
            for market in markets:
                matchup_id = market.get("matchupId")
                if matchup_id:
                    markets_by_matchup.setdefault(matchup_id, []).append(market)

            # Convert to StandardEvent
            for matchup in matchups:
                event = self._parse_matchup(matchup, sport, league_name, markets_by_matchup, metrics)
                if event and event.id not in seen_ids:
                    seen_ids.add(event.id)
                    all_events.append(event)
                    metrics.events_parsed += 1

        # Apply limit if specified
        if limit and len(all_events) > limit:
            all_events = all_events[:limit]

        logger.info(f"[{self.provider_id}] Extracted {len(all_events)} events for {sport}")

        # Log extraction summary with any warnings
        metrics.log_summary(self.provider_id, sport)

        return all_events

    def _check_pagination(self, data: Any, endpoint: str, metrics: ExtractionMetrics):
        """
        Check API response for pagination indicators that might indicate truncated results.

        Pinnacle API may paginate results. We check for common pagination patterns:
        - totalCount / total field
        - hasMore / has_more field
        - nextPage / next field
        - limit/offset in response metadata
        """
        if isinstance(data, dict):
            # Check for pagination metadata
            total_count = data.get("totalCount") or data.get("total") or data.get("totalElements")
            has_more = data.get("hasMore") or data.get("has_more") or data.get("more")
            next_page = data.get("nextPage") or data.get("next") or data.get("cursor")
            limit = data.get("limit") or data.get("pageSize")

            # Get actual result count
            results = data.get("data") or data.get("items") or data.get("results")
            actual_count = len(results) if isinstance(results, list) else None

            if total_count and actual_count and actual_count < total_count:
                metrics.pagination_warnings.append(
                    f"{endpoint}: returned {actual_count} of {total_count} total (data truncated!)"
                )

            if has_more:
                metrics.pagination_warnings.append(
                    f"{endpoint}: hasMore=true indicates additional pages exist"
                )

            if next_page:
                metrics.pagination_warnings.append(
                    f"{endpoint}: nextPage cursor present - pagination not fully traversed"
                )

            if limit and actual_count and actual_count >= limit:
                metrics.pagination_warnings.append(
                    f"{endpoint}: returned {actual_count} results matching limit={limit} - may be truncated"
                )

        elif isinstance(data, list):
            # For list responses, check if count hits common API limits
            count = len(data)
            common_limits = [50, 100, 200, 250, 500, 1000]

            if count in common_limits:
                # Log as info, not warning - could be coincidence
                logger.debug(
                    f"[{self.provider_id}] {endpoint}: returned exactly {count} results "
                    f"(common API limit - verify not truncated)"
                )

    async def _fetch_league(
        self,
        league: dict,
        semaphore: asyncio.Semaphore,
        metrics: ExtractionMetrics
    ) -> Optional[tuple[str, List[dict], List[dict]]]:
        """
        Fetch matchups and markets for a single league.

        Returns:
            Tuple of (league_name, matchups, markets) or None on error
        """
        league_id = league["id"]
        league_name = league.get("name", "Unknown")
        expected_matchups = league.get("matchupCount", 0)

        async with semaphore:
            try:
                # Fetch matchups and markets in parallel
                matchups_url = f"{self.base_url}/leagues/{league_id}/matchups"
                markets_url = f"{self.base_url}/leagues/{league_id}/markets/straight"

                matchups_task = self.transport.get(matchups_url)
                markets_task = self.transport.get(markets_url)

                matchups, markets = await asyncio.gather(matchups_task, markets_task)

                matchups = matchups if matchups else []
                markets = markets if markets else []

                # Check for pagination in matchups/markets
                self._check_pagination(matchups, f"matchups/{league_name}", metrics)
                self._check_pagination(markets, f"markets/{league_name}", metrics)

                # Validate expected vs actual matchup count
                if expected_matchups > 0 and len(matchups) < expected_matchups * 0.5:
                    # Significant discrepancy - may indicate pagination or filtering issue
                    metrics.pagination_warnings.append(
                        f"League '{league_name}': expected ~{expected_matchups} matchups, got {len(matchups)}"
                    )

                return (
                    league_name,
                    matchups,
                    markets
                )

            except Exception as e:
                logger.debug(f"[{self.provider_id}] Error fetching league {league_name}: {e}")
                return None

    def _parse_matchup(
        self,
        matchup: dict,
        sport: str,
        league_name: str,
        markets_by_matchup: Dict[int, List[dict]],
        metrics: ExtractionMetrics
    ) -> Optional[StandardEvent]:
        """Parse a Pinnacle matchup + markets into StandardEvent"""
        try:
            # Check if this is a special/derivative matchup with parent
            # Parent contains the actual home/away participants
            parent = matchup.get("parent")
            if parent and isinstance(parent, dict) and "participants" in parent:
                matchup_id = parent.get("id")  # Use parent ID for market matching
                participants = parent.get("participants", [])
                start_time_str = parent.get("startTime")
            else:
                matchup_id = matchup.get("id")
                participants = matchup.get("participants", [])
                start_time_str = matchup.get("startTime")

            if len(participants) < 2:
                metrics.events_skipped_no_participants += 1
                return None

            # Extract teams
            home_participant = next((p for p in participants if p.get("alignment") == "home"), None)
            away_participant = next((p for p in participants if p.get("alignment") == "away"), None)

            if not home_participant or not away_participant:
                metrics.events_skipped_no_teams += 1
                return None

            home_team_raw = home_participant.get("name", "")
            away_team_raw = away_participant.get("name", "")

            # Normalize team names to lowercase for consistent matching
            home_team = normalize_team_name(home_team_raw)
            away_team = normalize_team_name(away_team_raw)

            # Parse start time (already extracted above)
            start_time = None
            if start_time_str:
                try:
                    start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                except Exception:
                    pass

            # Get raw markets for this matchup
            raw_markets = markets_by_matchup.get(matchup_id, [])

            # Parse all market types
            parsed_markets = self._parse_markets(raw_markets)

            if not parsed_markets:
                metrics.events_skipped_no_markets += 1
                return None

            # Build StandardEvent
            event = StandardEvent(
                id=f"{self.provider_id}_{matchup_id}",
                name=f"{home_team_raw} vs {away_team_raw}",
                provider=self.provider_id,
                sport=sport,
                league=league_name,
                home_team=home_team,
                away_team=away_team,
                start_time=start_time.isoformat() if start_time else "",
                markets=parsed_markets,
                url=""
            )

            return event

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Error parsing matchup: {e}")
            metrics.events_skipped_error += 1
            return None

    def _parse_markets(self, raw_markets: List[dict]) -> List[dict]:
        """
        Parse moneyline/1x2 markets from Pinnacle API response.

        Only extracts winner markets (moneyline). Other markets (spread, total)
        are skipped as per project scope - only 1x2/moneyline markets stored.
        """
        parsed = []

        for market in raw_markets:
            # Only process full game markets (period 0)
            if market.get("period") != 0:
                continue

            # Only process open markets
            if market.get("status") != "open":
                continue

            # Only parse moneyline (1x2/moneyline) - skip spread/total
            if market.get("type") != "moneyline":
                continue

            prices = market.get("prices", [])
            if prices:
                parsed.extend(self._parse_moneyline(prices))

        return parsed

    def _parse_moneyline(self, prices: List[dict]) -> List[dict]:
        """Parse moneyline (winner) market."""
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

        if not outcomes:
            return []

        # Determine market type (moneyline vs 1x2)
        has_draw = any(o["name"] == "draw" for o in outcomes)
        market_type = "1x2" if has_draw else "moneyline"

        return [{
            "type": market_type,
            "outcomes": outcomes
        }]

    def _american_to_decimal(self, american_odds: int) -> float:
        """Convert American odds to decimal format"""
        if american_odds > 0:
            return (american_odds / 100) + 1
        else:
            return (-100 / american_odds) + 1
