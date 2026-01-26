# Altenar Platform - Research Summary

## 🎯 Quick Feasibility Test Results

**Status:** ✅ FEASIBLE (With WebSocket approach)
**Estimated Implementation:** 6-8 hours
**Complexity:** Medium-High (WebSocket interception required)

---

## Key Findings

### 1. Platform Architecture

**Altenar uses WebSocket SDK for sportsbook data, NOT REST API**

- **SDK URL:** `https://sb2wsdk-altenar2.biahosted.com/altenarWSDK.js`
- **Integration Type:** "betin"
- **Version:** V3
- **Protocol:** WebSocket (similar to ComeOn/Hajper platforms)

### 2. API Structure Discovered

#### ✅ Working Endpoints (REST API)
```
GET https://betinia.se/en/api/v3/sportbook/category/list
GET https://betinia.se/en/api/v3/project/info?fields=sportsbook
```

These provide:
- Sports list (Football, Tennis, Basketball, Ice Hockey, etc.)
- Championship/League metadata
- SDK configuration

#### ❌ Events/Odds NOT Available via REST
- Tried `/sportbook/fixture/list`, `/sportbook/match/list`, etc.
- All return 404
- **Conclusion:** Events and odds are delivered via WebSocket

### 3. Sports Available
```
Football     (sportId: 66)  - Premier League, Serie A, La Liga, Bundesliga, Ligue 1
Tennis       (sportId: 68)  - ATP, WTA
Ice Hockey   (sportId: 70)  - NHL, KHL
Basketball   (sportId: 67)  - NBA, Euroleague
Table Tennis (sportId: 77)
Handball     (sportId: 73)
Volleyball   (sportId: 69)
E-Sports     (sportId: 145)
```

### 4. Championship IDs (Examples)
```
Premier League: 2936
Serie A:        2942
La Liga:        2941
Bundesliga:     2950
Ligue 1:        2943
NBA:            2980
NHL:            3232
```

---

## Implementation Strategy

### Similar to ComeOn/Hajper (WebSocket Platforms)

**Required Approach:**
1. Use browser automation (Playwright/BrowserTransport)
2. Load sportsbook page to initialize WebSocket connection
3. Intercept WebSocket frames
4. Decode/parse WebSocket messages containing events/odds
5. Map to StandardEvent format

### Code Structure (Estimated)

```python
class AltenarRetriever(BrowserRetriever):
    """
    Altenar platform retriever using WebSocket interception.
    Similar to HajperRetriever implementation.
    """

    def __init__(self, config, transport):
        self.api_base = config.get('api_base')  # REST API for metadata
        self.site_url = config.get('site_url')  # For WebSocket
        self.skin_id = config.get('skin_id')    # From config

    async def extract(self, sport: str):
        # 1. Get sport/league metadata from REST API
        # 2. Navigate to sportsbook page in browser
        # 3. Setup WebSocket interception
        # 4. Wait for WebSocket messages
        # 5. Parse events from WebSocket data
        # 6. Return StandardEvents
```

### Key Challenges

1. **WebSocket Protocol:**
   Need to understand Altenar's WebSocket message format (similar effort to Hajper)

2. **SDK Initialization:**
   May need specific parameters (walletCode, skinId) from REST API

3. **Message Decoding:**
   Could be JSON, binary, or encoded (need inspection)

4. **Multi-Provider:**
   Both Betinia and FrankFred use same platform but may have different skinIds

---

## Comparison with Existing Platforms

| Platform | Approach | Complexity | Our Experience |
|----------|----------|------------|----------------|
| Kambi | REST API | Low | ✅ 13 providers working |
| Pinnacle | REST API | Low | ✅ Working |
| ComeOn | WebSocket | Medium-High | ✅ Working (comeon.py) |
| Hajper | WebSocket | Medium-High | ✅ Working (hajper.py) |
| **Altenar** | **WebSocket** | **Medium-High** | **Can reuse patterns** |

**Good News:** We've already implemented WebSocket interception twice (ComeOn, Hajper), so we have the patterns!

---

## Implementation Plan

