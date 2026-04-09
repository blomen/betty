# International Signal Providers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Stake.com and Cloudbet as signal-only providers to strengthen consensus model for Pinnacle execution.

**Architecture:** Two new `Retriever` subclasses — `StakeRetriever` (GraphQL) and `CloudbetRetriever` (REST) — each using `HttpTransport`, registered in `factory.py`, configured in `providers.yaml` and `sports.yaml`. Both produce `StandardEvent` objects identical to existing providers. New `signal_international` extraction tier runs every 5 minutes independently.

**Tech Stack:** Python 3.10+, aiohttp, `Retriever` base class, `StandardEvent` dataclass, existing `HttpTransport`

**Note:** Fairlay deprioritized (invite-only, complex onboarding). Marathonbet/1xBet/Betway deferred to Phase 2 (requires investigation of DOM/API structure). This plan covers the two API-first providers.

---

### Task 1: Stake.com GraphQL Parser (tests + implementation)

**Files:**
- Create: `backend/src/providers/stake.py`
- Create: `backend/tests/providers/test_stake_parser.py`

- [ ] **Step 1: Write failing tests for Stake.com response parsing**

Create `backend/tests/providers/test_stake_parser.py`:

```python
"""Tests for Stake.com GraphQL response parsing."""
import pytest
from src.providers.stake import parse_fixture, parse_outcomes_to_market


FIXTURE_FOOTBALL = {
    "id": "fixture_123",
    "name": "Manchester United vs Arsenal",
    "status": "open",
    "startTime": "2026-04-15T15:00:00Z",
    "tournament": {
        "name": "Premier League",
        "category": {"sport": {"name": "Football", "slug": "football"}}
    },
    "data": {
        "competitors": [
            {"name": "Manchester United", "extId": "1"},
            {"name": "Arsenal", "extId": "2"},
        ]
    },
    "groups": [
        {
            "name": "Winner",
            "templates": [{
                "markets": [{
                    "outcomes": [
                        {"id": "o1", "name": "Manchester United", "odds": 2.80, "active": True},
                        {"id": "o2", "name": "Draw", "odds": 3.40, "active": True},
                        {"id": "o3", "name": "Arsenal", "odds": 2.50, "active": True},
                    ]
                }]
            }]
        }
    ]
}

FIXTURE_BASKETBALL = {
    "id": "fixture_456",
    "name": "Lakers vs Celtics",
    "status": "open",
    "startTime": "2026-04-15T01:00:00Z",
    "tournament": {
        "name": "NBA",
        "category": {"sport": {"name": "Basketball", "slug": "basketball"}}
    },
    "data": {
        "competitors": [
            {"name": "Los Angeles Lakers", "extId": "1"},
            {"name": "Boston Celtics", "extId": "2"},
        ]
    },
    "groups": [
        {
            "name": "Winner",
            "templates": [{
                "markets": [{
                    "outcomes": [
                        {"id": "o1", "name": "Los Angeles Lakers", "odds": 1.85, "active": True},
                        {"id": "o2", "name": "Boston Celtics", "odds": 1.95, "active": True},
                    ]
                }]
            }]
        }
    ]
}

FIXTURE_LIVE = {
    "id": "fixture_789",
    "name": "Real Madrid vs Barcelona",
    "status": "in_progress",
    "startTime": "2026-04-15T20:00:00Z",
    "tournament": {
        "name": "La Liga",
        "category": {"sport": {"name": "Football", "slug": "football"}}
    },
    "data": {"competitors": [{"name": "Real Madrid"}, {"name": "Barcelona"}]},
    "groups": []
}


class TestParseOutcomesToMarket:
    def test_1x2_three_outcomes(self):
        outcomes = [
            {"id": "o1", "name": "Manchester United", "odds": 2.80, "active": True},
            {"id": "o2", "name": "Draw", "odds": 3.40, "active": True},
            {"id": "o3", "name": "Arsenal", "odds": 2.50, "active": True},
        ]
        result = parse_outcomes_to_market(outcomes, "winner", "football")
        assert result is not None
        assert result["type"] == "1x2"
        assert len(result["outcomes"]) == 3
        assert result["outcomes"][0] == {"name": "home", "odds": 2.80}
        assert result["outcomes"][1] == {"name": "draw", "odds": 3.40}
        assert result["outcomes"][2] == {"name": "away", "odds": 2.50}

    def test_moneyline_two_outcomes(self):
        outcomes = [
            {"id": "o1", "name": "Lakers", "odds": 1.85, "active": True},
            {"id": "o2", "name": "Celtics", "odds": 1.95, "active": True},
        ]
        result = parse_outcomes_to_market(outcomes, "winner", "basketball")
        assert result is not None
        assert result["type"] == "moneyline"
        assert len(result["outcomes"]) == 2
        assert result["outcomes"][0] == {"name": "home", "odds": 1.85}
        assert result["outcomes"][1] == {"name": "away", "odds": 1.95}

    def test_inactive_outcome_skips(self):
        outcomes = [
            {"id": "o1", "name": "Team A", "odds": 2.0, "active": True},
            {"id": "o2", "name": "Team B", "odds": 0, "active": False},
        ]
        result = parse_outcomes_to_market(outcomes, "winner", "basketball")
        assert result is None

    def test_empty_outcomes(self):
        result = parse_outcomes_to_market([], "winner", "football")
        assert result is None


class TestParseFixture:
    def test_football_1x2(self):
        event = parse_fixture(FIXTURE_FOOTBALL, "football", "stake")
        assert event is not None
        assert event.home_team == "manchester united"
        assert event.away_team == "arsenal"
        assert event.sport == "football"
        assert event.league == "Premier League"
        assert event.provider == "stake"
        assert event.start_time == "2026-04-15T15:00:00Z"
        assert len(event.markets) == 1
        assert event.markets[0]["type"] == "1x2"

    def test_basketball_moneyline(self):
        event = parse_fixture(FIXTURE_BASKETBALL, "basketball", "stake")
        assert event is not None
        assert event.home_team == "los angeles lakers"
        assert event.away_team == "boston celtics"
        assert len(event.markets) == 1
        assert event.markets[0]["type"] == "moneyline"

    def test_live_fixture_skipped(self):
        event = parse_fixture(FIXTURE_LIVE, "football", "stake")
        assert event is None

    def test_no_groups_skipped(self):
        fixture = {**FIXTURE_FOOTBALL, "groups": []}
        event = parse_fixture(fixture, "football", "stake")
        assert event is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/providers/test_stake_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.providers.stake'`

