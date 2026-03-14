# Provider Improvements: Interwetten, 10bet, 888sport

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve three underperforming providers — interwetten (API rewrite for 13x speedup), 10bet (Pass 2 enrichment for football spread/total), 888sport (confirmed dead end, document limitation).

**Architecture:** Interwetten gets a full rewrite from browser DOM scraping to JSON API calls via `context.request.get()` with XHR header. 10bet gets Pass 2 event detail DOM enrichment (same pattern as interwetten/ComeOn). 888sport gets documented as API-limited (no fix possible without authenticated session).

**Tech Stack:** Python 3.10+ / asyncio / Playwright `context.request` API / StandardEvent dataclass

---

## Chunk 1: Interwetten JSON API Rewrite

The investigation discovered that adding `X-Requested-With: XMLHttpRequest` to any interwetten event URL returns full JSON with all markets (Asian Handicap, Over/Under, etc.). This eliminates both browser DOM scraping passes entirely.

### Task 1: Write JSON response parser tests

**Files:**
- Create: `backend/tests/providers/test_interwetten_api.py`

- [ ] **Step 1: Write tests for the JSON event detail parser**

The JSON response from `/en/sportsbook/e/{id}/{slug}` with XHR header has this structure:
```json
{
  "event": {
    "id": 18058921,
    "name": "Leverkusen - Bayern Munich",
    "startTime": "2026-03-14T14:30:00Z",
    "mainMarket": { "outcomes": [{"name": "Leverkusen", "tip": "1", "odd": 4.9}, {"name": "Draw", "tip": "X", "odd": 4.1}, {"name": "Bayern Munich", "tip": "2", "odd": 1.65}] },
    "templateGroups": [
      { "name": "Asian Handicaps", "templates": [
        { "name": "Asian Handicap", "id": "U18", "markets": [
          { "id": 419595148, "outcomes": [
            {"name": "Leverkusen (+1)", "tip": "1", "odd": 1.9},
            {"name": "Bayern Munich (-1)", "tip": "2", "odd": 1.83}
          ]}
        ]}
      ]},
      { "name": "Goals", "templates": [
        { "name": "How many goals", "id": "U5", "markets": [
          { "id": 419595154, "outcomes": [
            {"name": "Over 3.5", "tip": " ", "odd": 1.7},
            {"name": "Under 3.5", "tip": " ", "odd": 2.1}
          ]}
        ]}
      ]}
    ]
  },
  "league": {"id": 1019, "name": "Germany Bundesliga", "sportId": 10},
  "sport": {"id": 10, "name": "Football"}
}
```

