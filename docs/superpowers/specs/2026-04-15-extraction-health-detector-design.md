# Extraction Health Detector

**Date:** 2026-04-15
**Status:** Approved

## Problem

The `/health/extraction` endpoint only checks the latest extraction run. It missed:
- Pinnacle down for 1.5+ hours (sharp source — everything depends on it)
- Polymarket failing 6 consecutive runs (UniqueViolation from sequence drift)
- API soft providers with a 6-hour gap (unibet, betinia, betsson, etc.)
- 443K odds sequence desync causing cascading INSERT failures

The endpoint reported `"status": "ok"` throughout. The scheduled agent checking every 3h couldn't catch a 1.5h outage even if the endpoint were smarter.

## Solution

Replace the shallow `/health/extraction` implementation with a deep health assessment. Same endpoint, same URL, backward-compatible JSON shape. One function querying `provider_run_metrics` and `opportunities` for the last hour, running 5 checks.

Tighten the scheduled monitoring agent interval from 3h to 30min.

## The 5 Checks

### 1. Sharp Source Down (CRITICAL)

- **Query:** Last successful Pinnacle run from `provider_run_metrics`
- **Threshold:** No successful run in last 10 minutes
- **Rationale:** Pinnacle interval is 1min, typical run ~130s. 10min = ~5 missed cycles. All value detection depends on fresh sharp odds.
- **Message:** `CRITICAL: pinnacle has not completed successfully in {N} minutes`

### 2. Consecutive Provider Failures (CRITICAL ≥3, WARNING ≥2)

- **Query:** Last 5 runs per provider ordered by `start_time DESC`, check for leading consecutive `status != 'success'`
- **Catches:** Polymarket's 6x failure streak, systematic DB errors, API auth failures
- **Message:** `CRITICAL: {provider} has failed {N} consecutive runs — {last_error_snippet}`

### 3. Provider Staleness (WARNING)

- **Query:** Last successful run per provider vs its configured interval from `providers.yaml`
- **Threshold:** No successful run in `3 × interval_minutes`
  - sharp (1min) → 3min (but check #1 covers this at 10min)
  - api_soft (2min) → 6min
  - polymarket (5min) → 15min
  - signal_international (5min) → 15min
  - browser_soft (10min) → 30min
  - browser_antibot (15min) → 45min
- **Excludes:** Providers not in `active_providers` list
- **Message:** `WARNING: {provider} last succeeded {N} minutes ago (threshold: {T} min)`

### 4. Database Integrity Errors (CRITICAL)

- **Query:** Scan `error_message` in `provider_run_metrics` from last hour for patterns: `UniqueViolation`, `IntegrityError`, `duplicate key`, `sequence`
- **Threshold:** Any match
- **Message:** `CRITICAL: database integrity errors in {N} recent runs ({pattern})`

### 5. Opportunity Volume Drop (WARNING)

- **Query:** Count active opportunities (`is_active = true`) vs count with `detected_at > NOW() - 1 hour` compared to a baseline (count with `detected_at > NOW() - 2 hours AND detected_at <= NOW() - 1 hour`)
- **Threshold:** >50% drop (current hour vs previous hour)
- **Guard:** Skip if baseline < 50 opportunities (too few to be meaningful)
- **Message:** `WARNING: opportunity volume dropped {pct}% ({baseline} → {current}) in last hour`

## Response Shape

Backward-compatible with current format. Richer `status` and `issues`:

```json
{
  "status": "critical",
  "issues": [
    "CRITICAL: pinnacle has not completed successfully in 92 minutes",
    "CRITICAL: polymarket has failed 6 consecutive runs — UniqueViolation...",
    "WARNING: unibet last succeeded 387 minutes ago (threshold: 6 min)",
    "CRITICAL: database integrity errors in 4 recent runs (UniqueViolation)",
    "WARNING: opportunity volume dropped 62% (1600 → 608) in last hour"
  ],
  "runs": [...],
  "checked_at": "2026-04-15T..."
}
```

Status escalation: any CRITICAL issue → `"critical"`, else any WARNING → `"warning"`, else `"ok"`.

## Implementation

### Scope

- **Modify:** `backend/src/api/__init__.py` — replace `health_extraction()` with the deep assessment
- **Read:** `backend/src/config/providers.yaml` — for tier intervals and active providers list
- **No new files** — all logic lives in the endpoint function

### Provider Interval Lookup

Load tier config once per request (YAML is small, cached by OS). Build a dict mapping `provider_id → expected_interval_minutes` by iterating tiers and their provider lists. Only check providers in `active_providers`.

### Query Strategy

All 5 checks in a single `_query()` function run in a thread (existing pattern). Queries:

1. `SELECT provider_id, MAX(start_time) FROM provider_run_metrics WHERE status = 'success' AND provider_id = 'pinnacle' AND start_time > NOW() - '1 hour'`
2. `SELECT provider_id, status, error_message, start_time FROM provider_run_metrics WHERE start_time > NOW() - '1 hour' ORDER BY start_time DESC` — then group in Python to find consecutive failures
3. Same query as #2 — compute per-provider last success time
4. Same query as #2 — scan error_message for integrity patterns
5. Two counts on `opportunities` table

One main query (#2) serves checks 2, 3, and 4. Total: 3 queries.

### Scheduled Agent

Tighten the existing extraction monitoring scheduled agent from 3h → 30min interval.

## What This Catches vs Today

| Scenario | Today | After |
|----------|-------|-------|
| Pinnacle down 1.5h | `"ok"` | `"critical"` in ≤10min |
| Provider fails 6x consecutively | `"ok"` (if latest run is different provider) | `"critical"` after 3rd failure |
| API soft providers 6h gap | `"ok"` | `"warning"` after 6min |
| Sequence drift / UniqueViolation | `"ok"` | `"critical"` on first occurrence |
| Opportunity volume halved | `"ok"` | `"warning"` within 1h |
| Scheduled agent catches it | Up to 3h delay | Up to 30min delay |
