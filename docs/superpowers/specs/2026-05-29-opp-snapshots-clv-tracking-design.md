# Opp Snapshots — CLV Tracking for All Opportunities

**Status:** Design approved 2026-05-29. Foundation iteration.

## Problem

We compute CLV (closing line value) for every placed bet — 87% of 461 bets have it (`bets.clv_pct`, `bets.closing_odds`). But we detect ~1,700 unique opportunities per 30 days; only a handful become bets. The rest leave no trace: the live `opportunities` table is wiped each extraction cycle (~2 min retention), and its `closing_line_value` column has zero populated rows.

The raw data needed to compute CLV for every opp already exists in `odds_movements` (3 months retained). We're throwing away 4× more signal than we keep.

## Goal

Persist a snapshot of every opp the scanner detects, then backfill same-provider CLV, vs-Pinnacle CLV, and (for arbs) closing prob sum once the event starts. Build the data layer cleanly; defer validation dashboards, scanner calibration, and Stats-page UI to follow-up iterations.

## Non-Goals

- Backfill historical opps from `odds_movements`. Replaying scanner logic over 3 months of old data would conflate "what today's scanner would say" with "what the scanner did say"; matching, normalization, and inversion logic have all evolved. If wanted later, do as a tagged separate effort (`source = 'retrospective'`).
- Stats-page UI / per-provider scorecards / scanner-feedback tuning. These layer on top once data accumulates; out of scope for foundation.
- Bonus-type opps. Snapshots cover `value`, `arb`, `reverse_value` only.

## Schema

New table `opp_snapshots`:

```sql
opp_snapshots:
  id                              SERIAL PRIMARY KEY
  event_id                        VARCHAR NOT NULL    REFERENCES events(id)
  type                            VARCHAR NOT NULL    -- 'value' | 'arb' | 'reverse_value'
  market                          VARCHAR NOT NULL
  outcome1                        VARCHAR NOT NULL
  point                           DOUBLE PRECISION
  scope                           VARCHAR(16) NOT NULL DEFAULT 'ft'

  -- Leg 1 (always present)
  provider1_id                    VARCHAR NOT NULL    REFERENCES providers(id)
  odds1_at_detection              DOUBLE PRECISION NOT NULL
  fair_odds1_at_detection         DOUBLE PRECISION    -- Pinnacle devigged at detect
  edge_pct_at_detection           DOUBLE PRECISION

  -- Leg 2 (arb-only, NULL for value/reverse_value)
  provider2_id                    VARCHAR             REFERENCES providers(id)
  outcome2                        VARCHAR
  odds2_at_detection              DOUBLE PRECISION

  -- Lifecycle
  first_detected_at               TIMESTAMP NOT NULL  -- frozen at first sighting
  last_detected_at                TIMESTAMP NOT NULL  -- bumped on re-detect
  detection_count                 INT NOT NULL DEFAULT 1
  time_to_start_minutes_at_detection DOUBLE PRECISION  -- frozen at first sighting

  -- Backfilled at event start (NULL until then)
  provider1_closing_odds          DOUBLE PRECISION
  provider1_closing_age_minutes   DOUBLE PRECISION    -- start_time - odds.updated_at
  provider2_closing_odds          DOUBLE PRECISION    -- arbs
  provider2_closing_age_minutes   DOUBLE PRECISION    -- arbs
  pinnacle_closing_fair           DOUBLE PRECISION    -- devigged Pinnacle close
  pinnacle_closing_age_minutes    DOUBLE PRECISION
  provider_clv_pct                DOUBLE PRECISION    -- (odds1_at_detect / provider1_close - 1)*100
  pinnacle_clv_pct                DOUBLE PRECISION    -- (odds1_at_detect / pinnacle_close - 1)*100
  closing_prob_sum                DOUBLE PRECISION    -- arbs only: 1/p1_close + 1/p2_close
  was_arb_at_close                BOOLEAN             -- arbs only: closing_prob_sum < 1
  clv_computed_at                 TIMESTAMP           -- non-null = backfill attempted

CONSTRAINT uq_opp_snapshot UNIQUE (event_id, market, outcome1, provider1_id, type, scope)
  -- mirrors the live `opportunities` uniqueness; one row per logical opp instance
INDEX ix_opp_snap_provider_type_first  ON opp_snapshots (provider1_id, type, first_detected_at)
INDEX ix_opp_snap_first_detected_at    ON opp_snapshots (first_detected_at)
INDEX ix_opp_snap_clv_pending          ON opp_snapshots (event_id) WHERE clv_computed_at IS NULL
```

**Uniqueness rationale.** Same logical opp may re-detect every scanner pass for hours. One row per `(event, market, outcome1, provider1_id, type, scope)` is the right granularity for per-provider sharpness analysis — re-detections bump `last_detected_at` and `detection_count` instead of inserting duplicates. Detection-time fields (`odds1_at_detection`, `edge_pct_at_detection`, `time_to_start_minutes_at_detection`) are frozen at first sighting — they represent "what the scanner first surfaced", which is what CLV scores against.

**Closing-age columns.** A soft book quoting at 24h-out and never updating before kickoff would otherwise look like CLV when it's actually stale data. Recording `provider1_closing_age_minutes = (start_time - odds.updated_at)` lets downstream analysis filter `WHERE provider_closing_age_minutes < 60` without making a hard cutoff at write-time (which would silently lose data).

**Bonus opps excluded.** No `bonus`-type opps; scope is `value`, `arb`, `reverse_value`.