```python
"""Tests for interwetten JSON API response parsing."""
import pytest
from src.providers.interwetten_api_parser import (
    parse_event_json,
    parse_spread_from_template,
    parse_total_from_template,
    parse_main_market,
)

# --- Main market (1x2/moneyline) ---

class TestParseMainMarket:
    def test_1x2_three_way(self):
        main = {"outcomes": [
            {"name": "Leverkusen", "tip": "1", "odd": 4.9},
            {"name": "Draw", "tip": "X", "odd": 4.1},
            {"name": "Bayern Munich", "tip": "2", "odd": 1.65},
        ]}
        result = parse_main_market(main, "Team A", "Team B")
        assert result["type"] == "1x2"
        assert len(result["outcomes"]) == 3
        assert result["outcomes"][0] == {"name": "home", "odds": 4.9}
        assert result["outcomes"][1] == {"name": "draw", "odds": 4.1}
        assert result["outcomes"][2] == {"name": "away", "odds": 1.65}

    def test_moneyline_two_way(self):
        main = {"outcomes": [
            {"name": "Lakers", "tip": "1", "odd": 1.8},
            {"name": "Celtics", "tip": "2", "odd": 2.0},
        ]}
        result = parse_main_market(main, "Lakers", "Celtics")
        assert result["type"] == "moneyline"
        assert len(result["outcomes"]) == 2

    def test_empty_outcomes(self):
        assert parse_main_market({"outcomes": []}, "A", "B") is None

    def test_locked_outcomes_skipped(self):
        main = {"outcomes": [
            {"name": "A", "tip": "1", "odd": 0},
            {"name": "B", "tip": "2", "odd": 1.5},
        ]}
        result = parse_main_market(main, "A", "B")
        # Only 1 valid outcome (odds > 1.0) → None
        assert result is None


# --- Spread (Asian Handicap) ---

class TestParseSpreadFromTemplate:
    def test_asian_handicap(self):
        template = {"name": "Asian Handicap", "markets": [{"outcomes": [
            {"name": "Leverkusen (+1)", "tip": "1", "odd": 1.9},
            {"name": "Bayern Munich (-1)", "tip": "2", "odd": 1.83},
        ]}]}
        result = parse_spread_from_template(template)
        assert result is not None
        assert result["type"] == "spread"
        assert len(result["outcomes"]) == 2
        assert result["outcomes"][0]["point"] == 1.0
        assert result["outcomes"][1]["point"] == -1.0

    def test_handicap_generic(self):
        template = {"name": "Handicap", "markets": [{"outcomes": [
            {"name": "Lakers (+5.5)", "tip": "1", "odd": 1.85},
            {"name": "Celtics (-5.5)", "tip": "2", "odd": 1.95},
        ]}]}
        result = parse_spread_from_template(template)
        assert result is not None
        assert result["outcomes"][0]["point"] == 5.5

    def test_no_point_in_name(self):
        template = {"name": "Asian Handicap", "markets": [{"outcomes": [
            {"name": "Team A", "tip": "1", "odd": 1.9},
            {"name": "Team B", "tip": "2", "odd": 1.83},
        ]}]}
        result = parse_spread_from_template(template)
        assert result is None  # Can't extract point → skip

    def test_empty_markets(self):
        template = {"name": "Asian Handicap", "markets": []}
        assert parse_spread_from_template(template) is None


# --- Total (Over/Under) ---

class TestParseTotalFromTemplate:
    def test_how_many_goals(self):
        template = {"name": "How many goals", "markets": [{"outcomes": [
            {"name": "Over 3.5", "tip": " ", "odd": 1.7},
            {"name": "Under 3.5", "tip": " ", "odd": 2.1},
        ]}]}
        result = parse_total_from_template(template)
        assert result is not None
        assert result["type"] == "total"
        assert result["outcomes"][0] == {"name": "over", "odds": 1.7, "point": 3.5}
        assert result["outcomes"][1] == {"name": "under", "odds": 2.1, "point": 3.5}

    def test_over_under_basketball(self):
        template = {"name": "Over/Under", "markets": [{"outcomes": [
            {"name": "Over 220.5", "tip": " ", "odd": 1.85},
            {"name": "Under 220.5", "tip": " ", "odd": 1.95},
        ]}]}
        result = parse_total_from_template(template)
        assert result is not None
        assert result["outcomes"][0]["point"] == 220.5

    def test_empty_markets(self):
        template = {"name": "How many goals", "markets": []}
        assert parse_total_from_template(template) is None


# --- Full event JSON parsing ---

class TestParseEventJson:
    def test_full_event_with_all_markets(self):
        data = {
            "event": {
                "id": 18058921,
                "name": "Leverkusen - Bayern Munich",
                "startTime": "2026-03-14T14:30:00Z",
                "mainMarket": {"outcomes": [
                    {"name": "Leverkusen", "tip": "1", "odd": 4.9},
                    {"name": "Draw", "tip": "X", "odd": 4.1},
                    {"name": "Bayern Munich", "tip": "2", "odd": 1.65},
                ]},
                "templateGroups": [
                    {"name": "Asian Handicaps", "templates": [
                        {"name": "Asian Handicap", "markets": [{"outcomes": [
                            {"name": "Leverkusen (+1)", "tip": "1", "odd": 1.9},
                            {"name": "Bayern Munich (-1)", "tip": "2", "odd": 1.83},
                        ]}]}
                    ]},
                    {"name": "Goals", "templates": [
                        {"name": "How many goals", "markets": [{"outcomes": [
                            {"name": "Over 2.5", "tip": " ", "odd": 1.7},
                            {"name": "Under 2.5", "tip": " ", "odd": 2.1},
                        ]}]}
                    ]},
                ],
            },
            "league": {"id": 1019, "name": "Germany Bundesliga"},
            "sport": {"id": 10, "name": "Football"},
        }
        event = parse_event_json(data, provider_id="interwetten")
        assert event is not None
        assert event.home_team is not None
        assert event.away_team is not None
        assert len(event.markets) == 3  # 1x2 + spread + total
        types = [m["type"] for m in event.markets]
        assert "1x2" in types
        assert "spread" in types
        assert "total" in types

    def test_event_without_template_groups(self):
        data = {
            "event": {
                "id": 123,
                "name": "A - B",
                "startTime": "2026-03-14T14:30:00Z",
                "mainMarket": {"outcomes": [
                    {"name": "A", "tip": "1", "odd": 1.5},
                    {"name": "B", "tip": "2", "odd": 2.5},
                ]},
            },
            "league": {"id": 1, "name": "Test"},
            "sport": {"id": 10, "name": "Football"},
        }
        event = parse_event_json(data, provider_id="interwetten")
        assert event is not None
        assert len(event.markets) == 1  # moneyline only
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/providers/test_interwetten_api.py -v`
Expected: FAIL — module `interwetten_api_parser` does not exist.

