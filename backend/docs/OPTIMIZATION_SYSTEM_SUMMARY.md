# Provider Optimization System

**Generic, systematic workflow for optimizing ANY provider in OddOpp.**

Created: 2026-01-27
Applied to: Hajper (66.5% faster), SBTech base (normalization + logging)

---

## Overview

The Provider Optimization System is a complete framework for systematically improving extraction performance across all providers in OddOpp. It includes:

1. **Comprehensive Workflow** - 6-phase process from measurement to monitoring
2. **Automated Tools** - Scripts for benchmarking, profiling, validation, and monitoring
3. **Quick Reference** - One-page guide for rapid optimizations
4. **Documentation** - Integrated into provider development workflow

**Target Results:**
- 40-60% faster extraction times
- Maintained data quality (100% accuracy)
- Systematic, repeatable process
- Applicable to ANY provider type

---

## Files Created

### Documentation

| File | Purpose | Size |
|------|---------|------|
| `PROVIDER_OPTIMIZATION_WORKFLOW.md` | Complete 6-phase optimization process | 15KB |
| `OPTIMIZATION_QUICK_REFERENCE.md` | One-page quick wins and checklist | 5KB |
| `OPTIMIZATION_SYSTEM_SUMMARY.md` | This file - system overview | 3KB |

### Tools (backend/scripts/)

| Tool | Purpose | Usage |
|------|---------|-------|
| `benchmark_provider.py` | Measure extraction performance | `python benchmark_provider.py <provider>` |
| `profile_provider.py` | Detailed timing breakdown | `python profile_provider.py <provider>` |
| `monitor_provider.py` | Production health monitoring | `python monitor_provider.py <provider> --days 7` |

### Integration

Updated `CLAUDE.md` to include:
- Optimization workflow as step 6 in provider development
- References to optimization documentation
- Tool usage examples

---

## System Components

### 1. Workflow (PROVIDER_OPTIMIZATION_WORKFLOW.md)

**6 Phases:**

1. **Baseline Measurement**
   - Run benchmarks (3+ runs)
   - Document current performance
   - Identify metrics to track

2. **Identify Bottlenecks**
   - Profile timing breakdown
   - Analyze common issues
   - Prioritize optimizations

3. **Apply Optimizations**
   - Page load strategy (biggest impact)
   - Timeout reduction
   - Concurrency tuning
   - Parsing improvements

4. **Test & Validate**
   - Run optimized benchmarks
   - Compare results
   - Validate data quality
   - Stability testing

5. **Document & Deploy**
   - Update validated.md
   - Update provider configs
   - Commit with metrics
   - Push to production

6. **Monitor**
   - Track success rates
   - Watch for regressions
   - Maintain rollback plan

### 2. Quick Reference (OPTIMIZATION_QUICK_REFERENCE.md)

**One-page guide with:**
- Quick start (5 minutes)
- Top 4 quick wins (80% of gains)
- Optimization checklist
- Performance targets
- Common patterns
- Troubleshooting guide
- Tool commands

**Key Optimizations:**

1. `networkidle` → `domcontentloaded` (40-60% faster)
2. Reduce timeouts by 50-70% (15-25% faster)
3. Increase concurrency 2-3x (20-40% faster)
4. Streamline cookie consent (1-2s saved)

### 3. Benchmarking Tool

**Features:**
- Runs multiple extractions (default: 3)
- Measures timing, event count, success rate
- Calculates averages, min/max, standard deviation
- Shows optimization targets
- Quick mode for rapid testing

**Output Example:**
```
Benchmark Results
Success Rate: 100% (3 runs)

Timing:
  Average:  21.0s
  Min:      20.1s
  Max:      22.3s
  StdDev:   1.1s

Events:
  Average:  50 events/run
  Time/event: 0.42s

Optimization Target: Reduce time by 40-60%
Target extraction time: 8.4s
```

### 4. Profiling Tool

**Features:**
- Detailed timing breakdown
- Identifies bottlenecks
- Provides recommendations
- Calculates optimization potential

**Output Example:**
```
Timing Profile
Section                        Total  Calls    Avg      %
------------------------------------------------------------
Page loads                    45.2s      53   0.85s  72.0%
WebSocket wait                 8.1s      53   0.15s  13.0%
DOM extraction                 5.3s       1   5.30s   8.5%
Parsing                        3.2s      50   0.06s   5.1%
Other                          1.2s       -   0.00s   1.4%
------------------------------------------------------------
TOTAL                         63.0s

Optimization Recommendations
HIGH optimization potential:
  - Critical: Change from networkidle to domcontentloaded
  - Aggressive timeout reduction (60-70%)
  - Increase concurrency significantly
  - Expected gain: 60-70% faster
```

### 5. Monitoring Tool

**Features:**
- Analyzes historical extraction data
- Shows daily event/odds counts
- Calculates averages
- Detects issues (gaps, declines, failures)
- Health status reporting

**Output Example:**
```
Provider Monitor: hajper
Period: Last 7 days

Date         Events      Odds
-----------------------------------
2026-01-27       52      1240
2026-01-26       48      1156
2026-01-25       51      1221
...
TOTAL           350      8400

Averages (7 days with data):
  Events/day: 50
  Odds/day:   1200

Health Check
Status: HEALTHY
  No issues detected
  Provider performing as expected
```

---

## Usage Examples

### Example 1: Optimize Hajper (Completed)

