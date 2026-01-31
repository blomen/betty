from typing import List, Any
import logging
import asyncio
from typing import Dict, Optional

# Kambi Specific Logic adapted from APIExtractor
from ..core import Retriever, StandardEvent
from ..matching.normalizer import normalize_team_name, normalize_market, normalize_outcome

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

    def __init__(self, config: dict, transport=None, circuit_breaker=None, rate_limit_config=None):
        # Create transport with circuit breaker and rate limit config if not provided
        if transport is None:
            from ..core import HttpTransport
            transport = HttpTransport(
                circuit_breaker=circuit_breaker,
                rate_limit_config=rate_limit_config
            )
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
            group_data = await self.transport.get(
                groups_url,
                params=self.default_params,
                provider_id=self.provider_id
            )
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
        # Reduced from 5 to 2 to prevent rate limiting on Kambi's shared backend
        sem = asyncio.Semaphore(2)

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

        # Deduplicate events by ID (same event can appear in multiple groups)
        seen_ids = set()
        unique_events = []
        for event in all_events:
            if event.id not in seen_ids:
                seen_ids.add(event.id)
                unique_events.append(event)

        return unique_events
        
    async def _fetch_group_events(self, group: dict) -> List[StandardEvent]:
        endpoint = "betoffer/group/{group_id}.json" # Default
        if "endpoints" in self.config and "events" in self.config["endpoints"]:
            endpoint = self.config["endpoints"]["events"]
            
        endpoint = endpoint.format(group_id=group["id"])
        url = f"{self.base_url}/{self.brand}/{endpoint}"

        data = await self.transport.get(url, params=self.default_params, provider_id=self.provider_id)
        if not data: return []
        
        return self.parse(data, group.get("sport", "").lower())

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        # Logic from APIExtractor._kambi_parse_event
        if not data: return []

        events_raw = data.get("events", [])
        betoffers = data.get("betOffers", [])
        outcomes = data.get("outcomes", [])

        outcome_map = {o.get("id"): o for o in outcomes}

        # Extraction metrics
        parsed_count = 0
        skipped_live = 0
        skipped_no_teams = 0
        skipped_no_markets = 0
        skipped_error = 0

        events = []
        for event_raw in events_raw:
            if event_raw.get("state") == "STARTED":
                skipped_live += 1
                continue
            event = self._parse_single_event(event_raw, betoffers, outcome_map, sport)
            if event:
                events.append(event)
                parsed_count += 1
            elif event is None:
                # Track why events were skipped (logged in _parse_single_event)
                pass

        # Log extraction summary
        total = len(events_raw)
        if total > 0:
            logger.debug(
                f"[{self.provider_id}] {sport}: parsed {parsed_count}/{total} events, "
                f"skipped: {skipped_live} live"
            )

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

            if not home_team or not away_team: return None

            # Normalize team names to lowercase for consistent matching
            home_team_normalized = normalize_team_name(home_team)
            away_team_normalized = normalize_team_name(away_team)

            name = event_raw.get("name", "") or f"{home_team} vs {away_team}"

            markets = []
            for betoffer in betoffers:
                if betoffer.get("eventId") != event_raw.get("id"): continue
                market = self._parse_market(betoffer, outcome_map, home_team_normalized, away_team_normalized)
                if market: markets.append(market)

            if not markets: return None

            path = event_raw.get("path", [])
            league = path[-1].get("name", "") if path else ""

            return StandardEvent(
                id=event_id,
                name=name,
                home_team=home_team_normalized,
                away_team=away_team_normalized,
                sport=sport,
                league=league,
                start_time=event_raw.get("start", ""),
                markets=markets,
                provider=self.provider_id,
            )
        except Exception as e:
            logger.debug(f"[{self.provider_id}] Failed to parse event {event_raw.get('id', 'unknown')}: {e}")
            return None

    def _parse_market(self, betoffer: dict, outcome_map: dict, home_team: str = "", away_team: str = "") -> dict | None:
        try:
            # Filter by betOfferType.id FIRST (most reliable)
            # 2 = Match (1x2/moneyline)
            # Other IDs: 3=Correct Score, 6=Over/Under, 7=Asian Handicap, 127=Player Props
            ALLOWED_BET_OFFER_TYPE_IDS = {2}
            bet_offer_type_id = betoffer.get("betOfferType", {}).get("id", 0)
            if bet_offer_type_id not in ALLOWED_BET_OFFER_TYPE_IDS:
                return None  # Skip non-1x2 markets early

            # Filter by criterion label - only full match result
            # Accept: Full Time (football), Moneyline (basketball/hockey), Match Odds (tennis)
            # Skips: 1st Half, 2nd Half, Draw No Bet, Most Corners, etc.
            criterion = betoffer.get("criterion", {})
            label = (criterion.get("englishLabel") or criterion.get("label") or "").lower()

            # Accept labels containing these keywords for match winner bets
            MATCH_KEYWORDS = ("full time", "fulltid", "heltid", "match", "moneyline")
            if not any(kw in label for kw in MATCH_KEYWORDS):
                return None

            # Exclude partial markets (quarters, halves, periods) - they contain keywords but aren't full match
            EXCLUDE_PATTERNS = ("quarter", "period", "half", "1st", "2nd", "3rd", "4th")
            if any(pat in label for pat in EXCLUDE_PATTERNS):
                return None

            raw_market_type = criterion.get("label", "")
            # Normalize market type to standard format (1x2, over_under, spread, etc.)
            market_type = normalize_market(raw_market_type)

            outcomes = []
            for outcome_ref in betoffer.get("outcomes", []):
                outcome = outcome_map.get(outcome_ref.get("id"), outcome_ref)
                odds = outcome.get("odds", 0) / 1000
                if odds <= 1: continue
                # Parse Line/Point (e.g. 224500 -> 224.5)
                point = outcome.get("line")
                if point is not None:
                    point = float(point) / 1000

                # Normalize outcome name (maps team names to home/away, Swedish ja/nej, etc.)
                raw_name = outcome.get("label", "")
                normalized_name = normalize_outcome(raw_name, home_team, away_team)

                outcomes.append({
                    "name": normalized_name,
                    "odds": round(odds, 3),
                    "point": point
                })
            if not outcomes: return None

            # Determine market type from outcome structure:
            # - 3 outcomes with draw = 1x2 (football)
            # - 2 outcomes (home/away only) = moneyline (basketball, hockey, tennis)
            has_draw = any(o["name"] == "draw" for o in outcomes)
            market_type = "1x2" if has_draw else "moneyline"

            return {"type": market_type, "outcomes": outcomes}
        except Exception as e:
            logger.debug(f"[{self.provider_id}] Failed to parse market: {e}")
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
        """Match sport name against target, using config-driven aliases."""
        group_sport = group_sport.lower()
        target_sport = target_sport.lower()

        if group_sport == target_sport:
            return True

        # Load aliases from config
        from ..config import ConfigLoader
        config_loader = ConfigLoader.get_instance()
        aliases = config_loader.get_sport_aliases(target_sport)

        return group_sport in aliases