### Phase 1: WebSocket Analysis (2-3 hours)
1. Load Betinia sportsbook in browser with DevTools
2. Monitor WebSocket connections
3. Identify message format (JSON, binary, etc.)
4. Document message structure for events/odds
5. Create test script to validate WebSocket parsing

### Phase 2: Base Implementation (2-3 hours)
1. Create `backend/src/providers/altenar.py`
2. Implement WebSocket interception (based on Hajper pattern)
3. Parse events from WebSocket messages
4. Map to StandardEvent format
5. Handle metadata from REST API

### Phase 3: Multi-Provider Support (1-2 hours)
1. Configure Betinia (skinId: "betiniase2")
2. Configure FrankFred (need to find skinId)
3. Test both providers
4. Validate data quality

### Phase 4: Testing & Refinement (1-2 hours)
1. Extract all sports
2. Compare coverage with other providers
3. Improve market normalization
4. Add error handling
5. Write tests

---

## Comparison: Betinia vs FrankFred

### Betinia
- ✅ Successfully accessed API
- ✅ Got WebSocket SDK configuration
- ✅ Skin ID: "betiniase2"
- ✅ Wallet Code: "501125"
- **Status:** Ready for implementation

### FrankFred
- ⚠️ Only captured 8 API calls (vs 89 for Betinia)
- ⚠️ Need to verify if uses same Altenar platform
- ⚠️ Need to find skinId/configuration
- **Status:** Need additional research

---

## Estimated Effort vs Value

### Effort Breakdown
```
WebSocket analysis:    2-3 hours
Base implementation:   2-3 hours
Multi-provider:        1-2 hours
Testing/refinement:    1-2 hours
---------------------------------
Total:                 6-10 hours
```

### Value Assessment

**Pros:**
- ✅ Unlocks 2 providers (Betinia + FrankFred)
- ✅ Adds new platform type to our arsenal
- ✅ Can reuse patterns from ComeOn/Hajper
- ✅ Public API (no authentication required)
- ✅ WebSocket approach proven to work

**Cons:**
- ❌ 6-10 hours effort for 2 providers
- ❌ WebSocket complexity (not simple REST API)
- ❌ May need ongoing maintenance if SDK changes
- ❌ Already have 24 working providers

---

## Recommendation

### Option 1: Proceed with Implementation (6-10 hours)
**When to choose:**
- Want to expand provider coverage
- Want to learn/add Altenar platform
- Have time for medium-complexity work

**Next steps:**
1. Manual WebSocket inspection in browser
2. Document message format
3. Implement based on Hajper pattern

### Option 2: Defer for Now
**When to choose:**
- 24 providers already sufficient
- Prefer to improve existing providers
- Want quicker wins

**Alternatives:**
- Fix Hajper sport misclassification issues
- Improve ComeOn market classification
- Wait for Happy Casino Kambi migration
- Focus on data analysis/matching

---

## Decision Point

**Given the findings, what would you like to do?**

**A)** Proceed with Altenar implementation (6-10 hours)
**B)** Defer Altenar, focus on improving existing providers
**C)** Defer Altenar, wait for easier provider opportunities
**D)** Manual WebSocket inspection first (30-60 min) to validate exact effort

---

## Files Created During Research

```
scrap/altenar_quick_test.py                    - Platform accessibility test
scrap/altenar_betinia_apis.json               - Captured API calls
scrap/altenar_frankfred_apis.json             - FrankFred API calls
scrap/betinia_sportbook_category_list.json    - Sports/leagues metadata
scrap/betinia_project_info_fields=sportsbook.json  - WebSocket SDK config
scrap/test_betinia_api.py                     - API endpoint tests
scrap/test_betinia_events.py                  - Events API tests

ALTENAR_IMPLEMENTATION_PLAN.md                 - Initial implementation plan
ALTENAR_RESEARCH_SUMMARY.md                    - This file
```

---

## Conclusion

**Altenar is FEASIBLE but requires WebSocket implementation** (similar complexity to ComeOn/Hajper).

Estimated: 6-10 hours for full implementation of both Betinia and FrankFred.

The platform is accessible and extractable, but not a "quick win" due to WebSocket complexity.

**Your call:** Worth the 6-10 hour investment for 2 more providers, or better to focus elsewhere?
