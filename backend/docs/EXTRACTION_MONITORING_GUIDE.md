# Full Extraction Monitoring System

Comprehensive monitoring infrastructure for tracking, analyzing, and optimizing provider extraction performance.

## Overview

The monitoring system provides:

- **Database persistence** - Historical metrics stored in SQLite
- **Centralized logging** - Rotating log files with structured output
- **Real-time progress** - Live updates during extraction
- **Post-extraction analysis** - Automated issue detection and provider comparison
- **Performance tracking** - Metrics for every extraction run

## Quick Start

### Run Monitored Extraction

```bash
cd backend

# Run all active providers
python scripts/run_monitored_extraction.py

# Run specific providers
python scripts/run_monitored_extraction.py --providers unibet,leovegas,betsson

# Export metrics to JSON
python scripts/run_monitored_extraction.py --export-json results.json
```

### Analyze Results

```bash
# Compare provider performance across recent runs
python scripts/compare_providers.py --runs 5

# Detect systematic issues
python scripts/analyze_extraction_issues.py

# Analyze specific run
python scripts/analyze_extraction_issues.py --run-id run_1769618145
```

## Database Schema

### Tables

**extraction_runs**
- Tracks each complete pipeline run
- Aggregates: total events, odds, success rates
- Metadata: trigger type, config snapshot

**provider_run_metrics**
- Per-provider metrics for each run
- Timing, event counts, success status
- Performance data: retries, cache hits

**sport_run_metrics**
- Per-sport granular tracking
- Identifies which sports fail consistently
- Error type classification

### Migration

```bash
python scripts/migrate_metrics_tables.py
```

## Logging

### Log Files

All logs stored in `backend/logs/`:

- **extraction.log** - All extraction activity (10MB, 5 backups)
- **errors.log** - ERROR+ only (5MB, 3 backups)
- **api.log** - API server logs (if running)

### Log Rotation

Automatic rotation when files reach size limits. Old logs preserved with `.1`, `.2` suffixes.

### Configuration

Edit `src/logging_config.py` to customize:
- Log levels
- File sizes
- Backup counts
- Log formats

## Monitoring Scripts

### run_monitored_extraction.py

Full extraction with comprehensive monitoring.

**Features:**
- Real-time progress display
- Metrics persistence to database
- Detailed extraction report
- Error summary with recommendations
- JSON export capability

**Usage:**
```bash
# All providers, all sports (from sports.json)
python scripts/run_monitored_extraction.py

# Specific providers
python scripts/run_monitored_extraction.py --providers unibet,leovegas

# Export results
python scripts/run_monitored_extraction.py --export-json output.json
```

**Output:**
```
================================================================================
EXTRACTION REPORT
================================================================================
Duration: 45.2s (0.8 minutes)
Run ID: run_1769618145

--- SUMMARY ---
Total Events: 7349
Total Odds: 243261
Matched Events: 1681
Polymarket Events: 2278

--- PROVIDER RESULTS ---

Successful (5):
  [OK] unibet                  1234 events across 4 sports
  [OK] leovegas                 987 events across 4 sports
  ...

Partial Success (2):
  [!!] betsson                  456 events, 3/4 sports OK
       Error in ice_hockey: Timeout after 60s

Failed (1):
  [XX] hajper                   0 events
       Error in football: Connection refused

--- RECOMMENDATIONS ---
 * Investigate 1 failed providers (see logs/errors.log)
 * Review 2 providers with partial failures
================================================================================
```

### compare_providers.py

Compare provider performance across multiple runs.

**Usage:**
```bash
# Last 5 runs
python scripts/compare_providers.py --runs 5

# Specific providers only
python scripts/compare_providers.py --providers unibet,leovegas,betsson
```

**Output:**
```
Provider              Runs   Avg Events     Avg Odds  Success%   Avg Time
------------------------------------------------------------------------
unibet                   5         1234        45678     100.0%      12.3s
leovegas                 5          987        34567     100.0%      15.2s
betsson                  5          456        23456      80.0%      18.7s
hajper                   5            0            0       0.0%       0.0s
```

### analyze_extraction_issues.py

Detect systematic issues across extraction runs.

**Detects:**
- Providers with zero events
- Consistently failing sports
- Slow providers (>60s)
- Circuit breaker trips

**Usage:**
```bash
# Analyze last 10 runs
python scripts/analyze_extraction_issues.py

# Analyze specific run
python scripts/analyze_extraction_issues.py --run-id run_1769618145
```

