# Provider Optimization Workflow

Systematic approach to optimize extraction performance for any provider.

**Target:** Reduce extraction time by 40-60% while maintaining data quality.

---

## Phase 1: Baseline Measurement

### 1.1 Run Benchmark

```bash
python scripts/benchmark_provider.py <provider_id> --sport football --runs 3
```

**Metrics to capture:**
- Total extraction time
- Events extracted count
- Time per event
- Network wait time
- Page load time
- Parsing time

### 1.2 Document Current Performance

Create benchmark report:
```
Provider: <provider_id>
Date: YYYY-MM-DD
Runs: 3

Results:
- Average time: XX.Xs
- Events/run: XX
- Time/event: X.XXs
- Success rate: XX%

Bottlenecks identified:
[ ] Long page load waits
[ ] Conservative timeouts
[ ] Low concurrency
[ ] Network wait strategy
[ ] Unnecessary delays
[ ] Inefficient parsing
```

---

## Phase 2: Identify Bottlenecks

### 2.1 Common Performance Issues

**Network/Browser (70% of optimization potential):**
- `wait_until="networkidle"` → Waits for ALL network activity (slow)
- Conservative timeouts (60s+)
- Sequential page loads (no parallelization)
- Excessive wait times (3s+)
- Cookie consent delays

**Parsing/Processing (20% of optimization potential):**
- Inefficient JSON parsing
- Redundant normalization
- Synchronous operations
- Large data structures in memory

**Concurrency (10% of optimization potential):**
- Low parallel request limits
- Conservative semaphores
- Blocking operations

### 2.2 Profile with Timing Tool

```bash
python scripts/profile_provider.py <provider_id>
```

Output shows time breakdown:
```
=== Timing Breakdown ===
Page loads:        45.2s (72%)
WebSocket wait:     8.1s (13%)
DOM extraction:     5.3s ( 8%)
Parsing:            3.2s ( 5%)
Other:              1.2s ( 2%)
```

---

## Phase 3: Apply Optimizations

### 3.1 Browser/Network Optimizations

#### A. Page Load Strategy (HIGHEST IMPACT)

**Priority order (fastest to slowest):**

1. **`domcontentloaded`** (RECOMMENDED for most sites)
   - Fires when HTML parsed, DOM ready
   - Before images/stylesheets fully load
   - Best balance: speed + reliability
   ```python
   await page.goto(url, wait_until="domcontentloaded", timeout=20000)
   ```

2. **`load`** (Use if API calls triggered late)
   - Fires when page fully loaded
   - Includes images/stylesheets
   - Slower but more complete
   ```python
   await page.goto(url, wait_until="load", timeout=25000)
   ```

3. **`networkidle`** (AVOID unless necessary)
   - Waits for ALL network activity to cease
   - Very slow, often unnecessary
   - Only use if API calls unpredictable
   ```python
   await page.goto(url, wait_until="networkidle", timeout=30000)
   ```

**Decision Matrix:**
| Site Characteristic | Best Strategy |
|---------------------|---------------|
| REST API (immediate) | `domcontentloaded` |
| WebSocket/RSocket | `domcontentloaded` + 1.5-2s wait |
| Lazy-loaded content | `load` + scroll |
| Highly dynamic | `networkidle` (last resort) |

#### B. Timeout Optimization

**Before:**
```python
await page.goto(url, wait_until="networkidle", timeout=90000)
await page.wait_for_timeout(3000)
```

**After:**
```python
await page.goto(url, wait_until="domcontentloaded", timeout=20000)
await page.wait_for_timeout(1500)  # Reduced by 50%
```

**Guidelines:**
- Main page: 20-30s timeout
- Sub-pages: 15-20s timeout
- Post-load wait: 1-2s (unless WebSocket needs longer)
- Python wrapper: page_timeout + 5s

#### C. Concurrency Tuning

**Multi-page extractors (leagues, events):**

```python
# Before
concurrent_limit = 3  # Too conservative

# After - calculate based on site
concurrent_limit = min(
    config.get('concurrent_leagues', 8),  # Config override
    10  # Hard max to avoid rate limiting
)
```

**Concurrency guidelines:**
- Small sites (< 100 events): 5-8 concurrent
- Medium sites (100-500 events): 8-12 concurrent
- Large sites (500+ events): 10-15 concurrent
- Monitor for rate limiting/blocks

#### D. Cookie Consent Optimization