- [ ] **Step 3: Commit test file**


### Task 2: Implement JSON response parser

**Files:**
- Create: `backend/src/providers/interwetten_api_parser.py`

- [ ] **Step 1: Implement parser functions**

```python
"""
Interwetten JSON API Response Parser

Parses event detail JSON returned by interwetten when called with
X-Requested-With: XMLHttpRequest header. Extracts 1x2/moneyline,
spread (Asian Handicap), and total (How many goals / Over/Under).
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from ..core import StandardEvent
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)

# Template names that map to spread markets
SPREAD_TEMPLATE_NAMES = {"Asian Handicap", "Handicap", "Handicap Games"}
# Template names that map to total markets
TOTAL_TEMPLATE_NAMES = {"How many goals", "Over/Under", "How many games"}

_POINT_RE = re.compile(r"\(([+-]?\d+\.?\d*)\)")
_TOTAL_POINT_RE = re.compile(r"(\d+\.?\d*)")


def parse_main_market(
    main_market: dict, home_team: str, away_team: str
) -> Optional[dict]:
    """Parse mainMarket JSON into 1x2 or moneyline market dict."""
    outcomes_raw = main_market.get("outcomes", [])
    outcomes = []
    has_draw = False

    for o in outcomes_raw:
        odds = o.get("odd", 0)
        if not odds or odds <= 1.0:
            continue
        tip = o.get("tip", "")
        if tip == "1":
            outcomes.append({"name": "home", "odds": odds})
        elif tip == "X":
            outcomes.append({"name": "draw", "odds": odds})
            has_draw = True
        elif tip == "2":
            outcomes.append({"name": "away", "odds": odds})

    if len(outcomes) < 2:
        return None

    return {
        "type": "1x2" if has_draw else "moneyline",
        "outcomes": outcomes,
    }


def parse_spread_from_template(template: dict) -> Optional[dict]:
    """Parse an Asian Handicap / Handicap template into spread market."""
    markets = template.get("markets", [])
    if not markets:
        return None

    # Take first market (main line)
    first = markets[0]
    outcomes_raw = first.get("outcomes", [])
    outcomes = []
    has_point = False

    for o in outcomes_raw:
        odds = o.get("odd", 0)
        if not odds or odds <= 1.0:
            continue
        tip = o.get("tip", "")
        name = o.get("name", "")

        side = "home" if tip == "1" else "away" if tip == "2" else None
        if side is None:
            continue

        # Extract point from name: "Team (+1.5)" → 1.5
        m = _POINT_RE.search(name)
        if m:
            point = float(m.group(1))
            outcomes.append({"name": side, "odds": odds, "point": point})
            has_point = True
        else:
            outcomes.append({"name": side, "odds": odds})

    if len(outcomes) < 2 or not has_point:
        return None

    return {"type": "spread", "outcomes": outcomes}


def parse_total_from_template(template: dict) -> Optional[dict]:
    """Parse a How many goals / Over/Under template into total market."""
    markets = template.get("markets", [])
    if not markets:
        return None

    first = markets[0]
    outcomes_raw = first.get("outcomes", [])
    outcomes = []

    for o in outcomes_raw:
        odds = o.get("odd", 0)
        if not odds or odds <= 1.0:
            continue
        name = o.get("name", "").strip()
        name_lower = name.lower()

        if name_lower.startswith("over") or name_lower.startswith("över"):
            side = "over"
        elif name_lower.startswith("under"):
            side = "under"
        else:
            continue

        m = _TOTAL_POINT_RE.search(name)
        point = float(m.group(1)) if m else None
        outcome: dict = {"name": side, "odds": odds}
        if point is not None:
            outcome["point"] = point
        outcomes.append(outcome)

    if len(outcomes) < 2:
        return None

    return {"type": "total", "outcomes": outcomes}


def parse_event_json(
    data: dict, provider_id: str = "interwetten"
) -> Optional[StandardEvent]:
    """Parse a full interwetten event JSON response into StandardEvent."""
    event_data = data.get("event")
    if not event_data:
        return None

    event_id = event_data.get("id")
    event_name = event_data.get("name", "")
    start_time = event_data.get("startTime", "")

    # Parse team names from "Home - Away" format
    parts = event_name.split(" - ", 1)
    if len(parts) != 2:
        return None

    home_raw, away_raw = parts[0].strip(), parts[1].strip()
    home = normalize_team_name(home_raw)
    away = normalize_team_name(away_raw)

    league_data = data.get("league", {})
    league_name = league_data.get("name", "")

    sport_data = data.get("sport", {})
    sport_name = sport_data.get("name", "").lower()

    # Map interwetten sport names to our canonical names
    sport_map = {
        "football": "football", "basketball": "basketball",
        "ice hockey": "ice_hockey", "tennis": "tennis",
        "handball": "handball", "volleyball": "volleyball",
        "american football": "american_football",
        "baseball": "baseball", "rugby": "rugby",
        "cricket": "cricket", "darts": "darts", "boxing": "boxing",
    }
    sport = sport_map.get(sport_name, sport_name)

    # Parse markets
    markets = []

    # 1x2 / moneyline from mainMarket
    main = event_data.get("mainMarket")
    if main:
        mm = parse_main_market(main, home, away)
        if mm:
            markets.append(mm)

    # Spread + total from templateGroups
    for group in event_data.get("templateGroups", []):
        for template in group.get("templates", []):
            tname = template.get("name", "")
            if tname in SPREAD_TEMPLATE_NAMES:
                spread = parse_spread_from_template(template)
                if spread:
                    markets.append(spread)
                    break  # Only first spread template
        for template in group.get("templates", []):
            tname = template.get("name", "")
            if tname in TOTAL_TEMPLATE_NAMES:
                total = parse_total_from_template(template)
                if total:
                    markets.append(total)
                    break  # Only first total template

    if not markets:
        return None

    return StandardEvent(
        id=f"interwetten_{event_id}",
        name=f"{home_raw} vs {away_raw}",
        provider=provider_id,
        sport=sport,
        league=league_name,
        home_team=home,
        away_team=away,
        start_time=start_time,
        markets=markets,
    )
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/providers/test_interwetten_api.py -v`
Expected: All tests PASS.