**Output:**
```
================================================================================
ISSUE ANALYSIS - 10 Run(s)
================================================================================

[!!] PROVIDERS WITH ZERO EVENTS:
  * hajper: 10/10 runs
  * snabbare: 7/10 runs

[!!] SLOW PROVIDERS (>60s):
  * spectate: avg 78.3s (8 occurrences)
  * fastbet: avg 65.1s (3 occurrences)

[XX] CONSISTENTLY FAILING SPORTS:
  * betsson / ice_hockey: 9/10 runs
      Error types: timeout, extraction_error
```

## Metrics Access

### Via Database

```python
from src.db.models import get_session, ExtractionRun, ProviderRunMetrics

session = get_session()

# Get last run
run = session.query(ExtractionRun).order_by(
    ExtractionRun.start_time.desc()
).first()

print(f"Run: {run.id}")
print(f"Duration: {run.duration_seconds:.1f}s")
print(f"Events: {run.total_events}")
print(f"Success rate: {run.providers_succeeded}/{run.providers_attempted}")

# Get provider metrics
for pm in run.provider_metrics:
    print(f"{pm.provider_id}: {pm.events_processed} events in {pm.duration_seconds:.1f}s")

session.close()
```

### Via API

If API server is running:

```bash
# Current metrics
curl http://localhost:8000/api/metrics/current

# Historical metrics
curl http://localhost:8000/api/metrics/history?limit=10

# Provider status
curl http://localhost:8000/api/monitor/providers
```

## Troubleshooting

### No metrics in database

Check that metrics are enabled in `providers.yaml`:

```yaml
orchestrator:
  metrics:
    enabled: true
```

### Logs not rotating

Check file permissions on `backend/logs/` directory.

### Memory usage growing

Reduce metrics history in `providers.yaml`:

```yaml
orchestrator:
  metrics:
    retention_count: 50  # Default: 100
```

## Performance Tips

### Optimize Slow Providers

Use issue analysis to identify slow providers:

```bash
python scripts/analyze_extraction_issues.py
```

See `PROVIDER_OPTIMIZATION_WORKFLOW.md` for optimization guide.

### Reduce Log Volume

Lower log level for production:

```python
# In logging_config.py
setup_logging('extraction', level='INFO')  # Was: 'DEBUG'
```

### Database Maintenance

Periodically clean old runs:

```python
from src.db.models import get_session, ExtractionRun
from datetime import datetime, timedelta

session = get_session()

# Delete runs older than 30 days
cutoff = datetime.utcnow() - timedelta(days=30)
session.query(ExtractionRun).filter(
    ExtractionRun.start_time < cutoff
).delete()

session.commit()
session.close()
```

## Integration

### Scheduled Extraction

Add to cron (Linux/Mac) or Task Scheduler (Windows):

```bash
# Run every hour
0 * * * * cd /path/to/oddopp/backend && python scripts/run_monitored_extraction.py
```

### Alerting

Add email alerts for critical issues:

```bash
python scripts/analyze_extraction_issues.py > issues.txt

if grep -q "[!!]" issues.txt; then
    mail -s "OddOpp: Extraction Issues" admin@example.com < issues.txt
fi
```

### CI/CD

Run extraction and fail build on errors:

```yaml
# GitHub Actions
- name: Test extraction
  run: |
    cd backend
    python scripts/run_monitored_extraction.py --providers polymarket
    python scripts/analyze_extraction_issues.py
```

## Files Reference

| File | Purpose |
|------|---------|
| `src/db/models.py` | Database schema with metrics tables |
| `src/pipeline/metrics.py` | MetricsCollector class with persistence |
| `src/logging_config.py` | Centralized logging configuration |
| `scripts/migrate_metrics_tables.py` | Database migration |
| `scripts/run_monitored_extraction.py` | Main monitoring script |
| `scripts/compare_providers.py` | Provider comparison analysis |
| `scripts/analyze_extraction_issues.py` | Issue detection |
| `logs/extraction.log` | All extraction logs |
| `logs/errors.log` | Error-only logs |

## Next Steps

1. Run initial monitored extraction
2. Review logs in `logs/extraction.log`
3. Run provider comparison after multiple runs
4. Use issue analysis to identify problems
5. Optimize problematic providers
6. Set up scheduled extraction (optional)
7. Configure alerting (optional)
