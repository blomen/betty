from typing import List, Any
import logging
import asyncio
import time
from dataclasses import dataclass
from typing import Dict, Optional

# Kambi Specific Logic adapted from APIExtractor
from ..core import Retriever, StandardEvent
from ..matching.normalizer import normalize_team_name, normalize_market, normalize_outcome
from .shared.metrics import ExtractionMetrics

logger = logging.getLogger(__name__)

# TTL for shared group cache (1 hour)
GROUP_CACHE_TTL_SECONDS = 3600


@dataclass
class CachedGroupData:
    """Cache entry with TTL for group data."""
    data: Any
    created_at: float

    def is_expired(self) -> bool:
        """Check if entry has expired (default 1 hour TTL)."""
        return time.time() - self.created_at > GROUP_CACHE_TTL_SECONDS


class KambiRetriever(Retriever):
    """
    Kambi Logic ported to the new Retriever Architecture.
    """

    # Shared class-level cache for group data across all Kambi providers
    # This avoids fetching the same group tree multiple times
    # Key format: "{base_url}/{brand}/group.json"
    # Now uses TTL-based caching to prevent stale data
    _SHARED_GROUP_CACHE: Dict[str, CachedGroupData] = {}

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

    async def extract(self, sport: str, limit: int = 50, target_leagues: set[str] | None = None, **kwargs) -> List[StandardEvent]:
        metrics = ExtractionMetrics()

        # 1. Get Groups (using shared cache with TTL)
        groups_url = f"{self.base_url}/{self.brand}/group.json"

        # Check shared cache first (with TTL expiration)
        cached_entry = self._SHARED_GROUP_CACHE.get(groups_url)
        if cached_entry and not cached_entry.is_expired():
            logger.debug(f"[{self.provider_id}] Using cached groups for {groups_url}")
            group_data = cached_entry.data
        else:
            if cached_entry and cached_entry.is_expired():
                logger.debug(f"[{self.provider_id}] Cache expired for {groups_url}, refetching")
            logger.info(f"[{self.provider_id}] Fetching groups from: {groups_url}")
            group_data = await self.transport.get(
                groups_url,
                params=self.default_params,
                provider_id=self.provider_id
            )
            if group_data:
                self._SHARED_GROUP_CACHE[groups_url] = CachedGroupData(
                    data=group_data,
                    created_at=time.time()
                )
                logger.debug(f"[{self.provider_id}] Cached groups for {groups_url} (TTL={GROUP_CACHE_TTL_SECONDS}s)")

        if not group_data:
            return []

        # 2. Find target sport group
        groups = []
        self._extract_groups_recursive(group_data, groups)

        target_groups = [g for g in groups if self._match_sport(g.get("sport", ""), sport)]

        # Filter by target leagues if provided (Pinnacle cheat sheet)
        # Falls back to unfiltered if league filter removes everything
        if target_leagues and target_groups:
            original = len(target_groups)
            filtered = [
                g for g in target_groups
                if self._match_league(g.get("name", ""), target_leagues)
            ]
            if filtered:
                target_groups = filtered
                skipped = original - len(target_groups)
                if skipped > 0:
                    logger.info(f"[{self.provider_id}] {sport}: filtered to {len(target_groups)}/{original} league groups (Pinnacle coverage)")
            else:
                logger.info(f"[{self.provider_id}] {sport}: league filter matched 0/{original} groups, using all")

        if not target_groups:
            logger.warning(f"[{self.provider_id}] No groups found for {sport}")
            return []

        if limit and len(target_groups) > limit:
            target_groups = target_groups[:limit]

        metrics.groups_fetched = len(target_groups)

        # 3. Fetch Events for each group in parallel (with concurrency limit)
        all_events = []

        # Use semaphore to limit concurrent requests (avoid overwhelming the API)
        # Reduced from 5 to 2 to prevent rate limiting on Kambi's shared backend
        sem = asyncio.Semaphore(2)

        async def fetch_with_limit(group):
            async with sem:
                return await self._fetch_group_events(group, sport, metrics)

        # Fetch all groups in parallel
        tasks = [fetch_with_limit(group) for group in target_groups]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect results (filter out errors)
        for result in results:
            if isinstance(result, list):
                all_events.extend(result)
            elif isinstance(result, Exception):
                logger.debug(f"[{self.provider_id}] Group fetch error: {result}")
                metrics.groups_failed += 1

        # Deduplicate events by ID (same event can appear in multiple groups)
        seen_ids = set()
        unique_events = []
        for event in all_events:
            if event.id not in seen_ids:
                seen_ids.add(event.id)
                unique_events.append(event)

        # Log extraction summary
        metrics.log_summary(self.provider_id, sport, len(all_events))

        return unique_events

    @classmethod
    def clear_group_cache(cls):
        """Clear the shared group cache. Useful for testing or forced refresh."""
        cls._SHARED_GROUP_CACHE.clear()
        logger.info("Kambi group cache cleared")

    @classmethod
    def get_cache_stats(cls) -> Dict:
        """Get statistics about the shared group cache."""
        now = time.time()
        stats = {
            "total_entries": len(cls._SHARED_GROUP_CACHE),
            "expired_entries": 0,
            "active_entries": 0,
            "oldest_age_seconds": 0,
        }

        for url, entry in cls._SHARED_GROUP_CACHE.items():
            age = now - entry.created_at
            if entry.is_expired():
                stats["expired_entries"] += 1
            else:
                stats["active_entries"] += 1
            if age > stats["oldest_age_seconds"]:
                stats["oldest_age_seconds"] = age

        return stats
        
    async def _fetch_group_events(self, group: dict, sport: str, metrics: ExtractionMetrics) -> List[StandardEvent]:
        endpoint = "betoffer/group/{group_id}.json"  # Default
        if "endpoints" in self.config and "events" in self.config["endpoints"]:
            endpoint = self.config["endpoints"]["events"]

        endpoint = endpoint.format(group_id=group["id"])
        url = f"{self.base_url}/{self.brand}/{endpoint}"

        data = await self.transport.get(url, params=self.default_params, provider_id=self.provider_id)
        if not data:
            return []

        # Check for pagination indicators
        self._check_pagination(data, group.get("name", "unknown"), metrics)

        # Use canonical sport name (from config) not Kambi's group sport name
        # e.g., "mma" instead of "martial_arts", "esports" instead of "valorant"
        return self.parse(data, sport, metrics)

    def _check_pagination(self, data: Any, group_name: str, metrics: ExtractionMetrics):
        """
        Check API response for pagination indicators that might indicate truncated results.

        Kambi API may paginate results. We check for common pagination patterns.
        """
        if not isinstance(data, dict):
            return

        # Check for pagination metadata in Kambi responses
        # Kambi typically uses: "pagination", "total", "limit", "offset"
        pagination = data.get("pagination", {})
        total = pagination.get("total") or data.get("total") or data.get("totalCount")
        limit = pagination.get("limit") or data.get("limit")
        offset = pagination.get("offset") or data.get("offset", 0)

        events = data.get("events", [])
        betoffers = data.get("betOffers", [])
        event_count = len(events)
        betoffer_count = len(betoffers)

        # Check if total indicates more data exists
        if total and event_count < total:
            metrics.pagination_warnings.append(
                f"Group '{group_name}': returned {event_count} of {total} total events (data truncated!)"
            )

        # Check if we hit the limit
        if limit and event_count >= limit:
            metrics.pagination_warnings.append(
                f"Group '{group_name}': returned {event_count} events matching limit={limit} - may be truncated"
            )

        # Check for common API caps (events hitting exact round numbers)
        common_limits = [50, 100, 200, 250, 500]
        if event_count in common_limits:
            logger.debug(
                f"[{self.provider_id}] Group '{group_name}': returned exactly {event_count} events "
                f"(common API limit - verify not truncated)"
            )

    def parse(self, data: Any, sport: str, metrics: ExtractionMetrics = None) -> List[StandardEvent]:
        # Logic from APIExtractor._kambi_parse_event
        if not data:
            return []

        # Create local metrics if not provided (for backwards compatibility)
        if metrics is None:
            metrics = ExtractionMetrics()

        events_raw = data.get("events", [])
        betoffers = data.get("betOffers", [])
        outcomes = data.get("outcomes", [])

        outcome_map = {o.get("id"): o for o in outcomes}

        events = []
        for event_raw in events_raw:
            if event_raw.get("state") == "STARTED":
                metrics.events_skipped_live += 1
                continue
            event = self._parse_single_event(event_raw, betoffers, outcome_map, sport, metrics)
            if event:
                events.append(event)
                metrics.events_parsed += 1

        # Log extraction summary at debug level (main summary logged by extract())
        total = len(events_raw)
        if total > 0:
            logger.debug(
                f"[{self.provider_id}] {sport}: parsed {len(events)}/{total} events, "
                f"skipped: {metrics.events_skipped_live} live"
            )

        return events

    def _parse_single_event(
        self,
        event_raw: dict,
        betoffers: list,
        outcome_map: dict,
        sport: str,
        metrics: ExtractionMetrics
    ) -> StandardEvent | None:
        try:
            event_id = str(event_raw.get("id", ""))
            home_team = event_raw.get("homeName", "")
            away_team = event_raw.get("awayName", "")

            if not home_team or not away_team:
                participants = event_raw.get("participants", [])
                for p in participants:
                    if p.get("home"):
                        home_team = p.get("name", "")
                    else:
                        away_team = p.get("name", "")

            if not home_team or not away_team:
                metrics.events_skipped_no_teams += 1
                return None

            # Normalize team names to lowercase for consistent matching
            home_team_normalized = normalize_team_name(home_team)
            away_team_normalized = normalize_team_name(away_team)

            name = event_raw.get("name", "") or f"{home_team} vs {away_team}"

            markets = []
            for betoffer in betoffers:
                if betoffer.get("eventId") != event_raw.get("id"):
                    continue
                market = self._parse_market(betoffer, outcome_map, home_team_normalized, away_team_normalized)
                if market:
                    markets.append(market)

            if not markets:
                metrics.events_skipped_no_markets += 1
                return None

            # Deduplicate winner markets: if both 1x2 and moneyline exist
            # (e.g. ice hockey "Match Odds - Regular Time" + "Moneyline - Including Overtime"),
            # prefer 1x2 (regulation time has draw info, avoids double counting).
            # Keep spread/total markets alongside winner markets.
            winner_markets = [m for m in markets if m["type"] in ("1x2", "moneyline")]
            other_markets = [m for m in markets if m["type"] not in ("1x2", "moneyline")]
            if len(winner_markets) > 1:
                markets_1x2 = [m for m in winner_markets if m["type"] == "1x2"]
                winner_markets = markets_1x2[:1] if markets_1x2 else winner_markets[:1]
            markets = winner_markets + other_markets

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
            metrics.events_skipped_error += 1
            return None

    def _parse_market(self, betoffer: dict, outcome_map: dict, home_team: str = "", away_team: str = "") -> dict | None:
        try:
            # Filter by betOfferType.id FIRST (most reliable)
            # 2 = Match (1x2/moneyline), 6 = Over/Under (total), 7 = Asian Handicap (spread)
            ALLOWED_BET_OFFER_TYPE_IDS = {2, 6, 7}
            bet_offer_type_id = betoffer.get("betOfferType", {}).get("id", 0)
            if bet_offer_type_id not in ALLOWED_BET_OFFER_TYPE_IDS:
                return None

            # Filter by criterion label - only full match markets
            criterion = betoffer.get("criterion", {})
            label = (criterion.get("englishLabel") or criterion.get("label") or "").lower()

            # Accept labels containing these keywords
            MATCH_KEYWORDS = (
                "full time", "fulltid", "heltid",       # Football regulation
                "match", "moneyline",                    # General
                "bout",                                  # Boxing, MMA
                "regular time",                          # Rugby, generic regulation
                "including overtime",                    # American football, ice hockey
                "including extra ends",                  # Curling
                "over/under", "handicap",                # Spread/total
            )
            if not any(kw in label for kw in MATCH_KEYWORDS):
                return None

            # Exclude partial markets and derivative bets
            EXCLUDE_PATTERNS = (
                "quarter", "period", "half",
                "1st", "2nd", "3rd", "4th",
                "draw no bet",
            )
            if any(pat in label for pat in EXCLUDE_PATTERNS):
                return None

            outcomes = []
            for outcome_ref in betoffer.get("outcomes", []):
                outcome = outcome_map.get(outcome_ref.get("id"), outcome_ref)
                odds = outcome.get("odds", 0) / 1000
                if odds <= 1: continue
                # Parse Line/Point (e.g. 224500 -> 224.5)
                point = outcome.get("line")
                if point is not None:
                    point = float(point) / 1000

                # Normalize outcome name
                raw_name = outcome.get("label", "")

                # For totals (betOfferType 6): map "Over X" / "Under X" to over/under
                if bet_offer_type_id == 6:
                    raw_lower = raw_name.lower().strip()
                    if raw_lower.startswith("over"):
                        normalized_name = "over"
                    elif raw_lower.startswith("under"):
                        normalized_name = "under"
                    else:
                        normalized_name = normalize_outcome(raw_name, home_team, away_team)
                else:
                    normalized_name = normalize_outcome(raw_name, home_team, away_team)

                outcomes.append({
                    "name": normalized_name,
                    "odds": round(odds, 3),
                    "point": point
                })
            if not outcomes: return None

            # Determine market type from betOfferType ID and outcome structure
            if bet_offer_type_id == 6:
                market_type = "total"
            elif bet_offer_type_id == 7:
                market_type = "spread"
            else:
                # betOfferType 2: determine from outcome structure
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

    def _match_league(self, group_name: str, target_leagues: set[str]) -> bool:
        """Check if Kambi group name matches any Pinnacle league."""
        name = group_name.lower().strip()
        # Direct match
        if name in target_leagues:
            return True
        # Substring match: "Premier League" matches "england - premier league"
        for league in target_leagues:
            if name in league or league in name:
                return True
        return False
