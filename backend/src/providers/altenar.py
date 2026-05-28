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

import asyncio
import contextlib
import logging
from datetime import datetime
from typing import Any

from ..core import Retriever, StandardEvent
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)


# Altenar ships 1.8334 / 1.8334 (= 11/6 implied 0.5455 each side) on spread/total
# markets the bookmaker hasn't actively priced. The customer website renders the
# real ladder from a separate (likely WebSocket) feed Betty doesn't subscribe to,
# so this sentinel never matches anything the user can actually bet — and
# comparing it to Pinnacle's real prices generates phantom +EV / arb opportunities.
_PLACEHOLDER_ODDS = 1.8334
_PLACEHOLDER_TOLERANCE = 0.001


def _is_placeholder_market(outcomes: list[dict]) -> bool:
    """True if every outcome carries Altenar's ~1.8334 no-juice sentinel.

    Real bookmaker spreads/totals almost never have all legs at the same value
    around 1.83 (a pick'em mainline lands at ~1.91/1.91 with vig). Requiring an
    exact value match — not just leg equality — avoids dropping genuinely
    balanced markets.
    """
    if len(outcomes) < 2:
        return False
    prices = [o.get("odds") for o in outcomes]
    if any(not p for p in prices):
        return False
    return all(abs(p - _PLACEHOLDER_ODDS) < _PLACEHOLDER_TOLERANCE for p in prices)


def scope_for(type_id: int, sport: str | None) -> str:
    """Map Altenar typeId + sport to canonical scope.

    Sport-aware because typeId 18 means "regulation only" for hockey
    (OT-inclusive lives at 412) but means "Full Time" for football
    (which doesn't have OT in standard markets).
    """
    # Hockey regulation-only total/spread
    if type_id == 18 and sport == "ice_hockey":
        return "reg"
    # Similarly, typeId 16 (spread) is regulation-only for hockey
    if type_id == 16 and sport == "ice_hockey":
        return "reg"
    # Everything else maps to 'ft' by default. New typeIds with scope ambiguity
    # must be added here explicitly.
    _FT_TYPEIDS = {
        1,
        186,
        219,
        251,
        406,
        30001,  # moneyline / 1x2
        18,
        189,
        225,
        238,
        258,
        412,  # total
        16,
        187,
        223,
        237,
        256,
        410,  # spread
    }
    if type_id in _FT_TYPEIDS:
        return "ft"
    # Unknown typeId — caller decides whether to emit. Default 'ft' to keep
    # backward compatibility; a unit test guards completeness.
    return "ft"


