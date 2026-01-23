# Betsson Provider Validation Report

**Provider:** Betsson (Gecko Platform)
**Date:** 2026-01-22
**Status:** NOT PRODUCTION READY
**Validation Result:** 0/7 checks passed

---

## Executive Summary

The Betsson provider (Gecko platform) is currently **incomplete** and **not production-ready**. The implementation has critical issues that prevent it from extracting any usable events:

1. **Returns empty markets** (line 273: `markets=[]`)
2. **Data structure mismatch** - Code expects wrong API response format
3. **Missing normalization** - No team name normalization applied
4. **Missing market mapping** - No market type or outcome standardization
5. **Cannot complete extraction** - Returns 0 events

---

## Validation Results

### [X] 1. Sports Coverage
- **Status:** FAIL
- **Issue:** No events returned during extraction
- **Details:** Provider initializes and navigates to Betsson site successfully, captures API responses, but fails to parse any events from the response data.

### [X] 2. Event Discovery
- **Status:** FAIL
- **Issue:** No events to validate
- **Expected:** Events with `sport`, `home_team`, `away_team`, `start_time`
- **Actual:** Empty list returned

### [X] 3. Market Coverage
- **Status:** FAIL
- **Issue:** Markets array is hardcoded to empty list
- **Code Location:** `backend/src/providers/gecko.py:273`
- **Problem:**
  ```python
  return StandardEvent(
      ...
      markets=[],  # CRITICAL BUG: Always empty!
      odds=odds_data,  # Created but never used
      ...
  )
  ```
- **Expected:** At minimum, moneyline/1x2, over_under, and spread markets
- **Actual:** Empty markets list

### [X] 4. Normalization
- **Status:** FAIL
- **Issue:** No team name normalization applied
- **Code Location:** `backend/src/providers/gecko.py:229-230`
- **Problem:**
  ```python
  home_team = fixture_data.get('homeTeam', {}).get('name', '')
  away_team = fixture_data.get('awayTeam', {}).get('name', '')
  # Used directly without normalization!
  ```
- **Missing:**
  - No lowercase conversion
  - No accent removal
  - No suffix stripping (FC, SC, etc.)
  - No alias mapping

### [X] 5. Database Compliance
- **Status:** FAIL
- **Issue:** Cannot validate - no events returned
- **Additional Issues:**
  - Markets array empty (violates "at least one market" requirement)
  - No odds validation (odds > 1.0 check)
  - No unique constraint handling

### [X] 6. Performance
- **Status:** UNKNOWN (Cannot test without working extraction)
- **Expected:** < 30s per sport
- **Actual:** Extraction runs but returns no data

### [X] 7. Error Handling
- **Status:** PARTIAL PASS
- **Good:** Has try/catch blocks, logs errors, returns empty list on failure
- **Issue:** Silently returns empty list instead of identifying root cause

---

## Critical Issues

### Issue 1: Data Structure Mismatch (HIGH PRIORITY)

**Problem:** The parsing code expects a different API response structure than what Betsson actually returns.

**Code Expectation** (`backend/src/providers/gecko.py:186-196`):
```python
categories = items.get('categories', {})
category = categories.get(category_id, {})  # e.g., category_id = "1"

competitions = category.get('competitions', {})  # EXPECTS THIS
for comp_id, comp_data in competitions.items():
    fixtures = comp_data.get('fixtures', {})
```

**Actual API Response** (from `scrap/betsson_categories_debug.json`):
```json
{
  "data": {
    "items": {
      "indexBySlug": {...},
      "categories": {
        "1": {
          "label": "Fotboll",
          "regions": {...}  // NOT "competitions"!
        }
      }
    }
  }
}
```

**Impact:** The code looks for `category.competitions` but the API returns `category.regions`. This causes zero events to be extracted.

**Solution Required:**
1. Analyze actual Betsson API response structure
2. Understand the relationship between categories -> regions -> competitions -> fixtures
3. Rewrite `_parse_events()` method to match actual structure
4. May need to look for normalized data at `items` level (e.g., `items.regions`, `items.competitions`, `items.fixtures`)

---

### Issue 2: Empty Markets Array (CRITICAL)