- [ ] **Step 3: Commit**


### Task 3: Write top-leagues JSON parser tests

**Files:**
- Modify: `backend/tests/providers/test_interwetten_api.py`

- [ ] **Step 1: Add tests for top-leagues endpoint parsing**

The `/en/sportsbook/top-leagues?topLinkId={id}` endpoint returns:
```json
{
  "leagues": [
    {
      "id": 1021, "name": "England Premier League",
      "events": [
        {"id": 18058921, "name": "Arsenal - Everton", "startTime": "2026-03-15T15:00:00Z",
         "mainMarket": {"outcomes": [...]}, "marketCount": 45,
         "href": "/en/sportsbook/e/18058921/arsenal---everton"}
      ]
    }
  ]
}
```

```python
class TestParseTopLeaguesResponse:
    def test_extracts_event_ids_and_hrefs(self):
        from src.providers.interwetten_api_parser import parse_top_leagues_response
        data = {
            "leagues": [{
                "id": 1021, "name": "England Premier League",
                "events": [
                    {"id": 18058921, "name": "Arsenal - Everton",
                     "startTime": "2026-03-15T15:00:00Z",
                     "marketCount": 45,
                     "href": "/en/sportsbook/e/18058921/arsenal---everton",
                     "mainMarket": {"outcomes": [
                         {"name": "Arsenal", "tip": "1", "odd": 1.5},
                         {"name": "Draw", "tip": "X", "odd": 4.1},
                         {"name": "Everton", "tip": "2", "odd": 6.5},
                     ]}},
                ],
            }],
        }
        events_info = parse_top_leagues_response(data)
        assert len(events_info) == 1
        assert events_info[0]["id"] == 18058921
        assert events_info[0]["href"] == "/en/sportsbook/e/18058921/arsenal---everton"
        assert events_info[0]["league"] == "England Premier League"

    def test_empty_leagues(self):
        from src.providers.interwetten_api_parser import parse_top_leagues_response
        assert parse_top_leagues_response({"leagues": []}) == []
```

- [ ] **Step 2: Implement `parse_top_leagues_response`**

Add to `interwetten_api_parser.py`:
```python
def parse_top_leagues_response(data: dict) -> list[dict]:
    """Parse top-leagues JSON into list of event info dicts.

    Returns list of {id, href, league, name} for each event found.
    Used for league/event discovery before fetching full event details.
    """
    results = []
    for league in data.get("leagues", []):
        league_name = league.get("name", "")
        for ev in league.get("events", []):
            results.append({
                "id": ev.get("id"),
                "href": ev.get("href", ""),
                "league": league_name,
                "name": ev.get("name", ""),
            })
    return results
```

