# Provider Validation Guide

## Overview

### Purpose
This document defines production-ready criteria for sports betting providers in the OddOpp platform. Use this guide to:
- Validate new provider implementations before production
- Audit existing providers for completeness
- Ensure consistent data quality across all providers
- Debug extraction issues systematically

### Production-Ready Definition
A provider is **production-ready** when it:
1. Extracts events with complete required fields (sport, teams, start_time)
2. Returns standardized market types (1x2/moneyline, over_under, spread)
3. Normalizes team names according to platform conventions
4. Handles errors gracefully without crashing the pipeline
5. Meets performance benchmarks (< 30s per sport)
6. Complies with database schema constraints

---

## Validation Checklist

### 1. Sports Coverage

**Requirement:** Provider must support extraction for available sports. Not all sports required, but any supported sport must work reliably.

**Supported Sports:**
- `football` (soccer)
- `basketball`
- `tennis`
- `ice_hockey`
- `american_football`
- `baseball`
- `cricket`
- `rugby`
- `mma`
- `boxing`
- `motorsports`
- `esports`
- `handball`

**Validation:**
```python
# Test extraction for each supported sport
provider = ExtractorFactory.get_provider("provider_name")
events = await provider.extract("football")
assert len(events) > 0, "No events returned for football"
assert all(e.sport == "football" for e in events)
```

**Pass Criteria:**
- [ ] Returns events for at least 1 sport
- [ ] Returned events have correct `sport` field
- [ ] No exceptions thrown during extraction

---

### 2. League & Event Discovery

**Requirement:** Provider must return events for any available league. No specific leagues required.

**Event Fields (Required):**
- `sport` (str): Lowercase sport name
- `home_team` (str): Home/Team 1 name
- `away_team` (str): Away/Team 2 name
- `start_time` (datetime): Event start time (or None for upcoming)
- `league` (str, optional): League/competition name

**Validation:**
```python
events = await provider.extract("football")
for event in events:
    assert event.home_team, f"Missing home_team: {event}"
    assert event.away_team, f"Missing away_team: {event}"
    assert event.sport, f"Missing sport: {event}"
```

**Pass Criteria:**
- [ ] All events have `home_team` and `away_team`
- [ ] All events have `sport` field
- [ ] `start_time` present (or None if unavailable)
- [ ] Events span multiple leagues/competitions

**Example (Kambi):**
```python
# backend/src/providers/kambi.py
event = StandardEvent(
    sport=sport,
    home_team=raw["event"]["homeName"],
    away_team=raw["event"]["awayName"],
    start_time=parse_datetime(raw["event"]["start"]),
    league=raw["group"].get("englishName")
)
```

---

### 3. Market Type Coverage

**Priority 1: MANDATORY**

Must support moneyline/1x2 markets:

| Market Type | Outcomes | Description |
|-------------|----------|-------------|
| `1x2` | `home`, `draw`, `away` | Three-way moneyline (football, hockey) |
| `moneyline` | `home`, `away` | Two-way moneyline (basketball, tennis) |

**Priority 2: REQUIRED**

Must support totals and spreads:

| Market Type | Outcomes | Point Required | Description |
|-------------|----------|----------------|-------------|
| `over_under` | `over`, `under` | Yes (e.g., 2.5) | Total goals/points |
| `spread` | `home`, `away` | Yes (e.g., +3.5) | Handicap betting |

**Priority 3: OPTIONAL**

All other markets (player props, corners, cards, etc.) are skipped for validation.

**Validation:**
```python
markets = {}
for event in events:
    for market in event.markets:
        markets.setdefault(market.market_type, []).append(market)

# Priority 1
assert "1x2" in markets or "moneyline" in markets, "Missing moneyline market"

# Priority 2
assert "over_under" in markets, "Missing over_under market"
assert "spread" in markets, "Missing spread market"

# Check point values
for market in markets["over_under"]:
    assert market.point is not None, "over_under missing point value"
```

**Pass Criteria:**
- [ ] Moneyline/1x2 market present with correct outcomes
- [ ] Over/under market present with point values
- [ ] Spread market present with point values
- [ ] All outcomes have odds > 1.0

**Example (Spectate):**
```python
# backend/src/providers/spectate.py
MARKET_TYPE_MAP = {
    "fullTimeResult": "1x2",
    "total": "over_under",
    "handicap": "spread",
    "moneyLine": "moneyline"
}

OUTCOME_MAP = {
    "home": "home",
    "away": "away",
    "draw": "draw",
    "over": "over",
    "under": "under"
}
```

