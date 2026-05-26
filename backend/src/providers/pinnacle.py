import asyncio
import logging
from datetime import datetime
from typing import Any

from ..config import ConfigLoader
from ..core import Retriever, StandardEvent
from ..matching.normalizer import normalize_team_name
from .shared.metrics import ExtractionMetrics

logger = logging.getLogger(__name__)

# Default cap for concurrent league fetches when YAML doesn't override.
# YAML's `concurrent_leagues` setting is honored per-instance via __init__.
# 10 matches the proxy-throttled value documented in providers.yaml.
DEFAULT_MAX_CONCURRENT_LEAGUES = 10


class PinnacleRetriever(Retriever):
    """
    Pinnacle Guest API Retriever
    Uses guest.api.arcadia.pinnacle.com which requires NO authentication
    """

    def __init__(self, config: dict, transport=None, circuit_breaker=None, rate_limit_config=None):
        if transport is None:
            import os

            from ..core import HttpTransport

            transport = HttpTransport(
                circuit_breaker=circuit_breaker,
                rate_limit_config=rate_limit_config,
                proxy=os.environ.get("PROXY_URL"),
            )
        super().__init__(config, transport)
        self.base_url = config.get("api_base", "https://guest.api.arcadia.pinnacle.com/0.1")

        # Honor concurrent_leagues from providers.yaml. Previously the YAML
        # setting was read by the orchestrator for sport-level concurrency
        # only, so pinnacle's internal league concurrency was hard-coded at
        # 50 — five times the value the YAML comment said was needed to avoid
        # ISP-proxy 403 storms.
        self._max_concurrent_leagues = int(config.get("concurrent_leagues", DEFAULT_MAX_CONCURRENT_LEAGUES))

        # Per-instance set so "log unknown market type once" semantics are
        # scoped to a single retriever lifetime instead of the process.
        # The factory builds a fresh retriever each pipeline run, so this
        # naturally bounds memory and re-logs novel types if they reappear.
        self._logged_unknown_types: set[str] = set()

        # Build sport ID map from config
        config_loader = ConfigLoader.get_instance()
        self._sport_map = {s.key: s.pinnacle_sport_id for s in config_loader.sports if s.pinnacle_sport_id}

    def _get_sport_url(self, sport: str) -> str:
        """Not used - we implement custom extract logic"""
        return ""

    def parse(self, data: Any, sport: str) -> list[StandardEvent]:
        """Not used - we override extract() completely"""
        return []

    async def extract(self, sport: str, limit: int = None, **kwargs) -> list[StandardEvent]:
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

        logger.debug(f"[{self.provider_id}] Fetching {sport} (sport_id={sport_id})")

        # Get active leagues
        leagues_url = f"{self.base_url}/sports/{sport_id}/leagues"
        leagues_data = await self.transport.get(leagues_url, params={"all": "false"})

        if not leagues_data:
            logger.debug(f"[{self.provider_id}] No leagues data for sport {sport_id}")
            return []

        # Check for pagination indicators in leagues response
        self._check_pagination(leagues_data, "leagues", metrics)

        # Filter leagues with active matchups
        active_leagues = [l for l in leagues_data if l.get("matchupCount", 0) > 0]

        if not active_leagues:
            logger.debug(f"[{self.provider_id}] No active leagues for {sport}")
            return []

        logger.debug(f"[{self.provider_id}] Found {len(active_leagues)} active leagues")
        metrics.leagues_fetched = len(active_leagues)

        # Parallel fetch all leagues with semaphore to limit concurrency
        semaphore = asyncio.Semaphore(self._max_concurrent_leagues)
        league_results = await asyncio.gather(
            *[self._fetch_league(league, semaphore, metrics) for league in active_leagues], return_exceptions=True
        )

        # Parse all results and deduplicate
        seen_ids: set = set()
        all_events: list[StandardEvent] = []

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
            markets_by_matchup: dict[int, list[dict]] = {}
            for market in markets or []:
                matchup_id = market.get("matchupId")
                if matchup_id:
                    markets_by_matchup.setdefault(matchup_id, []).append(market)

            # Convert to StandardEvent
            for matchup in matchups:
                event = self._parse_matchup(matchup, sport, league_name, markets_by_matchup, metrics)
                if not event:
                    continue

                if event.id not in seen_ids:
                    seen_ids.add(event.id)
                    all_events.append(event)
                    metrics.events_parsed += 1
                elif event.live_state:
                    # Live version of an already-seen prematch event:
                    # merge live_state into the existing event
                    for existing in all_events:
                        if existing.id == event.id:
                            existing.live_state = event.live_state
                            break

        # Apply limit if specified
        if limit and len(all_events) > limit:
            all_events = all_events[:limit]

        logger.debug(f"[{self.provider_id}] Extracted {len(all_events)} events for {sport}")

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
                metrics.pagination_warnings.append(f"{endpoint}: hasMore=true indicates additional pages exist")

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
        self, league: dict, semaphore: asyncio.Semaphore, metrics: ExtractionMetrics
    ) -> tuple[str, list[dict], list[dict]] | None:
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

                results = await asyncio.gather(matchups_task, markets_task, return_exceptions=True)
                matchups = results[0] if not isinstance(results[0], Exception) else []
                markets = results[1] if not isinstance(results[1], Exception) else []

                # Check for pagination in matchups/markets
                self._check_pagination(matchups, f"matchups/{league_name}", metrics)
                self._check_pagination(markets, f"markets/{league_name}", metrics)

                # Validate expected vs actual matchup count
                if expected_matchups > 0 and len(matchups) < expected_matchups * 0.5:
                    # Significant discrepancy - may indicate pagination or filtering issue
                    metrics.pagination_warnings.append(
                        f"League '{league_name}': expected ~{expected_matchups} matchups, got {len(matchups)}"
                    )

                return (league_name, matchups, markets)

            except Exception as e:
                logger.debug(f"[{self.provider_id}] Error fetching league {league_name}: {e}")
                return None

    def _parse_matchup(
        self,
        matchup: dict,
        sport: str,
        league_name: str,
        markets_by_matchup: dict[int, list[dict]],
        metrics: ExtractionMetrics,
    ) -> StandardEvent | None:
        """Parse a Pinnacle matchup + markets into StandardEvent.

        Also captures live state (scores, minute, period) for started matchups.
        Events with status="started" are returned even without open markets
        so the pipeline can update live scores on the Event model.
        """
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

            # Parse start time (already extracted above). A parse failure means
            # the event has no usable date — drop it rather than ship an empty
            # start_time which downstream date-matching treats as "today",
            # producing wrong fuzzy matches against today's events on other books.
            start_time = None
            if start_time_str:
                try:
                    start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                except (ValueError, TypeError) as e:
                    logger.warning(
                        f"[{self.provider_id}] Skipping matchup {matchup_id}: "
                        f"unparseable start_time={start_time_str!r} ({e})"
                    )
                    metrics.events_skipped_error += 1
                    return None

            # ── Capture live state (scores, minute, period) ──────────
            matchup_status = matchup.get("status")  # "pending" or "started"
            live_state = {}

            if matchup_status == "started":
                match_state = matchup.get("state", {})
                home_state = home_participant.get("state", {})
                away_state = away_participant.get("state", {})

                live_state = {
                    "match_status": "started",
                    "home_score": home_state.get("score"),
                    "away_score": away_state.get("score"),
                    "match_minute": match_state.get("minutes"),
                    "match_period": match_state.get("state"),
                }

                # Collect richer stats from parent (corners, cards, scoreByQuarter)
                stats = {}
                parent_parts = (parent or {}).get("participants", [])
                for p in parent_parts:
                    alignment = p.get("alignment")
                    p_state = p.get("state", {})
                    if alignment and p_state:
                        stats[alignment] = p_state
                # Also check main participants for sport-specific data
                for p in participants:
                    alignment = p.get("alignment")
                    p_state = p.get("state", {})
                    p_stats = p.get("stats", [])
                    if alignment:
                        if alignment not in stats:
                            stats[alignment] = p_state
                        if p_stats:
                            stats[f"{alignment}_periods"] = p_stats
                if stats:
                    live_state["stats"] = stats

            # Get raw markets for this matchup
            raw_markets = markets_by_matchup.get(matchup_id, [])

            # Parse all market types
            parsed_markets = self._parse_markets(raw_markets, sport=sport)

            # For started matchups: return event even without markets (for score tracking)
            if not parsed_markets and matchup_status != "started":
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
                markets=parsed_markets or [],
                url="",
                live_state=live_state,
            )

            return event

        except Exception as e:
            logger.debug(f"[{self.provider_id}] Error parsing matchup: {e}")
            metrics.events_skipped_error += 1
            return None

    # Core market types used by the value scanner
    _CORE_TYPES = {"moneyline", "spread", "total"}

    # Esports map periods: period 1 = map 1, period 2 = map 2, etc.
    _ESPORTS_MAP_PERIODS = {1, 2, 3, 4, 5}

    # Baseball innings-bucket periods: period 1 = first 5 innings (F5),
    # period 3 = first 3 innings (F3). Sharp reference for soft books that
    # offer the same buckets (Kambi/Altenar baseball, Cloudbet F5 totals).
    _BASEBALL_PERIOD_SCOPE = {1: "f5", 3: "f3"}

    def _parse_markets(self, raw_markets: list[dict], sport: str = "") -> list[dict]:
        """
        Parse markets from Pinnacle API response.

        Core markets (moneyline/spread/total): period 0, mainline + alternate
        handicaps. Alternates are kept so soft providers laddering at non-mainline
        points (kalshi, cloudbet, polymarket) have a sharp comparison baseline.
        Period 6 (regulation time): ice hockey 1x2, plus spread/total when
        period 0 doesn't have them (minor leagues).
        Baseball periods 1/3 (F5/F3): innings-bucket sharp reference, scope
        tagged so the scanner can compare against same-scope soft markets only.
        Esports map markets (period 1-5): moneyline/total per map (mainline only).
        """
        parsed = []
        is_esports = sport == "esports"

        # Pre-scan: which core market types does period 0 have?
        # Used to avoid period-6 spread/total overwriting OT-included odds.
        p0_types: set = set()
        for market in raw_markets:
            if (
                market.get("status") == "open"
                and market.get("period", 0) == 0
                and market.get("type") in self._CORE_TYPES
                and not market.get("isAlternate", False)
            ):
                p0_types.add(market["type"])

        for market in raw_markets:
            # Only process open markets
            if market.get("status") != "open":
                continue

            market_type = market.get("type")
            period = market.get("period", 0)

            prices = market.get("prices", [])
            if not prices:
                continue

            # Capture provider-specific IDs at market level
            market_meta = {
                "matchup_id": str(market.get("matchupId", "")),
                "period": period,
                "line_id": str(market.get("lineId", "")),
            }

            # ── Period 0 (full game / OT-included) ──
            if period == 0:
                if market_type in self._CORE_TYPES:
                    before = len(parsed)
                    if market_type == "moneyline":
                        parsed.extend(self._parse_moneyline(prices, market_meta))
                    elif market_type == "spread":
                        parsed.extend(self._parse_spread(prices, market_meta))
                    elif market_type == "total":
                        parsed.extend(self._parse_total(prices, market_meta))
                    for m in parsed[before:]:
                        m["scope"] = "ft"

                else:
                    # Log unknown types once for discovery
                    if market_type and market_type not in self._logged_unknown_types:
                        self._logged_unknown_types.add(market_type)
                        logger.debug(
                            f"[pinnacle] Unknown market type '{market_type}' period={period} prices={len(prices)}"
                        )

            # ── Period 6 (regulation time) — ice hockey ──
            # Ice hockey period=0 is OT-included (2-way moneyline);
            # period=6 is regulation time (3-way moneyline with draw).
            # Moneyline: always extract — auto-classifies as 1x2 (draw present).
            # Spread/total: only extract if period=0 doesn't have them, to avoid
            # overwriting OT-included odds with regulation-time odds.
            # Guard sport — Pinnacle uses period 6 specifically for ice hockey
            # regulation time. If another sport ever ships period=6 markets, the
            # 'reg' tag would be wrong (their canonical scope is 'ft' too), so
            # we'd silently drop them via the scanner's scope filter. Skip them
            # entirely instead.
            elif period == 6 and sport == "ice_hockey":
                if market_type in self._CORE_TYPES:
                    before = len(parsed)
                    if market_type == "moneyline":
                        # Always extract — has draw, so auto-classifies as 1x2
                        parsed.extend(self._parse_moneyline(prices, market_meta))
                    elif market_type in ("spread", "total") and market_type not in p0_types:
                        # Only fill in when period=0 doesn't offer this market type
                        if market_type == "spread":
                            parsed.extend(self._parse_spread(prices, market_meta))
                        else:
                            parsed.extend(self._parse_total(prices, market_meta))
                    for m in parsed[before:]:
                        m["scope"] = "reg"

            # ── Esports map periods (1-5) — map-level value scanning ──
            elif is_esports and period in self._ESPORTS_MAP_PERIODS:
                before = len(parsed)
                if market_type == "moneyline":
                    parsed.extend(
                        self._parse_moneyline(prices, market_meta, market_type_override=f"moneyline_m{period}")
                    )
                elif market_type == "total" and not market.get("isAlternate", False):
                    parsed.extend(self._parse_total(prices, market_meta, market_type_override=f"total_m{period}"))
                for m in parsed[before:]:
                    m["scope"] = f"map_{period}"

            # ── Baseball innings buckets (F5 = period 1, F3 = period 3) ──
            # Mainline + alternates kept (same rationale as period 0): soft
            # books that ladder F5 totals (Cloudbet, Kalshi) need the alternate
            # ladder for comparison. The scanner's scope filter will refuse
            # to mix these with full-game odds.
            elif sport == "baseball" and period in self._BASEBALL_PERIOD_SCOPE:
                scope = self._BASEBALL_PERIOD_SCOPE[period]
                if market_type in self._CORE_TYPES:
                    before = len(parsed)
                    if market_type == "moneyline":
                        parsed.extend(self._parse_moneyline(prices, market_meta))
                    elif market_type == "spread":
                        parsed.extend(self._parse_spread(prices, market_meta))
                    elif market_type == "total":
                        parsed.extend(self._parse_total(prices, market_meta))
                    for m in parsed[before:]:
                        m["scope"] = scope

        return parsed

    def _parse_moneyline(self, prices: list[dict], market_meta: dict, market_type_override: str = None) -> list[dict]:
        """Parse moneyline (winner) market."""
        outcomes = []

        for price_obj in prices:
            designation = price_obj.get("designation")
            american_odds = price_obj.get("price")

            if designation and american_odds is not None:
                decimal_odds = self._american_to_decimal(american_odds)
                outcomes.append(
                    {
                        "name": designation,
                        "odds": decimal_odds,
                        "provider_meta": {"designation": designation},
                    }
                )

        if not outcomes:
            return []

        if market_type_override:
            market_type = market_type_override
        else:
            # Determine market type (moneyline vs 1x2)
            has_draw = any(o["name"] == "draw" for o in outcomes)
            market_type = "1x2" if has_draw else "moneyline"

        return [
            {
                "type": market_type,
                "outcomes": outcomes,
                "provider_meta": market_meta,
            }
        ]

    def _parse_spread(self, prices: list[dict], market_meta: dict) -> list[dict]:
        """Parse spread (handicap) market."""
        outcomes = []

        for price_obj in prices:
            designation = price_obj.get("designation")
            american_odds = price_obj.get("price")
            points = price_obj.get("points")

            if designation and american_odds is not None and points is not None:
                decimal_odds = self._american_to_decimal(american_odds)
                outcomes.append(
                    {
                        "name": designation,
                        "odds": decimal_odds,
                        "point": float(points),
                        "provider_meta": {"designation": designation},
                    }
                )

        if not outcomes:
            return []

        return [{"type": "spread", "outcomes": outcomes, "provider_meta": market_meta}]

    def _parse_total(self, prices: list[dict], market_meta: dict, market_type_override: str = None) -> list[dict]:
        """Parse total (over/under) market."""
        outcomes = []

        for price_obj in prices:
            designation = price_obj.get("designation")
            american_odds = price_obj.get("price")
            points = price_obj.get("points")

            if designation and american_odds is not None and points is not None:
                decimal_odds = self._american_to_decimal(american_odds)
                outcomes.append(
                    {
                        "name": designation,
                        "odds": decimal_odds,
                        "point": float(points),
                        "provider_meta": {"designation": designation},
                    }
                )

        if not outcomes:
            return []

        mt = market_type_override or "total"
        return [{"type": mt, "outcomes": outcomes, "provider_meta": market_meta}]

    def _american_to_decimal(self, american_odds: int) -> float:
        """Convert American odds to decimal format"""
        if american_odds > 0:
            return (american_odds / 100) + 1
        else:
            return (-100 / american_odds) + 1