**Before:**
```python
try:
    await page.click('button:has-text("Accept")', timeout=3000)
    await asyncio.sleep(2)
except:
    pass
```

**After:**
```python
try:
    await page.click('button:has-text("Accept")', timeout=2000)
    await asyncio.sleep(0.5)  # Reduced
except:
    pass  # Don't log, normal case
```

### 3.2 API/Request Optimizations

#### A. HTTP vs Browser Transport

**Decision tree:**
```
Does site require JavaScript rendering?
├─ NO → Use HttpTransport (10-100x faster)
│   └─ Pure REST API, no bot detection
└─ YES → Use BrowserTransport
    ├─ Aggressive bot detection? → headless=False
    └─ Otherwise → headless=True (faster)
```

#### B. Response Caching

For providers that make multiple calls to same endpoints:

```python
from ..core import ResponseCache

class OptimizedRetriever(Retriever):
    def __init__(self, config, transport=None):
        super().__init__(config, transport)
        self.cache = ResponseCache(ttl=900)  # 15min cache
```

### 3.3 Parsing Optimizations

#### A. Lazy Parsing

Only parse what you need:

```python
# Before: Parse everything
all_events = [self._parse_event(e) for e in events_raw]

# After: Parse only up to limit
events = []
for event_raw in events_raw:
    if limit and len(events) >= limit:
        break  # Early exit
    event = self._parse_event(event_raw)
    if event:
        events.append(event)
```

#### B. Normalize Once

**Before:**
```python
# Normalizing in loop
for event in events:
    normalized = normalize_team_name(event.home_team)
```

**After:**
```python
# Normalize during parsing (already have the data)
def _parse_event(self, data):
    home_team = normalize_team_name(data.get('home'))  # Once
    away_team = normalize_team_name(data.get('away'))  # Once
```

#### C. Avoid Redundant Operations

```python
# Before: Multiple JSON serializations
event_json = json.dumps(event_data)
stored = json.loads(event_json)

# After: Work with objects directly
stored = event_data  # No serialization needed
```

---

## Phase 4: Test & Validate

### 4.1 Run Optimized Benchmark

```bash
python scripts/benchmark_provider.py <provider_id> --sport football --runs 3
```

### 4.2 Compare Results

```
=== Performance Comparison ===

Before:
- Average time: 62.6s
- Events: 50
- Time/event: 1.25s

After:
- Average time: 21.0s (66.5% faster)
- Events: 50
- Time/event: 0.42s

Improvement: 41.6s saved per extraction
```

### 4.3 Data Quality Validation

**Critical checks:**
```bash
python scripts/validate_provider.py <provider_id>
```

Verify:
- [ ] Same number of events extracted (±5%)
- [ ] All events have required fields
- [ ] Team names properly normalized
- [ ] Market data complete
- [ ] Odds values reasonable (1.01-100.0)
- [ ] No duplicate events

### 4.4 Stability Test

Run multiple times to ensure consistency:

```bash
for i in {1..10}; do
    python scripts/benchmark_provider.py <provider_id> --quick
done
```

Check for:
- Consistent timing (±20%)
- No errors/timeouts
- Stable event counts

---

## Phase 5: Document & Deploy

### 5.1 Update Provider Documentation

Update `backend/docs/validated.md`:

```yaml
<provider_id>:
  status: PRODUCTION
  extraction_time: 21.0s  # Updated
  events_per_extraction: 50
  optimization_applied: 2026-01-27
  optimization_gains: 66.5% faster
  notes: Optimized page load strategy, timeouts, and concurrency
```

### 5.2 Update Provider Config

If config changes made:

```yaml
# backend/src/config/providers.yaml
provider_id:
  concurrent_leagues: 8  # Document why
  max_timeout: 20000     # Document why
```

### 5.3 Commit Changes

```bash
git add backend/src/providers/<provider>.py
git add backend/src/config/providers.yaml
git add backend/docs/validated.md
git commit -m "Optimize <provider>: 66% faster extraction

- Changed wait_until: networkidle -> domcontentloaded
- Reduced timeouts: 90s -> 20s
- Increased concurrency: 5 -> 8
- Result: 62.6s -> 21.0s per extraction

Validation: 50 events extracted, all fields correct"
```

---

## Phase 6: Monitor

### 6.1 Production Monitoring

After deployment, monitor for:

```bash
# Check extraction success rate
python scripts/monitor_provider.py <provider_id> --days 7
```

