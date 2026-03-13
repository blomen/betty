# M10 Extraction Pipeline Optimizer — Design Spec

## Goal

Maximize extraction value: more Pinnacle matches, better provider coverage, smarter scheduling, actionable diagnostics. Two layers: an analytics engine for immediate rule-based insights (works with 40 existing runs), and an ML auto-optimizer that activates when data thresholds are met.

## Core Philosophy

**Diagnose before deprioritize.** When a provider underperforms, the system must:

1. Diagnose — identify the root cause (low match rate? missing markets? slow? timing out?)
2. Recommend fix — specific, actionable suggestion (e.g., "check sports.yaml aliases for tennis")
3. Track fix attempts — log when a recommendation was acted on and whether it improved things
4. Deprioritize only as last resort — after fixes attempted and failed, reduce extraction frequency rather than full skip

This philosophy applies to all 4 ML sub-models, not just provider scoring.

## Architecture

```
Extraction completes
    |
    v
Phase 1 hooks: pinnacle_coverage_log + extraction_features + provider_value_log
    |
    v
AnalyticsEngine.refresh(session, run_id)  [best-effort, try/except, same session]
    |
    v
  1. Query provider_run_metrics + sport_run_metrics + opportunities + bets
  2. Run diagnostic rules -> generate/update provider_recommendations
  3. Append Provider ROI + Recommendations to extraction report (CLI)
    |
    v
If ml_threshold_reached (checked per sub-model):
  4. Retrain M10 sub-models (reads from Phase 1 feature tables)
  5. ML recommendations supplement rule-based ones
```

## Backfill Strategy

Phase 1 tables (`extraction_features`, `provider_value_log`, `pinnacle_coverage_log`) have 0 rows — the hooks were deployed after the 40 existing runs completed. The analytics engine operates in two modes:

**Layer 1 analytics (immediate):** Queries the pre-existing tables directly (`extraction_runs`, `provider_run_metrics`, `sport_run_metrics`, `opportunities`, `bets`). Does NOT depend on the Phase 1 ML tables. Works today with 40 runs of data.

**Layer 2 ML models:** Depend on Phase 1 tables. These populate naturally going forward. No backfill needed — the activation thresholds (50+ runs) mean enough new data will accumulate before ML kicks in.

## Layer 1: Extraction Analytics Engine

Queries existing tables directly: `extraction_runs` (40 rows), `provider_run_metrics` (162 rows), `sport_run_metrics` (1,596 rows), `opportunities` (2,313 rows), `bets` (238 rows).

### Module: Provider Value Attribution

Answers: "Which providers are worth extracting?"

Links the full chain: provider -> odds extracted -> opportunities generated -> bets placed -> actual P&L.

**Opportunity-to-provider linkage:** The `opportunities` table has `provider1_id` which directly identifies the source provider. For bets, `bets.provider_id` links to the provider. No `run_id` join is needed — we aggregate per-provider across all time or rolling windows based on timestamps (`opportunities.created_at`, `bets.placed_at`).

**Platform deduplication:** Kambi brands (unibet, leovegas, expekt, etc.) share identical odds. The opportunities table fans out one row per alias member. Analytics must group by **canonical provider** using `PROVIDER_CANONICAL` from `constants.py` (e.g., all Kambi brands roll up to `unibet`). The `provider_run_metrics` table only has rows for canonical providers (extraction runs once per platform), so no dedup needed there.

Per-provider metrics (computed over rolling windows: last 5 runs, last 10 runs, all time):
- `avg_events` — average events extracted per run (from `provider_run_metrics.events_processed`)
- `avg_odds` — average odds rows per run (from `provider_run_metrics.odds_processed`)
- `avg_value_bets` — average value bet opportunities per run (from `opportunities` grouped by canonical provider, time-windowed to extraction run period)
- `avg_edge` — average edge % of opportunities from this provider (from `opportunities.edge_pct`)
- `total_bets_placed` — bets placed for this provider (from `bets.provider_id`, mapped to canonical)
- `win_rate` — win % of resolved bets (from `bets` where `result IN ('won', 'lost')`)
- `net_pnl` — net P&L (from `bets`: won = odds * stake - stake, lost = -stake)
- `avg_duration` — average extraction time (from `provider_run_metrics.duration_seconds`)
- `seconds_per_value_bet` — extraction cost: duration / value bets. When value_bets = 0, set to NULL (the diagnostic engine flags these providers separately)

### Module: Coverage Gap Analysis

Answers: "What are we missing?"

Per-provider per-sport:
- Event coverage: what % of Pinnacle events does this provider match?
- Market coverage: spread coverage %, total coverage % (on matched events)
- Trend: is match rate improving or degrading run-over-run?
- Dead weight detection: sport/provider combos producing 0 value bets

