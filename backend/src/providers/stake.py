"""Stake.com GraphQL odds extractor.

Stake uses a GraphQL API at https://stake.com/_api/graphql.
No authentication is required for odds data.
"""
from typing import List, Optional, Any
import logging

from ..core.retriever import Retriever, StandardEvent
from ..core.transport import HttpTransport
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://stake.com/_api/graphql"

_HEADERS = {
    "Content-Type": "application/json",
    "x-language": "en",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
}

# Statuses that indicate a live/finished event — skip these
_SKIP_STATUSES = {"in_progress", "ended", "cancelled", "suspended"}

# Draw indicator names (case-insensitive)
_DRAW_NAMES = {"draw", "x", "tie"}

# Sport slug mapping: internal sport key -> Stake sport slug
_SPORT_MAP = {
    "football": "football",
    "basketball": "basketball",
    "ice-hockey": "ice-hockey",
    "tennis": "tennis",
    "baseball": "baseball",
    "american-football": "american-football",
    "mma": "mma",
    "boxing": "boxing",
}

# GraphQL query to fetch pre-match fixtures for a sport
_FIXTURES_QUERY = """
query SportFixtures($sport: String!, $limit: Int) {
  sport(slug: $sport) {
    fixtures(status: upcoming, limit: $limit) {
      id
      slug
      status
      startTime
      home { name }
      away { name }
      tournament { name }
      betGroups {
        name
        outcomes {
          id
          active
          odds
          name
        }
      }
    }
  }
}
"""


def parse_outcomes_to_market(
    outcomes: list,
    group_name: str,
    sport: str,
) -> Optional[dict]:
    """Parse a Stake outcome list into a normalized market dict.

    Returns None if:
    - outcomes list is empty
    - any outcome is inactive (odds not available)
    """
    if not outcomes:
        return None

    # All outcomes must be active
    if not all(o.get("active") for o in outcomes):
        return None

    # Detect draw presence to distinguish 1x2 from moneyline
    has_draw = any(o.get("name", "").lower() in _DRAW_NAMES for o in outcomes)
    market_type = "1x2" if has_draw else "moneyline"

    normalized_outcomes = []
    home_assigned = False
    for o in outcomes:
        name_raw = o.get("name", "")
        odds = o.get("odds")
        if name_raw.lower() in _DRAW_NAMES:
            normalized_outcomes.append({"name": "draw", "odds": odds})
        elif not home_assigned:
            normalized_outcomes.append({"name": "home", "odds": odds})
            home_assigned = True
        else:
            normalized_outcomes.append({"name": "away", "odds": odds})

    return {"type": market_type, "outcomes": normalized_outcomes}


def parse_fixture(
    fixture: dict,
    sport: str,
    provider_id: str,
) -> Optional[StandardEvent]:
    """Parse a single Stake fixture dict into a StandardEvent.

    Returns None for live/ended/cancelled/suspended events or fixtures
    with no parseable markets.
    """
    status = fixture.get("status", "")
    if status in _SKIP_STATUSES:
        return None

    bet_groups = fixture.get("betGroups") or []
    if not bet_groups:
        return None

    fixture_id = fixture.get("id", "")
    start_time = fixture.get("startTime", "")
    home_raw = (fixture.get("home") or {}).get("name", "")
    away_raw = (fixture.get("away") or {}).get("name", "")
    league = (fixture.get("tournament") or {}).get("name", "")

    home_team = normalize_team_name(home_raw)
    away_team = normalize_team_name(away_raw)

    markets = []
    for group in bet_groups:
        group_name = group.get("name", "")
        outcomes = group.get("outcomes") or []
        market = parse_outcomes_to_market(outcomes, group_name, sport)
        if market:
            markets.append(market)

    if not markets:
        return None

    event_name = f"{home_raw} vs {away_raw}"
    event_id = f"stake_{fixture_id}"

    return StandardEvent(
        id=event_id,
        name=event_name,
        sport=sport,
        markets=markets,
        provider=provider_id,
        url=f"https://stake.com/sports/{sport}/event/{fixture_id}",
        start_time=start_time,
        home_team=home_team,
        away_team=away_team,
        league=league,
    )


class StakeRetriever(Retriever):
    """Retriever for Stake.com pre-match odds via GraphQL API."""

    def __init__(self, config: dict, transport=None):
        if transport is None:
            transport = HttpTransport()
        super().__init__(config, transport)

    def _get_sport_url(self, sport: str) -> str:
        """Not used — we override extract() with a POST request."""
        return ""

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """Not used — parsing is done inside extract()."""
        return []

    async def extract(self, sport: str, limit: int = 200, **kwargs) -> List[StandardEvent]:
        """Extract pre-match fixtures for a sport from Stake GraphQL API."""
        sport_slug = _SPORT_MAP.get(sport)
        if not sport_slug:
            logger.warning(f"[{self.provider_id}] Sport '{sport}' not mapped for Stake")
            return []

        payload = {
            "query": _FIXTURES_QUERY,
            "variables": {"sport": sport_slug, "limit": limit},
        }

        data = await self.transport.post(
            GRAPHQL_URL,
            json=payload,
            headers=_HEADERS,
        )

        if not data:
            logger.warning(f"[{self.provider_id}] No data returned for sport '{sport}'")
            return []

        fixtures = (
            data.get("data", {})
            .get("sport", {})
            .get("fixtures") or []
        )

        events = []
        for fixture in fixtures:
            event = parse_fixture(fixture, sport, self.provider_id)
            if event:
                events.append(event)

        logger.debug(
            f"[{self.provider_id}] Parsed {len(events)} events for sport '{sport}' "
            f"from {len(fixtures)} fixtures"
        )
        return events