- [ ] **Step 3: Implement Stake.com parser and retriever**

Create `backend/src/providers/stake.py`:

```python
"""
Stake.com GraphQL Retriever

Signal-only provider — reads public odds via GraphQL API.
No authentication required for fixture/odds data.
"""
from typing import List, Optional, Any
import logging

from ..core import Retriever, StandardEvent, HttpTransport
from ..config import ConfigLoader
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)

# Stake sport slugs → canonical sport keys
SPORT_SLUG_MAP = {
    "football": "football",
    "basketball": "basketball",
    "ice-hockey": "ice_hockey",
    "american-football": "american_football",
    "tennis": "tennis",
    "baseball": "baseball",
    "mma": "mma",
    "boxing": "boxing",
    "esports": "esports",
}

# GraphQL query for fetching fixtures with odds by sport
FIXTURES_QUERY = """
query SportFixtures($sportSlug: String!, $limit: Int) {
  upcomingFixtures(sportSlug: $sportSlug, limit: $limit, status: [open]) {
    id
    name
    status
    startTime
    tournament {
      name
      category {
        sport { name slug }
      }
    }
    data {
      competitors {
        name
        extId
      }
    }
    groups(groupNames: ["Winner"]) {
      name
      templates {
        markets {
          outcomes {
            id
            name
            odds
            active
          }
        }
      }
    }
  }
}
"""

# Live statuses to skip
LIVE_STATUSES = {"in_progress", "ended", "cancelled", "suspended"}

# Draw-like outcome names
DRAW_NAMES = {"draw", "x", "tie"}


def parse_outcomes_to_market(
    outcomes: list, group_name: str, sport: str
) -> Optional[dict]:
    """Parse Stake outcomes list into a normalized market dict."""
    if not outcomes:
        return None

    active = [o for o in outcomes if o.get("active") and o.get("odds", 0) > 0]
    if len(active) < 2:
        return None

    group_lower = group_name.lower()

    if group_lower == "winner":
        has_draw = any(o["name"].lower() in DRAW_NAMES for o in active)

        if has_draw and len(active) == 3:
            # 1x2 (football-style: home, draw, away)
            home = next(o for o in active if o["name"].lower() not in DRAW_NAMES)
            draw = next(o for o in active if o["name"].lower() in DRAW_NAMES)
            away = next(
                o for o in active
                if o["name"].lower() not in DRAW_NAMES and o["id"] != home["id"]
            )
            return {
                "type": "1x2",
                "outcomes": [
                    {"name": "home", "odds": home["odds"]},
                    {"name": "draw", "odds": draw["odds"]},
                    {"name": "away", "odds": away["odds"]},
                ],
            }
        elif len(active) == 2:
            # Moneyline (basketball, tennis, etc.)
            return {
                "type": "moneyline",
                "outcomes": [
                    {"name": "home", "odds": active[0]["odds"]},
                    {"name": "away", "odds": active[1]["odds"]},
                ],
            }

    return None


def parse_fixture(
    fixture: dict, sport: str, provider_id: str
) -> Optional[StandardEvent]:
    """Parse a single Stake fixture into a StandardEvent."""
    status = fixture.get("status", "").lower()
    if status in LIVE_STATUSES:
        return None

    groups = fixture.get("groups") or []
    if not groups:
        return None

    # Extract competitors
    competitors = (fixture.get("data") or {}).get("competitors") or []
    if len(competitors) < 2:
        # Try parsing from fixture name "Home vs Away"
        name = fixture.get("name", "")
        parts = [p.strip() for p in name.split(" vs ")]
        if len(parts) < 2:
            parts = [p.strip() for p in name.split(" v ")]
        if len(parts) < 2:
            return None
        home_name, away_name = parts[0], parts[1]
    else:
        home_name = competitors[0].get("name", "")
        away_name = competitors[1].get("name", "")

    if not home_name or not away_name:
        return None

    # Parse markets from groups
    markets = []
    for group in groups:
        group_name = group.get("name", "")
        templates = group.get("templates") or []
        for template in templates:
            for market in template.get("markets") or []:
                outcomes = market.get("outcomes") or []
                parsed = parse_outcomes_to_market(outcomes, group_name, sport)
                if parsed:
                    markets.append(parsed)

    if not markets:
        return None

    tournament = fixture.get("tournament") or {}
    league = tournament.get("name", "")

    return StandardEvent(
        id=f"stake_{fixture.get('id', '')}",
        name=f"{home_name} vs {away_name}",
        sport=sport,
        markets=markets,
        provider=provider_id,
        start_time=fixture.get("startTime", ""),
        home_team=normalize_team_name(home_name),
        away_team=normalize_team_name(away_name),
        league=league,
    )


class StakeRetriever(Retriever):
    """
    Stake.com GraphQL Retriever — signal-only.

    Public GraphQL API at https://stake.com/_api/graphql
    No auth needed for odds data. Cloudflare protected — needs proper headers.
    """

    # Canonical sport → Stake slug
    SPORT_MAP = {
        "football": "football",
        "basketball": "basketball",
        "ice_hockey": "ice-hockey",
        "american_football": "american-football",
        "tennis": "tennis",
        "baseball": "baseball",
        "mma": "mma",
        "boxing": "boxing",
        "esports": "esports",
    }

    def __init__(self, config: dict, transport=None, circuit_breaker=None, rate_limit_config=None):
        if transport is None:
            transport = HttpTransport(
                circuit_breaker=circuit_breaker,
                rate_limit_config=rate_limit_config,
            )
        super().__init__(config, transport)
        self.api_url = config.get("api_base", "https://stake.com/_api/graphql")

    def _get_sport_url(self, sport: str) -> str:
        """Not used — we override extract() for GraphQL."""
        return ""

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """Not used — parsing happens in extract()."""
        return []

    async def extract(self, sport: str, limit: int = 200, **kwargs) -> List[StandardEvent]:
        """Fetch fixtures from Stake.com GraphQL API."""
        stake_slug = self.SPORT_MAP.get(sport)
        if not stake_slug:
            return []

        payload = {
            "query": FIXTURES_QUERY,
            "variables": {"sportSlug": stake_slug, "limit": limit},
        }

        headers = {
            "Content-Type": "application/json",
            "x-language": "en",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

        try:
            data = await self.transport.post(self.api_url, json=payload, headers=headers)
        except Exception as e:
            logger.warning(f"Stake.com API error for {sport}: {e}")
            return []

        if not data:
            return []

        fixtures = (data.get("data") or {}).get("upcomingFixtures") or []
        events = []
        for fixture in fixtures:
            event = parse_fixture(fixture, sport, self.provider_id)
            if event:
                events.append(event)

        logger.info(f"Stake.com {sport}: {len(events)} events from {len(fixtures)} fixtures")
        return events
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/providers/test_stake_parser.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/providers/stake.py backend/tests/providers/test_stake_parser.py
git commit -m "feat(providers): add Stake.com GraphQL retriever + parser tests"
```