Coverage data sources: `sport_run_metrics` (available now, 1,596 rows — has per-provider per-sport event counts and market breakdowns) and `pinnacle_coverage_log` (populates going forward with richer delta data).

### Module: Scheduling Efficiency

Answers: "Are we running at the right times?"

Per-tier analysis:
- Value bets per second of extraction time
- Events per second vs value bets per second (efficiency gap)
- Time-of-day yield: do certain hours produce more value bets?
- Staleness proxy: time between extraction run start and value scan — shorter gap = fresher odds. (Direct odds timestamps not available; `odds_age_minutes` on opportunities populates going forward.)

Current baselines from 40 runs:
- sharp: 51s avg, 2,256 events
- api_soft: 153s avg, 9,872 events
- browser_soft: 947s avg, 3,166 events
- manual: 13s avg, 803 events

## Layer 2: ML Auto-Optimizer

Four sub-models using LightGBM on tabular data. Walk-forward cross-validation with 5-run embargo to prevent temporal leakage. Each model activates independently when its data threshold is met.

### M10a: Schedule Optimizer

**Question:** When should each tier run?

**Input features:**
- hour_of_day, day_of_week
- events_starting_next_2h, events_starting_next_6h
- minutes_since_last_sharp, minutes_since_last_soft
- providers_attempted, providers_succeeded

**Target:** value_bets_found + avg_edge_pct from that run

**Output:** recommended polling interval per tier per time-of-day bucket (e.g., "run api_soft every 45min on weekday evenings, every 120min on Monday mornings")

**Activation threshold:** 50+ runs per tier in extraction_features table

### M10b: Provider Priority Scorer

**Question:** Which providers to extract first, and how to fix underperformers?

**Input features:**
- provider_id (one-hot or label-encoded)
- sport distribution (% of events per sport)
- historical match_rate (rolling)
- duration_seconds (rolling avg)
- value_bets_from_provider, avg_edge_from_provider
- exclusive_events (events only this provider has)

**Target:** value bets generated per extraction second

**Output:**
1. Ranked provider list per tier
2. Diagnostic: WHY underperformers score low (feature importance)
3. Fix recommendation before any deprioritization

**Activation threshold:** 100+ provider_value_log rows per provider. At current extraction frequency (~1 run/tier/day), this is ~3 months away. In the interim, the rule-based diagnostics from Layer 1 handle provider scoring.

### M10c: Timeout Tuner

**Question:** How long to wait before giving up on a provider?

**Input features (all from `provider_run_metrics`):**
- provider_id, sport
- circuit_breaker_tripped (boolean, from `provider_run_metrics`)
- recent failure rate (computed: failed runs / total runs in last N)
- duration trend (rolling avg of `duration_seconds` from last 5 `provider_run_metrics` rows)
- time of day (from `extraction_runs.start_time`)

**Target:** success/failure of extraction (binary classification)

**Output:** per-provider timeout recommendation (currently hardcoded in providers.yaml)

**Activation threshold:** 50+ runs per provider in provider_run_metrics

### M10d: Coverage Optimizer

**Question:** How to close the Pinnacle coverage gap?

**Input features:**
- pinnacle_coverage_log data (per-provider per-sport)
- coverage trends over time
- market type gaps (spread/total/ml separately)

**Target:** identify systematic gaps and rank by potential value

**Output:**
1. Prioritized gap list: gap_size x sport_volume x avg_edge_for_sport
2. Root cause classification: "missing aliases" / "needs enrichment pass" / "API limitation" / "sport not supported"
3. Specific fix suggestion per gap

**Activation threshold:** 20+ pinnacle_coverage_log rows per provider

### Model Training Infrastructure

- **Algorithm:** LightGBM gradient-boosted trees (tabular data, <10k rows — no deep learning needed). Note: parent ML spec references XGBoost; LightGBM chosen for faster training on small datasets. Both are gradient-boosted trees — the distinction is implementation, not approach.
- **Validation:** Walk-forward with 5-run purge/embargo (no future data leakage)
- **Storage:** ml_model_registry table (already exists from Phase 1)
- **Retraining trigger:** after every 10 extraction runs, if above activation threshold
- **Feature store:** ml_features table with domain='extraction' (already exists)

## New Database Tables

### provider_recommendations

Tracks diagnostic recommendations and their lifecycle.