---

### 4. Data Normalization

#### 4.1 Team Names

**Normalization Rules:**
1. Lowercase all characters
2. Remove accents/diacritics (é -> e, ü -> u)
3. Remove club suffixes: FC, SC, IF, BK, SK, CF, AC, etc.
4. Remove club prefixes: Real, Sporting, Club, etc.
5. Remove age indicators: U21, U19, B team, II, etc.
6. Map to aliases if defined in `backend/src/matching/aliases.yaml`

**Implementation:** `backend/src/matching/normalizer.py:25`

**Validation:**
```python
from backend.src.matching.normalizer import normalize_team_name

test_cases = [
    ("Real Madrid CF", "madrid"),
    ("FC Barcelona", "barcelona"),
    ("Manchester United FC", "manchester united"),
    ("Sporting CP U21", "sporting"),
]

for input_name, expected in test_cases:
    normalized = normalize_team_name(input_name)
    assert normalized == expected, f"{input_name} -> {normalized} != {expected}"
```

**Pass Criteria:**
- [ ] All team names lowercase
- [ ] No accents/diacritics in output
- [ ] Common suffixes removed (FC, SC, etc.)
- [ ] Aliases applied when available

#### 4.2 Canonical Event IDs

**Format:** `{sport}:{home_normalized}:{away_normalized}:{YYYYMMDD}`

**Example:** `football:arsenal:chelsea:20260122`

**Implementation:** `backend/src/pipeline/utils.py:15`

**Validation:**
```python
event = StandardEvent(
    sport="football",
    home_team="Arsenal FC",
    away_team="Chelsea FC",
    start_time=datetime(2026, 1, 22, 15, 0)
)
canonical_id = generate_canonical_id(event)
assert canonical_id == "football:arsenal:chelsea:20260122"
```

**Pass Criteria:**
- [ ] Sport lowercase
- [ ] Teams normalized (lowercase, no suffixes)
- [ ] Date in YYYYMMDD format
- [ ] Colon-separated format

#### 4.3 Market Types

**Standardized Market Types:**
- `1x2` (three-way: home/draw/away)
- `moneyline` (two-way: home/away)
- `over_under` (totals with point)
- `spread` (handicap with point)

**Standardized Outcomes:**
- `home`, `away`, `draw`
- `over`, `under`

**Pass Criteria:**
- [ ] All markets use standardized type names
- [ ] All outcomes use standardized names
- [ ] Unknown markets logged and skipped

**Example (Spectate):**
```python
# Map provider market names to standard names
market_type = MARKET_TYPE_MAP.get(raw_market["type"], raw_market["type"])
if market_type not in ["1x2", "moneyline", "over_under", "spread"]:
    logger.debug(f"Skipping market type: {market_type}")
    continue
```

---

### 5. Database Schema Compliance

#### 5.1 Event Model

**Required Fields:** `backend/src/db/models.py:29`

```python
class Event(Base):
    id: str              # Canonical ID (primary key)
    sport: str           # Required
    home_team: str       # Required, normalized
    away_team: str       # Required, normalized
    league: str | None   # Optional
    start_time: datetime | None  # Optional
```

**Validation:**
```python
# All events must have these fields
assert event.sport is not None
assert event.home_team is not None
assert event.away_team is not None
assert len(event.home_team) > 0
assert len(event.away_team) > 0
```

#### 5.2 Odds Model

**Required Fields:** `backend/src/db/models.py:78`

```python
class Odds(Base):
    event_id: str        # FK to Event.id
    provider_id: int     # FK to Provider.id
    market: str          # Required (1x2, moneyline, etc.)
    outcome: str         # Required (home, away, draw, etc.)
    odds: float          # Required (decimal, > 1.0)
    point: float | None  # Optional (for spreads/totals)
```

**Unique Constraint:**
```python
__table_args__ = (
    UniqueConstraint('event_id', 'provider_id', 'market', 'outcome'),
)
```

**Validation:**
```python
# All odds must meet these criteria
assert odds.event_id is not None
assert odds.provider_id is not None
assert odds.market in ["1x2", "moneyline", "over_under", "spread"]
assert odds.outcome in ["home", "away", "draw", "over", "under"]
assert odds.odds > 1.0, "Odds must be greater than 1.0"

# Point required for spreads/totals
if odds.market in ["over_under", "spread"]:
    assert odds.point is not None, f"{odds.market} requires point value"
```