## Write Path — Snapshot Insert

Hook the scanner's existing opportunity-upsert path. Wherever it currently `INSERT ... ON CONFLICT UPDATE` into `opportunities`, also upsert into `opp_snapshots`:

- **First sighting** (no existing row for the unique key): INSERT with `first_detected_at = now()`, `last_detected_at = now()`, `detection_count = 1`, `time_to_start_minutes_at_detection` derived from `event.start_time - now()`.
- **Re-detection** (existing row, opp still live): UPDATE only `last_detected_at = now()`, `detection_count = detection_count + 1`. Do NOT overwrite `odds1_at_detection`, `fair_odds1_at_detection`, `edge_pct_at_detection`, `time_to_start_minutes_at_detection`, or `odds2_at_detection` — those are first-sighting values.

**Inline, not async.** The extra upsert runs in the same DB session as the scanner's opp write, atomically. Estimated cost: ~1 ms per opp × ~1,500 opps per scan = ~1.5 s per cycle, well within tolerance vs. extraction itself. Atomicity is worth more than micro-latency at this volume. Revisit if profiling later shows it's a hot path.

## CLV Backfill Job

Mirror the existing `BetService.snapshot_closing_odds()` pattern (`backend/src/services/bet_service.py:568`). Add a sibling `OppSnapshotService.compute_closing_clv()`.

**Trigger.** Call from the same scheduler hook that already runs bet-CLV (`backend/src/pipeline/scheduler.py:1122`). Run them back-to-back; both depend on "event has started".

**Work-finding query.**
```sql
SELECT s.* FROM opp_snapshots s
JOIN events e ON e.id = s.event_id
WHERE s.clv_computed_at IS NULL
  AND e.start_time <= now()
LIMIT 500
```
The partial index `ix_opp_snap_clv_pending` keeps this O(matches).

**Per-row computation.**

1. `provider1_closing_odds` — latest `odds` row for `(event_id, provider1_id, market, outcome1, point, scope)`. NULL-tolerant: if the provider stopped quoting before start, leave it NULL and continue.
2. `provider1_closing_age_minutes` — `(event.start_time - odds.updated_at)` in minutes.
3. `pinnacle_closing_fair` — Pinnacle's odds for `(event_id, 'pinnacle', market, outcome1, point, scope)`, then multiplicative devig against sibling outcomes. Reuse `backend/src/analysis/devig.py` (same code path as bet-CLV).
4. `pinnacle_closing_age_minutes` — same as above, against Pinnacle's `updated_at`.
5. **For arbs only:** `provider2_closing_odds` + `provider2_closing_age_minutes` using leg-2 fields; then `closing_prob_sum = 1/p1_close + 1/p2_close` if both non-NULL; then `was_arb_at_close = closing_prob_sum < 1`.

**Write.**
- `provider_clv_pct = (odds1_at_detection / provider1_closing_odds - 1) * 100` iff `provider1_closing_odds IS NOT NULL`
- `pinnacle_clv_pct = (odds1_at_detection / pinnacle_closing_fair - 1) * 100` iff `pinnacle_closing_fair IS NOT NULL`
- `clv_computed_at = now()` — set even when both closings were NULL (we tried; don't reprocess every cycle).

**Batch.** 500 rows per cycle bounds runtime; the scheduler tick that runs bet-CLV already fires frequently enough that backlog will not grow.

## Closing-Time Definition

"Latest `odds` row at-or-before `event.start_time`" — same definition bet-side CLV uses. This keeps bet-CLV and opp-CLV numerically comparable (essential for "did my actual placements outperform the unplayed opps" analysis).

Data-quality concerns about stale provider quotes are addressed by the `*_closing_age_minutes` columns, not by changing the cutoff rule.

## What This Enables (Future Iterations, Out of Scope Here)

- **Validation:** "Over N value opps, mean `pinnacle_clv_pct` is X% ± Y%" — proves or disproves scanner sharpness with sample sizes the placed-bet table can't reach.
- **Scanner calibration:** `GROUP BY (provider1_id, sport, market, ttk_bucket)` to find combos with consistently positive CLV vs noise; bias the live scanner accordingly.
- **Shadow stats UI:** "If you'd played every value opp at the offered price, your CLV would be X%" on the Stats page.
- **Edge-pct calibration:** When scanner says "5% edge", do we realize 5% CLV?

None of these require schema changes — all are queries on `opp_snapshots`.

## Files Touched (Anticipated)

- `backend/src/db/models.py` — new `OppSnapshot` model + Alembic migration.
- `backend/src/services/opp_snapshot_service.py` — new service with `upsert_snapshot()` and `compute_closing_clv()`.
- `backend/src/analysis/scanner.py` (or wherever opps are upserted) — add inline `OppSnapshotService.upsert_snapshot()` call alongside existing opp upsert.
- `backend/src/pipeline/scheduler.py` — extend the settlement-tier hook (~line 1122) to call `OppSnapshotService.compute_closing_clv()` after `BetService.snapshot_closing_odds()`.

Exact files confirmed during plan-writing; this list is for scoping the migration footprint, not a binding contract.

## Open Questions

None at design time. Resolve during plan-writing:
- Exact scanner write site that opps flow through (single or multiple paths?). Affects whether `upsert_snapshot()` is called once or N times.
- Whether the existing `closing_line_value` column on `opportunities` is safe to drop (zero populated rows in 30d; nothing reads it per `git grep`). Probably yes; confirm before dropping.