- [ ] **Step 3: Run tests and commit**

Run: `cd backend && python -m pytest tests/providers/test_interwetten_api.py -v`
Expected: All PASS.


### Task 4: Rewrite InterwettenRetriever to use JSON API

**Files:**
- Modify: `backend/src/providers/interwetten.py`

- [ ] **Step 1: Rewrite extract() to use JSON API instead of DOM scraping**

The new strategy:
1. Initialize browser session (one `page.goto()` to establish cookies)
2. Discover leagues via sport overview page DOM (`/en/sportsbook/o/{sportId}/{slug}`) — single page load, extract all `a[href*="/l/"]` links
3. For each league, fetch league page with XHR header → get event list with IDs and hrefs
4. For each event, fetch event detail with XHR header → get full JSON with all markets
5. Parse using `interwetten_api_parser.parse_event_json()`

Key changes:
- Remove `SPORT_LEAGUES` hardcoded dict entirely
- Remove `JS_EXTRACT_DETAIL_MARKETS` JS snippet
- Remove `_extract_league()` DOM parsing method
- Remove `_enrich_with_detail_markets()` Pass 2
- Remove `_parse_raw_event()`, `_parse_spread_market()`, `_parse_total_market()`
- Keep `_parse_datetime_str()` (still needed for league page datetime parsing)
- Add `_discover_leagues_from_overview()` — single page load to scrape league links
- Add `_fetch_event_json()` — `context.request.get()` with XHR header
- Replace concurrent browser tabs with concurrent HTTP requests (much faster)

New extract flow:
```python
async def extract(self, sport, limit=500, **kwargs):
    await self.transport._ensure_browser()
    page = self.transport.page
    # One page load to establish session
    await self._ensure_init(f"{self.base_url}/en/sportsbook", "sportsbook")

    # Discover leagues from sport overview (single page load)
    leagues = await self._discover_leagues_from_overview(page, sport)
    # Filter to target_leagues if provided
    target_leagues = kwargs.get("target_leagues")
    if target_leagues:
        leagues = self._filter_leagues(leagues, target_leagues)

    # Fetch each league page via XHR to get event IDs + hrefs
    event_hrefs = await self._collect_event_hrefs(leagues)

    # Fetch each event detail via XHR → full JSON → parse
    sem = asyncio.Semaphore(10)  # 10 concurrent HTTP requests
    events = []
    async def fetch_one(href):
        async with sem:
            return await self._fetch_event_json(href)
    tasks = [fetch_one(href) for href in event_hrefs]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, StandardEvent):
            events.append(r)
    return events[:limit]
```

Important implementation details:
- Access context via `self.transport.context` or `self.transport.page.context`
- `context.request.get(url, headers={"X-Requested-With": "XMLHttpRequest"})` returns JSON
- Wrap requests in `asyncio.wait_for(..., timeout=10)` to avoid hangs
- Sport IDs for overview pages: football=10, basketball=15, ice_hockey=40, tennis=11, handball=1002, etc.
- League page with XHR returns event list (not full detail) → need to follow up with event detail XHR
- League page WITHOUT XHR returns HTML (current behavior) → we use XHR to get JSON

Missing method bodies to implement:

```python
XHR_HEADERS = {"X-Requested-With": "XMLHttpRequest"}

async def _collect_event_hrefs(self, leagues: list[dict]) -> list[str]:
    """Fetch each league page via XHR to collect event hrefs."""
    context = self.transport.context
    sem = asyncio.Semaphore(10)
    all_hrefs = []

    async def fetch_league(league):
        async with sem:
            url = f"{self.base_url}/en/sportsbook/l/{league['id']}/{league['slug']}"
            try:
                resp = await asyncio.wait_for(
                    context.request.get(url, headers=self.XHR_HEADERS), timeout=10
                )
                if resp.status != 200:
                    return []
                data = await resp.json()
                # Extract event hrefs from league JSON response
                events = data.get("events", [])
                return [e.get("href", "") for e in events if e.get("href")]
            except Exception as e:
                logger.debug(f"[{self.provider_id}] League {league.get('slug')}: {e}")
                return []

    tasks = [fetch_league(lg) for lg in leagues]
    results = await asyncio.gather(*tasks)
    for hrefs in results:
        all_hrefs.extend(hrefs)
    # Deduplicate
    return list(dict.fromkeys(all_hrefs))

async def _fetch_event_json(self, href: str) -> Optional[StandardEvent]:
    """Fetch a single event detail via XHR and parse into StandardEvent."""
    context = self.transport.context
    url = f"{self.base_url}{href}"
    try:
        resp = await asyncio.wait_for(
            context.request.get(url, headers=self.XHR_HEADERS), timeout=10
        )
        if resp.status != 200:
            return None
        data = await resp.json()
        return parse_event_json(data, provider_id=self.provider_id)
    except Exception as e:
        logger.debug(f"[{self.provider_id}] Event {href}: {e}")
        return None
```