---

### Task 2: Cloudbet REST Feed Parser (tests + implementation)

**Files:**
- Create: `backend/src/providers/cloudbet.py`
- Create: `backend/tests/providers/test_cloudbet_parser.py`

- [ ] **Step 1: Write failing tests for Cloudbet response parsing**

Create `backend/tests/providers/test_cloudbet_parser.py`:

```python
"""Tests for Cloudbet REST Feed API response parsing."""
import pytest
from src.providers.cloudbet import parse_event, parse_selections_to_market


SELECTIONS_MONEYLINE = [
    {"outcome": "home", "params": "", "price": 1.264, "probability": 0.76,
     "status": "SELECTION_ENABLED", "side": "BACK"},
    {"outcome": "away", "params": "", "price": 4.001, "probability": 0.24,
     "status": "SELECTION_ENABLED", "side": "BACK"},
]

SELECTIONS_1X2 = [
    {"outcome": "home", "params": "", "price": 2.80, "probability": 0.34,
     "status": "SELECTION_ENABLED", "side": "BACK"},
    {"outcome": "draw", "params": "", "price": 3.40, "probability": 0.28,
     "status": "SELECTION_ENABLED", "side": "BACK"},
    {"outcome": "away", "params": "", "price": 2.50, "probability": 0.38,
     "status": "SELECTION_ENABLED", "side": "BACK"},
]

SELECTIONS_HANDICAP = [
    {"outcome": "home", "params": "handicap=-1.5", "price": 2.10,
     "status": "SELECTION_ENABLED", "side": "BACK"},
    {"outcome": "away", "params": "handicap=-1.5", "price": 1.75,
     "status": "SELECTION_ENABLED", "side": "BACK"},
    {"outcome": "home", "params": "handicap=-2.5", "price": 3.00,
     "status": "SELECTION_ENABLED", "side": "BACK"},
    {"outcome": "away", "params": "handicap=-2.5", "price": 1.40,
     "status": "SELECTION_ENABLED", "side": "BACK"},
]

SELECTIONS_TOTALS = [
    {"outcome": "over", "params": "total=2.5", "price": 1.90,
     "status": "SELECTION_ENABLED", "side": "BACK"},
    {"outcome": "under", "params": "total=2.5", "price": 1.90,
     "status": "SELECTION_ENABLED", "side": "BACK"},
    {"outcome": "over", "params": "total=3.5", "price": 2.60,
     "status": "SELECTION_ENABLED", "side": "BACK"},
    {"outcome": "under", "params": "total=3.5", "price": 1.50,
     "status": "SELECTION_ENABLED", "side": "BACK"},
]

EVENT_FOOTBALL = {
    "id": 12345,
    "name": "Manchester City V Liverpool",
    "status": "TRADING",
    "startTime": "2026-04-15T15:00:00Z",
    "home": {"name": "Manchester City", "key": "c1-manchester-city"},
    "away": {"name": "Liverpool", "key": "c2-liverpool"},
    "markets": {
        "soccer.match_odds": {
            "submarkets": {
                "period=ft": {
                    "selections": SELECTIONS_1X2
                }
            }
        },
        "soccer.asian_handicap": {
            "submarkets": {
                "period=ft": {
                    "selections": SELECTIONS_HANDICAP
                }
            }
        },
        "soccer.total_goals": {
            "submarkets": {
                "period=ft": {
                    "selections": SELECTIONS_TOTALS
                }
            }
        },
    }
}

EVENT_LIVE = {
    "id": 99999,
    "name": "Team A V Team B",
    "status": "TRADING_LIVE",
    "startTime": "2026-04-15T20:00:00Z",
    "home": {"name": "Team A"},
    "away": {"name": "Team B"},
    "markets": {}
}


class TestParseSelectionsToMarket:
    def test_moneyline(self):
        result = parse_selections_to_market(SELECTIONS_MONEYLINE, "basketball.moneyline")
        assert result is not None
        assert result["type"] == "moneyline"
        assert len(result["outcomes"]) == 2
        assert result["outcomes"][0] == {"name": "home", "odds": 1.264}
        assert result["outcomes"][1] == {"name": "away", "odds": 4.001}

    def test_1x2(self):
        result = parse_selections_to_market(SELECTIONS_1X2, "soccer.match_odds")
        assert result is not None
        assert result["type"] == "1x2"
        assert len(result["outcomes"]) == 3
        assert result["outcomes"][0] == {"name": "home", "odds": 2.80}
        assert result["outcomes"][1] == {"name": "draw", "odds": 3.40}
        assert result["outcomes"][2] == {"name": "away", "odds": 2.50}

    def test_handicap_main_line(self):
        result = parse_selections_to_market(SELECTIONS_HANDICAP, "soccer.asian_handicap")
        assert result is not None
        assert result["type"] == "spread"
        assert len(result["outcomes"]) == 2
        # First handicap line only (main line)
        assert result["outcomes"][0]["point"] == -1.5
        assert result["outcomes"][0]["odds"] == 2.10

    def test_totals_main_line(self):
        result = parse_selections_to_market(SELECTIONS_TOTALS, "soccer.total_goals")
        assert result is not None
        assert result["type"] == "total"
        assert len(result["outcomes"]) == 2
        assert result["outcomes"][0]["name"] == "over"
        assert result["outcomes"][0]["point"] == 2.5
        assert result["outcomes"][0]["odds"] == 1.90

    def test_disabled_selections_skipped(self):
        disabled = [
            {"outcome": "home", "params": "", "price": 1.5,
             "status": "SELECTION_DISABLED", "side": "BACK"},
            {"outcome": "away", "params": "", "price": 2.5,
             "status": "SELECTION_ENABLED", "side": "BACK"},
        ]
        result = parse_selections_to_market(disabled, "basketball.moneyline")
        assert result is None

    def test_empty_selections(self):
        result = parse_selections_to_market([], "basketball.moneyline")
        assert result is None


class TestParseEvent:
    def test_football_all_markets(self):
        event = parse_event(EVENT_FOOTBALL, "football", "cloudbet")
        assert event is not None
        assert event.home_team == "manchester city"
        assert event.away_team == "liverpool"
        assert event.sport == "football"
        assert event.provider == "cloudbet"
        assert event.start_time == "2026-04-15T15:00:00Z"
        market_types = [m["type"] for m in event.markets]
        assert "1x2" in market_types
        assert "spread" in market_types
        assert "total" in market_types

    def test_live_event_skipped(self):
        event = parse_event(EVENT_LIVE, "football", "cloudbet")
        assert event is None

    def test_no_home_away_skipped(self):
        no_teams = {**EVENT_FOOTBALL, "home": None, "away": None}
        event = parse_event(no_teams, "football", "cloudbet")
        assert event is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/providers/test_cloudbet_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.providers.cloudbet'`