**Pass Criteria:**
- [ ] All odds have valid event_id (canonical ID)
- [ ] All odds have valid provider_id
- [ ] Market and outcome standardized
- [ ] Odds > 1.0 (decimal format)
- [ ] Point present for spreads/totals
- [ ] No duplicate (event_id, provider_id, market, outcome) combinations

---

## Performance Criteria

### Response Time Benchmarks

| Metric | Target | Maximum |
|--------|--------|---------|
| Single sport extraction | < 10s | < 30s |
| Full provider extraction | < 60s | < 120s |
| API call timeout | 10s | 30s |

**Validation:**
```python
import time

start = time.time()
events = await provider.extract("football")
elapsed = time.time() - start

assert elapsed < 30, f"Extraction took {elapsed:.1f}s (max 30s)"
```

### API Efficiency

**Best Practices:**
1. Use bulk endpoints when available (not one-per-event)
2. Minimize HTTP calls (batch requests)
3. Implement response caching (TTL 2-5 minutes)
4. Use connection pooling (aiohttp sessions)

**Anti-patterns:**
- Making separate API calls per event
- No caching for repeated data
- Synchronous HTTP calls
- Excessive pagination without batching

**Example (Kambi - Efficient):**
```python
# Single API call returns all events for sport/league
url = f"https://offering-api.kambicdn.com/offering/api/v2018/listView/football.json"
response = await session.get(url)
all_events = response["events"]  # ~100-500 events in one call
```

**Example (Snabbare - Less Efficient):**
```python
# DOM scraping requires page load
async with async_playwright() as p:
    browser = await p.chromium.launch()
    page = await browser.new_page()
    await page.goto(url)  # Full page load + JS execution
```

### Caching Requirements

**Cacheable Data:**
- Event lists (TTL: 2-5 minutes)
- Static odds (TTL: 5 minutes)
- League metadata (TTL: 1 hour)

**Non-cacheable Data:**
- Live odds (always fresh)
- User-specific data

**Pass Criteria:**
- [ ] Response time < 30s per sport
- [ ] Bulk endpoints used when available
- [ ] Caching implemented for repeated calls
- [ ] Connection pooling configured

---

## Error Handling Requirements

### HTTP Error Responses

**Required Handling:**

| Status Code | Action | Log Level |
|-------------|--------|-----------|
| 200 OK | Process normally | DEBUG |
| 403 Forbidden | Return empty, continue | WARNING |
| 404 Not Found | Return empty, continue | INFO |
| 429 Rate Limited | Backoff + retry (3x) | WARNING |
| 500-599 Server Error | Return empty, continue | ERROR |
| Timeout | Log + return empty | ERROR |

**Implementation Example:**
```python
try:
    async with session.get(url, timeout=10) as response:
        if response.status == 403:
            logger.warning(f"403 Forbidden: {url}")
            return []
        elif response.status == 429:
            await asyncio.sleep(5)  # Backoff
            # Retry logic...
        elif response.status >= 500:
            logger.error(f"Server error {response.status}: {url}")
            return []

        response.raise_for_status()
        return await response.json()

except asyncio.TimeoutError:
    logger.error(f"Timeout fetching {url}")
    return []
except Exception as e:
    logger.error(f"Unexpected error: {e}")
    return []
```

### Data Validation Rules

**Skip Events When:**
- Missing `home_team` or `away_team`
- Missing `sport` field
- Event already started (if upcoming-only)
- Duplicate canonical ID

**Skip Markets When:**
- No outcomes available
- Unknown market type
- Missing point value (for spreads/totals)

**Skip Odds When:**
- Odds <= 1.0
- Missing required fields
- Duplicate (event_id, provider_id, market, outcome)

**Implementation Example:**
```python
def validate_event(event: StandardEvent) -> bool:
    if not event.home_team or not event.away_team:
        logger.debug(f"Skipping event missing teams: {event}")
        return False

    if not event.sport:
        logger.debug(f"Skipping event missing sport: {event}")
        return False

    if event.start_time and event.start_time < datetime.now(UTC):
        logger.debug(f"Skipping started event: {event}")
        return False

    return True

valid_events = [e for e in events if validate_event(e)]
```

### Resilience Patterns