# Sentinel exported for the test_altenar_typeid_scope_map_is_complete check.
# WARNING: built with sport=None, so the sport-overridden scopes don't show
# here. In particular TYPEID_SCOPE[18] == 'ft' BUT scope_for(18, 'ice_hockey')
# == 'reg' (same for typeId 16). To know the real scope for an emitted row,
# call scope_for(tid, sport) — never index TYPEID_SCOPE directly in extractor
# code. The parametrized scope_for tests cover the sport-aware paths.
TYPEID_SCOPE = {
    tid: scope_for(tid, None)
    for tid in (
        1,
        186,
        219,
        251,
        406,
        30001,
        18,
        189,
        225,
        238,
        258,
        412,
        16,
        187,
        223,
        237,
        256,
        410,
    )
}


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
        66: "football",
        67: "basketball",
        68: "tennis",
        69: "volleyball",
        70: "ice_hockey",
        71: "boxing",
        73: "handball",
        74: "cricket",
        75: "american_football",
        76: "baseball",
        77: "table_tennis",
        84: "mma",
        101: "rugby",
        102: "rugby",
        145: "esports",
    }

    # Market type mapping from Altenar typeId to our market types
    MARKET_TYPE_MAPPING = {
        # 1x2 / moneyline (match winner)
        1: "1x2",  # Match result (football, handball, rugby)
        186: "moneyline",  # Winner (tennis, volleyball, table tennis, MMA)
        219: "moneyline",  # Winner incl. OT (basketball, american football)
        251: "moneyline",  # Winner incl. extra innings (baseball)
        406: "moneyline",  # Winner incl. OT+penalties (ice hockey)
        30001: "moneyline",  # Match winner (esports)
        # Total (over/under)
        18: "total",  # Total (football, ice hockey, MMA, rugby)
        189: "total",  # Total games (tennis)
        225: "total",  # Total incl. OT (basketball, american football)
        238: "total",  # Total points (volleyball, table tennis)
        258: "total",  # Total incl. extra innings (baseball)
        412: "total",  # Total incl. OT+penalties (ice hockey)
        # Spread (handicap)
        16: "spread",  # Handicap (handball, rugby)
        187: "spread",  # Game handicap (tennis)
        223: "spread",  # Spread incl. OT (basketball, american football)
        237: "spread",  # Point handicap (volleyball, table tennis)
        256: "spread",  # Handicap incl. extra innings (baseball)
        410: "spread",  # Handicap incl. OT+penalties (ice hockey)
    }

    def __init__(self, config: dict[str, Any], transport=None, circuit_breaker=None, rate_limit_config=None):
        if transport is None:
            from ..core import HttpTransport

            transport = HttpTransport(
                circuit_breaker=circuit_breaker,
                rate_limit_config=rate_limit_config,
            )
        super().__init__(config, transport)

        # Altenar API base
        self.api_base = config.get("api_base", "https://sb2frontend-altenar2.biahosted.com/api")

        # Integration ID (skin)
        self.integration = config.get("integration", "betiniase2")

    @staticmethod
    def _build_id_index(items: list[dict]) -> dict[int, dict]:
        """Build O(1) lookup index from list of dicts with 'id' field."""
        return {item["id"]: item for item in items if "id" in item}

    def _standardize_outcome(
        self, outcome_name: str, market_type: str, raw_home: str, raw_away: str, outcome_index: int = -1
    ) -> str:
        """
        Standardize outcome names to platform conventions.

        Args:
            outcome_name: Raw outcome name from API
            market_type: Market type (1x2, moneyline, spread, total)
            raw_home: Raw home team name (before normalization)
            raw_away: Raw away team name (before normalization)
            outcome_index: Position in the odds list (0=first, 1=second) for fallback

        Returns:
            Standardized outcome name (home, away, draw, over, under)
        """
        outcome_lower = outcome_name.lower().strip()

        # Handle total markets: "Over X.5" → "over", "Under X.5" → "under"
        if market_type == "total":
            if outcome_lower.startswith("over") or outcome_lower.startswith("över"):
                return "over"
            if outcome_lower.startswith("under"):
                return "under"
            return outcome_name

        # Handle 1x2, moneyline, and spread markets
        if market_type in ("1x2", "moneyline", "spread"):
            # Check for draw first (1x2 only)
            if market_type == "1x2" and outcome_lower in ["x", "draw", "tie", "x2"]:
                return "draw"

            # Simple numeric markers (common in all sports)
            if outcome_lower in ["1", "2"]:
                return "home" if outcome_lower == "1" else "away"

            # Explicit home/away keywords
            if outcome_lower in ["home", "hemma"]:
                return "home"
            if outcome_lower in ["away", "borta"]:
                return "away"

            # Extract team name without parentheses and extra text
            import re

            def extract_base_name(team_name):
                base = re.sub(r"\([^)]*\)", "", team_name).strip()
                return normalize_team_name(base)

            home_base = extract_base_name(raw_home)
            away_base = extract_base_name(raw_away)
            outcome_base = extract_base_name(outcome_name)

            # Try exact match with normalized names
            if outcome_base == home_base:
                return "home"
            if outcome_base == away_base:
                return "away"

            # Try partial match - check if any word from team name is in outcome
            # Filter out very short words (< 3 chars) to avoid false matches
            home_words = {w for w in home_base.split() if len(w) >= 3}
            away_words = {w for w in away_base.split() if len(w) >= 3}
            outcome_words = {w for w in outcome_base.split() if len(w) >= 3}

            home_overlap = home_words & outcome_words
            away_overlap = away_words & outcome_words

            if home_overlap and not away_overlap:
                return "home"
            if away_overlap and not home_overlap:
                return "away"

            # Positional fallback for 2-way markets (moneyline, spread)
            # When outcome name doesn't match team names (common in esports/MMA),
            # use position: first outcome = home, second = away
            if market_type in ("moneyline", "spread") and outcome_index >= 0:
                if outcome_index == 0:
                    return "home"
                elif outcome_index == 1:
                    return "away"

        # If no match found, return original (will be logged as 'other')
        return outcome_name

    async def _fetch_events(self, endpoint: str, sport_id: int | None = None) -> dict[str, Any]:
        """
        Fetch events from Altenar API endpoint.

        Uses self.transport (HttpTransport) for connection reuse across calls.

        Args:
            endpoint: API endpoint (e.g., 'widget/GetUpcoming')
            sport_id: Optional sport ID to filter events (e.g., 67 for basketball)

        Returns:
            Response data with events, competitors, markets, odds
        """
        try:
            url = f"{self.api_base}/{endpoint}"

            params = {
                "culture": "en-GB",
                "timezoneOffset": "0",
                "integration": self.integration,
                "deviceType": "1",
                "numFormat": "en-GB",
            }

            # Add sport filter if provided
            if sport_id is not None:
                params["sportId"] = str(sport_id)

            data = await self.transport.get(url, params=params)
            if data and isinstance(data, dict):
                return data

            logger.warning(f"[{self.provider_id}] {endpoint} returned no data")
            return {}

        except Exception as e:
            logger.error(f"[{self.provider_id}] Error fetching {endpoint}: {e}")
            return {}

    def _parse_event(
        self,
        event_data: dict,
        sport: str,
        reference_data: dict[str, list[dict]],
        sport_id: int = None,
    ) -> StandardEvent | None:
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
            event_id = str(event_data.get("id", ""))
            event_name = event_data.get("name", "")

            if not event_id:
                return None

            # Parse start time
            start_time_str = event_data.get("startDate")
            start_time = None
            if start_time_str:
                try:
                    start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                except Exception as e:
                    logger.debug(f"[{self.provider_id}] Failed to parse start time: {e}")

            # Get competitors (teams) — O(1) via pre-built index
            comp_idx = reference_data.get("_comp_idx", {})
            competitor_ids = event_data.get("competitorIds", [])
            competitors = [comp_idx[cid] for cid in competitor_ids if cid in comp_idx]

            # Determine home/away teams (normalize immediately)
            raw_home = competitors[0]["name"] if len(competitors) > 0 else None
            raw_away = competitors[1]["name"] if len(competitors) > 1 else None

            # For events with only one competitor (e.g., futures), use event name
            if not raw_home and not raw_away:
                # This might be a special market (futures, outright, etc.)
                # Skip for now
                return None

            # Normalize team names
            home_team = normalize_team_name(raw_home) if raw_home else None
            away_team = normalize_team_name(raw_away) if raw_away else None

            # Get championship (league) — O(1) via pre-built index
            champ_idx = reference_data.get("_champ_idx", {})
            champ_id = event_data.get("champId")
            champ = champ_idx.get(champ_id)
            league = champ["name"] if champ else "Unknown"
            # Category ID (country) for URL building
            category_id = champ.get("catId") if champ else None

            # Parse markets
            markets = []
            market_ids = event_data.get("marketIds", [])

            market_idx = reference_data.get("_market_idx", {})
            odd_idx = reference_data.get("_odd_idx", {})
            for market_id in market_ids:
                market = market_idx.get(market_id)
                if not market:
                    continue

                # Map market type — skip unsupported markets early
                market_type_id = market.get("typeId")
                market_type = self.MARKET_TYPE_MAPPING.get(market_type_id)

                # Note: regulation-only hockey markets (typeId 18/16) are now stored
                # with scope='reg' instead of being skipped — see scope_for().
                # The scanner refuses to compare across scopes, so this won't
                # produce false arbs against Pinnacle's period-0 OT-inclusive odds.

                if not market_type:
                    # Fallback: match by market name keywords (catches unmapped
                    # sport-specific typeIds like football Asian Handicap)
                    market_name = (market.get("name") or "").lower()
                    if any(
                        kw in market_name
                        for kw in (
                            "handicap",
                            "handikapp",
                            "asian handicap",
                            "spread",
                            "puck line",
                            "run line",
                        )
                    ):
                        market_type = "spread"
                    elif any(
                        kw in market_name
                        for kw in (
                            "over/under",
                            "över/under",
                            "total",
                        )
                    ):
                        market_type = "total"
                    else:
                        continue

                # Extract point value from market's 'sv' field for spread/total
                market_point = None
                if market_type in ("spread", "total"):
                    sv = market.get("sv")
                    if sv:
                        with contextlib.suppress(ValueError, TypeError):
                            market_point = float(sv)

                # Get odds for this market
                odd_ids = market.get("oddIds", [])
                outcomes = []

                for idx, odd_id in enumerate(odd_ids):
                    odd = odd_idx.get(odd_id)
                    if odd:
                        raw_outcome = odd.get("name", "")
                        standardized_outcome = self._standardize_outcome(
                            raw_outcome, market_type, raw_home, raw_away, outcome_index=idx
                        )
                        # Debug: log outcome mapping for moneyline to catch inversions
                        if market_type == "moneyline" and standardized_outcome in ("home", "away"):
                            logger.debug(
                                f"[{self.provider_id}] ML outcome: raw='{raw_outcome}' → {standardized_outcome} | home='{raw_home}' away='{raw_away}' idx={idx} odds={odd.get('price')}"
                            )

                        outcome_dict = {
                            "name": standardized_outcome,
                            "odds": odd.get("price", 0.0),
                            "provider_meta": {
                                "outcome_id": str(odd_id),
                            },
                        }
                        if market_point is not None:
                            # Altenar exposes BOTH home and away spread outcomes at the
                            # SAME `market.sv` point (book-line convention — point IDs
                            # the line). The scanner's keying expects per-outcome
                            # convention (home@P, away@-P share a line). Negate point
                            # for away spread outcomes so both legs land in the same
                            # market_key. Totals (over/under) legitimately share a
                            # point on both sides — only spread needs the flip.
                            if market_type == "spread" and standardized_outcome == "away":
                                outcome_dict["point"] = -market_point
                            else:
                                outcome_dict["point"] = market_point
                        outcomes.append(outcome_dict)

                if outcomes:
                    if market_type in ("spread", "total") and _is_placeholder_market(outcomes):
                        logger.debug(
                            f"[{self.provider_id}] Skipping placeholder {market_type} "
                            f"(point={market_point}, both legs ~{_PLACEHOLDER_ODDS}) "
                            f"for event {event_id}"
                        )
                        continue
                    market_meta = {
                        "event_id": event_id,
                        "market_id": str(market_id),
                    }
                    # Include Altenar routing IDs for bet placement URL building
                    if sport_id is not None:
                        market_meta["sport_id"] = str(sport_id)
                    if category_id is not None:
                        market_meta["category_id"] = str(category_id)
                    if champ_id is not None:
                        market_meta["championship_id"] = str(champ_id)
                    market_dict = {
                        "type": market_type,
                        "outcomes": outcomes,
                        "provider_meta": market_meta,
                        "scope": scope_for(market_type_id, sport),
                    }
                    markets.append(market_dict)

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
                markets=markets,
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

    def parse(self, data: Any, sport: str) -> list[StandardEvent]:
        """
        Parse Altenar API response data.

        Not used - we override extract() completely to handle the API call
        and parsing in one method.
        """
        return []

    async def extract(self, sport: str, limit: int = 100, **kwargs) -> list[StandardEvent]:
        """
        Extract events using REST API.

        Args:
            sport: Sport key (e.g., 'football')
            limit: Maximum number of events to return

        Returns:
            List of StandardEvents
        """
        logger.debug(f"[{self.provider_id}] Starting extraction for {sport}")

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
            logger.debug(f"[{self.provider_id}] Fetching upcoming events for {sport} (sportId={sport_id})")
            data = await self._fetch_events("widget/GetUpcoming", sport_id=sport_id)

            if not data or "events" not in data:
                logger.warning(f"[{self.provider_id}] No data returned from API")
                return []

            # All events should match the requested sport (no client-side filtering needed)
            sport_events = data.get("events", [])

            logger.debug(f"[{self.provider_id}] Found {len(sport_events)} {sport} events")

            # Build O(1) lookup indexes (called once, used per-event)
            # Without indexing: ~4 list scans per market × ~3 markets × ~500 events = ~6000 O(n) scans
            # With indexing: 4 dict builds + O(1) lookups = massive speedup
            reference_data = {
                "competitors": data.get("competitors", []),
                "champs": data.get("champs", []),
                "markets": data.get("markets", []),
                "odds": data.get("odds", []),
                # Pre-built indexes for O(1) lookups
                "_comp_idx": self._build_id_index(data.get("competitors", [])),
                "_champ_idx": self._build_id_index(data.get("champs", [])),
                "_market_idx": self._build_id_index(data.get("markets", [])),
                "_odd_idx": self._build_id_index(data.get("odds", [])),
            }

            # Parse events
            events = []
            for event_data in sport_events:
                event = self._parse_event(event_data, sport, reference_data, sport_id=sport_id)
                if event:
                    events.append(event)

                # Check limit
                if limit and len(events) >= limit:
                    break

            logger.debug(f"[{self.provider_id}] Parsed {len(events)} {sport} events")

            # Pass 2: Enrich events missing spread/total via GetEventDetails
            # Football on Altenar has no spread markets at all (platform limitation)
            if events and sport != "football":
                enriched = await self._enrich_missing_spreads(events, sport_id, sport)
                if enriched:
                    logger.info(f"[{self.provider_id}] Enriched {enriched} spread/total markets for {sport}")

            return events

        except Exception as e:
            logger.error(f"[{self.provider_id}] Error extracting {sport}: {e}", exc_info=True)
            return []

    MAX_ENRICH_EVENTS = 200

    async def _enrich_missing_spreads(self, events: list[StandardEvent], sport_id: int, sport: str = "") -> int:
        """Fetch spread/total from GetEventDetails for events missing them.

        The bulk GetUpcoming endpoint often omits spread markets (73% missing for football).
        GetEventDetails per-event endpoint returns full market data including spread/total.
        Uses batched parallel requests with semaphore to respect rate limits.
        """
        # Filter to events that have 1x2/ML but no spread
        todo = [ev for ev in events if not any(m["type"] == "spread" for m in ev.markets)]
        if not todo:
            return 0

        if len(todo) > self.MAX_ENRICH_EVENTS:
            todo = todo[: self.MAX_ENRICH_EVENTS]

        logger.debug(f"[{self.provider_id}] Enriching {len(todo)} events missing spread via GetEventDetails")

        enriched = 0
        sem = asyncio.Semaphore(20)
        BATCH_SIZE = 50

        async def _fetch_event_detail(ev: StandardEvent):
            async with sem:
                try:
                    url = f"{self.api_base}/widget/GetEventDetails"
                    params = {
                        "culture": "en-GB",
                        "timezoneOffset": "0",
                        "integration": self.integration,
                        "deviceType": "1",
                        "numFormat": "en-GB",
                        "eventId": ev.id,
                    }
                    data = await self.transport.get(url, params=params)
                    if not data or not isinstance(data, dict):
                        return None
                    return (ev, data)
                except Exception:
                    return None

        # Process in batches
        for i in range(0, len(todo), BATCH_SIZE):
            batch = todo[i : i + BATCH_SIZE]
            results = await asyncio.gather(
                *[_fetch_event_detail(ev) for ev in batch],
                return_exceptions=True,
            )
            for r in results:
                if r is None or isinstance(r, Exception):
                    continue
                ev, detail = r
                new_markets = self._extract_spread_total_from_detail(detail, ev, sport)
                if new_markets:
                    ev.markets.extend(new_markets)
                    enriched += len(new_markets)

        return enriched

    def _extract_spread_total_from_detail(
        self, detail: dict[str, Any], event: StandardEvent, sport: str = ""
    ) -> list[dict]:
        """Extract spread/total markets from GetEventDetails response.

        Reuses existing MARKET_TYPE_MAPPING and _standardize_outcome() logic.
        Only returns spread/total markets not already present on the event.
        """
        markets_data = detail.get("markets", [])
        odds_data = detail.get("odds", [])
        competitors = detail.get("competitors", [])

        if not markets_data or not odds_data:
            return []

        # Build indexes
        odd_idx = self._build_id_index(odds_data)

        # Get raw team names from competitors for outcome matching
        raw_home, raw_away = "", ""
        events_data = detail.get("events", [])
        if events_data and competitors:
            ev_data = events_data[0] if events_data else {}
            comp_idx = self._build_id_index(competitors)
            for cid in ev_data.get("competitorIds", []):
                comp = comp_idx.get(cid)
                if comp:
                    if not raw_home:
                        raw_home = comp.get("name", "")
                    else:
                        raw_away = comp.get("name", "")

        # Existing market type+point combos to avoid duplicates
        existing_keys = set()
        for m in event.markets:
            key = m["type"]
            for o in m.get("outcomes", []):
                if "point" in o:
                    key = f"{m['type']}_{o['point']}"
            existing_keys.add(key)

        new_markets = []
        for market in markets_data:
            market_type_id = market.get("typeId")
            market_type = self.MARKET_TYPE_MAPPING.get(market_type_id)

            if market_type not in ("spread", "total"):
                continue

            # Note: regulation-only hockey markets (typeId 18/16) are now stored
            # with scope='reg' instead of being skipped — see scope_for().

            # Extract point value
            market_point = None
            if market_type in ("spread", "total"):
                sv = market.get("sv")
                if sv:
                    with contextlib.suppress(ValueError, TypeError):
                        market_point = float(sv)

            # Check for duplicate
            dup_key = market_type
            if market_point is not None:
                dup_key = f"{market_type}_{market_point}"
            if dup_key in existing_keys:
                continue

            # Parse outcomes
            odd_ids = market.get("oddIds", [])
            outcomes = []
            for idx, odd_id in enumerate(odd_ids):
                odd = odd_idx.get(odd_id)
                if not odd:
                    continue
                raw_outcome = odd.get("name", "")
                standardized = self._standardize_outcome(
                    raw_outcome, market_type, raw_home, raw_away, outcome_index=idx
                )
                outcome_dict = {
                    "name": standardized,
                    "odds": odd.get("price", 0.0),
                }
                if market_point is not None:
                    outcome_dict["point"] = market_point
                outcomes.append(outcome_dict)

            if outcomes:
                if _is_placeholder_market(outcomes):
                    logger.debug(
                        f"[{self.provider_id}] Skipping placeholder {market_type} "
                        f"(point={market_point}, both legs ~{_PLACEHOLDER_ODDS}) "
                        f"for event {event.id}"
                    )
                    continue
                new_markets.append(
                    {
                        "type": market_type,
                        "outcomes": outcomes,
                        "scope": scope_for(market_type_id, sport),
                    }
                )
                existing_keys.add(dup_key)

        return new_markets
