# Gecko/Betsson Platform Implementation

## Overview

Betsson Group sites (Betsson, Betsafe, NordicBet, ComeOn, Coolbet) use the **Gecko sportsbook platform**, built on BetRadar/Sportradar infrastructure.

## Provider Status

**Implemented:**
- ✓ GeckoRetriever base class (`backend/src/providers/gecko.py`)
- ✓ Factory integration (`backend/src/factory.py`)
- ✓ Configuration for Betsson, Betsafe, NordicBet (`backend/src/config/providers.yaml`)
- ✓ Browser transport with security bypass

**Pending:**
- Data parser implementation (parse events from optimal API endpoint)

## Optimal API Endpoint (No Live Events)

After analyzing 88 API endpoints, the most efficient is:

### `/api/sb/v1/widgets/view/v1`

**Parameters:**
```
categoryIds={sport_category_id}
configurationKey=sportsbook.category
excludedWidgetKeys=sportsbook.category.live
priceFormats=1
```

**Performance:**
- Returns **427 events** in single call
- Response size: ~2.5MB
- Contains full event data (teams, times, odds, markets)

**Alternative Endpoints Tested:**
- `/widgets/categories/v2` - 3,129 fixture IDs but NO event data (2.2MB)
- `/widgets/event-market/v1` - Individual events (4-16 per call, requires many requests)
- `/competitions/liveEvents` - Live events only (skip for upcoming events)

## Sport Category IDs

```python
CATEGORY_IDS = {
    "football": "1",
    "basketball": "2",
    "tennis": "3",
    "ice_hockey": "4",
    "american_football": "5",
    "baseball": "6",
    "handball": "7",
}
```

## API Base URLs

- **Betsson**: `https://www.betsson.com/api/sb/v1/`
- **Betsafe**: `https://www.betsafe.com/sv/api/sb/v1/`
- **NordicBet**: `https://www.nordicbet.com/sv/api/sb/v1/`

All share the same Gecko API structure.

## Response Structure

```json
{
  "data": {
    "widgets": [
      {
        "key": "sportsbook.category.upcoming",
        "type": "EventsTableMasterDetail",
        "version": "v1",
        "data": {
          "fixtureGroupings": {},
          "items": [
            {
              "label": "Competition Name",
              "fixtures": {
                "f-{fixture_id}": {
                  "homeTeam": {"name": "Team A"},
                  "awayTeam": {"name": "Team B"},
                  "startTime": "2026-01-23T19:00:00Z",
                  "markets": {
                    "m-{market_id}": {
                      "type": "FTCS",
                      "selections": {
                        "s-{selection_id}": {
                          "label": "1",
                          "odds": 2.50
                        }
                      }
                    }
                  }
                }
              }
            }
          ]
        }
      }
    ]
  }
}
```

## Implementation Strategy

1. **Navigate** to sport page (`/sv/odds/{sport_slug}`)
2. **Intercept** API response for `/widgets/view/v1`
3. **Parse** widgets array → items → fixtures
4. **Extract** event data: teams, time, odds
5. **Map** to StandardEvent format

## Security Requirements

- Requires **BrowserTransport** (headless=False)
- Gecko has bot detection (WAF, fingerprinting)
- Browser automation with stealth mode needed

## Next Steps

1. Implement parser for `widgets/view/v1` response structure
2. Map Gecko market types to standard market format
3. Test with all configured providers (Betsson, Betsafe, NordicBet)
4. Add ComeOn, Coolbet configurations