**Problem:** Markets are hardcoded to empty array instead of being populated.

**Code Location:** `backend/src/providers/gecko.py:245-273`

**Current Behavior:**
```python
def _parse_fixture(...):
    # Lines 245-260: Creates odds_data dict
    odds_data = {}
    markets = fixture_data.get('markets', {})
    for market_id, market_data in markets.items():
        market_type = market_data.get('type', '')
        selections = market_data.get('selections', {})
        for selection_id, selection_data in selections.items():
            outcome = selection_data.get('label', '')
            odd_value = selection_data.get('odds')
            if outcome and odd_value:
                odds_data[outcome] = float(odd_value)

    # Line 273: IGNORES odds_data completely!
    return StandardEvent(
        ...
        markets=[],  # Should be populated with Market objects
        odds=odds_data  # Wrong format (should be in markets)
    )
```

**Solution Required:**
1. Convert `odds_data` dict into proper `Market` and `Outcome` objects
2. Apply market type standardization (1x2, moneyline, over_under, spread)
3. Apply outcome standardization (home, away, draw, over, under)
4. Extract point values for spreads/totals
5. Populate `markets` list with standardized Market objects

**Example Fix:**
```python
from ..core import Market, Outcome

def _parse_fixture(...):
    ...
    markets_list = []

    # Parse markets from fixture_data
    raw_markets = fixture_data.get('markets', {})
    for market_id, market_data in raw_markets.items():
        # Map market type to standard name
        market_type = self._map_market_type(market_data.get('type', ''))
        if not market_type:
            continue  # Skip unknown markets

        # Extract point value (for spreads/totals)
        point = market_data.get('line') or market_data.get('point')

        # Parse outcomes
        outcomes = []
        selections = market_data.get('selections', {})
        for sel_id, sel_data in selections.items():
            outcome_name = self._map_outcome(sel_data.get('label', ''))
            odds_value = sel_data.get('odds')

            if outcome_name and odds_value and odds_value > 1.0:
                outcomes.append(Outcome(
                    outcome=outcome_name,
                    odds=float(odds_value)
                ))

        if outcomes:
            markets_list.append(Market(
                market_type=market_type,
                outcomes=outcomes,
                point=point
            ))

    return StandardEvent(
        ...
        markets=markets_list,  # Properly populated!
    )
```

---

### Issue 3: No Normalization (HIGH PRIORITY)

**Problem:** Team names are used directly from API without normalization.

**Code Location:** `backend/src/providers/gecko.py:229-230`

**Current Code:**
```python
home_team = fixture_data.get('homeTeam', {}).get('name', '')
away_team = fixture_data.get('awayTeam', {}).get('name', '')
# Used directly in StandardEvent without normalization
```

**Issues:**
- Team names may have uppercase letters
- May contain accents/diacritics (e.g., "Malmö FF")
- May have suffixes like "FC", "SC", "IF"
- Won't match with other providers' normalized names

**Solution Required:**
```python
from ..matching.normalizer import normalize_team_name

home_team_raw = fixture_data.get('homeTeam', {}).get('name', '')
away_team_raw = fixture_data.get('awayTeam', {}).get('name', '')

home_team = normalize_team_name(home_team_raw)
away_team = normalize_team_name(away_team_raw)
```

---

### Issue 4: Missing Market/Outcome Mapping

**Problem:** No mapping of provider-specific market types and outcomes to standardized names.

**Required Mappings:**

**Market Types:**
```python
MARKET_TYPE_MAP = {
    "match_winner": "1x2",
    "winner": "moneyline",
    "totals": "over_under",
    "total": "over_under",
    "handicap": "spread",
    "asian_handicap": "spread",
    # Add more as discovered from API
}
```

**Outcomes:**
```python
OUTCOME_MAP = {
    "home": "home",
    "away": "away",
    "draw": "draw",
    "1": "home",
    "2": "away",
    "X": "draw",
    "over": "over",
    "under": "under",
    # Add more as discovered from API
}
```

**Example Reference:** See `backend/src/providers/spectate.py:28-60` for comprehensive mapping.

---

## Missing Features