- [ ] **Step 3: Implement Cloudbet parser and retriever**

Create `backend/src/providers/cloudbet.py`:

```python
"""
Cloudbet REST Feed API Retriever

Signal-only provider — reads odds via Cloudbet Feed API.
Requires Affiliate API key (free, from affiliate account).
Feed data may be cached up to 1 minute behind.

API docs: https://cloudbet.github.io/wiki/en/docs/sports/api/
Base URL: https://sports-api.cloudbet.com/pub/v2/odds/
"""
from typing import List, Optional, Any
import logging
import re

from ..core import Retriever, StandardEvent, HttpTransport
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)

# Cloudbet sport keys → canonical
SPORT_KEY_MAP = {
    "soccer": "football",
    "basketball": "basketball",
    "ice-hockey": "ice_hockey",
    "american-football": "american_football",
    "tennis": "tennis",
    "baseball": "baseball",
    "mma": "mma",
    "boxing": "boxing",
    "esports": "esports",
}

# Canonical → Cloudbet sport key
REVERSE_SPORT_MAP = {v: k for k, v in SPORT_KEY_MAP.items()}

# Market key patterns → our market types
MARKET_TYPE_MAP = {
    "match_odds": "1x2",
    "matchOdds": "1x2",
    "moneyline": "moneyline",
    "asian_handicap": "spread",
    "asianHandicap": "spread",
    "handicap": "spread",
    "total_goals": "total",
    "totalGoals": "total",
    "totals": "total",
}

# Cloudbet market keys per sport for the competitions endpoint
SPORT_MARKET_KEYS = {
    "football": ["soccer.match_odds", "soccer.asian_handicap", "soccer.total_goals"],
    "basketball": ["basketball.moneyline", "basketball.handicap", "basketball.totals"],
    "ice_hockey": ["ice-hockey.moneyline", "ice-hockey.handicap", "ice-hockey.totals"],
    "american_football": ["american-football.moneyline", "american-football.handicap", "american-football.totals"],
    "tennis": ["tennis.moneyline"],
    "baseball": ["baseball.moneyline", "baseball.runLine", "baseball.totals"],
    "mma": ["mma.moneyline"],
    "boxing": ["boxing.moneyline"],
    "esports": ["esports.moneyline"],
}

# Live statuses to skip
LIVE_STATUSES = {"TRADING_LIVE", "RESULTED", "CANCELLED", "SUSPENDED"}


def _extract_market_subtype(market_key: str) -> Optional[str]:
    """Extract market type from Cloudbet market key like 'soccer.match_odds'."""
    parts = market_key.split(".")
    if len(parts) < 2:
        return None
    subtype = parts[-1]
    # Try both snake_case and camelCase
    return MARKET_TYPE_MAP.get(subtype)


def parse_selections_to_market(
    selections: list, market_key: str
) -> Optional[dict]:
    """Parse Cloudbet selections into a normalized market dict."""
    if not selections:
        return None

    enabled = [s for s in selections if s.get("status") == "SELECTION_ENABLED"]
    if len(enabled) < 2:
        return None

    market_type = _extract_market_subtype(market_key)
    if not market_type:
        return None

    if market_type == "1x2":
        home = next((s for s in enabled if s["outcome"] == "home"), None)
        draw = next((s for s in enabled if s["outcome"] == "draw"), None)
        away = next((s for s in enabled if s["outcome"] == "away"), None)
        if not (home and draw and away):
            return None
        return {
            "type": "1x2",
            "outcomes": [
                {"name": "home", "odds": home["price"]},
                {"name": "draw", "odds": draw["price"]},
                {"name": "away", "odds": away["price"]},
            ],
        }

    elif market_type == "moneyline":
        home = next((s for s in enabled if s["outcome"] == "home"), None)
        away = next((s for s in enabled if s["outcome"] == "away"), None)
        if not (home and away):
            return None
        return {
            "type": "moneyline",
            "outcomes": [
                {"name": "home", "odds": home["price"]},
                {"name": "away", "odds": away["price"]},
            ],
        }

    elif market_type == "spread":
        # Get main line only (first handicap value)
        handicap_values = set()
        for s in enabled:
            match = re.search(r"handicap=(-?[\d.]+)", s.get("params", ""))
            if match:
                handicap_values.add(float(match.group(1)))
        if not handicap_values:
            return None
        # Pick the main line (smallest absolute handicap)
        main_hcap = min(handicap_values, key=abs)
        main_sels = [
            s for s in enabled
            if f"handicap={main_hcap}" in s.get("params", "")
               or f"handicap=-{abs(main_hcap)}" in s.get("params", "")
        ]
        home_sel = next((s for s in main_sels if s["outcome"] == "home"), None)
        away_sel = next((s for s in main_sels if s["outcome"] == "away"), None)
        if not (home_sel and away_sel):
            return None
        return {
            "type": "spread",
            "outcomes": [
                {"name": "home", "odds": home_sel["price"], "point": main_hcap},
                {"name": "away", "odds": away_sel["price"], "point": -main_hcap},
            ],
        }

    elif market_type == "total":
        # Get main line only (first total value)
        total_values = set()
        for s in enabled:
            match = re.search(r"total=([\d.]+)", s.get("params", ""))
            if match:
                total_values.add(float(match.group(1)))
        if not total_values:
            return None
        main_total = min(total_values)
        main_sels = [
            s for s in enabled
            if f"total={main_total}" in s.get("params", "")
        ]
        over_sel = next((s for s in main_sels if s["outcome"] == "over"), None)
        under_sel = next((s for s in main_sels if s["outcome"] == "under"), None)
        if not (over_sel and under_sel):
            return None
        return {
            "type": "total",
            "outcomes": [
                {"name": "over", "odds": over_sel["price"], "point": main_total},
                {"name": "under", "odds": under_sel["price"], "point": main_total},
            ],
        }

    return None


def parse_event(
    event: dict, sport: str, provider_id: str
) -> Optional[StandardEvent]:
    """Parse a single Cloudbet event into a StandardEvent."""
    status = event.get("status", "")
    if status in LIVE_STATUSES:
        return None

    home_data = event.get("home")
    away_data = event.get("away")
    if not home_data or not away_data:
        return None

    home_name = home_data.get("name", "")
    away_name = away_data.get("name", "")
    if not home_name or not away_name:
        return None

    markets_data = event.get("markets") or {}
    markets = []
    for market_key, market_info in markets_data.items():
        submarkets = market_info.get("submarkets") or {}
        for submarket_key, submarket in submarkets.items():
            selections = submarket.get("selections") or []
            parsed = parse_selections_to_market(selections, market_key)
            if parsed:
                markets.append(parsed)
                break  # One submarket per market type (prefer full-time)

    if not markets:
        return None

    return StandardEvent(
        id=f"cloudbet_{event.get('id', '')}",
        name=f"{home_name} vs {away_name}",
        sport=sport,
        markets=markets,
        provider=provider_id,
        start_time=event.get("startTime", ""),
        home_team=normalize_team_name(home_name),
        away_team=normalize_team_name(away_name),
        league="",  # Available from competition context, not individual event
    )


class CloudbetRetriever(Retriever):
    """
    Cloudbet Feed API Retriever — signal-only.

    REST API at https://sports-api.cloudbet.com/pub/v2/odds/
    Requires Affiliate API key (free) in X-API-Key header.
    Feed data cached up to 1 minute behind.
    """

    def __init__(self, config: dict, transport=None, circuit_breaker=None, rate_limit_config=None):
        if transport is None:
            import os
            transport = HttpTransport(
                circuit_breaker=circuit_breaker,
                rate_limit_config=rate_limit_config,
            )
        super().__init__(config, transport)
        self.base_url = config.get("api_base", "https://sports-api.cloudbet.com/pub/v2/odds")
        self.api_key = config.get("api_key", "")

    def _get_sport_url(self, sport: str) -> str:
        """Not used — we override extract()."""
        return ""

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """Not used — parsing happens in extract()."""
        return []

    async def extract(self, sport: str, limit: int = 200, **kwargs) -> List[StandardEvent]:
        """Fetch odds from Cloudbet Feed API."""
        cloudbet_key = REVERSE_SPORT_MAP.get(sport)
        if not cloudbet_key:
            return []

        headers = {
            "accept": "application/json",
            "Content-Type": "application/json",
            "X-API-Key": self.api_key,
        }

        market_keys = SPORT_MARKET_KEYS.get(sport, [])

        # Step 1: Get competitions for this sport
        try:
            sport_data = await self.transport.get(
                f"{self.base_url}/sports/{cloudbet_key}",
                headers=headers,
            )
        except Exception as e:
            logger.warning(f"Cloudbet sport fetch error for {sport}: {e}")
            return []

        if not sport_data:
            return []

        # Collect competition keys
        comp_keys = []
        for category in sport_data.get("categories") or []:
            for comp in category.get("competitions") or []:
                if comp.get("eventCount", 0) > 0:
                    comp_keys.append(comp["key"])

        # Step 2: Fetch events per competition (limit to top 20 by event count)
        comp_keys = comp_keys[:20]
        events = []

        for comp_key in comp_keys:
            market_params = "&".join(f"markets={mk}" for mk in market_keys)
            url = f"{self.base_url}/competitions/{comp_key}?{market_params}"
            try:
                comp_data = await self.transport.get(url, headers=headers)
            except Exception as e:
                logger.debug(f"Cloudbet comp fetch error {comp_key}: {e}")
                continue

            if not comp_data:
                continue

            for event_data in comp_data.get("events") or []:
                event = parse_event(event_data, sport, self.provider_id)
                if event:
                    event.league = comp_data.get("name", "")
                    events.append(event)

        logger.info(f"Cloudbet {sport}: {len(events)} events from {len(comp_keys)} competitions")
        return events
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/providers/test_cloudbet_parser.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/providers/cloudbet.py backend/tests/providers/test_cloudbet_parser.py
git commit -m "feat(providers): add Cloudbet REST feed retriever + parser tests"
```

