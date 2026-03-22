# Boost Pipeline Gaps — Design Spec

**Date:** 2026-03-22
**Status:** Approved

## Problem

The specials/boosts pipeline has 5 gaps that reduce the quality of LLM probability estimates and block ML calibrator training:

1. Kambi & BetConstruct boosts have no `original_odds` — `edge_pct`, `boost_pct`, and bookmaker anchor check are all null/skipped
2. LLM cache never expires — stale probability estimates persist indefinitely
3. Two ML features (`brave_results_count`, `legs_matched_ratio`) are dead placeholders (always 0)
4. ML calibrator can't train — no outcome resolution connects settled boost bets to `ml_features`

(Original gap 2, "LLM has no live data / Brave Search", was dropped. The bookmaker anchor check + future calibrator handle accuracy better than noisy web snippets.)

## Design

### 1. Synthesize original odds for Kambi/BetConstruct

**File:** `backend/src/analysis/ev_enrichment.py` → `enrich_specials_with_ev()`

**Execution order:** Currently, boost edge calculation (lines 186-194) runs BEFORE event matching (`_match_boosts_to_events`, line 197). The Pinnacle proxy needs `matched_event_id` to be set first. Fix: move the edge calculation to AFTER `_match_boosts_to_events()`, then add the Pinnacle proxy step between matching and edge calculation.

When `original_odds` is null AND the boost matched a Pinnacle event (`matched_event_id` is set) AND it's a single-leg bet (`_detect_legs_from_title()` returns 1):

1. Query all Pinnacle odds for that `matched_event_id` from the `odds` table (provider_id = 'pinnacle', market in ('1x2', 'moneyline'))
2. Heuristic outcome mapping from boost title: scan for team names in the title to determine if it's a home/away/draw selection. If the boost title contains one of the matched event's team names, map to the corresponding outcome. Skip if ambiguous or no match.
3. De-vig using multiplicative method (same as existing `devig.py` → `get_fair_odds_for_outcome()`)
4. Set `original_odds = pinnacle_fair_odds`
5. Compute `edge_pct` and `boost_pct` normally

**Scope:** Single-leg match winner bets only. Combo boosts, player props, and any boost where the outcome can't be mapped to a Pinnacle market are skipped (leave `original_odds` null). The LLM enrichment handles these via probability estimation.

**Effect:** Bookmaker anchor check in `_apply_bookmaker_anchor()` now works for Kambi/BetConstruct single bets during LLM enrichment (not during EV enrichment — the anchor runs in `llm_enrichment.py`). The `boost_margin` ML feature also gets populated.

### 2. Cache TTL expiry (48 hours)

**File:** `backend/src/analysis/llm_enrichment.py`

**Constant:** `CACHE_TTL_HOURS = 48`

**Change in `_carry_forward_from_cache()`:** Before applying a cached result, check `created_at`. If older than 48 hours, skip carry-forward so the boost becomes a candidate for re-research. The old cache row stays in the DB until overwritten by the new LLM result.

**Change in `_load_cache_from_db()`:** Also load `created_at` into the cache dict (currently not loaded). Note: `created_at` is stored as an ISO string (`Column(String)`), not a DateTime — the TTL comparison must parse the string via `datetime.fromisoformat()` before computing age.

**Rationale:** Boosts typically live 1-7 days. 48h means each boost gets re-researched ~1-3 times before expiry, balancing API cost vs freshness.

### 3. Remove dead ML features

**Files:** `backend/src/ml/features/boost_features.py`, `backend/src/ml/models/boost_calibrator.py`

Remove two features that will never be populated:
- `brave_results_count` — Brave Search integration dropped
- `legs_matched_ratio` — no per-leg Pinnacle matching exists

**Feature count:** 19 → 17. Update `FEATURE_NAMES` in `boost_calibrator.py` and remove the parameters from `extract_boost_features()`.

**Model compatibility:** Add a version check when loading a saved model. If the loaded model has a different feature count than `FEATURE_NAMES`, discard it and log a warning. Safe since the calibrator likely hasn't been trained yet.

### 4. Boost outcome resolution for ML training

**Files:** `backend/src/ml/feature_store.py` (new function), `backend/src/services/bet_service.py` (hook)

**Join key:** `ml_features.source_id` stores the original scraper title (set at `llm_enrichment.py:768`). `bets.outcome` also stores the original scraper title for boost bets (set by frontend at `ValuePage.tsx:636`: `outcome: special.title`). These match reliably.

**New function `resolve_boost_outcomes(session, boost_title)`** — do NOT reuse existing `resolve_outcome()` helper (it only updates the first matching row via `.first()`). This function must handle ALL matching rows:
1. Query ALL `ml_features` where `source_type='boost'` AND `source_id=boost_title` AND `outcome IS NULL` (use `.all()`)
2. Query `bets` where `bet_type='boost'` AND `outcome=boost_title` AND `result IS NOT NULL`
3. For each matching feature row, map: `won` → `outcome=1.0, outcome_binary=1`, `lost` → `outcome=0.0, outcome_binary=0`, `void` → delete the feature row (not useful for calibration)
4. Set `resolved_at` timestamp on each updated row

**Hook in `settle_bet()`:** After settling a bet where `bet.bet_type == 'boost'`, call `resolve_boost_outcomes(self.db, bet.outcome)`. Immediate propagation, no batch job needed.

**Training:** No auto-training. Once 100+ resolved boost features accumulate, training can be triggered manually.

## Files Changed

| File | Change |
|------|--------|
| `backend/src/analysis/ev_enrichment.py` | Pinnacle proxy for missing original_odds |
| `backend/src/analysis/llm_enrichment.py` | Cache TTL check, load created_at, remove dead feature params from callers |
| `backend/src/ml/features/boost_features.py` | Remove brave_results_count, legs_matched_ratio params |
| `backend/src/ml/models/boost_calibrator.py` | Update FEATURE_NAMES, add model version check |
| `backend/src/ml/feature_store.py` | New resolve_boost_outcomes() |
| `backend/src/services/bet_service.py` | Hook resolve_boost_outcomes on boost settlement |

## Out of Scope

- Brave Search integration (dropped — bookmaker anchor + calibrator is the better approach)
- Per-leg Pinnacle matching for combo boosts
- Auto-training trigger for calibrator
- Auto-settlement of boost bets (user settles manually)