**Graceful Degradation:**
```python
# Don't crash entire extraction on single event failure
results = []
for event_data in raw_events:
    try:
        event = parse_event(event_data)
        results.append(event)
    except Exception as e:
        logger.warning(f"Failed to parse event: {e}")
        continue  # Continue with other events
```

**Deduplication:**
```python
# Remove duplicate events by canonical ID
seen_ids = set()
unique_events = []
for event in events:
    event_id = generate_canonical_id(event)
    if event_id not in seen_ids:
        seen_ids.add(event_id)
        unique_events.append(event)
```

**Concurrency Control:**
```python
# Rate limiting with semaphore
semaphore = asyncio.Semaphore(5)  # Max 5 concurrent requests

async def fetch_with_limit(url):
    async with semaphore:
        return await session.get(url)
```

**Pass Criteria:**
- [ ] All HTTP errors handled without crashing
- [ ] Invalid data skipped with debug logs
- [ ] Single failures don't stop entire extraction
- [ ] Duplicate events deduplicated
- [ ] Rate limiting implemented if needed

---

## Testing Procedures

### Manual Validation Steps

**Step 1: Basic Extraction**
```bash
cd backend
python -c "
import asyncio
from src.factory import ExtractorFactory

async def test():
    provider = ExtractorFactory.get_provider('provider_name')
    events = await provider.extract('football')
    print(f'Extracted {len(events)} events')
    if events:
        print(f'Sample event: {events[0]}')

asyncio.run(test())
"
```

**Step 2: Data Quality Check**
```bash
python -c "
import asyncio
from src.factory import ExtractorFactory
from src.matching.normalizer import normalize_team_name

async def test():
    provider = ExtractorFactory.get_provider('provider_name')
    events = await provider.extract('football')

    for event in events[:5]:
        print(f'\nEvent: {event.home_team} vs {event.away_team}')
        print(f'  Sport: {event.sport}')
        print(f'  League: {event.league}')
        print(f'  Markets: {len(event.markets)}')

        for market in event.markets[:3]:
            print(f'    {market.market_type}: {market.outcomes}')

asyncio.run(test())
"
```

**Step 3: Pipeline Integration**
```bash
python main.py --providers provider_name --sports football
```

### Automated Test Example

**File:** `tests/test_provider_validation.py`

```python
import pytest
from src.factory import ExtractorFactory
from src.matching.normalizer import normalize_team_name
from src.pipeline.utils import generate_canonical_id

@pytest.mark.asyncio
async def test_provider_basic_extraction():
    """Test: Provider returns events with required fields"""
    provider = ExtractorFactory.get_provider("kambi")
    events = await provider.extract("football")

    assert len(events) > 0, "No events returned"

    for event in events:
        assert event.sport == "football"
        assert event.home_team
        assert event.away_team
        assert len(event.markets) > 0

@pytest.mark.asyncio
async def test_provider_market_coverage():
    """Test: Provider returns required market types"""
    provider = ExtractorFactory.get_provider("kambi")
    events = await provider.extract("football")

    market_types = set()
    for event in events:
        for market in event.markets:
            market_types.add(market.market_type)

    # Priority 1: Moneyline
    assert "1x2" in market_types or "moneyline" in market_types

    # Priority 2: Totals and spreads
    assert "over_under" in market_types
    assert "spread" in market_types

@pytest.mark.asyncio
async def test_provider_normalization():
    """Test: Team names are properly normalized"""
    provider = ExtractorFactory.get_provider("kambi")
    events = await provider.extract("football")

    for event in events[:10]:
        # Should be lowercase
        assert event.home_team.islower()
        assert event.away_team.islower()

        # Should not contain common suffixes
        assert "fc" not in event.home_team.split()
        assert "sc" not in event.away_team.split()

@pytest.mark.asyncio
async def test_provider_odds_validation():
    """Test: All odds are valid decimals > 1.0"""
    provider = ExtractorFactory.get_provider("kambi")
    events = await provider.extract("football")

    for event in events:
        for market in event.markets:
            for outcome in market.outcomes:
                assert outcome.odds > 1.0, f"Invalid odds: {outcome.odds}"
                assert isinstance(outcome.odds, (float, int))

@pytest.mark.asyncio
async def test_provider_performance():
    """Test: Extraction completes within time limit"""
    import time

    provider = ExtractorFactory.get_provider("kambi")
    start = time.time()
    events = await provider.extract("football")
    elapsed = time.time() - start

    assert elapsed < 30, f"Extraction too slow: {elapsed:.1f}s"
```

