from typing import List, Any
import logging
import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

# Kambi Specific Logic adapted from APIExtractor
from ..core import Retriever, StandardEvent
from ..matching.normalizer import normalize_team_name, normalize_market, normalize_outcome
from .shared.metrics import ExtractionMetrics

logger = logging.getLogger(__name__)

# TTL for shared group cache (1 hour)
GROUP_CACHE_TTL_SECONDS = 3600

# TTL for shared event cache (5 minutes — events don't change often within a run)
EVENT_CACHE_TTL_SECONDS = 300


@dataclass
class CachedGroupData:
    """Cache entry with TTL for group data."""
    data: Any
    created_at: float

    def is_expired(self) -> bool:
        """Check if entry has expired (default 1 hour TTL)."""
        return time.time() - self.created_at > GROUP_CACHE_TTL_SECONDS


@dataclass
class CachedEventData:
    """Cache entry for parsed events per group, shared across Kambi brands."""
    events: List[StandardEvent]
    created_at: float

    def is_expired(self) -> bool:
        return time.time() - self.created_at > EVENT_CACHE_TTL_SECONDS


class KambiRetriever(Retriever):
    """
    Kambi Logic ported to the new Retriever Architecture.
    """

    # Shared class-level cache for group data across all Kambi providers
    # All brands share the same group tree, so keyed by base_url only
    # This means 1 fetch serves all 8 providers instead of 8 redundant fetches
    # Uses TTL-based caching to prevent stale data
    _SHARED_GROUP_CACHE: Dict[str, CachedGroupData] = {}

    # Shared class-level cache for PARSED events per group
    # All 8 Kambi brands return identical events (same API backend, different brand slug)
    # Key: (base_url, group_id, sport) → CachedEventData with list of StandardEvents
    # First brand fetches + parses, subsequent brands clone with their provider_id
    # Saves ~7 redundant API calls per group × ~50 groups = ~350 avoided HTTP requests
    _SHARED_EVENT_CACHE: Dict[tuple, CachedEventData] = {}

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
        # Cache key is base_url only — all Kambi brands share the same group tree
        groups_url = f"{self.base_url}/{self.brand}/group.json"
        cache_key = self.base_url

        # Check shared cache first (with TTL expiration)
        cached_entry = self._SHARED_GROUP_CACHE.get(cache_key)
        if cached_entry and not cached_entry.is_expired():
            logger.debug(f"[{self.provider_id}] Using cached groups (shared across brands)")
            group_data = cached_entry.data
        else:
            if cached_entry and cached_entry.is_expired():
                logger.debug(f"[{self.provider_id}] Cache expired, refetching")
            logger.debug(f"[{self.provider_id}] Fetching groups from: {groups_url}")
            group_data = await self.transport.get(
                groups_url,
                params=self.default_params,
                provider_id=self.provider_id
            )
            if group_data:
                self._SHARED_GROUP_CACHE[cache_key] = CachedGroupData(
                    data=group_data,
                    created_at=time.time()
                )
                logger.debug(f"[{self.provider_id}] Cached groups for all brands (TTL={GROUP_CACHE_TTL_SECONDS}s)")

        if not group_data:
            return []

        # 2. Find target sport groups
        # Kambi group tree: Sport Root (depth=2) → Region (depth=3) → League (depth=4)
        # Fetch ALL groups — deduplication below merges markets from overlapping groups
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
                    logger.debug(f"[{self.provider_id}] {sport}: filtered to {len(target_groups)}/{original} league groups (Pinnacle coverage)")
            else:
                logger.debug(f"[{self.provider_id}] {sport}: league filter matched 0/{original} groups, using all")

        if not target_groups:
            logger.warning(f"[{self.provider_id}] No groups found for {sport}")
            return []

        if limit and len(target_groups) > limit:
            target_groups = target_groups[:limit]

        metrics.groups_fetched = len(target_groups)

        # 3. Fetch Events for each group in parallel (with concurrency limit)
        all_events = []

        # Use semaphore to limit concurrent requests
        # Kambi backend handles 50+ concurrent — 5 is safe and faster than 2
        sem = asyncio.Semaphore(5)

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

        # Deduplicate events by ID — keep the version with the most markets
        # Same event appears in parent + child groups with different betoffer sets;
        # child groups typically have richer match-level betoffers
        event_map: Dict[str, StandardEvent] = {}
        for event in all_events:
            existing = event_map.get(event.id)
            if existing is None or len(event.markets) > len(existing.markets):
                event_map[event.id] = event
        unique_events = list(event_map.values())

        # Log extraction summary
        metrics.log_summary(self.provider_id, sport, len(all_events))

        return unique_events

    @classmethod
    def clear_group_cache(cls):
        """Clear the shared group and event caches. Useful for testing or forced refresh."""
        cls._SHARED_GROUP_CACHE.clear()
        cls._SHARED_EVENT_CACHE.clear()
        logger.info("Kambi group + event caches cleared")

    @classmethod
    def get_cache_stats(cls) -> Dict:
        """Get statistics about the shared caches."""
        now = time.time()
        stats = {
            "group_cache_entries": len(cls._SHARED_GROUP_CACHE),
            "event_cache_entries": len(cls._SHARED_EVENT_CACHE),
            "event_cache_events": sum(
                len(entry.events) for entry in cls._SHARED_EVENT_CACHE.values()
                if not entry.is_expired()
            ),
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
        # Check shared event cache first — all Kambi brands return identical events
        cache_key = (self.base_url, group["id"], sport)
        cached = self._SHARED_EVENT_CACHE.get(cache_key)
        if cached and not cached.is_expired():
            # Clone events with this brand's provider_id (cheap shallow copy)
            cloned = []
            for ev in cached.events:
                clone = StandardEvent(
                    id=ev.id, name=ev.name,
                    home_team=ev.home_team, away_team=ev.away_team,
                    sport=ev.sport, league=ev.league,
                    start_time=ev.start_time,
                    markets=ev.markets,  # markets are read-only, safe to share
                    provider=self.provider_id,
                )
                cloned.append(clone)
            logger.debug(
                f"[{self.provider_id}] Cache hit for group {group['id']} ({sport}): "
                f"{len(cloned)} events"
            )
            metrics.events_parsed += len(cloned)
            return cloned

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
        events = self.parse(data, sport, metrics)

        # Cache parsed events for other brands to reuse
        self._SHARED_EVENT_CACHE[cache_key] = CachedEventData(
            events=events, created_at=time.time()
        )

        return events

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

            # Deduplicate winner markets only (prefer 1x2 over moneyline).
            # Keep ALL spread/total lines — storage filters to Pinnacle's point.
            winner = None
            other = []
            for m in markets:
                mtype = m["type"]
                if mtype in ("1x2", "moneyline"):
                    if winner is None:
                        winner = m
                    elif mtype == "1x2" and winner["type"] == "moneyline":
                        winner = m  # Prefer 1x2 over moneyline
                    # else: skip duplicate moneyline / second 1x2
                else:
                    other.append(m)
            markets = ([winner] if winner else []) + other

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
            # 1 = Handicap/Spread (Puck Line, Point Spread), 2 = Match (1x2/moneyline)
            # 6 = Over/Under (total), 7 = Asian Handicap (spread)
            ALLOWED_BET_OFFER_TYPE_IDS = {1, 2, 6, 7}
            bet_offer_type_id = betoffer.get("betOfferType", {}).get("id", 0)
            if bet_offer_type_id not in ALLOWED_BET_OFFER_TYPE_IDS:
                return None

            # For spread/total: only keep main lines (skip alternates)
            # Kambi tags main lines with 'MAIN_LINE'; without this filter,
            # basketball events can have 30+ alternate spread/total lines each
            tags = betoffer.get("tags", [])
            if bet_offer_type_id in (1, 6, 7):
                if "MAIN_LINE" not in tags:
                    return None

            # Filter by criterion label
            criterion = betoffer.get("criterion", {})
            label = (criterion.get("englishLabel") or criterion.get("label") or "").lower()

            # Exclude partial markets, derivative bets, and futures (applies to ALL bet offer types)
            EXCLUDE_PATTERNS = (
                "quarter", "period", "half",
                "1st", "2nd", "3rd", "4th", "5th",
                "first",                                        # "First Inning", "First Half", etc.
                "inning",                                       # Baseball period markets ("Innings 1-5", "5 Innings")
                "map ",                                         # Esports period markets ("Map 1", "Map 2")
                "draw no bet",
                "competition", "season", "trophy", "award",  # Futures/outrights
                "0:00-", "5:00-", "10:00-",                   # Time-segment markets
                "conference winner", "division winner",        # Season futures
                "group winner",                                # Tournament futures
                "team total", "lags total",                    # Team-specific totals (not match total)
                "total goals by", "total points by",            # Kambi team totals (e.g. "Total Goals by Sweden")
                "total games by", "total sets by",              # Tennis team totals
                "total corners", "total cards", "total shots",  # Prop totals (not match total)
                "total fouls", "total offsides",                # Prop totals (not match total)
            )
            if any(pat in label for pat in EXCLUDE_PATTERNS):
                return None

            # Log criterion labels for spread/total markets that pass filters (diagnostic)
            if bet_offer_type_id in (1, 6, 7) and label:
                logger.debug(
                    f"[kambi] Accepted betOfferType={bet_offer_type_id} label='{label}'"
                )

            # For betOfferType 2 (match winner), apply keyword filter to ensure full-match only
            # betOfferType 1 (handicap), 6 (total), 7 (spread) pass after EXCLUDE_PATTERNS
            if bet_offer_type_id == 2:
                MATCH_KEYWORDS = (
                    "full time", "fulltid", "heltid",       # Football regulation
                    "match", "moneyline",                    # General
                    "bout",                                  # Boxing, MMA
                    "regular time",                          # Rugby, generic regulation
                    "including overtime",                    # American football, ice hockey
                    "including extra ends",                  # Curling
                    "inklusive",                             # Swedish "including overtime" variant
                )
                if not any(kw in label for kw in MATCH_KEYWORDS):
                    logger.debug(f"[{self.provider_id}] Dropped betOffer type={bet_offer_type_id} label='{label}'")
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
                    "point": point,
                    "provider_meta": {
                        "outcome_id": str(outcome.get("id", "")),
                    },
                })
            if not outcomes: return None

            # Determine market type from betOfferType ID and outcome structure
            if bet_offer_type_id == 6:
                # Kambi sometimes uses betOfferType 6 for esports match winner.
                # Real totals have over/under outcomes; if none found, it's moneyline.
                has_over_under = any(o["name"] in ("over", "under") for o in outcomes)
                if has_over_under:
                    market_type = "total"
                else:
                    has_draw = any(o["name"] == "draw" for o in outcomes)
                    market_type = "1x2" if has_draw else "moneyline"
            elif bet_offer_type_id in (1, 7):
                # 1 = Handicap (Puck Line, Point Spread), 7 = Asian Handicap
                market_type = "spread"

                # betOfferType 7 (Asian Handicap): Kambi returns per-outcome line
                # values with OPPOSITE signs (home@+X, away@-X) — inverted vs
                # Pinnacle convention (home@-X, away@+X). This creates entries at
                # the wrong market_key, causing the scanner to compare completely
                # different bets (e.g., "Rangers -1.5" vs "Phillies -1.5").
                # Fix: normalize to Asian convention (both outcomes at abs(point)).
                # The scanner's _fix_asian_spread_grouping handles matching by
                # odds proximity against Pinnacle.
                if bet_offer_type_id == 7:
                    for o in outcomes:
                        if o["point"] is not None:
                            o["point"] = abs(o["point"])
            else:
                # betOfferType 2: determine from outcome structure
                has_draw = any(o["name"] == "draw" for o in outcomes)
                market_type = "1x2" if has_draw else "moneyline"

            return {
                "type": market_type,
                "outcomes": outcomes,
                "provider_meta": {
                    "betoffer_id": str(betoffer.get("id", "")),
                    "event_id": str(betoffer.get("eventId", "")),
                },
            }
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