### 1. Market Type Standardization
- **Priority:** HIGH
- **Required For:** Market coverage validation (Priority 1 & 2 markets)
- **Implementation Needed:**
  - Create `MARKET_TYPE_MAP` dictionary
  - Implement `_map_market_type()` method
  - Only extract priority 1 & 2 markets (1x2, moneyline, over_under, spread)
  - Log and skip unknown market types

### 2. Outcome Standardization
- **Priority:** HIGH
- **Required For:** Database compliance, matching across providers
- **Implementation Needed:**
  - Create `OUTCOME_MAP` dictionary
  - Implement `_map_outcome()` method
  - Normalize all outcomes to: home, away, draw, over, under

### 3. Point Value Extraction
- **Priority:** HIGH
- **Required For:** Spreads and totals markets
- **Implementation Needed:**
  - Extract point/line values from market data
  - Store in `Market.point` field
  - Validate point exists for over_under and spread markets

### 4. Data Validation
- **Priority:** MEDIUM
- **Implementation Needed:**
  - Validate odds > 1.0
  - Skip events with missing teams
  - Skip events with no markets
  - Skip started events (if upcoming-only)

---

## Data Structure Analysis

Based on exploration of `scrap/betsson_categories_debug.json`:

### Actual API Structure

```
data:
  items:
    indexBySlug: {...}      // URL path to ID mappings
    categories:
      "1":                   // Football category
        label: "Fotboll"
        regions: {...}       // NOT "competitions"!
        slug: "fotboll"
        ...
      "2":                   // Basketball
        ...
```

### Questions to Resolve

1. Where are competitions stored?
   - Option A: In `category.regions` dict
   - Option B: At `items.competitions` level (normalized)
   - Need to explore: Check if `items` has `regions`, `competitions`, `fixtures` keys

2. Where are fixtures/events stored?
   - Option A: Nested in `competitions[id].fixtures`
   - Option B: At `items.fixtures` level (normalized, cross-referenced)

3. How to navigate from category -> competition -> fixture?
   - May need to follow ID references rather than nested structure
   - Example: `category.regions = {"117": {...}}` -> look up `items.regions["117"]`

### Recommended Investigation

Create a debug script to fully map the API structure:

```python
import json

with open('scrap/betsson_categories_debug.json') as f:
    data = json.load(f)

items = data['data']['items']

# Check what keys exist at items level
print("Items keys:", list(items.keys()))

# If there are normalized collections:
for key in ['regions', 'competitions', 'fixtures']:
    if key in items:
        print(f"Found items.{key}: {len(items[key])} entries")
        # Examine first entry structure
        first_id = list(items[key].keys())[0]
        first_item = items[key][first_id]
        print(f"  Sample {key} keys: {list(first_item.keys())}")

# Check category structure
cat1 = items['categories']['1']
print(f"\nCategory 1 'regions' type: {type(cat1['regions'])}")
print(f"Category 1 'regions' sample keys: {list(cat1['regions'].keys())[:5]}")

# Try to find the path to a fixture
# Starting from category -> regions -> ? -> fixtures?
```

---

## Recommended Fix Priority

### Phase 1: Data Structure (CRITICAL - BLOCKS ALL OTHER WORK)
1. [ ] Investigate actual Betsson API response structure
2. [ ] Map navigation path: categories -> ? -> fixtures
3. [ ] Rewrite `_parse_events()` to match actual structure
4. [ ] Verify fixture data is being extracted

### Phase 2: Core Functionality (HIGH PRIORITY)
5. [ ] Implement team name normalization
6. [ ] Create market type mapping (MARKET_TYPE_MAP)
7. [ ] Create outcome mapping (OUTCOME_MAP)
8. [ ] Fix markets array population (remove hardcoded `[]`)
9. [ ] Implement `_map_market_type()` and `_map_outcome()` methods

### Phase 3: Market Extraction (HIGH PRIORITY)
10. [ ] Extract Priority 1 markets (1x2/moneyline)
11. [ ] Extract Priority 2 markets (over_under, spread)
12. [ ] Extract point values for spreads/totals
13. [ ] Create Market and Outcome objects properly

### Phase 4: Validation & Error Handling (MEDIUM PRIORITY)
14. [ ] Validate odds > 1.0
15. [ ] Skip events with missing fields
16. [ ] Skip events with no markets
17. [ ] Add data quality logging