---

### Task 3: Register both providers in factory

**Files:**
- Modify: `backend/src/factory.py`

- [ ] **Step 1: Add Stake.com to factory**

In `backend/src/factory.py`, add import at the top with the other imports:

```python
from .providers.stake import StakeRetriever
from .providers.cloudbet import CloudbetRetriever
```

- [ ] **Step 2: Add elif blocks in `get_extractor()` method**

After the last `elif retriever_type ==` block (before any final else/error handling), add:

```python
        elif retriever_type == "stake":
            retriever = StakeRetriever(
                config,
                circuit_breaker=self._circuit_breaker,
                rate_limit_config=rate_limit_config,
            )
        elif retriever_type == "cloudbet":
            retriever = CloudbetRetriever(
                config,
                circuit_breaker=self._circuit_breaker,
                rate_limit_config=rate_limit_config,
            )
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/factory.py
git commit -m "feat(factory): register Stake.com + Cloudbet retrievers"
```

---

### Task 4: Configure providers in YAML

**Files:**
- Modify: `backend/src/config/providers.yaml`
- Modify: `backend/src/config/sports.yaml`

- [ ] **Step 1: Add provider entries to `providers.yaml`**

Add after the existing provider definitions (before the extraction tiers section):

```yaml
  stake:
    id: stake
    enabled: true
    retriever_type: stake
    api_base: https://stake.com/_api/graphql

  cloudbet:
    id: cloudbet
    enabled: true
    retriever_type: cloudbet
    api_base: https://sports-api.cloudbet.com/pub/v2/odds
    api_key: ${CLOUDBET_API_KEY}
```

