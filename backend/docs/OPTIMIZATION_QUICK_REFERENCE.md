# Provider Optimization Quick Reference

One-page guide for quick optimizations. See `PROVIDER_OPTIMIZATION_WORKFLOW.md` for full details.

---

## Quick Start (5 minutes)

```bash
# 1. Benchmark current performance
python scripts/benchmark_provider.py <provider_id> --runs 3

# 2. Apply quick wins (see below)

# 3. Test optimized version
python scripts/benchmark_provider.py <provider_id> --runs 3

# 4. Validate data quality
python scripts/validate_provider.py <provider_id>
```

---

## Quick Wins (80% of optimization gains)

### 1. Change Page Load Strategy (40-60% faster)

```python
# Before (SLOW)
await page.goto(url, wait_until="networkidle", timeout=90000)

# After (FAST)
await page.goto(url, wait_until="domcontentloaded", timeout=20000)
```

**Decision:**
- WebSocket/API site → `domcontentloaded`
- Image-heavy site → `load`
- Last resort → `networkidle`

### 2. Reduce Timeouts (15-25% faster)

```python
# Before
timeout=90000  # 90s
await page.wait_for_timeout(3000)  # 3s

# After
timeout=20000  # 20s (70% reduction)
await page.wait_for_timeout(1500)  # 1.5s (50% reduction)
```

### 3. Increase Concurrency (20-40% faster for multi-page)

```python
# Before
concurrent_limit = 3

# After
concurrent_limit = 8  # 2.5x more parallel
```

### 4. Streamline Cookie Consent (1-2s saved)

```python
# Before
try:
    await page.click('button:has-text("Accept")', timeout=3000)
    await asyncio.sleep(2)
except:
    pass

# After
try:
    await page.click('button:has-text("Accept")', timeout=2000)
    await asyncio.sleep(0.5)
except:
    pass
```

---

## Optimization Checklist

Quick checklist for every optimization:

**Before:**
- [ ] Run benchmark (3 runs)
- [ ] Document baseline time

**Changes:**
- [ ] networkidle → domcontentloaded/load
- [ ] Reduce timeouts by 50-70%
- [ ] Reduce wait times by 50%
- [ ] Increase concurrency (if multi-page)

**After:**
- [ ] Run benchmark (3 runs)
- [ ] Verify 40%+ improvement
- [ ] Validate data quality
- [ ] Commit with metrics

---

## Performance Targets

| Current Time | Target | Expected Gain |
|--------------|--------|---------------|
| 60-90s | 20-35s | 60-70% faster |
| 30-60s | 15-25s | 40-60% faster |
| 15-30s | 10-15s | 30-40% faster |
| <15s | <10s | Limited gains |

---

## Common Patterns

### Pattern 1: Multi-League Extractor (Hajper, ComeOn)

```python
# Optimization targets:
- wait_until: networkidle → domcontentloaded
- Main page timeout: 90s → 30s
- League page timeout: 45s → 20s
- Post-load wait: 2-3s → 1.5-1.8s
- Concurrency: 5 → 8

# Expected: 60-70% faster
```

### Pattern 2: API Interceptor (SBTech, Spring Builder)

```python
# Optimization targets:
- wait_until: networkidle → load
- Timeout: 60s → 25s
- Post-load wait: 8s → 5s
- Add response logging

# Expected: 50-60% faster
```

### Pattern 3: REST API (Pinnacle, Altenar)

```python
# Optimization targets:
- Add response caching (15min TTL)
- Parallel requests: 3 → 8
- Early exit on limit
- No timeout changes needed

# Expected: 40-60% faster
```

---

## Troubleshooting

**Problem:** 0 events after optimization
```python
# Fix: Increase post-load wait
await page.wait_for_timeout(2000)  # Try 2s instead of 1.5s

# Or: Use more conservative strategy
wait_until="load"  # Instead of domcontentloaded
```

**Problem:** Intermittent failures
```python
# Fix: Reduce concurrency
concurrent_limit = 5  # Down from 8

# And: Increase timeout slightly
timeout=25000  # Up from 20000
```

**Problem:** Missing data fields
```python
# Fix: Wait longer for API
await page.wait_for_timeout(2000)  # Up from 1500

# Check: Verify API response structure hasn't changed
```

---

## Tools

```bash
# Benchmark
python scripts/benchmark_provider.py <provider> --runs 3

# Quick test (1 run, 10 events)
python scripts/benchmark_provider.py <provider> --quick

# Profile (timing breakdown)
python scripts/profile_provider.py <provider>

# Validate data quality
python scripts/validate_provider.py <provider>

# Monitor production
python scripts/monitor_provider.py <provider> --days 7
```

---

## Example: Before/After

**Hajper Optimization:**

```python
# BEFORE: 62.6s for 50 events
await page.goto(url, wait_until="networkidle", timeout=90000)
await page.wait_for_timeout(3000)
concurrent_limit = 5

# AFTER: 21.0s for 50 events (66% faster)
await page.goto(url, wait_until="domcontentloaded", timeout=20000)
await page.wait_for_timeout(1500)
concurrent_limit = 8
```

**Gains:**
- Time saved: 41.6s (66.5% reduction)
- Same event count: 50
- Same data quality: All fields validated

---

## Full Workflow

For detailed workflow, see: `backend/docs/PROVIDER_OPTIMIZATION_WORKFLOW.md`

**6 Phases:**
1. Baseline Measurement
2. Identify Bottlenecks
3. Apply Optimizations
4. Test & Validate
5. Document & Deploy
6. Monitor

---

## Key Metrics

Track these for every optimization:

```yaml
Provider: <provider_id>
Before:
  Time: 62.6s
  Events: 50
  Time/event: 1.25s

After:
  Time: 21.0s
  Events: 50
  Time/event: 0.42s

Improvement: 66.5% faster (41.6s saved)
```
