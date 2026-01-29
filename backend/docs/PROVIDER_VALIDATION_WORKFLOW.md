# Provider Validation & Debugging Workflow

Systematic workflow for validating, debugging, and comparing betting providers.

## Quick Start

```bash
# Full validation of a provider
python scripts/validate_provider_full.py unibet

# Debug a specific sport
python scripts/debug_provider.py unibet --sport basketball --verbose

# Compare multiple providers
python scripts/compare_providers.py unibet betsson pinnacle
```

---

## Scripts Overview

| Script | Purpose | Output |
|--------|---------|--------|
| `validate_provider_full.py` | Comprehensive multi-sport validation | Console + JSON report |
| `debug_provider.py` | Investigate specific extraction issues | Detailed analysis |
| `compare_providers.py` | Side-by-side provider comparison | Comparison tables |

---

## 1. Full Validation

The main validation script tests a provider across all configured sports and generates a comprehensive report.

### Usage

```bash
# Basic validation
python scripts/validate_provider_full.py unibet

# Save JSON report
python scripts/validate_provider_full.py unibet --json

# Test specific sports only
python scripts/validate_provider_full.py unibet --sports football,basketball,ice_hockey

# List available providers
python scripts/validate_provider_full.py --list
```

### Output Report

```
============================================================
PROVIDER VALIDATION REPORT: unibet
Date: 2026-01-28 14:30:00
============================================================

SPORTS COVERAGE (8/12 with events)
------------------------------------------------------------
Sport                | Events  |   Time  | Status
------------------------------------------------------------
football             |     405 |    1.2s | [OK]
basketball           |     277 |    0.9s | [OK]
ice_hockey           |     202 |    0.8s | [OK]
tennis               |     186 |    0.7s | [OK]
american_football    |      12 |    0.3s | [OK]
baseball             |       0 |    0.1s | [NO_EVENTS]
cricket              |      34 |    0.5s | [OK]
mma                  |      32 |    0.4s | [OK]
------------------------------------------------------------
TOTAL                |   1,148 |    4.9s |

MARKET COVERAGE
------------------------------------------------------------
Market Type     |   Count |  Events | Coverage
------------------------------------------------------------
1x2             |    2340 |    1170 |    91.7%
moneyline       |     212 |     106 |     8.3%
over_under      |    4680 |    1170 |    91.7%
spread          |    2106 |    1053 |    82.5%
------------------------------------------------------------
TOTAL MARKETS   |   17770 |

DATA QUALITY
------------------------------------------------------------
[X] All team names normalized (lowercase)
[X] All odds > 1.0 (valid decimal)
[X] Point values present for spreads/totals
[ ] Start times present (23 missing)
[X] No duplicate events

PERFORMANCE
------------------------------------------------------------
Total extraction time: 4.9s
Average per sport: 0.61s
Slowest sport: football (1.2s)

STATUS: PRODUCTION READY (9/10 checks passed)
============================================================
```

### JSON Output

Reports are saved to `backend/reports/{provider}_{timestamp}.json`:

```json
{
  "provider_id": "unibet",
  "timestamp": "2026-01-28 14:30:00",
  "sports": [
    {"sport": "football", "events": 405, "time_seconds": 1.2, "status": "OK"}
  ],
  "markets": [
    {"market_type": "1x2", "count": 2340, "events_with_market": 1170, "coverage_pct": 91.7}
  ],
  "quality_checks": {
    "names_normalized": true,
    "odds_valid": true,
    "points_present": true,
    "start_times_present": false,
    "no_duplicates": true,
    "events_missing_start_time": 23
  },
  "total_events": 1148,
  "total_markets": 17770,
  "total_time_seconds": 4.9,
  "status": "PRODUCTION READY",
  "checks_passed": 9,
  "checks_total": 10
}
```

---

## 2. Debug Provider

For investigating specific issues with detailed output.

### Usage

```bash
# Basic debug
python scripts/debug_provider.py unibet

# Debug specific sport
python scripts/debug_provider.py unibet --sport basketball

# Verbose logging (shows API calls)
python scripts/debug_provider.py unibet --sport football --verbose

# Limit sample output
python scripts/debug_provider.py unibet --sport football --limit 3
```

### Features

1. **Provider Config** - Shows provider configuration
2. **Sample Events** - Detailed view of extracted events
3. **Normalization Analysis** - Checks team name formatting
4. **Market Analysis** - Market type distribution and issues
5. **Duplicate Check** - Identifies duplicate events

### Example Output

```
============================================================
 DEBUG: unibet / football
============================================================
Timestamp: 2026-01-28 14:35:00
Limit: 10
Verbose: False

============================================================
 PROVIDER CONFIG
============================================================
  ID: unibet
  Name: Unibet
  Retriever: kambi
  Domain: www.unibet.com
  API Base: https://eu-offering-api.kambicdn.com/offering/v2018/ub

============================================================
 EXTRACTION
============================================================
Extracting football events...
Extracted 405 events in 1.23s

============================================================
 SAMPLE EVENTS (first 10)
============================================================

  Event ID: 12345678
  Name: Arsenal vs Chelsea
  Home: arsenal
  Away: chelsea
  Sport: football
  League: Premier League
  Start: 2026-01-29T15:00:00Z
  Markets: 45
  Market Details:
    [1] 1X2
        - 1: 2.15
        - X: 3.40
        - 2: 3.25
    [2] Over/Under
        - Over 2.5: 1.85 (2.5)
        - Under 2.5: 1.95 (2.5)
    ... and 43 more markets

============================================================
 NORMALIZATION ANALYSIS
============================================================
No normalization issues found in sampled events

============================================================
 MARKET ANALYSIS
============================================================
Market type distribution (8532 total):
  1X2: 405
  Over/Under: 3240
  Handicap: 1620
  Both Teams To Score: 810
  ...

============================================================
 SUMMARY
============================================================
Total events: 405
Total markets: 8532
Extraction time: 1.23s
Events with start_time: 405/405
Events with league: 403/405
```

