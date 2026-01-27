# Provider Implementation Guide

Complete step-by-step workflow for implementing a new sports betting provider from research to production.

---

## Table of Contents

1. [Overview](#overview)
2. [Phase 1: Research & Discovery](#phase-1-research--discovery)
3. [Phase 2: Implementation](#phase-2-implementation)
4. [Phase 3: Configuration](#phase-3-configuration)
5. [Phase 4: Testing](#phase-4-testing)
6. [Phase 5: Validation](#phase-5-validation)
7. [Phase 6: Production Deployment](#phase-6-production-deployment)
8. [Debugging Guide](#debugging-guide)
9. [Common Pitfalls](#common-pitfalls)
10. [Reference Examples](#reference-examples)

---

## Overview

### What This Guide Covers

This guide walks you through the complete process of adding a new betting provider to OddOpp:

- How to research and analyze a betting site
- How to choose the right implementation approach
- How to write provider code following established patterns
- How to configure and test your implementation
- How to validate production readiness
- How to debug common issues

### Prerequisites

Before starting:
- Python 3.10+ installed
- Backend dependencies installed: `pip install -e ".[dev]"`
- Playwright installed (for browser-based providers): `pip install -e ".[scrape]"`
- Familiarity with async/await patterns
- Browser DevTools experience (Network tab)

### Time Estimates

- Research Phase: 1-3 hours
- Implementation Phase: 2-6 hours
- Testing Phase: 1-2 hours
- Validation Phase: 1 hour
- Total: ~5-12 hours depending on complexity

---

## Phase 1: Research & Discovery

### Goal
Understand the betting site's data structure and choose the optimal extraction method.

### Step 1.1: Initial Site Analysis

**Open the betting site in your browser:**

1. Navigate to the main sports betting page
2. Open DevTools (F12)
3. Go to Network tab
4. Filter by "Fetch/XHR" or "WS" (WebSocket)
5. Navigate through the site (click sports, leagues, events)

**Questions to answer:**

- [ ] Is the site using a REST API?
- [ ] Is it using WebSocket/RSocket for real-time data?
- [ ] Is content server-side rendered or client-side (check View Source)?
- [ ] Does the site have bot detection (Cloudflare, Imperva, etc.)?
- [ ] Are there any authentication requirements?

### Step 1.2: Identify Data Sources

**Method A: REST API (Easiest)**

Look for API calls like:
```
GET https://api.provider.com/sports/football/events
GET https://api.provider.com/v2/leagues/{id}/fixtures
```

**Indicators:**
- JSON responses in Network tab
- Predictable URL patterns
- No complex authentication

**Method B: GraphQL**

Look for:
```
POST https://api.provider.com/graphql
Content-Type: application/json

{"query": "{ events(sportId: 1) { ... } }"}
```

**Method C: WebSocket/RSocket**

Look for:
```
WS wss://api.provider.com/socket
Messages: Binary frames or JSON
```

**Indicators:**
- "WS" type in Network tab
- Real-time updates
- Binary data (RSocket)

**Method D: Browser API Interception**

If the site loads a page and THEN fetches data via XHR:
```
1. Page loads: https://provider.com/sports/football
2. Browser fetches: https://api.provider.com/events (JSON)
3. JavaScript renders the DOM
```

**Method E: DOM Scraping (Last Resort)**

Only if:
- No API calls visible
- Server-side rendered HTML
- Data embedded in HTML elements

### Step 1.3: Map Sports and Leagues

**Create a mapping table:**

| Sport | Provider ID | Provider Name | OddOpp Name |
|-------|-------------|---------------|-------------|
| Football | 1 | "fotboll" | "football" |
| Basketball | 2 | "basket" | "basketball" |
| Tennis | 5 | "tennis" | "tennis" |

**Document sport URLs:**
```
Football: /sports/1-fotboll
Basketball: /sports/2-basket
API format: /api/sports/{id}/events
```

### Step 1.4: Identify Market Types

**Find available markets:**

- Moneyline (home/away)
- 1x2 (home/draw/away)
- Over/Under (totals)
- Spread (handicap)
- Player props
- Corners, cards, etc.

**Document market IDs:**
```json
{
  "1": "1x2",
  "2": "over_under",
  "3": "spread",
  "18": "both_teams_to_score"
}
```

### Step 1.5: Choose Implementation Strategy

Based on your findings:

| Data Source | Retriever Type | Base Class | Transport | Difficulty |
|-------------|----------------|------------|-----------|------------|
| REST API | `custom` or new type | `Retriever` | `HttpTransport` | Easy |
| REST API (shared platform) | Reuse existing | Existing (e.g., `KambiRetriever`) | `HttpTransport` | Very Easy |
| Browser + API Interception | `custom` | `BrowserRetriever` | `BrowserTransport` | Medium |
| WebSocket/RSocket | `custom` | `BrowserRetriever` | `BrowserTransport` | Medium-Hard |
| DOM Scraping | `custom` | `BrowserRetriever` | `BrowserTransport` | Hard |

**Decision Tree:**

```
Is it a known platform (Kambi, SBTech, Altenar)?
├─ YES → Reuse existing retriever (just config)
└─ NO → Does it have a public REST API?
    ├─ YES → Create new Retriever (extends Retriever)
    └─ NO → Does it need browser automation?
        ├─ YES → Create BrowserRetriever
        └─ NO → Consider if provider is worth implementing
```

### Step 1.6: Document Your Findings

Create a research document: `backend/docs/{PROVIDER}_RESEARCH.md`

**Template:**

```markdown
# {Provider Name} Research

## Overview
- Provider: {Name}
- Domain: {URL}
- License: {Country}
- Platform: {Kambi/SBTech/Custom/etc.}

## Data Source Analysis
- Type: REST API / WebSocket / DOM Scraping
- Base URL: {URL}
- Authentication: None / API Key / Session Cookie

## Sport Mapping
| Sport | Provider ID | Endpoint |
|-------|-------------|----------|
| ... | ... | ... |

## Market Type Mapping
| Market | Provider ID | Provider Name |
|--------|-------------|---------------|
| ... | ... | ... |

## Implementation Plan
- Retriever Type: {Retriever/BrowserRetriever}
- Estimated Difficulty: {Easy/Medium/Hard}
- Special Requirements: {Proxy/Authentication/etc.}

## Sample API Response
```json
{...}
```
```

---

## Phase 2: Implementation

### Goal
Create a working provider class that extracts events and normalizes them to `StandardEvent`.

### Step 2.1: Choose Your Base Class

**Option A: Pure API Provider (extends `Retriever`)**

Use when:
- REST API available
- No JavaScript rendering needed
- No bot detection

**Template:**

```python
# backend/src/providers/{provider_id}.py

from typing import List, Optional
import logging
from ..core import Retriever, StandardEvent
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)

class MyProviderRetriever(Retriever):
    """
    Retriever for {Provider Name}

    Platform: {Platform type}
    API: {API URL}
    Sports: {Supported sports}
    """

    # Sport mapping (provider ID -> OddOpp sport name)
    SPORT_MAPPING = {
        1: 'football',
        2: 'basketball',
        5: 'tennis',
    }

    # Market type mapping
    MARKET_TYPE_MAPPING = {
        1: '1x2',
        2: 'over_under',
        3: 'spread',
    }

    def __init__(self, config: dict, transport=None):
        super().__init__(config, transport)
        self.api_base = config.get('api_base')
        self.integration_id = config.get('integration_id')

    def _get_sport_url(self, sport: str) -> str:
        """Convert sport name to API endpoint."""
        sport_id = self._get_sport_id(sport)
        return f"{self.api_base}/sports/{sport_id}/events"

    def _get_sport_id(self, sport: str) -> int:
        """Get provider sport ID from OddOpp sport name."""
        for sport_id, sport_name in self.SPORT_MAPPING.items():
            if sport_name == sport:
                return sport_id
        raise ValueError(f"Sport '{sport}' not supported")

    async def extract(self, sport: str, limit: int = 100) -> List[StandardEvent]:
        """
        Extract events for a sport.

        Args:
            sport: OddOpp sport name (football, basketball, etc.)
            limit: Maximum events to return

        Returns:
            List of StandardEvent objects
        """
        try:
            # Get sport ID
            sport_id = self._get_sport_id(sport)

            # Fetch events from API
            url = f"{self.api_base}/events?sportId={sport_id}"
            async with self.transport.session.get(url) as response:
                if response.status != 200:
                    logger.warning(f"API returned {response.status} for {sport}")
                    return []

                data = await response.json()

            # Parse events
            events = self.parse(data, sport)

            # Apply limit
            return events[:limit] if limit else events

        except Exception as e:
            logger.error(f"Failed to extract {sport}: {e}")
            return []

    def parse(self, data: dict, sport: str) -> List[StandardEvent]:
        """
        Parse API response to StandardEvent list.

        Args:
            data: Raw API response (JSON dict)
            sport: Sport name for filtering

        Returns:
            List of StandardEvent objects
        """
        events = []

        for raw_event in data.get('events', []):
            try:
                # Extract basic event info
                home_team = raw_event['competitors'][0]['name']
                away_team = raw_event['competitors'][1]['name']

                # Normalize team names
                home_normalized = normalize_team_name(home_team)
                away_normalized = normalize_team_name(away_team)

                # Parse markets
                markets = self._parse_markets(raw_event.get('markets', []))

                # Skip events without markets
                if not markets:
                    continue

                event = StandardEvent(
                    id=str(raw_event['id']),
                    name=f"{home_normalized} vs {away_normalized}",
                    sport=sport,
                    home_team=home_normalized,
                    away_team=away_normalized,
                    start_time=raw_event.get('startTime'),
                    league=raw_event.get('league', {}).get('name'),
                    markets=markets,
                    provider=self.config['id'],
                    url=None
                )

                events.append(event)

            except (KeyError, IndexError) as e:
                logger.debug(f"Failed to parse event: {e}")
                continue

        return events

    def _parse_markets(self, raw_markets: List[dict]) -> List[dict]:
        """Parse market data with type mapping and normalization."""
        markets = []

        for raw_market in raw_markets:
            try:
                # Map market type
                market_type_id = raw_market['typeId']
                market_type = self.MARKET_TYPE_MAPPING.get(market_type_id)

                if not market_type:
                    # Skip unknown market types
                    continue

                # Parse outcomes
                outcomes = []
                for raw_outcome in raw_market.get('outcomes', []):
                    outcome_name = self._standardize_outcome(
                        raw_outcome['name'],
                        market_type
                    )

                    outcomes.append({
                        'name': outcome_name,
                        'odds': float(raw_outcome['odds'])
                    })

                if not outcomes:
                    continue

                market = {
                    'type': market_type,
                    'outcomes': outcomes
                }

                # Add point for spreads/totals
                if market_type in ['over_under', 'spread']:
                    point = self._extract_point(raw_market)
                    if point is not None:
                        market['point'] = point

                markets.append(market)

            except (KeyError, ValueError) as e:
                logger.debug(f"Failed to parse market: {e}")
                continue

        return markets

    def _standardize_outcome(self, raw_outcome: str, market_type: str) -> str:
        """Standardize outcome names to OddOpp format."""
        # Lowercase for comparison
        outcome_lower = raw_outcome.lower()

        # 1x2 outcomes
        if market_type == '1x2':
            if 'home' in outcome_lower or outcome_lower == '1':
                return 'home'
            elif 'draw' in outcome_lower or outcome_lower == 'x':
                return 'draw'
            elif 'away' in outcome_lower or outcome_lower == '2':
                return 'away'

        # Moneyline outcomes
        elif market_type == 'moneyline':
            if 'home' in outcome_lower or outcome_lower == '1':
                return 'home'
            elif 'away' in outcome_lower or outcome_lower == '2':
                return 'away'

        # Over/Under outcomes
        elif market_type == 'over_under':
            if 'over' in outcome_lower or 'över' in outcome_lower:
                return 'over'
            elif 'under' in outcome_lower:
                return 'under'

        # Spread outcomes
        elif market_type == 'spread':
            if 'home' in outcome_lower or outcome_lower == '1':
                return 'home'
            elif 'away' in outcome_lower or outcome_lower == '2':
                return 'away'

        # Fallback: return as-is (will be logged as unexpected)
        logger.debug(f"Unstandardized outcome: {raw_outcome} for {market_type}")
        return raw_outcome

    def _extract_point(self, raw_market: dict) -> Optional[float]:
        """Extract point value from market (for spreads/totals)."""
        # Try direct field first
        if 'point' in raw_market:
            return float(raw_market['point'])

        # Try parsing from market name
        import re
        market_name = raw_market.get('name', '')
        match = re.search(r'([-+]?\d+\.?\d*)', market_name)
        if match:
            return float(match.group(1))

        return None
```

**Option B: Browser-Based Provider (extends `BrowserRetriever`)**

Use when:
- Need to load JavaScript
- Need to intercept API calls
- Need to handle WebSocket/RSocket

**Template:**

```python
# backend/src/providers/{provider_id}.py

from typing import List
import logging
import json
from ..core import BrowserRetriever, StandardEvent
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)

class MyBrowserProviderRetriever(BrowserRetriever):
    """
    Browser-based retriever for {Provider Name}
    Uses API interception to capture event data.
    """

    SPORT_SLUGS = {
        'football': '1-fotboll',
        'basketball': '2-basket',
        'tennis': '5-tennis',
    }

    # API patterns to intercept
    API_PATTERNS = [
        '**/api/events**',
        '**/api/v2/sports/**',
    ]

    def __init__(self, config: dict, transport):
        super().__init__(config, transport)
        self.base_url = config.get('base_url')
        self._api_responses = []

    def _get_sport_url(self, sport: str) -> str:
        """Get URL for sport page."""
        slug = self.SPORT_SLUGS.get(sport)
        if not slug:
            raise ValueError(f"Sport '{sport}' not supported")
        return f"{self.base_url}/sports/{slug}"

    async def extract(self, sport: str, limit: int = 100) -> List[StandardEvent]:
        """Extract events using browser automation."""
        try:
            # Ensure browser is initialized
            await self._ensure_init(self._get_sport_url(sport))

            # Navigate to sport page
            sport_url = self._get_sport_url(sport)
            page = self.transport.page

            # Setup API response interception
            self._api_responses.clear()

            async def handle_response(response):
                if any(pattern.replace('**', '') in response.url
                       for pattern in self.API_PATTERNS):
                    try:
                        data = await response.json()
                        self._api_responses.append(data)
                    except:
                        pass

            page.on('response', handle_response)

            # Navigate and wait for API calls
            await page.goto(sport_url, wait_until='networkidle')
            await page.wait_for_timeout(2000)  # Wait for API calls

            # Parse captured responses
            events = []
            for response_data in self._api_responses:
                events.extend(self.parse(response_data, sport))

            return events[:limit] if limit else events

        except Exception as e:
            logger.error(f"Failed to extract {sport}: {e}")
            return []

    def parse(self, data: dict, sport: str) -> List[StandardEvent]:
        """Parse API response to StandardEvent list."""
        # Similar to Option A parse method
        pass
```

### Step 2.2: Implement Team Name Normalization

**Always use `normalize_team_name()` for team names:**

```python
from ..matching.normalizer import normalize_team_name

# In your parse method:
home_normalized = normalize_team_name(raw_event['home_team'])
away_normalized = normalize_team_name(raw_event['away_team'])
```

**What normalization does:**
- Converts to lowercase
- Removes accents/diacritics (é → e)
- Removes club suffixes (FC, SC, IF, BK, etc.)
- Removes club prefixes (Real, Sporting, Club)
- Removes age indicators (U21, U19, B, II)
- Applies aliases from `backend/src/matching/aliases.yaml`

### Step 2.3: Implement Market Type Standardization

**Required standard market types:**

```python
STANDARD_MARKET_TYPES = {
    '1x2',           # Three-way: home/draw/away
    'moneyline',     # Two-way: home/away
    'over_under',    # Totals: over/under + point
    'spread',        # Handicap: home/away + point
}

STANDARD_OUTCOMES = {
    '1x2': ['home', 'draw', 'away'],
    'moneyline': ['home', 'away'],
    'over_under': ['over', 'under'],
    'spread': ['home', 'away'],
}
```

**Always standardize outcomes:**

```python
def _standardize_outcome(self, raw_outcome: str, market_type: str) -> str:
    """Map provider-specific outcome names to standard names."""
    outcome_lower = raw_outcome.lower()

    # Handle common variations
    OUTCOME_MAP = {
        '1': 'home',
        '2': 'away',
        'x': 'draw',
        'hemma': 'home',
        'borta': 'away',
        'oavgjort': 'draw',
        'över': 'over',
        'under': 'under',
    }

    return OUTCOME_MAP.get(outcome_lower, raw_outcome)
```

### Step 2.4: Handle Edge Cases

**Empty responses:**

```python
async def extract(self, sport: str, limit: int = 100) -> List[StandardEvent]:
    try:
        data = await self._fetch_data(sport)

        # Always return empty list on error, never raise exception
        if not data or 'events' not in data:
            logger.warning(f"No events found for {sport}")
            return []

        return self.parse(data, sport)
    except Exception as e:
        logger.error(f"Extraction failed for {sport}: {e}")
        return []  # Return empty, don't crash
```

**Partial data:**

```python
def parse(self, data: dict, sport: str) -> List[StandardEvent]:
    events = []

    for raw_event in data.get('events', []):
        try:
            # Parse individual event
            event = self._parse_event(raw_event, sport)
            if event:
                events.append(event)
        except Exception as e:
            # Log and skip this event, continue with others
            logger.debug(f"Failed to parse event: {e}")
            continue

    return events
```

**Rate limiting:**

```python
import asyncio
from asyncio import Semaphore

class MyRetriever(Retriever):
    def __init__(self, config: dict, transport=None):
        super().__init__(config, transport)
        self._semaphore = Semaphore(5)  # Max 5 concurrent requests

    async def _fetch_with_limit(self, url: str):
        async with self._semaphore:
            async with self.transport.session.get(url) as response:
                return await response.json()
```

---

## Phase 3: Configuration

### Goal
Register your provider in the configuration system.

### Step 3.1: Update providers.yaml

**Add provider definition to `backend/src/config/providers.yaml`:**

```yaml
providers:
  # ... existing providers ...

  myprovider:
    id: myprovider
    name: My Provider
    domain: myprovider.com
    retriever_type: custom  # or kambi, spectate, etc.
    api_base: https://api.myprovider.com
    integration_id: myprovider_v1  # Optional

    # Optional: For multi-league providers
    max_leagues: 50
    concurrent_leagues: 5

    # Optional: Sport-specific parameters
    params:
      football:
        sport_id: 1
        tournament_ids: [100, 101, 102]
      basketball:
        sport_id: 2
        tournament_ids: [200, 201]

# DO NOT add to active list yet (wait for validation)
active:
  - unibet
  - leovegas
  # ... existing active providers ...
  # - myprovider  # Add after validation
```

### Step 3.2: Register Retriever Type in Factory

**Edit `backend/src/factory.py`:**

```python
def get_extractor(self, provider_id: str) -> Retriever:
    # ... existing code ...

    # Add your retriever type
    elif retriever_type == "custom":
        if provider_id == "myprovider":
            from .providers.myprovider import MyProviderRetriever
            retriever = MyProviderRetriever(config, transport=transport)
        # ... other custom providers ...

    # ... rest of code ...
```

**For shared platform providers (like Kambi):**

If multiple providers use the same platform, just add config - no code changes needed:

```yaml
providers:
  newkambi:
    <<: *kambi_defaults  # Reuse Kambi config
    brand: newbrand      # Only change brand code

active:
  - newkambi
```

### Step 3.3: Validate Configuration

**Test config loading:**

```bash
python -c "
from backend.src.config.loader import ConfigLoader
loader = ConfigLoader()
config = loader.get_provider_config('myprovider')
print(f'Loaded config: {config.id}')
print(f'Retriever type: {config.retriever_type}')
print(f'API base: {config.api_base}')
"
```

**Expected output:**
```
Loaded config: myprovider
Retriever type: custom
API base: https://api.myprovider.com
```

**If you get errors:**
- `ProviderNotFoundError` → Provider not in providers.yaml
- `ValidationError` → YAML syntax error or missing required fields
- `AttributeError` → Field name mismatch

---

## Phase 4: Testing

### Goal
Verify your implementation works correctly before validation.

### Step 4.1: Manual Extraction Test

**Create test script: `scrap/test_myprovider.py`**

```python
import asyncio
import sys
sys.path.insert(0, 'backend')

from src.factory import ExtractorFactory

async def test_extraction():
    """Test basic provider extraction."""
    factory = ExtractorFactory.get_instance()
    provider = factory.get_extractor('myprovider')

    print(f"Testing provider: {provider.config['id']}")
    print(f"Retriever type: {type(provider).__name__}")

    # Test single sport
    sport = 'football'
    print(f"\nExtracting {sport}...")

    events = await provider.extract(sport, limit=10)

    print(f"Extracted {len(events)} events")

    if events:
        # Show first event details
        event = events[0]
        print(f"\nSample Event:")
        print(f"  Teams: {event.home_team} vs {event.away_team}")
        print(f"  Sport: {event.sport}")
        print(f"  League: {event.league}")
        print(f"  Markets: {len(event.markets)}")

        if event.markets:
            market = event.markets[0]
            print(f"\nSample Market:")
            print(f"  Type: {market['type']}")
            print(f"  Outcomes: {len(market['outcomes'])}")
            for outcome in market['outcomes']:
                print(f"    {outcome['name']}: {outcome['odds']}")

if __name__ == '__main__':
    asyncio.run(test_extraction())
```

**Run test:**

```bash
cd backend
python ../scrap/test_myprovider.py
```

**Expected output:**
```
Testing provider: myprovider
Retriever type: MyProviderRetriever

Extracting football...
Extracted 10 events

Sample Event:
  Teams: arsenal vs chelsea
  Sport: football
  League: Premier League
  Markets: 3

Sample Market:
  Type: 1x2
  Outcomes: 3
    home: 2.10
    draw: 3.50
    away: 3.20
```

### Step 4.2: Check Data Quality

**Verify team names are normalized:**

```python
def check_normalization(events):
    """Check if team names are properly normalized."""
    for event in events:
        home = event.home_team
        away = event.away_team

        # Should be lowercase
        assert home.islower(), f"Home team not lowercase: {home}"
        assert away.islower(), f"Away team not lowercase: {away}"

        # Should not contain common suffixes
        forbidden = ['fc', 'sc', 'if', 'bk', 'cf']
        home_words = home.split()
        away_words = away.split()

        for word in forbidden:
            assert word not in home_words, f"Suffix not removed: {home}"
            assert word not in away_words, f"Suffix not removed: {away}"

    print("✓ Team normalization passed")

# Add to test script
check_normalization(events)
```

**Verify market types:**

```python
def check_markets(events):
    """Check if markets are properly standardized."""
    valid_markets = {'1x2', 'moneyline', 'over_under', 'spread'}
    valid_outcomes = {
        '1x2': {'home', 'draw', 'away'},
        'moneyline': {'home', 'away'},
        'over_under': {'over', 'under'},
        'spread': {'home', 'away'},
    }

    for event in events:
        for market in event.markets:
            market_type = market['type']

            # Check market type is valid
            assert market_type in valid_markets, f"Invalid market type: {market_type}"

            # Check outcomes
            for outcome in market['outcomes']:
                outcome_name = outcome['name']
                assert outcome_name in valid_outcomes[market_type], \
                    f"Invalid outcome '{outcome_name}' for market '{market_type}'"

                # Check odds are valid
                odds = outcome['odds']
                assert odds > 1.0, f"Invalid odds: {odds}"

    print("✓ Market validation passed")

check_markets(events)
```

### Step 4.3: Unit Tests (Optional but Recommended)

**Create `backend/tests/test_myprovider.py`:**

```python
import pytest
from src.providers.myprovider import MyProviderRetriever
from src.matching.normalizer import normalize_team_name

def test_sport_mapping():
    """Test sport ID mapping."""
    retriever = MyProviderRetriever({'id': 'test'})

    assert retriever._get_sport_id('football') == 1
    assert retriever._get_sport_id('basketball') == 2

    with pytest.raises(ValueError):
        retriever._get_sport_id('invalid_sport')

def test_outcome_standardization():
    """Test outcome name standardization."""
    retriever = MyProviderRetriever({'id': 'test'})

    # 1x2 outcomes
    assert retriever._standardize_outcome('Home', '1x2') == 'home'
    assert retriever._standardize_outcome('Draw', '1x2') == 'draw'
    assert retriever._standardize_outcome('Away', '1x2') == 'away'

    # Over/Under outcomes
    assert retriever._standardize_outcome('Over 2.5', 'over_under') == 'over'
    assert retriever._standardize_outcome('Under 2.5', 'over_under') == 'under'

def test_team_normalization():
    """Test team name normalization."""
    test_cases = [
        ("Real Madrid CF", "madrid"),
        ("FC Barcelona", "barcelona"),
        ("Manchester United FC", "manchester united"),
    ]

    for input_name, expected in test_cases:
        assert normalize_team_name(input_name) == expected

@pytest.mark.asyncio
async def test_extraction():
    """Test actual extraction (integration test)."""
    from src.factory import ExtractorFactory

    factory = ExtractorFactory.get_instance()
    provider = factory.get_extractor('myprovider')

    events = await provider.extract('football', limit=5)

    assert len(events) > 0, "No events extracted"

    for event in events:
        assert event.sport == 'football'
        assert event.home_team
        assert event.away_team
        assert len(event.markets) > 0

@pytest.mark.asyncio
async def test_error_handling():
    """Test graceful error handling."""
    provider = MyProviderRetriever({
        'id': 'test',
        'api_base': 'https://invalid.url'
    })

    # Should return empty list, not raise exception
    events = await provider.extract('football')
    assert events == []
```

**Run tests:**

```bash
pytest backend/tests/test_myprovider.py -v
```

---

## Phase 5: Validation

### Goal
Verify your provider meets all production-ready criteria.

### Step 5.1: Run Validation Script

**Use the official validation script:**

```bash
cd backend
python scripts/validate_provider.py myprovider football
```

**Validation checks (7 criteria):**

1. **Sports Coverage** - Returns events for requested sport
2. **Event Discovery** - All events have required fields
3. **Market Coverage** - Priority 1 (moneyline/1x2) + Priority 2 (over_under, spread) present
4. **Normalization** - Team names lowercase, no suffixes
5. **Database Compliance** - All odds > 1.0, point values present
6. **Performance** - Extraction < 30s per sport
7. **Error Handling** - No exceptions thrown

**Expected output:**

```
============================================================
Validating Provider: myprovider
Sport: football
============================================================

[1/7] Testing sports coverage...
  PASS: Extracted 150 events

[2/7] Testing event discovery...
  PASS: All events have required fields

[3/7] Testing market coverage...
  PASS: Priority 1 & 2 markets present
  Markets found: 1x2, moneyline, over_under, spread

[4/7] Testing data normalization...
  PASS: Team names normalized

[5/7] Testing database compliance...
  PASS: All odds > 1.0

[6/7] Testing performance...
  PASS: Extraction took 8.2s (< 30s)

[7/7] Testing error handling...
  PASS: No exceptions thrown

============================================================
VALIDATION SUMMARY
============================================================
  [X] Sports Coverage
  [X] Event Discovery
  [X] Market Coverage
  [X] Normalization
  [X] Database Compliance
  [X] Performance
  [X] Error Handling

Result: 7/7 checks passed
Status: PRODUCTION READY
============================================================
```

### Step 5.2: Document Validation Results

**Create validation document: `backend/docs/{PROVIDER}_VALIDATION.md`**

```markdown
# {Provider} Validation Report

## Provider Information
- **Provider ID**: myprovider
- **Provider Name**: My Provider
- **Domain**: myprovider.com
- **Retriever Type**: custom
- **Validation Date**: 2026-01-26

## Validation Results

### Sports Coverage: PASS
- Tested Sports: football, basketball, tennis
- Events per sport: 150, 120, 95
- All sports working correctly

### Event Discovery: PASS
- Required fields present: home_team, away_team, sport, league
- start_time: Present (ISO format)
- Leagues: 15 unique leagues found

### Market Coverage: PASS
- Priority 1 (Mandatory): 1x2 present
- Priority 2 (Required): over_under, spread present
- Market count per event: 3-8 markets average
- All outcomes have valid odds

### Data Normalization: PASS
- Team names: Lowercase, no suffixes
- Market types: Standardized (1x2, over_under, spread)
- Outcomes: Standardized (home, away, draw, over, under)
- Point values: Extracted for spreads/totals

### Database Compliance: PASS
- All odds > 1.0: Yes (minimum: 1.01, maximum: 25.0)
- Point values present: Yes (for spreads and totals)
- Unique constraints: No duplicate odds
- Schema compliance: 100%

### Performance: PASS
- Football extraction: 8.2s (target: < 30s)
- Basketball extraction: 6.5s
- Tennis extraction: 5.8s
- Average: 6.8s per sport (EXCELLENT)

### Error Handling: PASS
- Graceful degradation: Yes
- Returns empty list on error: Yes
- Logs errors appropriately: Yes
- No uncaught exceptions: Yes

## Summary
- **Total Checks**: 7/7 PASSED
- **Status**: PRODUCTION READY
- **Recommended for**: Immediate production deployment

## Sample Data

### Sample Event
```json
{
  "home_team": "arsenal",
  "away_team": "chelsea",
  "sport": "football",
  "league": "Premier League",
  "markets": [
    {
      "type": "1x2",
      "outcomes": [
        {"name": "home", "odds": 2.10},
        {"name": "draw", "odds": 3.50},
        {"name": "away", "odds": 3.20}
      ]
    },
    {
      "type": "over_under",
      "point": 2.5,
      "outcomes": [
        {"name": "over", "odds": 1.85},
        {"name": "under", "odds": 1.95}
      ]
    }
  ]
}
```

## Implementation Notes
- REST API provider (fast, reliable)
- No browser automation required
- Clean JSON response format
- Sport-specific market type IDs handled correctly

## Known Limitations
- None identified

## Recommendations
- Enable in production
- Add to active providers list
- Monitor extraction performance in first 24 hours
```

### Step 5.3: Update Provider Status Matrix

**Edit `backend/docs/validated.md` - add your provider to the status matrix:**

```markdown
### Provider Status Matrix

| Provider | Sports | Markets | Normalization | Database | Performance | Error Handling | Status |
|----------|--------|---------|---------------|----------|-------------|----------------|--------|
| ... existing providers ... |
| **MyProvider** | PASS | PASS | PASS | PASS | PASS | PASS | **PRODUCTION** |

#### MyProvider - PRODUCTION READY - VALIDATED 2026-01-26
- **Implementation:** `backend/src/providers/myprovider.py`
- **Type:** REST API retriever
- **API Base:** `https://api.myprovider.com`
- **Status:** PRODUCTION READY (7/7 validation checks passed)
- **Validation Results (2026-01-26):**
  - Sports Coverage: PASS - 3/3 sports tested
  - Event Discovery: PASS - All required fields, 15 unique leagues
  - Market Coverage: PASS - Priority 1+2 markets
  - Data Normalization: PASS - Full normalization
  - Database Compliance: PASS - All odds > 1.0
  - Performance: PASS - 6.8s average (< 30s target)
  - Error Handling: PASS - Graceful degradation
- **Data Quality:**
  - Extraction: 150 events (football), 120 (basketball), 95 (tennis)
  - Markets: 3-8 markets per event average
  - Normalization: 100% compliant
- **Performance Metrics:**
  - Football: 8.2s for 150 events
  - Basketball: 6.5s for 120 events
  - Average: 6.8s per sport (excellent)
- **Notes:**
  - Clean REST API architecture
  - No browser automation required
  - Production ready for immediate use
```

---

## Phase 6: Production Deployment

### Goal
Enable your provider in production and monitor initial performance.

### Step 6.1: Enable in Configuration

**Edit `backend/src/config/providers.yaml`:**

```yaml
# Add to active providers list
active:
  - unibet
  - leovegas
  # ... existing providers ...
  - myprovider  # Add your provider
```

### Step 6.2: Test Pipeline Integration

**Run full extraction pipeline:**

```bash
cd backend
python main.py --providers myprovider --sports football basketball
```

**Expected output:**

```
=== Extraction Pipeline ===
Providers: myprovider
Sports: football, basketball

Extracting from myprovider...
  [football] Extracted 150 events
  [basketball] Extracted 120 events

Storing results...
  Created 270 canonical events
  Stored 1,500 odds records

=== Results ===
Total events: 270
Total odds: 1,500
Duration: 15.2s
```

### Step 6.3: Verify Database Storage

**Check database:**

```bash
python -c "
from backend.src.db.models import Event, Odds, engine
from sqlalchemy.orm import sessionmaker

Session = sessionmaker(bind=engine)
session = Session()

# Count events from your provider
events = session.query(Event).join(Odds).filter(
    Odds.provider_id == session.query(Provider.id).filter(
        Provider.name == 'myprovider'
    ).scalar()
).count()

print(f'Events in database: {events}')

# Show sample odds
odds = session.query(Odds).filter(
    Odds.provider_id == session.query(Provider.id).filter(
        Provider.name == 'myprovider'
    ).scalar()
).limit(5).all()

for odd in odds:
    print(f'{odd.event.home_team} vs {odd.event.away_team}: {odd.market} {odd.outcome} @ {odd.odds}')
"
```

### Step 6.4: Run Full Multi-Sport Test

**Test all supported sports:**

```bash
python main.py --providers myprovider
```

**Monitor for:**
- [ ] No exceptions in logs
- [ ] All sports return events
- [ ] Reasonable event counts (>50 per sport)
- [ ] Extraction completes in reasonable time (<2 minutes total)
- [ ] Database constraints not violated

### Step 6.5: Git Commit

**Commit your changes:**

```bash
git add backend/src/providers/myprovider.py
git add backend/src/config/providers.yaml
git add backend/docs/MYPROVIDER_VALIDATION.md
git add backend/docs/validated.md
git add backend/tests/test_myprovider.py  # if created

git commit -m "Add MyProvider support (production ready)

Validation Results (2026-01-26):
- 7/7 checks PASSED
- Sports: football, basketball, tennis
- Events: 150/120/95 per sport
- Markets: 1x2, over_under, spread
- Performance: 6.8s average per sport
- Status: PRODUCTION READY

Implementation:
- REST API retriever (no browser required)
- Full team name normalization
- Market type standardization
- Comprehensive error handling

Closes #XXX"
```

### Step 6.6: Monitor Initial Performance

**Check logs after 24 hours:**

```bash
tail -f logs/extraction.log | grep myprovider
```

**Look for:**
- Consistent event counts
- No error spikes
- Performance within expected range
- No rate limiting issues

---

## Debugging Guide

### Issue: No Events Extracted

**Symptoms:** `provider.extract()` returns empty list `[]`

**Debug Steps:**

1. **Check API response:**

```python
async def debug_api():
    provider = factory.get_extractor('myprovider')
    url = provider._get_sport_url('football')

    async with provider.transport.session.get(url) as response:
        print(f"Status: {response.status}")
        text = await response.text()
        print(f"Response: {text[:500]}")  # First 500 chars

asyncio.run(debug_api())
```

**Possible causes:**
- Wrong URL (check `_get_sport_url()`)
- API requires authentication
- Sport not available
- API response format changed

2. **Check sport mapping:**

```python
# Add debug logging to your retriever
def _get_sport_id(self, sport: str) -> int:
    logger.debug(f"Looking up sport: {sport}")
    logger.debug(f"Available sports: {list(self.SPORT_MAPPING.values())}")

    for sport_id, sport_name in self.SPORT_MAPPING.items():
        if sport_name == sport:
            logger.debug(f"Found sport_id: {sport_id}")
            return sport_id

    raise ValueError(f"Sport '{sport}' not supported")
```

3. **Check parse logic:**

```python
def parse(self, data: dict, sport: str) -> List[StandardEvent]:
    logger.debug(f"Parsing data keys: {data.keys()}")
    logger.debug(f"Events in response: {len(data.get('events', []))}")

    events = []
    for i, raw_event in enumerate(data.get('events', [])):
        logger.debug(f"Processing event {i}: {raw_event.get('id')}")
        # ... rest of parse logic
```

### Issue: Team Names Not Matching

**Symptoms:** Events not matched across providers, duplicate canonical IDs

**Debug Steps:**

1. **Check normalization:**

```python
from backend.src.matching.normalizer import normalize_team_name

raw_name = "Real Madrid CF"
normalized = normalize_team_name(raw_name)
print(f"{raw_name} -> {normalized}")

# Expected: madrid
```

2. **Verify normalization is applied:**

```python
def parse(self, data: dict, sport: str) -> List[StandardEvent]:
    for raw_event in data.get('events', []):
        raw_home = raw_event['home_team']
        raw_away = raw_event['away_team']

        # Must call normalize_team_name
        home_normalized = normalize_team_name(raw_home)
        away_normalized = normalize_team_name(raw_away)

        logger.debug(f"Normalized: {raw_home} -> {home_normalized}")
        logger.debug(f"Normalized: {raw_away} -> {away_normalized}")
```

3. **Check for missed suffixes:**

```python
# If team names still have suffixes, update normalizer
# Add to backend/src/matching/normalizer.py:SUFFIX_PATTERNS
SUFFIX_PATTERNS = [
    r'\bFC\b',
    r'\bSC\b',
    r'\bYOUR_NEW_SUFFIX\b',  # Add here
]
```

### Issue: Markets Missing

**Symptoms:** Events extracted but no markets, or wrong market types

**Debug Steps:**

1. **Check market type mapping:**

```python
def _parse_markets(self, raw_markets: List[dict]) -> List[dict]:
    logger.debug(f"Processing {len(raw_markets)} raw markets")

    for raw_market in raw_markets:
        market_type_id = raw_market.get('typeId')
        logger.debug(f"Market type ID: {market_type_id}")

        market_type = self.MARKET_TYPE_MAPPING.get(market_type_id)
        logger.debug(f"Mapped to: {market_type}")

        if not market_type:
            logger.warning(f"Unknown market type ID: {market_type_id}")
            logger.warning(f"Market data: {raw_market}")
```

2. **Find missing market type IDs:**

```python
# Collect all unknown market types
unknown_markets = set()

for raw_event in data['events']:
    for raw_market in raw_event.get('markets', []):
        type_id = raw_market['typeId']
        if type_id not in self.MARKET_TYPE_MAPPING:
            unknown_markets.add(type_id)

print(f"Unknown market type IDs: {unknown_markets}")
```

3. **Update mapping:**

```python
# Add discovered market types to your retriever
MARKET_TYPE_MAPPING = {
    1: '1x2',
    2: 'over_under',
    3: 'spread',
    # Add newly discovered types
    4: 'both_teams_to_score',  # Example
}
```

### Issue: Duplicate Odds in Database

**Symptoms:** `IntegrityError: UNIQUE constraint failed`

**Debug Steps:**

1. **Check unique constraint:**

```sql
-- backend/src/db/models.py:Odds
__table_args__ = (
    UniqueConstraint('event_id', 'provider_id', 'market', 'outcome', 'point'),
)
```

2. **Find duplicate:**

```python
# Check what's being inserted
def _upsert_odds(self, event_id, provider_id, market, outcome, odds, point=None):
    logger.debug(f"Upserting: event={event_id}, provider={provider_id}, "
                 f"market={market}, outcome={outcome}, point={point}, odds={odds}")

    # Check if already exists
    existing = session.query(Odds).filter(
        Odds.event_id == event_id,
        Odds.provider_id == provider_id,
        Odds.market == market,
        Odds.outcome == outcome,
        Odds.point == point  # Include point in uniqueness check
    ).first()

    if existing:
        logger.debug(f"Found existing odds: {existing.odds}")
```

3. **Check point values:**

```python
# Ensure point values are consistent
# Problem: over_under with point=2.5 vs point="2.5" (string)
# Solution: Always convert to float

def _extract_point(self, raw_market: dict) -> Optional[float]:
    point = raw_market.get('point')
    if point is not None:
        return float(point)  # Always float, not string
    return None
```

### Issue: Slow Extraction

**Symptoms:** Extraction takes > 30s per sport

**Debug Steps:**

1. **Add timing:**

```python
import time

async def extract(self, sport: str, limit: int = 100) -> List[StandardEvent]:
    start = time.time()

    logger.info(f"Starting extraction for {sport}")

    t1 = time.time()
    data = await self._fetch_data(sport)
    logger.info(f"Fetch took {time.time() - t1:.2f}s")

    t2 = time.time()
    events = self.parse(data, sport)
    logger.info(f"Parse took {time.time() - t2:.2f}s")

    logger.info(f"Total: {time.time() - start:.2f}s for {len(events)} events")

    return events
```

2. **Identify bottleneck:**

**If fetch is slow:**
- Use bulk endpoints instead of per-event calls
- Implement caching (5-minute TTL)
- Increase timeout values

**If parse is slow:**
- Reduce regex complexity
- Cache normalization results
- Profile with `cProfile`

3. **Optimize:**

```python
# Add caching
from functools import lru_cache

@lru_cache(maxsize=1000)
def _normalize_cached(self, team_name: str) -> str:
    return normalize_team_name(team_name)

# Add concurrency
from asyncio import Semaphore, gather

async def extract_all_sports(self, sports: List[str]):
    sem = Semaphore(5)  # Max 5 concurrent

    async def fetch_sport(sport):
        async with sem:
            return await self.extract(sport)

    results = await gather(*[fetch_sport(s) for s in sports])
    return results
```

### Issue: Browser-Based Provider Not Working

**Symptoms:** Playwright errors, page not loading, no API responses captured

**Debug Steps:**

1. **Test browser initialization:**

```python
async def debug_browser():
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # Visible browser
        page = await browser.new_page()

        await page.goto('https://provider.com/sports/football')

        # Wait and inspect
        await page.wait_for_timeout(5000)

        # Check if page loaded
        title = await page.title()
        print(f"Page title: {title}")

        # Check for errors
        page.on('console', lambda msg: print(f"Console: {msg.text}"))
        page.on('pageerror', lambda err: print(f"Page error: {err}"))

        await browser.close()

asyncio.run(debug_browser())
```

2. **Check API interception:**

```python
async def debug_interception(page):
    captured = []

    async def handle_response(response):
        print(f"Response: {response.url} ({response.status})")

        if 'api' in response.url:
            try:
                data = await response.json()
                captured.append(data)
                print(f"Captured API response: {len(str(data))} bytes")
            except:
                print(f"Failed to parse JSON")

    page.on('response', handle_response)

    await page.goto('https://provider.com/sports/football')
    await page.wait_for_timeout(5000)

    print(f"Total API responses captured: {len(captured)}")
    return captured
```

3. **Check for bot detection:**

```python
# If you see Cloudflare/Imperva challenges:
# 1. Try stealth mode
from playwright_stealth import stealth_async

async with async_playwright() as p:
    browser = await p.chromium.launch()
    page = await browser.new_page()
    await stealth_async(page)  # Apply stealth

# 2. Use mobile user agent
await page.set_extra_http_headers({
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X)'
})

# 3. Consider if provider is worth the effort
# (some require residential proxies - not worth it)
```

---

## Common Pitfalls

### 1. Not Normalizing Team Names

**❌ Wrong:**

```python
event = StandardEvent(
    home_team=raw_event['home'],  # Raw from API
    away_team=raw_event['away'],
    ...
)
```

**✅ Correct:**

```python
from ..matching.normalizer import normalize_team_name

event = StandardEvent(
    home_team=normalize_team_name(raw_event['home']),
    away_team=normalize_team_name(raw_event['away']),
    ...
)
```

### 2. Not Standardizing Outcomes

**❌ Wrong:**

```python
outcomes.append({
    'name': raw_outcome['name'],  # Provider-specific name
    'odds': raw_outcome['odds']
})
```

**✅ Correct:**

```python
outcomes.append({
    'name': self._standardize_outcome(raw_outcome['name'], market_type),
    'odds': float(raw_outcome['odds'])
})
```

### 3. Missing Point Values

**❌ Wrong:**

```python
market = {
    'type': 'over_under',
    'outcomes': [...]
    # Missing point!
}
```

**✅ Correct:**

```python
market = {
    'type': 'over_under',
    'point': 2.5,  # Required for spreads/totals
    'outcomes': [...]
}
```

### 4. Raising Exceptions Instead of Returning Empty

**❌ Wrong:**

```python
async def extract(self, sport: str) -> List[StandardEvent]:
    response = await self.transport.session.get(url)
    response.raise_for_status()  # Will crash on 404!
    return self.parse(await response.json(), sport)
```

**✅ Correct:**

```python
async def extract(self, sport: str) -> List[StandardEvent]:
    try:
        response = await self.transport.session.get(url)
        if response.status != 200:
            logger.warning(f"API returned {response.status}")
            return []  # Return empty, don't crash
        return self.parse(await response.json(), sport)
    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        return []  # Graceful degradation
```

### 5. Not Testing Edge Cases

**Test these scenarios:**

- Empty API response
- Malformed JSON
- Missing fields in response
- Network timeout
- Rate limiting (429 status)
- Invalid sport name
- Events with no markets
- Markets with no outcomes

### 6. Hardcoding Sport IDs

**❌ Wrong:**

```python
def extract(self, sport: str):
    if sport == 'football':
        sport_id = 1
    elif sport == 'basketball':
        sport_id = 2
    # Duplicated logic
```

**✅ Correct:**

```python
SPORT_MAPPING = {
    1: 'football',
    2: 'basketball',
}

def _get_sport_id(self, sport: str) -> int:
    for sport_id, sport_name in self.SPORT_MAPPING.items():
        if sport_name == sport:
            return sport_id
    raise ValueError(f"Sport '{sport}' not supported")
```

### 7. Not Using Configuration

**❌ Wrong:**

```python
class MyRetriever(Retriever):
    def __init__(self, config: dict, transport=None):
        super().__init__(config, transport)
        self.api_base = "https://api.provider.com"  # Hardcoded
```

**✅ Correct:**

```python
class MyRetriever(Retriever):
    def __init__(self, config: dict, transport=None):
        super().__init__(config, transport)
        self.api_base = config.get('api_base')  # From config

        if not self.api_base:
            raise ValueError("api_base required in config")
```

### 8. Not Logging Enough

**Add debug logging:**

```python
def parse(self, data: dict, sport: str) -> List[StandardEvent]:
    logger.debug(f"Parsing {len(data.get('events', []))} events for {sport}")

    events = []
    skipped = 0

    for raw_event in data.get('events', []):
        try:
            event = self._parse_event(raw_event, sport)
            events.append(event)
        except Exception as e:
            logger.debug(f"Skipped event: {e}")
            skipped += 1

    logger.info(f"Parsed {len(events)} events, skipped {skipped}")
    return events
```

---

## Reference Examples

### Example 1: Simple REST API (Altenar/Betinia)

**File:** `backend/src/providers/altenar.py`

**Key Features:**
- Pure REST API (no browser)
- Sport ID mapping
- Market type mapping with point extraction
- Team name normalization
- Performance: < 1s per sport

**Pattern:** Extend `Retriever`, override `extract()`, custom API workflow

### Example 2: Browser + API Interception (Gecko V2/Betsson)

**File:** `backend/src/providers/gecko_v2.py`

**Key Features:**
- Browser loads page
- Intercepts API responses
- Fast (no DOM parsing)
- Handles bot detection with stealth mode

**Pattern:** Extend `BrowserRetriever`, setup response handlers, parse JSON

### Example 3: Multi-League WebSocket (ComeOn)

**File:** `backend/src/providers/comeon_multileague.py`

**Key Features:**
- Navigate to league pages
- Intercept WebSocket/RSocket messages
- Decode binary frames
- Parallel league extraction with concurrency control

**Pattern:** Extend `BrowserRetriever`, WebSocket interception, binary decoding

### Example 4: Shared Platform (Kambi)

**File:** `backend/src/providers/kambi.py`

**Key Features:**
- Single implementation for 13 providers
- Brand-based differentiation (config only)
- Shared group tree cache
- Bulk API endpoints

**Pattern:** Config-driven, no code duplication

### Example 5: Platform Base Class (SBTech)

**File:** `backend/src/providers/sbtech_base.py`

**Key Features:**
- Base class for SBTech providers
- Shared parsing logic
- Subclasses override URL patterns

**Pattern:** Inheritance chain, template method

---

## Next Steps After Production

### 1. Monitor Performance

Set up alerts for:
- Extraction failures (>10% failure rate)
- Slow extractions (>30s per sport)
- Event count drops (>50% decrease)

### 2. Optimize Further

Once stable, consider:
- Response caching (5-minute TTL)
- Parallel sport extraction
- Incremental updates (only changed events)

### 3. Add More Sports

Expand sport coverage:
- Update SPORT_MAPPING
- Test new sports
- Validate market coverage

### 4. Handle Provider Changes

When provider updates their API:
- Check logs for new errors
- Re-run validation
- Update mappings if needed
- Document breaking changes

### 5. Share Knowledge

Document lessons learned:
- Update this guide with new patterns
- Add troubleshooting tips
- Share provider-specific gotchas

---

## Appendix

### Quick Reference: File Locations

| File | Purpose |
|------|---------|
| `backend/src/providers/{id}.py` | Provider implementation |
| `backend/src/config/providers.yaml` | Provider configuration |
| `backend/src/factory.py` | Retriever type routing |
| `backend/src/core/retriever.py` | Base class definitions |
| `backend/src/matching/normalizer.py` | Team name normalization |
| `backend/docs/validated.md` | Validation framework |
| `backend/tests/test_{id}.py` | Unit tests |
| `scripts/validate_provider.py` | Validation script |

### Quick Reference: Commands

```bash
# Test extraction
python -c "
import asyncio
from backend.src.factory import ExtractorFactory
provider = ExtractorFactory.get_instance().get_extractor('myprovider')
events = asyncio.run(provider.extract('football', limit=10))
print(f'Extracted {len(events)} events')
"

# Run validation
python backend/scripts/validate_provider.py myprovider football

# Run tests
pytest backend/tests/test_myprovider.py -v

# Run pipeline
python backend/main.py --providers myprovider --sports football
```

### Quick Reference: Standard Market Types

| Market Type | Outcomes | Point? | Description |
|-------------|----------|--------|-------------|
| `1x2` | home, draw, away | No | Three-way moneyline |
| `moneyline` | home, away | No | Two-way moneyline |
| `over_under` | over, under | **Yes** | Total goals/points |
| `spread` | home, away | **Yes** | Handicap betting |

### Quick Reference: Validation Criteria

| Check | Requirement |
|-------|-------------|
| Sports Coverage | Returns events for requested sport |
| Event Fields | home_team, away_team, sport present |
| Market Coverage | 1x2/moneyline + over_under + spread |
| Normalization | Lowercase teams, no suffixes |
| Database | Odds > 1.0, point for spreads/totals |
| Performance | < 30s per sport |
| Error Handling | Returns empty list, no exceptions |

---

**Last Updated:** 2026-01-26
**Version:** 1.0
**Maintained By:** OddOpp Development Team
