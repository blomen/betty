# Altenar Platform Implementation Plan

## Objective
Implement Altenar sportsbook platform support to unlock **Betinia** and **FrankFred** providers.

---

## Current Status

### Research Findings
- **Platform:** Altenar (B2B sportsbook software provider)
- **Target Providers:** Betinia (betinia.com), FrankFred (frankfred.com)
- **Public API Documentation:** Not available (requires partnership/license)
- **Implementation Approach:** Reverse engineering via browser inspection

### Challenges Encountered
1. ❌ Both Betinia and FrankFred have page load timeouts/anti-bot protection
2. ❌ Public API documentation not available
3. ❌ No existing open-source Altenar extractors found

---

## Implementation Options

### Option A: Manual Browser Inspection (Recommended)
**Effort:** 4-6 hours | **Success Rate:** High

**Steps:**
1. Manually visit betinia.com or frankfred.com in Chrome/Firefox
2. Open DevTools Network tab
3. Navigate to sportsbook → football events
4. Identify API endpoints used for odds data
5. Document:
   - Base URL structure
   - Request format (headers, params)
   - Response format (JSON structure)
   - Authentication requirements
6. Implement retriever based on findings

**Pros:**
- Most reliable method
- Can see exact API structure
- Can test in real-time

**Cons:**
- Manual effort required
- Requires human interaction

---

### Option B: Enhanced Automation with Stealth
**Effort:** 6-8 hours | **Success Rate:** Medium

**Steps:**
1. Implement stealth browser automation (like gecko_v2)
2. Add delays, human-like behavior
3. Use residential proxies if needed
4. Intercept API calls programmatically

**Pros:**
- Can be automated after initial setup
- Reusable for similar platforms

**Cons:**
- May still face anti-bot issues
- More complex implementation

---

### Option C: Contact Altenar for Partnership
**Effort:** Unknown | **Success Rate:** Low (requires commercial agreement)

**Steps:**
1. Contact Altenar for API access
2. Sign partnership/license agreement
3. Get official API documentation
4. Implement using official specs

**Pros:**
- Official, stable API
- Full support and documentation

**Cons:**
- Requires commercial relationship
- May have fees/restrictions
- Not suitable for personal/research projects

---

### Option D: Postpone and Focus on Other Providers
**Effort:** 0 hours | **Value:** Depends on goals

**Alternative Options:**
1. Wait for more providers to migrate to already-supported platforms (like Happy Casino → Kambi)
2. Improve existing 24 providers (market classification, coverage)
3. Focus on data analysis/matching instead of more providers

---

## Recommended Approach: Option A (Manual Inspection)

### Step-by-Step Implementation

#### Phase 1: API Discovery (1-2 hours)
1. Open https://www.betinia.com in Chrome
2. Open DevTools (F12) → Network tab
3. Filter: XHR + Fetch
4. Navigate: Sportsbook → Football → Premier League
5. Identify key API calls:
   - Sports/leagues list
   - Events list
   - Odds/markets data
6. Document:
   ```
   Base URL: ?
   Endpoints:
     - GET /api/sports → List of sports
     - GET /api/events?sport=football → Events
     - GET /api/odds?event_id=123 → Odds
   Auth: ?
   Headers: ?
   Response format: ?
   ```

#### Phase 2: Prototype Implementation (2-3 hours)
1. Create `backend/src/providers/altenar.py`
2. Implement based on findings:
   ```python
   class AltenarRetriever(Retriever):
       def __init__(self, config):
           self.api_base = config.get('api_base')
           # Based on findings

       async def extract(self, sport: str) -> List[StandardEvent]:
           # Implement based on API structure
   ```

3. Test with Betinia configuration
4. Validate data extraction

#### Phase 3: Configuration & Testing (1-2 hours)
1. Add to `providers.yaml`:
   ```yaml
   betinia:
     id: betinia
     name: Betinia
     retriever_type: altenar
     api_base: [discovered URL]
     domain: betinia.com

   frankfred:
     id: frankfred
     name: FrankFred
     retriever_type: altenar
     api_base: [discovered URL]
     domain: frankfred.com
   ```

2. Test both providers
3. Compare coverage with existing providers
4. Document findings

#### Phase 4: Refinement (1-2 hours)
1. Improve market normalization
2. Add error handling
3. Optimize performance
4. Write tests

---

## Expected Outcomes

### Success Criteria
- ✅ Extract events from Betinia
- ✅ Extract events from FrankFred
- ✅ Parse odds correctly
- ✅ Map to StandardEvent format
- ✅ Coverage: 100+ football events per provider

### Deliverables
1. `backend/src/providers/altenar.py` - Base retriever
2. Configuration for Betinia + FrankFred
3. Documentation of API structure
4. Test results

---

## Decision Required

**Which option should we proceed with?**

A. **Manual inspection** (4-6 hours, high success, I need your help to inspect the site)
B. **Enhanced automation** (6-8 hours, medium success, fully automated)
C. **Contact Altenar** (unknown timeline, requires commercial relationship)
D. **Postpone/skip** (focus on other improvements)

**My Recommendation:** Option A with your help to manually inspect the site, or Option D to focus on improving existing providers.

The 24 providers already implemented cover the Swedish market well. Adding Altenar would be valuable for completeness, but may not be critical if the sites have strong anti-bot protection.

---

## Alternative: Quick Provider Research

Before committing 4-6+ hours, we could spend 15-30 minutes checking if:
1. The sites are actually accessible (no geo-blocking)
2. They have reasonable odds coverage
3. The API structure is extractable

This would help validate if the effort is worthwhile.

---

## Next Steps

**Please advise:**
1. Should I proceed with manual inspection (needs your help to check the site)?
2. Should I try automated approach first?
3. Should we skip Altenar and focus on other improvements?
4. Should we do quick research first to validate feasibility?