```sql
CREATE TABLE provider_recommendations (
    id INTEGER PRIMARY KEY,
    provider_id TEXT NOT NULL,
    category TEXT NOT NULL,          -- 'match_rate', 'coverage', 'timing', 'roi', 'market_gap'
    severity TEXT NOT NULL,          -- 'critical', 'warning', 'info'
    message TEXT NOT NULL,           -- human-readable recommendation
    diagnostic_data JSON,            -- evidence: metrics, root cause, comparison data
    status TEXT NOT NULL DEFAULT 'open',  -- 'open', 'acted_on', 'resolved', 'wont_fix'
    acted_on_at DATETIME,
    resolved_at DATETIME,
    before_metric REAL,              -- metric value when recommendation was created
    after_metric REAL,               -- metric value after fix was applied
    source TEXT DEFAULT 'rules',     -- 'rules' or 'ml' (which layer generated it)
    created_at DATETIME DEFAULT (datetime('now'))
);
CREATE INDEX idx_recommendations_provider ON provider_recommendations(provider_id);
CREATE INDEX idx_recommendations_status ON provider_recommendations(status);
```

### No extraction_analytics cache table

Analytics aggregations (provider ROI, coverage gaps, scheduling efficiency) are computed on demand from `provider_run_metrics` (162 rows), `sport_run_metrics` (1,596 rows), `opportunities` (2,313 rows), and `bets` (238 rows). These are trivial queries on small tables — caching adds write complexity and stale data risk without measurable benefit. If table sizes grow to 100k+ rows, add materialized views then.

No new tables needed for ML — uses ml_model_registry + ml_features from Phase 1.

## Output Surfaces

### CLI Extraction Report (extended)

New sections appended after the existing report:

```
PROVIDER ROI (last 10 runs)
------------------------------------------------------------------------------------------
Provider         Ev/Run  VB/Run  Edge%  Bets  Win%    P&L  Sec/VB  Status
------------------------------------------------------------------------------------------
unibet              284    12.3   3.2%    18   31%   +420   2.1s   OK
betsson              85     8.1   4.7%     9   33%   +380   3.8s   OK
dbet                166     6.2   4.4%     5   20%   -120   8.3s   ~ slow match rate (62%)
comeon                42     0.8   2.1%     0    -       0  52.1s   ! fix: SPA stalls on tennis

RECOMMENDATIONS
------------------------------------------------------------------------------------------
! dbet: match rate dropped 62%->55% over last 5 runs -- check Altenar API changes
~ comeon: 0.8 VB/run at 52s extraction -- fix tennis SPA stall before deprioritizing
+ betinia: spread_count=0 -- enable Pass 2 GetEventDetails enrichment for spreads
```

### API Endpoints

- `GET /api/extraction/analytics` — full provider ROI + coverage gaps + scheduling efficiency
- `GET /api/extraction/recommendations` — active recommendations with status
- `PATCH /api/extraction/recommendations/{id}` — update recommendation status (acted_on, resolved, wont_fix)

### StatsPage Integration

Add "Extraction" section to existing StatsPage:
- Provider ROI table (sortable by any column)
- Coverage gap summary per sport
- Active recommendations list with action buttons

## File Structure

```
backend/src/ml/
    features/                  -- Phase 1 (EXISTS): data collection / feature extraction
        betting_features.py
        trading_features.py
        candle_features.py
        extraction_features.py
        pinnacle_coverage.py
    analytics/                 -- NEW: rule-based analysis on existing data
        __init__.py
        engine.py              -- AnalyticsEngine: refresh(), compute_provider_roi(), compute_coverage_gaps(), compute_scheduling()
        diagnostics.py         -- DiagnosticEngine: run_rules(), diagnose_provider()
        recommendations.py     -- RecommendationManager: create(), update_status(), get_active()
    optimizer/                 -- NEW: ML models that train when thresholds met
        __init__.py
        trainer.py             -- LightGBM training with walk-forward validation
        schedule.py            -- M10a: Schedule optimizer
        provider_priority.py   -- M10b: Provider priority scorer
        timeout.py             -- M10c: Timeout tuner
        coverage.py            -- M10d: Coverage optimizer
```

Relationship: `features/` collects raw data, `analytics/` produces rule-based insights from existing tables, `optimizer/` trains ML models on Phase 1 feature tables when thresholds are met.

## Integration Points

- `pipeline/orchestrator.py` — call `AnalyticsEngine.refresh(session, run_id)` after extraction. Wrapped in try/except, must never fail the extraction run. Runs in the same DB session as the extraction (reads data the extraction just wrote). Placed after the Phase 1 ML hooks.
- `pipeline/extraction_report.py` — call analytics engine to append Provider ROI + Recommendations sections
- `api/routes/extraction.py` — add analytics and recommendations endpoints
- `frontend/src/components/Terminal/pages/StatsPage.tsx` — add Extraction section

## Not In Scope

- Automatically modifying providers.yaml (ML outputs recommendations, human decides)
- Trading models (M5-M9) — separate plan
- Betting models (M1-M4) — separate plan
- Real-time extraction monitoring/alerting (webhook/push notifications)

## Dependencies

- `lightgbm` — gradient-boosted tree training (add to requirements.txt)
- All Phase 1 tables and hooks (ml_features, extraction_features, provider_value_log, pinnacle_coverage_log) — already deployed