### Phase 5: Testing (MEDIUM PRIORITY)
18. [ ] Run validation script successfully
19. [ ] Verify all 7 validation checks pass
20. [ ] Test with multiple sports
21. [ ] Verify data matches expectations

---

## Comparison with Working Providers

### Spectate (GOOD EXAMPLE)

Spectate uses similar GraphQL API structure and successfully:

1. **Maps market types** (`backend/src/providers/spectate.py:28-43`):
   ```python
   MARKET_TYPE_MAP = {
       "fullTimeResult": "1x2",
       "total": "over_under",
       "handicap": "spread",
       ...
   }
   ```

2. **Maps outcomes** (`backend/src/providers/spectate.py:45-60`):
   ```python
   OUTCOME_MAP = {
       "home": "home",
       "away": "away",
       "draw": "draw",
       "over": "over",
       "under": "under"
   }
   ```

3. **Normalizes team names** (`backend/src/providers/spectate.py:183`):
   ```python
   from ..matching.normalizer import normalize_team_name
   home_team = normalize_team_name(raw_event["participants"]["home"]["name"])
   ```

4. **Creates proper Market objects** (`backend/src/providers/spectate.py:195-220`):
   ```python
   markets = []
   for raw_market in raw_event["markets"]:
       market_type = MARKET_TYPE_MAP.get(raw_market["type"])
       if not market_type:
           continue

       outcomes = []
       for raw_outcome in raw_market["outcomes"]:
           outcome = OUTCOME_MAP.get(raw_outcome["type"])
           if outcome and raw_outcome["odds"] > 1.0:
               outcomes.append(Outcome(
                   outcome=outcome,
                   odds=raw_outcome["odds"]
               ))

       markets.append(Market(
           market_type=market_type,
           outcomes=outcomes,
           point=raw_market.get("line")
       ))
   ```

**Recommendation:** Follow Spectate's pattern for Gecko implementation.

---

## Next Steps

### Immediate (Before Any Other Work)
1. **Understand API structure:**
   - Create debug script to fully explore `betsson_categories_debug.json`
   - Map the actual path from category -> fixtures
   - Document the real data structure

2. **Fix data extraction:**
   - Rewrite `_parse_events()` to use correct structure
   - Verify events are being extracted
   - Run basic test to get > 0 events

### Short Term (Core Functionality)
3. **Add normalization:**
   - Import `normalize_team_name` from matching module
   - Apply to home_team and away_team

4. **Fix markets:**
   - Remove `markets=[]` hardcode
   - Create MARKET_TYPE_MAP and OUTCOME_MAP
   - Implement market/outcome mapping methods
   - Populate markets list with Market objects

### Medium Term (Production Ready)
5. **Validate and test:**
   - Run validation script
   - Fix issues until all 7 checks pass
   - Test with multiple sports
   - Add to active providers

---

## Files to Review

| File | Purpose | Status |
|------|---------|--------|
| `backend/src/providers/gecko.py` | Main provider implementation | NEEDS MAJOR FIXES |
| `backend/src/providers/spectate.py` | Reference for mapping/normalization | GOOD REFERENCE |
| `backend/src/matching/normalizer.py` | Team name normalization | USE THIS |
| `backend/src/core/retriever.py` | Market/Outcome classes | USE THESE |
| `scrap/betsson_categories_debug.json` | Actual API response | ANALYZE THIS |

---

## Conclusion

**Current Status:** NOT PRODUCTION READY (0/7 validation checks passed)

**Main Blockers:**
1. Data structure mismatch preventing event extraction
2. Empty markets array preventing data usage
3. Missing normalization causing match failures

**Effort Required:** HIGH (3-5 hours estimated)
- 1-2 hours: API structure investigation and fix
- 1-2 hours: Market extraction and mapping
- 1 hour: Testing and validation

**Recommendation:** Do not activate Betsson provider until all Phase 1 and Phase 2 items are completed and validated.

---

**Report Generated:** 2026-01-22
**Validation Framework:** `backend/docs/validated.md`
**Validation Script:** `scripts/validate_provider.py`