**Run Tests:**
```bash
pytest tests/test_provider_validation.py -v
```

### Sample Validation Script

**File:** `scripts/validate_provider.py`

```python
#!/usr/bin/env python3
"""
Provider Validation Script

Usage:
    python scripts/validate_provider.py kambi
    python scripts/validate_provider.py snabbare --sport basketball
"""

import asyncio
import sys
from datetime import datetime
from src.factory import ExtractorFactory

async def validate_provider(provider_name: str, sport: str = "football"):
    """Run comprehensive validation checks on a provider"""

    print(f"\n{'='*60}")
    print(f"Validating Provider: {provider_name}")
    print(f"Sport: {sport}")
    print(f"{'='*60}\n")

    results = {
        "sports_coverage": False,
        "event_discovery": False,
        "market_coverage": False,
        "normalization": False,
        "database_compliance": False,
        "performance": False,
        "error_handling": True  # Assume pass unless exception
    }

    try:
        # 1. Sports Coverage
        print("[1/7] Testing sports coverage...")
        provider = ExtractorFactory.get_provider(provider_name)
        events = await provider.extract(sport)

        if len(events) > 0:
            results["sports_coverage"] = True
            print(f"  PASS: Extracted {len(events)} events")
        else:
            print(f"  FAIL: No events returned")
            return results

        # 2. Event Discovery
        print("\n[2/7] Testing event discovery...")
        required_fields = all(
            e.sport and e.home_team and e.away_team
            for e in events
        )

        if required_fields:
            results["event_discovery"] = True
            print(f"  PASS: All events have required fields")
        else:
            print(f"  FAIL: Some events missing required fields")

        # 3. Market Coverage
        print("\n[3/7] Testing market coverage...")
        market_types = set()
        for event in events:
            for market in event.markets:
                market_types.add(market.market_type)

        has_moneyline = "1x2" in market_types or "moneyline" in market_types
        has_totals = "over_under" in market_types
        has_spreads = "spread" in market_types

        if has_moneyline and has_totals and has_spreads:
            results["market_coverage"] = True
            print(f"  PASS: Priority 1 & 2 markets present")
            print(f"  Markets found: {', '.join(sorted(market_types))}")
        else:
            print(f"  FAIL: Missing required markets")
            print(f"  Markets found: {', '.join(sorted(market_types))}")
            print(f"  Has moneyline: {has_moneyline}")
            print(f"  Has totals: {has_totals}")
            print(f"  Has spreads: {has_spreads}")

        # 4. Normalization
        print("\n[4/7] Testing data normalization...")
        normalized = all(
            e.home_team.islower() and e.away_team.islower()
            for e in events
        )

        if normalized:
            results["normalization"] = True
            print(f"  PASS: Team names normalized")
        else:
            print(f"  FAIL: Team names not properly normalized")
            for e in events[:3]:
                print(f"    {e.home_team} vs {e.away_team}")

        # 5. Database Compliance
        print("\n[5/7] Testing database compliance...")
        valid_odds = all(
            outcome.odds > 1.0
            for event in events
            for market in event.markets
            for outcome in market.outcomes
        )

        if valid_odds:
            results["database_compliance"] = True
            print(f"  PASS: All odds > 1.0")
        else:
            print(f"  FAIL: Some odds <= 1.0")

        # 6. Performance
        print("\n[6/7] Testing performance...")
        import time
        start = time.time()
        await provider.extract(sport)
        elapsed = time.time() - start

        if elapsed < 30:
            results["performance"] = True
            print(f"  PASS: Extraction took {elapsed:.1f}s (< 30s)")
        else:
            print(f"  FAIL: Extraction took {elapsed:.1f}s (>= 30s)")

        # 7. Error Handling
        print("\n[7/7] Testing error handling...")
        print(f"  PASS: No exceptions thrown")

    except Exception as e:
        print(f"  FAIL: Exception occurred: {e}")
        results["error_handling"] = False

    # Summary
    print(f"\n{'='*60}")
    print("VALIDATION SUMMARY")
    print(f"{'='*60}")

    passed = sum(results.values())
    total = len(results)

    for check, status in results.items():
        symbol = "[X]" if status else "[ ]"
        print(f"  {symbol} {check.replace('_', ' ').title()}")

    print(f"\nResult: {passed}/{total} checks passed")

    if passed == total:
        print("Status: PRODUCTION READY")
    elif passed >= 5:
        print("Status: NEEDS MINOR FIXES")
    else:
        print("Status: NOT READY")

    print(f"{'='*60}\n")

    return results

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/validate_provider.py <provider_name> [sport]")
        sys.exit(1)

    provider_name = sys.argv[1]
    sport = sys.argv[2] if len(sys.argv) > 2 else "football"

    asyncio.run(validate_provider(provider_name, sport))
```