**Red flags:**
- Success rate drops below 90%
- Extraction time increases significantly
- Event count drops by >20%
- New error patterns appear

### 6.2 Rollback Plan

If issues detected:

```bash
git revert <commit_hash>  # Rollback optimization
python scripts/validate_provider.py <provider_id>  # Verify restoration
```

---

## Optimization Checklist

Use this for every provider optimization:

### Pre-Optimization
- [ ] Baseline benchmark completed (3+ runs)
- [ ] Current performance documented
- [ ] Bottlenecks identified
- [ ] Git branch created

### Optimization
- [ ] Page load strategy optimized (networkidle → domcontentloaded/load)
- [ ] Timeouts reduced (50-70% reduction target)
- [ ] Wait times minimized (1-2s max)
- [ ] Concurrency increased (8-12 concurrent for multi-page)
- [ ] Cookie consent streamlined
- [ ] Unnecessary sleeps removed
- [ ] Transport type verified (HTTP vs Browser)

### Validation
- [ ] Optimized benchmark completed (3+ runs)
- [ ] Performance improved by 40%+
- [ ] Event count matches baseline (±5%)
- [ ] Data quality validated (all fields correct)
- [ ] Stability test passed (10 runs, no errors)
- [ ] Team names normalized

### Documentation
- [ ] validated.md updated
- [ ] providers.yaml updated (if config changed)
- [ ] Commit message clear with metrics
- [ ] Changes pushed to repository

### Post-Deployment
- [ ] Production monitoring enabled
- [ ] Success rate tracked (7 days)
- [ ] Rollback plan documented
- [ ] No regressions detected

---

## Quick Reference: Common Optimizations

| Issue | Before | After | Impact |
|-------|--------|-------|--------|
| Wait strategy | `networkidle` | `domcontentloaded` | 40-60% faster |
| Main page timeout | 90s | 20-30s | 60-70s saved |
| Sub-page timeout | 45s | 20s | 25s saved |
| Post-load wait | 3s | 1.5s | 1.5s/page |
| Concurrency | 3-5 | 8-12 | 50-100% more parallel |
| Cookie consent | 3s wait | 0.5s wait | 2.5s saved |

**Expected gains:** 40-70% reduction in extraction time with same data quality.

---

## Examples

### Example 1: Multi-League Extractor (Hajper)

**Baseline:** 62.6s for 50 events

**Optimizations:**
1. `networkidle` → `domcontentloaded`
2. Timeouts: 90s → 20s
3. Wait times: 2-3s → 1-1.5s
4. Concurrency: 5 → 8

**Result:** 21.0s (66.5% faster)

### Example 2: API Interceptor (SBTech)

**Baseline:** 45s for 100 events

**Optimizations:**
1. `networkidle` → `load`
2. Timeout: 60s → 25s
3. Post-load wait: 8s → 5s
4. Added response counter logging

**Result:** 18s (60% faster)

### Example 3: REST API (Pinnacle)

**Baseline:** 12s for 200 events

**Optimizations:**
1. Added response caching (15min TTL)
2. Parallel requests: 3 → 8
3. Early exit on limit reached

**Result:** 4s (67% faster)

---

## Troubleshooting

### Problem: 0 events after optimization

**Causes:**
- Wait strategy too aggressive (WebSocket not connected)
- Timeout too short (page not loaded)
- Post-load wait too short (API calls not complete)

**Solutions:**
1. Increase post-load wait by 0.5s increments
2. Try `load` instead of `domcontentloaded`
3. Use conservative test to verify events exist
4. Check if time-of-day issue (no matches scheduled)

### Problem: Intermittent failures

**Causes:**
- Rate limiting triggered
- Concurrency too high
- Timeouts too aggressive
- Network instability

**Solutions:**
1. Reduce concurrency by 20-30%
2. Increase timeout by 20%
3. Add retry logic with exponential backoff
4. Add random jitter to requests

### Problem: Missing data fields

**Causes:**
- Page not fully loaded
- API response incomplete
- Parsing logic changed

**Solutions:**
1. Increase post-load wait
2. Change wait strategy to more conservative
3. Review API response structure
4. Validate against baseline data

---

## Tools Reference

All optimization tools located in `backend/scripts/`:

- `benchmark_provider.py` - Performance benchmarking
- `profile_provider.py` - Detailed timing analysis
- `validate_provider.py` - Data quality validation
- `monitor_provider.py` - Production monitoring

Run with `--help` for usage details.