- [ ] **Step 2: Add signal_international extraction tier to `providers.yaml`**

Add in the extraction tiers section:

```yaml
  signal_international:
    interval: 300  # 5 minutes
    providers:
      - stake
      - cloudbet
```

- [ ] **Step 3: Add provider group to orchestrator config**

In the `provider_groups` section:

```yaml
    - name: signal_api
      retriever_types: [stake, cloudbet]
      max_concurrent: 2
      shared_resource: none
```

- [ ] **Step 4: Add sport mappings to `sports.yaml`**

For each sport entry, add the Stake slug and Cloudbet key. Example for football:

```yaml
  football:
    name: Football
    aliases: [soccer, fotboll]
    pinnacle_id: 29
    kambi_sport: football
    stake_slug: football
    cloudbet_key: soccer
```

Repeat for all sports:
- `basketball`: stake_slug: `basketball`, cloudbet_key: `basketball`
- `ice_hockey`: stake_slug: `ice-hockey`, cloudbet_key: `ice-hockey`
- `american_football`: stake_slug: `american-football`, cloudbet_key: `american-football`
- `tennis`: stake_slug: `tennis`, cloudbet_key: `tennis`
- `baseball`: stake_slug: `baseball`, cloudbet_key: `baseball`
- `mma`: stake_slug: `mma`, cloudbet_key: `mma`
- `boxing`: stake_slug: `boxing`, cloudbet_key: `boxing`
- `esports`: stake_slug: `esports`, cloudbet_key: `esports`