Also remove all dead code after rewrite: `SPORT_LEAGUES`, `OUTCOME_MAP`, `CONCURRENT_LEAGUE_PAGES`, `CONCURRENT_DETAIL_PAGES`, `MAX_DETAIL_EVENTS`, `DETAIL_SPORTS`, `SPREAD_LABELS`, `TOTAL_LABELS`, `JS_EXTRACT_DETAIL_MARKETS`, `_extract_league()`, `_enrich_with_detail_markets()`, `_parse_raw_event()`, `_parse_spread_market()`, `_parse_total_market()`.

- [ ] **Step 2: Run smoke test**

Run a quick test with football only against live site to verify the new approach works:
```bash
cd backend && python -c "
import asyncio, sys
sys.path.insert(0, '.')
async def test():
    from src.providers.interwetten import InterwettenRetriever
    r = InterwettenRetriever({'id': 'interwetten', 'site_url': 'https://www.interwetten.se'})
    events = await r.extract('football', target_leagues={'premier league', 'bundesliga', 'la liga'})
    print(f'Events: {len(events)}')
    for e in events[:5]:
        print(f'  {e.name} | {e.league} | markets={[m[\"type\"] for m in e.markets]}')
    await r.transport.close()
asyncio.run(test())
"
```
Expected: 50+ events with 1x2/spread/total markets, completing in <60s.

- [ ] **Step 3: Commit**


### Task 5: Add dynamic league discovery

**Files:**
- Modify: `backend/src/providers/interwetten.py`

- [ ] **Step 1: Implement `_discover_leagues_from_overview()`**

Navigate to `/en/sportsbook/o/{sportId}/{slug}`, scrape all `a[href*="/l/"]` links from the DOM. This replaces the hardcoded `SPORT_LEAGUES` dict.

```python
SPORT_OVERVIEW_MAP = {
    "football": (10, "football"),
    "ice_hockey": (40, "ice-hockey"),
    "basketball": (15, "basketball"),
    "tennis": (11, "tennis"),
    "handball": (1002, "handball"),
    "volleyball": (1012, "volleyball"),
    "rugby": (16, "rugby"),
    "cricket": (1027, "cricket"),
    "american_football": (13, "american-football"),
    "baseball": (14, "baseball"),
    "boxing": (90, "boxing"),
    "darts": (42, "darts"),
}

async def _discover_leagues_from_overview(self, page, sport):
    """Discover all league IDs from sport overview page (single page load)."""
    sport_info = self.SPORT_OVERVIEW_MAP.get(sport)
    if not sport_info:
        return []
    sport_id, slug = sport_info
    url = f"{self.base_url}/en/sportsbook/o/{sport_id}/{slug}"
    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
    await page.wait_for_selector('a[href*="/l/"]', timeout=5000)
    # Click "Show more" if present to reveal all leagues
    try:
        show_more = await page.query_selector('button:has-text("Show more")')
        if show_more:
            await show_more.click()
            await page.wait_for_timeout(500)
    except Exception:
        pass
    leagues = await page.evaluate("""() => {
        return Array.from(document.querySelectorAll('a[href*="/l/"]'))
            .map(a => {
                const m = a.href.match(/\\/l\\/(\\d+)\\/(.+?)(?:\\/|$|\\?)/);
                return m ? {id: parseInt(m[1]), slug: m[2], name: a.textContent.trim()} : null;
            })
            .filter(Boolean)
            .filter((v, i, a) => a.findIndex(x => x.id === v.id) === i);
    }""")
    return leagues or []
```

- [ ] **Step 2: Add `_filter_leagues()` for target_leagues matching**

Same pattern as ComeOn — substring match against Pinnacle target_leagues set.

- [ ] **Step 3: Run full smoke test with dynamic discovery and commit**


### Task 6: Update providers.yaml configuration

**Files:**
- Modify: `backend/src/config/providers.yaml`

- [ ] **Step 1: Reduce interwetten timeouts**

Since extraction is now HTTP-based (not browser DOM scraping), reduce timeouts:
```yaml
interwetten:
  provider_timeout: 300   # Was 900 — API calls are 10x faster than DOM
  sport_timeout: 120      # Was 420 — no concurrent browser tabs needed
```