**Usage:**
```bash
python scripts/validate_provider.py kambi
python scripts/validate_provider.py snabbare basketball
python scripts/validate_provider.py gecko
```

---

## Provider Status Matrix

### Current Providers

| Provider | Sports | Markets | Normalization | Database | Performance | Error Handling | Status |
|----------|--------|---------|---------------|----------|-------------|----------------|--------|
| **Kambi** (Unibet) | PASS | PASS | PASS | PASS | PASS | PASS | PRODUCTION |
| **Spectate** (MrGreen, Betsson) | PASS | PASS | PASS | PASS | PASS | PASS | PRODUCTION |
| **Snabbare** (DOM) | PASS | PARTIAL | PASS | PASS | SLOW | PASS | STAGING |
| **Polymarket** | PASS | PASS | PASS | PASS | PASS | PASS | PRODUCTION |
| **Gecko** (Betsson) | PARTIAL | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | IN PROGRESS |

### Detailed Status

#### Kambi (Unibet)
- **Implementation:** `backend/src/providers/kambi.py`
- **Type:** API-based retriever
- **Sports:** All major sports supported
- **Markets:** 1x2, moneyline, over_under, spread
- **Performance:** < 10s per sport (bulk API)
- **Notes:** Reference implementation for API-based providers

#### Spectate (MrGreen, Betsson)
- **Implementation:** `backend/src/providers/spectate.py`
- **Type:** Browser-based retriever (API endpoints via GraphQL)
- **Sports:** Football, basketball, ice_hockey, tennis
- **Markets:** Full coverage with comprehensive MARKET_TYPE_MAP
- **Performance:** < 15s per sport
- **Notes:** GraphQL API, requires browser context

#### Snabbare (DOM Scraper)
- **Implementation:** `backend/src/providers/snabbare.py`
- **Type:** DOM scraper (Playwright)
- **Sports:** Football, ice_hockey
- **Markets:** 1x2, over_under (partial spread coverage)
- **Performance:** 20-40s per sport (page loads)
- **Notes:** Slower due to DOM parsing, fallback option

#### Polymarket (Truth Source)
- **Implementation:** `backend/src/providers/polymarket.py`
- **Type:** API-based retriever
- **Sports:** Football, basketball, tennis, MMA, American football
- **Markets:** Binary outcomes (home/away)
- **Performance:** < 5s per sport
- **Notes:** Used as fair odds reference for value detection

#### Gecko (In Progress)
- **Implementation:** `backend/src/providers/gecko.py`
- **Type:** Unknown (API or Browser)
- **Sports:** Unknown
- **Markets:** Unknown
- **Status:** Incomplete implementation, needs validation

---

## New Provider Checklist Template

Use this checklist when implementing a new provider:

