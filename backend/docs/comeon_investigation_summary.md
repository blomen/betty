# ComeOn Provider Investigation Summary

**Date:** 2026-01-24
**Status:** STAGING (Not Production Ready)
**Implementation:** `backend/src/providers/comeon_enhanced.py`

## Executive Summary

ComeOn provider validation revealed a **critical platform limitation**: the web interface only exposes **29 "featured" events** despite API metadata showing 1,306 total events across 250 leagues. League URLs are cosmetic and don't filter content - all pages show the same events.

**Verdict:** Not recommended for production use due to low event coverage (29 vs 1,306 available).

## Validation Results

| Check | Status | Details |
|-------|--------|---------|
| Sports Coverage | PASS | Football supported |
| Event Discovery | **FAIL** | Only 29 events (expected 100+) |
| Market Coverage | PASS | 93% with 1x2 markets |
| Normalization | PASS | Team names properly normalized |
| Database Compliance | N/A | Not tested due to low volume |
| Performance | **FAIL** | 17.5 minutes for 29 events (36s/event) |
| Error Handling | PASS | Handles failures gracefully |

**Overall:** 3/5 checks passed → **STAGING**

## Technical Architecture

### Platform Details
- **Provider:** ComeOn Group (acquired by Cherry AB 2017)
- **Technology:** WebSocket/RSocket protocol (not REST API)
- **API Endpoint:** `wss://www.comeon.com/sportsbook-api/websocket`
- **Message Format:** Binary RSocket frames with JSON payloads

### Extraction Methods Tested

1. **Direct API Calls** - FAIL
   - No `/api/events` endpoint exists
   - Only `/api/leagues` metadata available

2. **API Interception** - FAIL
   - No REST API calls made by frontend
   - All data via WebSocket/RSocket

3. **WebSocket Interception** - PARTIAL SUCCESS
   - Captures INITIAL_STATE messages with events
   - Only receives "featured" events (29 total)
   - Binary RSocket protocol requires frame decoding

4. **DOM Scraping** - PARTIAL SUCCESS
   - Can extract visible events from page
   - Same 29 events as WebSocket
   - Faster but same coverage limitation

5. **Comprehensive League Loading** - FAIL
   - Loaded all 250 leagues individually
   - All pages show same 29 event IDs
   - League URLs don't filter content

6. **Event Detail Navigation** - NOT TESTED
   - Would require navigating to each event page
   - 29 events only, not full catalog

## Critical Discovery

**All league pages show identical content:**

```
Premier League (134): Events [2988525, 3010954, 2997939, 3001899, 3010967]
LaLiga (171):         Events [2988525, 3010954, 2997939, 3001899, 3010967]
Bundesliga (102):     Events [2988525, 3010954, 2997939, 3001899, 3010967]
Championship (194):   Events [2988525, 3010954, 2997939, 3001899, 3010967]
```

**Conclusion:** League URLs are cosmetic navigation elements. The website only exposes a curated set of "featured" or "upcoming" events, not the full catalog.

## Performance Analysis

**Extraction Performance:**
- **Time:** 17.5 minutes for 29 events
- **Rate:** 36 seconds per event
- **Bottleneck:** Browser navigation + WebSocket wait time
- **Target:** <30s total (failed by 34x)

**Why So Slow:**
- Loads 250 league pages (1.5s wait per page)
- 250 pages × 1.5s = 6.25 minutes just waiting
- Additional time for page navigation and WebSocket setup

## Data Quality

**Events Extracted:** 29
**Unique Leagues:** 7
- Premier League: 7 events
- Bundesliga: 6 events
- Eredivisie: 4 events
- Serie A: 3 events
- Pro League: 3 events
- Ligue 1: 3 events
- LaLiga: 3 events

**Markets:** 93% with 1x2 odds (27/29 events)
**Normalization:** PASS - all team names lowercase
**Total Odds:** 81

## Platform Limitations

1. **Limited Web Access**
   - Only 29 "featured" events accessible via web interface
   - Full catalog (1,306 events) NOT accessible

2. **No Public API**
   - No REST endpoint for events
   - Only WebSocket/RSocket protocol

3. **Cosmetic League URLs**
   - `/league/134` doesn't filter to Premier League events
   - All league pages show same featured events

4. **Complex Protocol**
   - Binary RSocket framing
   - Requires browser automation
   - Cannot use simple HTTP requests

## Comparison with Other Providers

| Provider | Events | Time | Method |
|----------|--------|------|--------|
| Kambi (Unibet) | 800+ | <30s | REST API |
| Spectate (Betsson) | 600+ | <30s | GraphQL API |
| Pinnacle | 1000+ | <30s | Guest API |
| **ComeOn** | **29** | **1050s** | **WebSocket/RSocket** |

## Recommendation

### For Production Use
**DO NOT USE** - Coverage too low (29 events vs 1,306 in database)

### Alternative Providers
- **Kambi** (Unibet, LeoVegas, Expekt): 800+ events, <30s, REST API
- **Spectate** (Betsson, MrGreen): 600+ events, <30s, GraphQL
- **Pinnacle**: 1000+ events, <30s, Guest API

### If ComeOn Coverage Needed
- Use as **supplement only** (provides 29 "featured" events)
- Accept low volume as platform limitation
- Consider if featured events have value (e.g., high-liquidity markets)

## Implementation Notes

### Current Code
- **File:** `backend/src/providers/comeon_enhanced.py`
- **Parent:** `BrowserRetriever`
- **Transport:** Playwright browser automation
- **Protocol:** WebSocket/RSocket with binary frame decoding

### Key Functions
- `_decode_rsocket_frame()`: Binary frame decoder
- `_get_leagues()`: Fetches league metadata via `/api/leagues`
- `_parse_websocket_event()`: Parses INITIAL_STATE event data

### Configuration
```yaml
# backend/src/config/providers.yaml
comeon:
  id: comeon
  name: ComeOn
  domain: comeon.com
  retriever_type: sbtech  # Uses factory routing to comeon_enhanced
  site_url: https://www.comeon.com
```

## Future Work

### Potential Improvements
1. **Skip comprehensive loading** - Load 1-2 pages only (same 29 events)
2. **Reduce wait times** - Optimize if keeping provider
3. **Event detail scraping** - Navigate to event pages for full markets

### Not Worth Pursuing
- **API reverse engineering** - No public API exists
- **Comprehensive league loading** - Proven ineffective
- **Alternative endpoints** - Extensively tested, none found

## Related Providers

### Hajper (Same Parent Company)
- Uses identical API structure to ComeOn
- Same WebSocket/RSocket protocol
- Likely has same 29-event limitation
- Not yet validated

### SBTech (Classic)
- **Bethard:** PRODUCTION READY (800+ events, classic SBTech API)
- **ComeOn/Hajper:** STAGING (29 events, modern WebSocket API)
- Different API generations despite same parent platform

## Conclusion

ComeOn's modern WebSocket/RSocket architecture provides robust real-time updates but severely limits event accessibility through the web interface. Only 29 "featured" events are exposed despite 1,306 total events in the system.

**Status:** STAGING (not production ready)
**Reason:** Platform limitation - inadequate event coverage
**Alternative:** Use Kambi, Spectate, or Pinnacle for comprehensive coverage