- [ ] **Step 2: Commit**


---

## Chunk 2: 10bet Pass 2 Event Detail Enrichment

The investigation confirmed that football spread/total markets exist on 10bet event detail pages but are not in the competition listing DOM. Add Pass 2 enrichment (navigate to event detail pages, scrape Asian Handicap + Asian Total).

### Task 7: Write Pass 2 enrichment tests

**Files:**
- Create: `backend/tests/providers/test_tenbet_enrichment.py`

- [ ] **Step 1: Write tests for event detail market parsing**

The event detail page has markets in `ta-AggregatedMarket` containers. Asian Handicap has outcomes like `"Arsenal -1.5"` with odds. Asian Total has `"Over 2.5"` / `"Under 2.5"`.

The JS extraction snippet on the event detail page will output dicts matching this structure (from `page.evaluate()`). Tests match this output format.

```python
"""Tests for 10bet event detail market parsing."""
import pytest
from src.providers.tenbet import TenBetRetriever


class TestParseDetailSpread:
    def test_parse_asian_handicap(self):
        """JS extraction returns: name (team), point (handicap value), odds."""
        retriever = TenBetRetriever({"id": "10bet", "site_url": "https://www.10bet.se"})
        # Raw format matches JS_EXTRACT_DETAIL_MARKETS output
        raw = {
            "spread": {
                "outcomes": [
                    {"name": "Arsenal", "point": "-1.5", "odds": "2.20"},
                    {"name": "Everton", "point": "+1.5", "odds": "1.67"},
                ],
            },
        }
        result = retriever._parse_detail_spread(raw["spread"])
        assert result is not None
        assert result["type"] == "spread"
        assert len(result["outcomes"]) == 2
        assert result["outcomes"][0]["point"] == -1.5
        assert result["outcomes"][1]["point"] == 1.5

    def test_no_outcomes(self):
        retriever = TenBetRetriever({"id": "10bet", "site_url": "https://www.10bet.se"})
        assert retriever._parse_detail_spread({"outcomes": []}) is None


class TestParseDetailTotal:
    def test_parse_asian_total(self):
        retriever = TenBetRetriever({"id": "10bet", "site_url": "https://www.10bet.se"})
        raw = {
            "total": {
                "outcomes": [
                    {"name": "Over 2.5", "odds": "1.95"},
                    {"name": "Under 2.5", "odds": "1.83"},
                ],
            },
        }
        result = retriever._parse_detail_total(raw["total"])
        assert result is not None
        assert result["type"] == "total"
        assert result["outcomes"][0]["point"] == 2.5

    def test_no_outcomes(self):
        retriever = TenBetRetriever({"id": "10bet", "site_url": "https://www.10bet.se"})
        assert retriever._parse_detail_total({"outcomes": []}) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/providers/test_tenbet_enrichment.py -v`
Expected: FAIL — methods don't exist yet.

- [ ] **Step 3: Commit test file**


### Task 8: Implement Pass 2 event detail enrichment for 10bet

**Files:**
- Modify: `backend/src/providers/tenbet.py`

- [ ] **Step 1: Add JS snippet to extract spread/total from event detail page**

The event detail page has expandable market sections. The key selectors:
- `[class*="ta-AggregatedMarket"]` — market container
- `[class*="ta-MarketName-AsianHandicap"]` — Asian Handicap section (spread)
- `[class*="ta-MarketName-AsianTotal"]` or `[class*="ta-MarketName-ÖverUnder"]` — Asian Total / O/U section
- `[class*="ta-infoTextHandicap"]` — handicap point values
- `[class*="ta-price_text"]` — odds values
- `[class*="ta-participantName"]` — team names in outcomes

Add `JS_EXTRACT_DETAIL_MARKETS` constant and `_enrich_events_with_details()` method:

