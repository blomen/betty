# Web Scraping Methods Guide

## Overview

When implementing a new provider, we should systematically try ALL available scraping methods before choosing the best approach. This document outlines each method, tools available, and a systematic testing process.

---

## Available Scraping Methods

### 1. Direct API Calls (Fastest, Best)

**When to use:**
- Public API endpoints available
- No authentication required
- Structured JSON responses

**Tools:**
- `aiohttp` - Async HTTP client
- `requests` - Sync HTTP client (if needed)

**Example Providers:**
- Kambi (bulk JSON API)
- Pinnacle (guest API)
- Polymarket (public REST API)

**Advantages:**
- Fastest (no browser overhead)
- Most reliable
- Easy to parse structured data
- Low resource usage

**How to find API endpoints:**
1. Open browser DevTools (Network tab)
2. Browse the site normally
3. Filter by XHR/Fetch requests
4. Look for JSON responses containing events/odds
5. Test endpoints with curl/Postman
6. Document query parameters

---

### 2. Browser Automation + API Interception (Fast, Reliable)

**When to use:**
- Site uses JavaScript to load data
- API endpoints exist but need browser session
- Bot detection requires real browser

**Tools:**
- `playwright` - Browser automation
- Network interception to capture API responses
- Can run headless for production

**Example Providers:**
- Bethard (SBTech API interception)
- Gecko V2 (Betsson API interception)

**Advantages:**
- Bypasses bot detection
- Captures API data (structured JSON)
- No DOM parsing needed
- Moderate speed

**Implementation Pattern:**
```python
async def extract(self, sport: str):
    await page.goto(url)

    # Intercept API calls
    page.on("response", lambda response:
        capture_if_api(response)
    )

    await page.wait_for_load_state('networkidle')
    # Parse captured API responses
```

---

### 3. WebSocket/RSocket Interception (Real-time)

**When to use:**
- Site streams data via WebSocket
- Real-time updates (live odds)
- No REST API available

**Tools:**
- `playwright` with WebSocket listeners
- Custom frame decoders for binary protocols

**Example Providers:**
- ComeOn (RSocket over WebSocket)

**Advantages:**
- Access to real-time data streams
- Can capture comprehensive event updates

**Challenges:**
- May only get partial data in initial messages
- Binary protocols need custom decoders
- Need to trigger the right page loads to get full data

**Implementation Pattern:**
```python
def on_websocket(ws):
    ws.on("framereceived", lambda payload:
        decode_and_store(payload)
    )

page.on("websocket", on_websocket)
```

---

### 4. DOM Scraping (Reliable Fallback)

**When to use:**
- No API endpoints found
- Data rendered in HTML
- Static or JavaScript-rendered content

**Tools:**
- `playwright` - For JavaScript-rendered content
- `beautifulsoup4` - HTML parsing
- `lxml` - Fast XML/HTML parsing

**Example Providers:**
- Snabbare (DOM scraping with Playwright)

**Advantages:**
- Works when no API available
- Can extract any visible data
- Reliable for static content

**Challenges:**
- Slower than API calls
- Fragile (breaks with HTML changes)
- Requires CSS selector maintenance

**Implementation Pattern:**
```python
# Wait for content
await page.wait_for_selector('.event-list')

# Extract elements
events = await page.query_selector_all('.event-row')

for event_el in events:
    home = await event_el.query_selector('.home-team')
    away = await event_el.query_selector('.away-team')
    # ...
```

---

### 5. HTML Parsing (Static Sites)

**When to use:**
- Site has no JavaScript rendering
- Server-side rendered HTML
- Simple page structure

**Tools:**
- `beautifulsoup4` - HTML parsing
- `lxml` - Fast parsing
- `requests` - HTTP client

**Advantages:**
- Very fast (no browser)
- Simple implementation
- Low resource usage

**Challenges:**
- Rare these days (most sites use JS)
- Limited to static content

---

### 6. Hybrid Approaches

**Combination strategies:**

#### A. Browser Load + DOM Scraping
- Use Playwright to load JavaScript
- Wait for content rendering
- Parse DOM elements
- Example: Snabbare

#### B. Browser Load + API + DOM
- Load page with browser
- Intercept API calls
- Fall back to DOM if API incomplete
- Example: Could enhance ComeOn

#### C. Multiple Page Types
- Different strategies for different page types
- List pages: API interception
- Detail pages: DOM scraping
- Example: Could enhance ComeOn

---

## Systematic Testing Process

### Step 1: Manual Site Exploration

1. **Browse the site manually**
   - How many events are visible?
   - What sports/leagues available?
   - What market types shown?
   - Are events paginated or lazy-loaded?

2. **Open DevTools Network Tab**
   - Reload the page
   - Look for API calls (XHR/Fetch)
   - Check WebSocket connections
   - Document all endpoints found

3. **Inspect HTML Structure**
   - View page source
   - Check if JavaScript-rendered
   - Identify event/market CSS classes
   - Look for data attributes

### Step 2: Try Direct API

1. **Test API endpoints in isolation**
   ```bash
   curl "https://api.example.com/events" | jq
   ```

2. **Check authentication requirements**
   - Any API keys needed?
   - Session cookies required?
   - CORS restrictions?