---

## 3. Compare Providers

Compare multiple providers side-by-side.

### Usage

```bash
# Live comparison (extracts from all providers)
python scripts/compare_providers.py unibet betsson pinnacle

# Compare specific sports
python scripts/compare_providers.py unibet betsson --sport football,basketball

# Historical comparison (from database)
python scripts/compare_providers.py --historical --runs 5

# List providers
python scripts/compare_providers.py --list
```

### Output

```
================================================================================
LIVE PROVIDER COMPARISON
Date: 2026-01-28 14:40:00
Providers: unibet, betsson, pinnacle
Sports: football, basketball, ice_hockey, american_football
================================================================================

Testing football...
  unibet: 405 events
  betsson: 398 events
  pinnacle: 512 events
Testing basketball...
  unibet: 277 events
  betsson: 265 events
  pinnacle: 340 events

================================================================================
EVENT COUNT COMPARISON
================================================================================
Sport              |       unibet |      betsson |     pinnacle
--------------------------------------------------------------
football           |          405 |          398 |          512
basketball         |          277 |          265 |          340
ice_hockey         |          202 |          195 |          285
american_football  |           12 |           10 |           45
--------------------------------------------------------------
TOTAL              |          896 |          868 |         1182

================================================================================
EXTRACTION TIME (seconds)
================================================================================
Sport              |       unibet |      betsson |     pinnacle
--------------------------------------------------------------
football           |          1.2 |          3.5 |          0.8
basketball         |          0.9 |          2.8 |          0.6
ice_hockey         |          0.8 |          2.4 |          0.5
american_football  |          0.3 |          1.2 |          0.2
--------------------------------------------------------------
TOTAL              |          3.2 |          9.9 |          2.1

================================================================================
MARKET TYPE COVERAGE
================================================================================

Market Type          |    unibet    |    betsson   |   pinnacle
--------------------------------------------------------------
1x2                  |      X       |      X       |      X
moneyline            |      X       |      X       |      X
over_under           |      X       |      X       |      X
spread               |      X       |      X       |      X
================================================================================
```

---

## Validation Workflow

### For New Providers

1. **Initial Test**
   ```bash
   python scripts/validate_provider_full.py {provider}
   ```

2. **Review Results**
   - Check sports coverage (which sports return events)
   - Check market coverage (1x2, spread, over/under present?)
   - Check data quality (normalization, valid odds)
   - Check performance (< 30s per sport)

3. **Debug Issues**
   ```bash
   python scripts/debug_provider.py {provider} --sport {failing_sport} --verbose
   ```

4. **Fix Provider Code**
   - Address normalization issues
   - Fix market parsing
   - Handle edge cases

5. **Re-Validate**
   ```bash
   python scripts/validate_provider_full.py {provider} --json
   ```

6. **Update Documentation**
   - Add results to `backend/docs/validated.md`

### For Existing Providers

1. **Periodic Validation**
   ```bash
   # Weekly check
   python scripts/validate_provider_full.py {provider} --json
   ```

2. **Compare Performance**
   ```bash
   python scripts/compare_providers.py {provider1} {provider2}
   ```

3. **Historical Analysis**
   ```bash
   python scripts/compare_providers.py --historical --runs 10
   ```

---

## Status Definitions

| Status | Meaning | Action |
|--------|---------|--------|
| PRODUCTION READY | All checks pass | Enable in production |
| STAGING | Minor issues | Fix before production |
| NEEDS WORK | Multiple failures | Debug and fix |

### Checks Performed

1. **Sports Coverage** - At least 3 sports return events
2. **Core Markets** - Has 1x2/moneyline AND over_under
3. **Team Names** - All normalized to lowercase
4. **Odds Valid** - All odds > 1.0
5. **Points Present** - Spread/total markets have point values
6. **Start Times** - 90%+ events have start_time
7. **No Duplicates** - No duplicate event IDs
8. **Performance** - Total extraction < 2 minutes
9. **No Errors** - No sports failed with errors/timeouts

---

## Troubleshooting

### No Events Returned

```bash
python scripts/debug_provider.py {provider} --sport {sport} --verbose
```

Check:
- Is the sport supported by this provider's API?
- Are there currently live/upcoming events?
- Is the API returning errors?

### Normalization Failures

The debug script identifies specific issues:
```
Found 5 normalization issues:
  - Home not lowercase: 'Arsenal FC'
  - Away not lowercase: 'Chelsea FC'
```

Fix in the provider's `parse()` method.

### Missing Markets

Check market type mappings in the provider:
```bash
python scripts/debug_provider.py {provider} --sport football
```

Review the "MARKET ANALYSIS" section for unmapped types.

### Performance Issues

```bash
# Compare against known-fast providers
python scripts/compare_providers.py {slow_provider} pinnacle --sport football
```

Consider:
- Reduce concurrent requests
- Add caching
- Optimize parsing logic

---

## File Locations

| File | Purpose |
|------|---------|
| `backend/scripts/validate_provider_full.py` | Full validation script |
| `backend/scripts/debug_provider.py` | Debug/investigation script |
| `backend/scripts/compare_providers.py` | Provider comparison |
| `backend/reports/` | JSON validation reports |
| `backend/docs/validated.md` | Provider validation status |