```python
JS_EXTRACT_DETAIL_MARKETS = """() => {
    const result = {spread: null, total: null};
    // Asian Handicap (spread)
    const ahEl = document.querySelector('[class*="ta-MarketName-AsianHandicap"]');
    if (ahEl) {
        const outcomes = [];
        ahEl.querySelectorAll('[class*="ta-selection"]').forEach(sel => {
            const name = sel.querySelector('[class*="ta-participantName"]');
            const price = sel.querySelector('[class*="ta-price_text"]');
            const info = sel.querySelector('[class*="ta-infoText"]');
            if (name && price) {
                outcomes.push({
                    name: name.textContent.trim(),
                    point: info ? info.textContent.trim() : '',
                    odds: price.textContent.trim()
                });
            }
        });
        if (outcomes.length >= 2) result.spread = {outcomes};
    }
    // Over/Under total
    const ouEl = document.querySelector(
        '[class*="ta-MarketName-ÖverUnder"], [class*="ta-MarketName-AsianTotal"]'
    );
    if (ouEl) {
        const outcomes = [];
        ouEl.querySelectorAll('[class*="ta-selection"]').forEach(sel => {
            const name = sel.querySelector('[class*="ta-participantName"], [class*="ta-label"]');
            const price = sel.querySelector('[class*="ta-price_text"]');
            if (name && price) {
                outcomes.push({
                    name: name.textContent.trim(),
                    odds: price.textContent.trim()
                });
            }
        });
        if (outcomes.length >= 2) result.total = {outcomes};
    }
    return result;
}"""
```

`_enrich_events_with_details()` uses a **page pool** pattern (same as interwetten's `_enrich_with_detail_markets`):
1. Create page pool with `asyncio.Queue` — open 4 extra pages via `context.new_page()`
2. Use `asyncio.Semaphore(4)` to throttle concurrent navigations
3. For each event: get page from pool → navigate to `/sports/{sport}/events/{eventId}` → evaluate JS → put page back
4. Cap at 150 events to avoid timeout
5. Close extra pages in `finally` block
6. Parse spread/total from JS output using `_parse_detail_spread()` and `_parse_detail_total()`

- [ ] **Step 2: Call enrichment at end of extract()**

After the existing competition scraping loop, add:
```python
# Pass 2: Enrich with event detail spread/total
if all_events:
    enriched = await self._enrich_events_with_details(all_events, sport)
    logger.info(f"[{self.provider_id}] {sport}: enriched {enriched}/{len(all_events)} with spread/total")
```

- [ ] **Step 3: Run tests and commit**


### Task 9: Smoke test 10bet enrichment

**Files:** None (manual verification)

- [ ] **Step 1: Run smoke test with football**

```bash
cd backend && python -c "
import asyncio, sys
sys.path.insert(0, '.')
async def test():
    from src.providers.tenbet import TenBetRetriever
    from src.core import BrowserTransport
    t = BrowserTransport(headless=True)
    r = TenBetRetriever({'id': '10bet', 'site_url': 'https://www.10bet.se'}, t)
    events = await r.extract('football')
    spread = sum(1 for e in events for m in e.markets if m['type'] == 'spread')
    total = sum(1 for e in events for m in e.markets if m['type'] == 'total')
    print(f'Events: {len(events)}, Spread: {spread}, Total: {total}')
    for e in events[:5]:
        print(f'  {e.name} | markets={[m[\"type\"] for m in e.markets]}')
    await t.close()
asyncio.run(test())
"
```
Expected: Football spread count > 0 (was always 0 before). Total count should remain similar or improve.

- [ ] **Step 2: Commit any fixes**


---

## Chunk 3: 888sport Documentation + Cleanup

### Task 10: Document 888sport API limitation

**Files:**
- Modify: `backend/src/providers/spectate.py` (add docstring note)
- Modify: `backend/src/config/providers.yaml` (update 888sport note)

- [ ] **Step 1: Add platform limitation documentation**

In `spectate.py`, update the class docstring:
```python
"""
Retriever for 888sport / Spectate based sites.
Uses BrowserTransport to bypass protections.

API limitation (confirmed 2026-03-14): The Spectate bulk API
(getUpcomingEvents) only returns 1x2/moneyline for football,
tennis, handball, MMA, esports, volleyball, rugby. Spread + total
markets are only available for basketball, ice_hockey, and baseball.
No event detail API exists — the SPA uses authenticated /load/state
which requires BankID login. This is a platform-level limitation,
not a parsing issue.
"""
```

In `providers.yaml`, update the 888sport comment:
```yaml
888sport:
  # ... existing config ...
  # NOTE (2026-03-14): Spectate bulk API only returns 1x2 for football/tennis/handball/etc.
  # Spread+total only available for basketball, ice_hockey, baseball.
  # No event detail API exists. This is a confirmed platform limitation.
```

- [ ] **Step 2: Commit**


### Task 11: End-to-end verification

- [ ] **Step 1: Run full extraction with all three providers**

Start the backend and trigger a browser_soft tier extraction. Verify:
- Interwetten: <120s extraction, 700+ events, spread+total coverage on football
- 10bet: Football spread count > 0 (was always 0)
- 888sport: No regression (same event count, same market coverage)

- [ ] **Step 2: Commit any final fixes**
