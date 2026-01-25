# ComeOn Extraction Optimization Plan

## Current Bottlenecks Identified

### 1. **MAJOR: Fixed Wait Times**
**Location:** `comeon_multileague.py:243`
```python
await page.wait_for_timeout(2000)  # 2 seconds per event!
```
**Impact:** With 579 events, this alone adds **19.3 minutes** to extraction time!
**Solution:** Reduce to 500ms or make it dynamic based on data received

### 2. **Network Idle Wait**
**Location:** `comeon_multileague.py:242`
```python
await page.goto(event_url, wait_until="networkidle", timeout=20000)
```
**Impact:** Waits for ALL network activity to stop (slow)
**Solution:** Use `"load"` or `"domcontentloaded"` instead

### 3. **High Concurrency Overhead**
**Current:** 15 concurrent event detail pages
**Impact:** Too many browser contexts = memory overhead + slower performance
**Solution:** Test optimal range (5-8 workers)

### 4. **Page Creation Overhead**
**Current:** New page for each event (`new_page()` called 579 times)
**Impact:** Browser context creation overhead
**Solution:** Page pooling - reuse pages instead of creating new ones

### 5. **Unnecessary Detail Extraction**
**Current:** Extracts details for all events, even those with good market data
**Impact:** Wasted time on events that already have sufficient data
**Solution:** Skip events that already have over/under markets from league page

---

## Optimization Strategy

### Phase 1: Quick Wins (Immediate Impact)
1. **Reduce wait timeout:** 2000ms → 500ms (-75% time)
2. **Change wait strategy:** "networkidle" → "load"
3. **Reduce concurrency:** 15 → 8 workers
4. **Smart event filtering:** Skip events with existing over/under data

**Expected Impact:** ~70% faster (15-20 min → 5-7 min)

### Phase 2: Advanced Optimizations
1. **Page pooling:** Reuse browser pages
2. **Batch processing:** Group similar events
3. **Conditional waits:** Only wait if WebSocket data not received
4. **Early termination:** Stop waiting when we have the data we need

**Expected Impact:** Additional 20-30% speed improvement

### Phase 3: Caching & Incremental
1. **League caching:** Store league structures
2. **Incremental updates:** Only fetch new/changed events
3. **Smart scheduling:** Run during peak hours for max availability

---

## Implementation Plan

### Optimization 1: Reduce Wait Times
```python
# Current
await page.wait_for_timeout(2000)  # 2 seconds

# Optimized
await page.wait_for_timeout(500)  # 500ms - still enough for WebSocket data
```

### Optimization 2: Faster Page Load Strategy
```python
# Current
await page.goto(event_url, wait_until="networkidle", timeout=20000)

# Optimized
await page.goto(event_url, wait_until="load", timeout=10000)
```

### Optimization 3: Optimal Concurrency
```python
# Current
concurrent_event_details: 15

# Optimized (test 5, 8, 10)
concurrent_event_details: 8
```

### Optimization 4: Smart Event Filtering
```python
def _should_extract_detail(self, event: StandardEvent) -> bool:
    """Skip events that already have over/under markets."""

    # Check if event already has over/under market with points
    has_over_under_with_points = False
    for market in event.markets:
        if market['type'] == 'over_under':
            for outcome in market['outcomes']:
                if 'point' in outcome:
                    has_over_under_with_points = True
                    break

    # If already have over/under with points, skip detail extraction
    if has_over_under_with_points:
        return False

    # Otherwise, apply existing filter logic
    filter_mode = self.config.get('detail_extraction_filter', 'all')
    # ... existing code
```

### Optimization 5: Page Pooling
```python
class PagePool:
    """Pool of reusable browser pages."""

    def __init__(self, transport, size=5):
        self.transport = transport
        self.size = size
        self.available = []
        self.in_use = set()

    async def acquire(self):
        """Get a page from pool."""
        if self.available:
            page = self.available.pop()
        elif len(self.in_use) + len(self.available) < self.size:
            page = await self.transport.new_page()
        else:
            # Wait for a page to become available
            while not self.available:
                await asyncio.sleep(0.1)
            page = self.available.pop()

        self.in_use.add(page)
        return page

    async def release(self, page):
        """Return page to pool."""
        self.in_use.remove(page)
        # Clear page state
        await page.goto('about:blank')
        self.available.append(page)
```

---

## Testing Plan

### Test 1: Baseline (Current Performance)
- Config: concurrent=15, wait=2000ms, networkidle
- Expected: ~15-20 minutes

### Test 2: Reduced Wait Times
- Config: concurrent=15, wait=500ms, load
- Expected: ~4-6 minutes

### Test 3: Optimal Concurrency
- Config: concurrent=8, wait=500ms, load
- Expected: ~4-5 minutes

### Test 4: Smart Filtering
- Config: concurrent=8, wait=500ms, load, skip_existing_markets
- Expected: ~3-4 minutes

### Test 5: All Optimizations
- Config: concurrent=8, wait=500ms, load, smart_filter, page_pool
- Expected: ~3-5 minutes

---

## Expected Results

| Configuration | Time | Speedup | Data Quality |
|---------------|------|---------|--------------|
| Current | 15-20 min | 1x | 100% (baseline) |
| Quick Wins | 5-7 min | 3x | 100% (same) |
| All Optimizations | 3-5 min | 4-5x | 100% (same) |

---

## Risk Mitigation

1. **Data Loss Risk:** Reduced wait times might miss some WebSocket data
   - **Mitigation:** Test with multiple wait times (300ms, 500ms, 800ms)
   - **Validation:** Compare market counts before/after

2. **Race Conditions:** Page pooling might cause state issues
   - **Mitigation:** Clear page state before reuse
   - **Validation:** Check for duplicate or missing events

3. **Rate Limiting:** Faster extraction might trigger limits
   - **Mitigation:** Monitor for 429 errors, implement backoff
   - **Validation:** Check extraction success rate

---

## Implementation Priority

### HIGH (Do First):
1. ✅ Reduce wait timeout (2000ms → 500ms)
2. ✅ Change wait strategy (networkidle → load)
3. ✅ Reduce concurrency (15 → 8)

### MEDIUM (Do Next):
4. ✅ Smart event filtering (skip existing markets)
5. ⏳ Test different wait times (300ms, 500ms, 800ms)

### LOW (Optional):
6. ⏳ Page pooling (if still need more speed)
7. ⏳ Incremental updates
8. ⏳ Caching

---

## Success Criteria

- ✅ Extraction time < 7 minutes (vs current 15-20 min)
- ✅ Same number of events extracted
- ✅ Same data quality (point values, markets)
- ✅ No errors or failures
- ✅ Database validation passes
