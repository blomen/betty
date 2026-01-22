from typing import List, Any
import logging
import asyncio
from typing import Dict, Optional

# Kambi Specific Logic adapted from APIExtractor
from ..core import Retriever, StandardEvent

logger = logging.getLogger(__name__)

class KambiRetriever(Retriever):
    """
    Kambi Logic ported to the new Retriever Architecture.
    """

    # Shared class-level cache for group data across all Kambi providers
    # This avoids fetching the same group tree multiple times
    # Key format: "{base_url}/{brand}/group.json"
    _SHARED_GROUP_CACHE = {}

    # We might need to fetch the groups first, then the events.
    # The Retriever interface assumes a single URL per sport usually,
    # but we can implement custom logic in `extract` or `_get_sport_url`.

    # Kambi requires a 2-step process:
    # 1. Fetch Group Tree -> Find Sport Group ID
    # 2. Fetch Events for that Group ID

    def __init__(self, config: dict, transport=None):
        super().__init__(config, transport)
        self.brand = config.get("brand") or config.get("id")
        self.base_url = config.get("api_base") or config.get("base_url")
        self.default_params = config.get("params", {})

    def _get_sport_url(self, sport: str) -> str:
        # This method in the base class returns a single URL.
        # kambi needs more complex logic. 
        # We will override `extract` instead or use this for the final call.
        return "" 

    async def extract(self, sport: str, limit: int = 50) -> List[StandardEvent]:
        # 1. Get Groups (using shared cache)
        groups_url = f"{self.base_url}/{self.brand}/group.json"

        # Check shared cache first
        if groups_url in self._SHARED_GROUP_CACHE:
            logger.debug(f"[{self.provider_id}] Using cached groups for {groups_url}")
            group_data = self._SHARED_GROUP_CACHE[groups_url]
        else:
            logger.info(f"[{self.provider_id}] Fetching groups from: {groups_url}")
            group_data = await self.transport.get(groups_url, params=self.default_params)
            if group_data:
                self._SHARED_GROUP_CACHE[groups_url] = group_data
                logger.debug(f"[{self.provider_id}] Cached groups for {groups_url}")

        if not group_data:
            return []
            
        # 2. Find target sport group
        groups = []
        self._extract_groups_recursive(group_data, groups)
        
        target_groups = [g for g in groups if self._match_sport(g.get("sport", ""), sport)]
        if not target_groups:
             logger.warning(f"[{self.provider_id}] No groups found for {sport}")
             return []
             
        if limit and len(target_groups) > limit:
            target_groups = target_groups[:limit]

        # 3. Fetch Events for each group in parallel (with concurrency limit)
        all_events = []

        # Use semaphore to limit concurrent requests (avoid overwhelming the API)
        sem = asyncio.Semaphore(5)

        async def fetch_with_limit(group):
            async with sem:
                return await self._fetch_group_events(group)

        # Fetch all groups in parallel
        tasks = [fetch_with_limit(group) for group in target_groups]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect results (filter out errors)
        for result in results:
            if isinstance(result, list):
                all_events.extend(result)
            elif isinstance(result, Exception):
                logger.debug(f"[{self.provider_id}] Group fetch error: {result}")

        return all_events
        
    async def _fetch_group_events(self, group: dict) -> List[StandardEvent]:
        endpoint = "betoffer/group/{group_id}.json" # Default
        if "endpoints" in self.config and "events" in self.config["endpoints"]:
            endpoint = self.config["endpoints"]["events"]
            
        endpoint = endpoint.format(group_id=group["id"])
        url = f"{self.base_url}/{self.brand}/{endpoint}"
        
        data = await self.transport.get(url, params=self.default_params)
        if not data: return []
        
        return self.parse(data, group.get("sport", "").lower())

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        # Logic from APIExtractor._kambi_parse_event
        if not data: return []
        
        events_raw = data.get("events", [])
        betoffers = data.get("betOffers", [])
        outcomes = data.get("outcomes", [])
        
        outcome_map = {o.get("id"): o for o in outcomes}
        
        events = []
        for event_raw in events_raw:
            if event_raw.get("state") == "STARTED": continue
            event = self._parse_single_event(event_raw, betoffers, outcome_map, sport)
            if event: events.append(event)
        return events

    def _parse_single_event(self, event_raw: dict, betoffers: list, outcome_map: dict, sport: str) -> StandardEvent | None:
        try:
            event_id = str(event_raw.get("id", ""))
            home_team = event_raw.get("homeName", "")
            away_team = event_raw.get("awayName", "")
            
            if not home_team or not away_team:
                participants = event_raw.get("participants", [])
                for p in participants:
                    if p.get("home"): home_team = p.get("name", "")
                    else: away_team = p.get("name", "")
            
            name = event_raw.get("name", "") or f"{home_team} vs {away_team}"
            if not home_team or not away_team: return None
            
            markets = []
            for betoffer in betoffers:
                if betoffer.get("eventId") != event_raw.get("id"): continue
                market = self._parse_market(betoffer, outcome_map)
                if market: markets.append(market)
            
            if not markets: return None
            
            path = event_raw.get("path", [])
            league = path[-1].get("name", "") if path else ""
            
            return StandardEvent(
                id=event_id,
                name=name,
                home_team=home_team,
                away_team=away_team,
                sport=sport,
                league=league,
                start_time=event_raw.get("start", ""),
                markets=markets,
                provider=self.provider_id,
            )
        except Exception:
            return None

    def _parse_market(self, betoffer: dict, outcome_map: dict) -> dict | None:
        try:
            market_type = betoffer.get("criterion", {}).get("label", "")
            outcomes = []
            for outcome_ref in betoffer.get("outcomes", []):
                outcome = outcome_map.get(outcome_ref.get("id"), outcome_ref)
                odds = outcome.get("odds", 0) / 1000
                if odds <= 1: continue
                # Parse Line/Point (e.g. 224500 -> 224.5)
                point = outcome.get("line")
                if point is not None:
                    point = float(point) / 1000

                outcomes.append({
                    "name": outcome.get("label", ""),
                    "odds": round(odds, 3),
                    "point": point
                })
            if not outcomes: return None
            return {"type": market_type, "outcomes": outcomes}
        except Exception:
            return None

    def _extract_groups_recursive(self, obj: Any, groups: list, depth: int = 0):
        # Copied helper
        if isinstance(obj, dict):
            if "id" in obj and "name" in obj:
                groups.append({
                    "id": obj["id"],
                    "name": obj.get("name", obj.get("englishName", "")),
                    "sport": obj.get("sport", ""),
                    "depth": depth,
                })
            for key in ["group", "groups", "children"]:
                if key in obj and isinstance(obj[key], (list, dict)):
                    self._extract_groups_recursive(obj[key], groups, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                self._extract_groups_recursive(item, groups, depth)

    def _match_sport(self, group_sport: str, target_sport: str) -> bool:
        # Copied helper
        group_sport = group_sport.lower()
        target_sport = target_sport.lower()
        if group_sport == target_sport: return True
        # Simplified aliases for PoC
        if target_sport == "football" and group_sport in ["soccer", "fotboll", "football"]: return True
        if target_sport == "ice_hockey" and group_sport in ["ice_hockey", "ishockey"]: return True
        if target_sport == "mma" and group_sport in ["martial_arts", "ufc/mma"]: return True
        if target_sport == "rugby" and group_sport in ["rugby_union", "rugby_league", "rugby"]: return True
        return False