3. **Document query parameters**
   - sport/league filters
   - pagination (limit/offset)
   - market type filters

### Step 3: Try Browser Automation

1. **API Interception First**
   ```python
   # Capture all API responses
   page.on("response", capture_api)
   await page.goto(url)
   ```

2. **Check WebSocket Connections**
   ```python
   # Monitor WebSocket data
   page.on("websocket", capture_ws)
   ```

3. **Measure data completeness**
   - How many events captured?
   - What market types available?
   - Any missing data?

### Step 4: Try DOM Scraping

1. **Identify all event containers**
   ```python
   events = await page.query_selector_all('.event')
   ```

2. **Extract all fields**
   - Teams, leagues, start times
   - Market types, odds
   - Event IDs

3. **Handle pagination/lazy loading**
   - Scroll to load more
   - Click "load more" buttons
   - Navigate through pages

### Step 5: Test Different Page Types

1. **Sport list page**
   - How many events shown?
   - Which markets available?

2. **League list page**
   - Does this show more events?
   - Different market coverage?

3. **Event detail page**
   - Full market list?
   - All odds available?

4. **"All events" or "Coupon" page**
   - Comprehensive event list?
   - Better for bulk extraction?

### Step 6: Compare Results

Create comparison matrix:

| Method | Events | Markets | Speed | Reliability | Maintenance |
|--------|--------|---------|-------|-------------|-------------|
| Direct API | ? | ? | ? | ? | ? |
| API Interception | ? | ? | ? | ? | ? |
| WebSocket | ? | ? | ? | ? | ? |
| DOM Scraping | ? | ? | ? | ? | ? |

### Step 7: Choose Best Method

**Decision criteria:**
1. **Event Coverage** (most important)
2. **Market Coverage** (important for arbitrage)
3. **Speed** (target: <30s per sport)
4. **Reliability** (doesn't break easily)
5. **Maintenance** (easy to update)

---

## ComeOn Specific Investigation

### Current Status
- Method: WebSocket INITIAL_STATE interception
- Events: **18** (suspiciously low)
- Markets: 1x2 only
- Speed: 22.7s ✓
- **PROBLEM: User reports many more events visible manually**

### Investigation Plan

1. **Manual Browse Check**
   - [ ] Count actual events on ComeOn.com
   - [ ] Check multiple sports
   - [ ] Document what's visible vs what we extract

2. **Try All Page Types**
   - [ ] Sport main page (e.g., /sportsbook/football)
   - [ ] League pages (e.g., /sportsbook/sport/1-fotboll/league/147)
   - [ ] "All events" or coupon page
   - [ ] Event detail pages
   - [ ] Search/filter functionality

3. **API Exploration**
   - [ ] Check for bulk event API endpoints
   - [ ] Test /sportsbook-api/ paths
   - [ ] Look for GraphQL endpoints
   - [ ] Document pagination parameters

4. **WebSocket Deep Dive**
   - [ ] What triggers full event list?
   - [ ] Test different page navigation sequences
   - [ ] Check for request messages we can send
   - [ ] Monitor DELTA messages (incremental updates)

5. **DOM Scraping Test**
   - [ ] Parse event list from HTML
   - [ ] Compare event count with WebSocket
   - [ ] Check market availability in DOM

6. **Hybrid Approach**
   - [ ] Event list from DOM
   - [ ] Market details from WebSocket/API
   - [ ] Combine for comprehensive coverage

---

## Testing Scripts Template

```python
# test_all_methods.py

async def test_direct_api(sport: str):
    """Test if direct API calls work."""
    # Try discovered endpoints
    pass

async def test_api_interception(sport: str):
    """Test browser + API interception."""
    pass

async def test_websocket(sport: str):
    """Test WebSocket data capture."""
    pass

async def test_dom_scraping(sport: str):
    """Test DOM element extraction."""
    pass

async def compare_methods():
    """Run all methods and compare results."""
    results = {}

    for method in ['api', 'interception', 'websocket', 'dom']:
        start = time.time()
        events = await test_method(method, 'football')
        elapsed = time.time() - start

        results[method] = {
            'events': len(events),
            'markets': count_market_types(events),
            'speed': elapsed,
        }

    print_comparison_table(results)
```

---

## Common Patterns by Site Type

### SBTech Platform
- Modern API: `/sportsbook-api/api/v2/leagues`
- Classic API: `/api/sportsbook/`, `/api/odds/`
- Method: Browser + API interception

### Kambi Platform
- Public API: `offering-api.kambicdn.com`
- Method: Direct API calls (fastest)

### Betsson Group (Gecko)
- Custom API: `/api/sb/v1/widgets/event-market`
- Method: Browser + API interception + stealth

### GraphQL-based
- Endpoint: `/graphql`
- Method: Direct GraphQL queries

---

## Best Practices

1. **Always start with manual exploration**
2. **Try direct API first** (fastest if available)
3. **Document all endpoints found**
4. **Test event coverage thoroughly**
5. **Compare multiple methods before choosing**
6. **Optimize after validation** (not before)
7. **Document trade-offs made**

---

## Next Steps for ComeOn

1. Create comprehensive testing script
2. Try all methods systematically
3. Find why only 18 events extracted (should be 100+)
4. Document actual event count on site
5. Choose method with best coverage
6. Re-validate with correct implementation