- [ ] **Step 5: Commit**

```bash
git add backend/src/config/providers.yaml backend/src/config/sports.yaml
git commit -m "feat(config): add Stake.com + Cloudbet provider and sport config"
```

---

### Task 5: Add `transport.post()` method if missing

**Files:**
- Modify: `backend/src/core/transport.py` (if `post()` doesn't exist)

- [ ] **Step 1: Check if HttpTransport has a `post()` method**

Run: `grep -n "async def post" backend/src/core/transport.py`

If it exists, skip to Task 6.

- [ ] **Step 2: Add `post()` method to HttpTransport**

If missing, add to the `HttpTransport` class alongside the existing `get()` method:

```python
    async def post(self, url: str, json: dict = None, headers: dict = None, **kwargs):
        """POST request — used for GraphQL APIs."""
        session = await self._get_session()
        try:
            async with session.post(url, json=json, headers=headers, **kwargs) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    logger.warning(f"POST {url} returned {resp.status}")
                    return None
        except Exception as e:
            logger.warning(f"POST {url} error: {e}")
            return None
```

- [ ] **Step 3: Commit (if changes made)**

```bash
git add backend/src/core/transport.py
git commit -m "feat(transport): add POST method for GraphQL APIs"
```

---

### Task 6: Smoke test on server

**Files:** None — deployment verification only.

- [ ] **Step 1: Add CLOUDBET_API_KEY to server environment**

SSH to server and add to `.env.docker`:

```bash
ssh root@148.251.40.251 "echo 'CLOUDBET_API_KEY=your_affiliate_key_here' >> /opt/firev/.env.docker"
```

Note: You need to create a free Cloudbet affiliate account first to get the API key. Stake.com needs no key.

- [ ] **Step 2: Deploy to server**

```bash
ssh root@148.251.40.251 "cd /opt/firev && git pull && docker compose up -d --build backend"
```

- [ ] **Step 3: Test Stake.com extraction**

```bash
ssh root@148.251.40.251 "cd /opt/firev && docker compose exec -T backend python -c \"
import asyncio
from src.factory import ExtractorFactory
async def test():
    factory = ExtractorFactory.get_instance()
    ext = factory.get_extractor('stake')
    events = await ext.extract('football')
    print(f'Stake football: {len(events)} events')
    for e in events[:3]:
        print(f'  {e.name} | {e.markets[0]}')
    await ext.close()
asyncio.run(test())
\""
```

Expected: `Stake football: XX events` with real fixture data.

- [ ] **Step 4: Test Cloudbet extraction (once API key is set)**

```bash
ssh root@148.251.40.251 "cd /opt/firev && docker compose exec -T backend python -c \"
import asyncio
from src.factory import ExtractorFactory
async def test():
    factory = ExtractorFactory.get_instance()
    ext = factory.get_extractor('cloudbet')
    events = await ext.extract('football')
    print(f'Cloudbet football: {len(events)} events')
    for e in events[:3]:
        print(f'  {e.name} | {[m[\"type\"] for m in e.markets]}')
    await ext.close()
asyncio.run(test())
\""
```

Expected: `Cloudbet football: XX events` with 1x2/spread/total markets.

- [ ] **Step 5: Commit any hotfixes from smoke test**

```bash
git add -A && git commit -m "fix(providers): smoke test adjustments for Stake/Cloudbet"
```