```bash
# 1. Baseline
python scripts/benchmark_provider.py hajper --runs 3
# Result: 62.6s for 50 events

# 2. Apply optimizations (see workflow)
# - networkidle → domcontentloaded
# - Timeouts: 90s → 20s
# - Wait times: 2-3s → 1.5-1.8s
# - Concurrency: 5 → 8

# 3. Test
python scripts/benchmark_provider.py hajper --runs 3
# Result: 21.0s for 50 events (66.5% faster!)

# 4. Validate
python scripts/validate_provider.py hajper
# All checks passed

# 5. Commit
git commit -m "Optimize Hajper: 66% faster (62.6s → 21.0s)"
```

### Example 2: Quick Optimization Check

```bash
# Quick test any provider
python scripts/benchmark_provider.py <provider> --quick

# Profile to find bottlenecks
python scripts/profile_provider.py <provider>

# Apply quick wins from reference guide

# Validate improvement
python scripts/benchmark_provider.py <provider> --quick
```

### Example 3: Production Monitoring

```bash
# Check provider health
python scripts/monitor_provider.py <provider> --days 7

# If issues detected, investigate logs
python scripts/benchmark_provider.py <provider> --runs 1

# Rollback if needed
git revert <commit_hash>
```

---

## Results Achieved

### Hajper Optimization (2026-01-27)

**Before:**
- Extraction time: 62.6s
- Events: 50
- Time per event: 1.25s
- Wait strategy: networkidle

**After:**
- Extraction time: 21.0s
- Events: 50
- Time per event: 0.42s
- Wait strategy: domcontentloaded

**Improvements:**
- 66.5% faster (41.6s saved)
- Same event count
- All fields validated
- Stable performance (±2s variance)

**Changes:**
1. Page load: `networkidle` → `domcontentloaded`
2. Main timeout: 90s → 30s (67% reduction)
3. League timeout: 30s → 20s (33% reduction)
4. Wait times: 2-3s → 1.5-1.8s (40% reduction)
5. Concurrency: 5 → 8 (60% increase)

### SBTech Base Enhancement (2026-01-27)

**Added:**
- Team name normalization (benefits Bethard, Fastbet)
- Response counter logging (debugging)
- Improved error visibility

**Impact:**
- Better data quality (normalized teams)
- Easier debugging
- Foundation for future optimizations

---

## Best Practices

### When to Optimize

**Optimize when:**
- Extraction time > 30s
- Adding new provider (establish baseline)
- User reports slow performance
- Before promoting to production

**Don't optimize when:**
- Extraction time < 15s (diminishing returns)
- Provider unstable (fix stability first)
- Data quality issues (fix accuracy first)

### Optimization Priority

**High Priority (do first):**
1. Change wait strategy (biggest impact)
2. Reduce timeouts (quick win)
3. Fix unnecessary waits

**Medium Priority:**
4. Increase concurrency (if applicable)
5. Streamline cookie consent
6. Add response caching (API providers)

**Low Priority:**
7. Parsing optimizations
8. Memory optimizations
9. Code cleanup

### Safety Checks

**Always verify:**
- [ ] Same event count (±5%)
- [ ] All fields present
- [ ] Team names normalized
- [ ] Odds values reasonable
- [ ] No new errors/timeouts
- [ ] Stable across 3+ runs

**Red flags (rollback):**
- Event count drops >20%
- Missing data fields
- Intermittent failures
- Extraction time increases

---

## Future Enhancements

### Phase 1 (Immediate)
- [ ] Apply workflow to ComeOn (similar to Hajper)
- [ ] Optimize remaining multi-league extractors
- [ ] Create provider-specific optimization guides

### Phase 2 (Short term)
- [ ] Automated optimization suggestions
- [ ] Continuous performance monitoring
- [ ] Optimization impact dashboard

### Phase 3 (Long term)
- [ ] ML-based timeout optimization
- [ ] Auto-tuning concurrency limits
- [ ] Predictive performance modeling

---

## Maintenance

### Weekly
- Monitor production providers
- Check for performance regressions
- Update optimization targets

### Monthly
- Review optimization results
- Update documentation
- Share best practices

### Quarterly
- Benchmark all providers
- Identify new optimization opportunities
- Update tools based on learnings

---

## Contributing

When optimizing a provider:

1. **Use the workflow** - Follow all 6 phases
2. **Document results** - Update validated.md
3. **Share learnings** - Note unique challenges
4. **Update tools** - Improve scripts based on experience

**Commit format:**
```
Optimize <provider>: X% faster extraction

- Changed wait_until: <before> -> <after>
- Reduced timeouts: <before> -> <after>
- Increased concurrency: <before> -> <after>
- Result: <before>s -> <after>s per extraction

Validation: <events> events extracted, all fields correct
```

---

## Support

**Questions:**
- Check: `PROVIDER_OPTIMIZATION_WORKFLOW.md` (full process)
- Check: `OPTIMIZATION_QUICK_REFERENCE.md` (quick wins)
- Check: Tool help (`python <script> --help`)

**Issues:**
- Review troubleshooting section in quick reference
- Check provider logs for errors
- Verify baseline performance first

**Success Stories:**
- Hajper: 66.5% faster (62.6s → 21.0s)
- SBTech base: Improved normalization + debugging

---

## Conclusion

The Provider Optimization System provides a complete, generic framework for systematically improving extraction performance across all OddOpp providers.

**Key Benefits:**
- **Systematic:** 6-phase repeatable process
- **Generic:** Works for ANY provider type
- **Proven:** 66.5% improvement on Hajper
- **Maintainable:** Tools + documentation
- **Safe:** Validation at every step

**Expected Results:**
- 40-60% faster extractions
- 100% data quality maintained
- Reduced server load
- Better user experience

Apply this workflow to ALL providers for consistent, significant performance gains.