```markdown
## Provider: [Name]

### Basic Information
- [ ] Provider name: _________
- [ ] Brand(s): _________
- [ ] Base URL: _________
- [ ] Retriever type: [ ] API  [ ] Browser  [ ] DOM
- [ ] Authentication required: [ ] Yes  [ ] No

### Implementation
- [ ] File created: `backend/src/providers/[name].py`
- [ ] Added to `backend/src/config/providers.yaml`
- [ ] Sports configured in `backend/src/config/sports.json`
- [ ] Retriever class extends `Retriever` or `BrowserRetriever`

### Validation Checklist

#### 1. Sports Coverage
- [ ] At least 1 sport supported
- [ ] Events returned for all configured sports
- [ ] Sport field correctly set in StandardEvent

#### 2. Event Discovery
- [ ] All events have home_team
- [ ] All events have away_team
- [ ] All events have sport
- [ ] start_time present (or None)
- [ ] league field populated when available

#### 3. Market Coverage
- [ ] Priority 1: Moneyline/1x2 present
- [ ] Priority 2: Over/under present with points
- [ ] Priority 2: Spread present with points
- [ ] All outcomes have odds > 1.0

#### 4. Normalization
- [ ] Team names lowercase
- [ ] Team names have no accents
- [ ] Common suffixes removed (FC, SC, etc.)
- [ ] Market types standardized
- [ ] Outcomes standardized

#### 5. Database Compliance
- [ ] Events have all required fields
- [ ] Odds have all required fields
- [ ] Point values present for spreads/totals
- [ ] No duplicate odds for same event/market/outcome

#### 6. Performance
- [ ] Single sport extraction < 30s
- [ ] Bulk endpoints used when available
- [ ] Caching implemented for repeated calls
- [ ] Connection pooling configured

#### 7. Error Handling
- [ ] HTTP errors handled gracefully
- [ ] Invalid events skipped (not crashed)
- [ ] Invalid markets skipped (not crashed)
- [ ] Invalid odds skipped (not crashed)
- [ ] Logging present for debug/errors

### Testing
- [ ] Manual extraction test successful
- [ ] Pipeline integration test successful
- [ ] Automated tests written
- [ ] Performance benchmarks met
- [ ] Error scenarios tested

### Documentation
- [ ] Provider added to Status Matrix
- [ ] Implementation notes documented
- [ ] Known limitations documented
- [ ] Example usage provided

### Final Review
- [ ] Code reviewed
- [ ] Tests passing
- [ ] No crashes/exceptions
- [ ] Ready for production

**Validation Date:** __________
**Validated By:** __________
**Status:** [ ] Production Ready  [ ] Needs Fixes  [ ] Not Ready
```

---

## Troubleshooting Guide

### Common Issues

#### Issue: No events returned
**Possible Causes:**
- Wrong URL or endpoint
- Authentication required
- Sport not available
- API response format changed

**Debug Steps:**
```python
# 1. Check raw API response
response = await session.get(url)
print(await response.text())

# 2. Verify sport configuration
from src.config.loader import ConfigLoader
config = ConfigLoader.get_sports_config()
print(config.get("football"))

# 3. Check provider status
provider = ExtractorFactory.get_provider("provider_name")
print(provider.is_available())
```

#### Issue: Team names not matching
**Possible Causes:**
- Normalization not applied
- Accents not removed
- Suffixes not stripped

**Debug Steps:**
```python
from src.matching.normalizer import normalize_team_name

raw_name = "Real Madrid CF"
normalized = normalize_team_name(raw_name)
print(f"{raw_name} -> {normalized}")

# Expected: madrid
```

#### Issue: Markets missing
**Possible Causes:**
- Market type not in MARKET_TYPE_MAP
- Unknown market type skipped
- Provider doesn't offer market

**Debug Steps:**
```python
# Add debug logging to see skipped markets
logger.debug(f"Raw market type: {raw_market['type']}")
logger.debug(f"Mapped to: {MARKET_TYPE_MAP.get(raw_market['type'])}")
```

#### Issue: Duplicate odds in database
**Possible Causes:**
- Same event extracted twice
- Deduplication not working
- Canonical ID mismatch

**Debug Steps:**
```python
from src.pipeline.utils import generate_canonical_id

# Check if canonical IDs match
event1 = StandardEvent(...)
event2 = StandardEvent(...)

id1 = generate_canonical_id(event1)
id2 = generate_canonical_id(event2)

print(f"ID1: {id1}")
print(f"ID2: {id2}")
print(f"Match: {id1 == id2}")
```

---

## Appendix

### Reference Files

| File | Purpose |
|------|---------|
| `backend/src/core/retriever.py` | Base Retriever class, StandardEvent definition |
| `backend/src/db/models.py` | Database schema (Event, Odds, Provider) |
| `backend/src/matching/normalizer.py` | Team name normalization logic |
| `backend/src/matching/matcher.py` | Fuzzy matching for team names |
| `backend/src/matching/aliases.yaml` | Team name aliases |
| `backend/src/pipeline/utils.py` | Canonical ID generation |
| `backend/src/config/sports.json` | Sports/leagues configuration |
| `backend/src/config/providers.yaml` | Provider configurations |
| `backend/src/config/loader.py` | Config loading and validation |

### External Resources

- **Kambi API Documentation:** (internal reference)
- **Spectate GraphQL Schema:** (reverse-engineered)
- **Polymarket API:** https://docs.polymarket.com

### Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-01-22 | Initial validation guide created |

---

**Last Updated:** 2026-01-22
**Maintained By:** OddOpp Development Team
