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

When `original_odds` is null AND the boost matched a Pinnacle event (`matched_event_id` is set):

1. Query Pinnacle odds for that event+market from the `odds` table
2. De-vig using multiplicative method (same as existing `devig.py`)
3. Set `original_odds = pinnacle_fair_odds`
4. Compute `edge_pct` and `boost_pct` normally

**Scope:** Single bets only. Combo boosts (multi-leg) cannot easily match individual legs to Pinnacle markets — leave `original_odds` null for those. The LLM enrichment handles combos via probability estimation.

**Effect:** Bookmaker anchor check in `_apply_bookmaker_anchor()` now works for Kambi/BetConstruct single bets. The `boost_margin` ML feature also gets populated.

### 2. Cache TTL expiry (48 hours)

**File:** `backend/src/analysis/llm_enrichment.py`

**Constant:** `CACHE_TTL_HOURS = 48`

**Change in `_carry_forward_from_cache()`:** Before applying a cached result, check `created_at`. If older than 48 hours, skip carry-forward so the boost becomes a candidate for re-research. The old cache row stays in the DB until overwritten by the new LLM result.

**Change in `_load_cache_from_db()`:** Also load `created_at` into the cache dict (currently not loaded).

**Rationale:** Boosts typically live 1-7 days. 48h means each boost gets re-researched ~1-3 times before expiry, balancing API cost vs freshness.

### 3. Remove dead ML features

**Files:** `backend/src/ml/features/boost_features.py`, `backend/src/ml/models/boost_calibrator.py`, `backend/src/analysis/llm_enrichment.py`

Remove two features that will never be populated:
- `brave_results_count` — Brave Search integration dropped
- `legs_matched_ratio` — no per-leg Pinnacle matching exists

**Feature count:** 19 → 17. Update `FEATURE_NAMES` in `boost_calibrator.py` and remove the parameters from `extract_boost_features()`.

**Model compatibility:** Add a version check when loading a saved model. If the loaded model has a different feature count than `FEATURE_NAMES`, discard it and log a warning. Safe since the calibrator likely hasn't been trained yet.

### 4. Boost outcome resolution for ML training

**Files:** `backend/src/ml/feature_store.py` (new function), `backend/src/services/bet_service.py` (hook)

**New function `resolve_boost_outcomes(session, title)`:**
1. Query `ml_features` where `source_type='boost'` AND `source_id=title` AND `outcome IS NULL`
2. Join to `bets` table where `bet_type='boost'` AND `outcome` matches the boost title AND `result` is not null
3. Map: `won` → `outcome_binary=1`, `lost` → `outcome_binary=0`, `void` → delete the feature row
4. Set `resolved_at` timestamp

**Hook in `settle_bet()`:** After settling a bet where `bet_type='boost'`, call `resolve_boost_outcomes()` for that specific title. Immediate propagation, no batch job needed.

**Training:** No auto-training. Once 100+ resolved boost features accumulate, training can be triggered manually.

## Files Changed

| File | Change |
|------|--------|
| `backend/src/analysis/ev_enrichment.py` | Pinnacle proxy for missing original_odds |
| `backend/src/analysis/llm_enrichment.py` | Cache TTL check, load created_at |
| `backend/src/ml/features/boost_features.py` | Remove brave_results_count, legs_matched_ratio |
| `backend/src/ml/models/boost_calibrator.py` | Update FEATURE_NAMES, add model version check |
| `backend/src/ml/feature_store.py` | New resolve_boost_outcomes() |
| `backend/src/services/bet_service.py` | Hook resolve_boost_outcomes on boost settlement |

## Out of Scope

- Brave Search integration (dropped — bookmaker anchor + calibrator is the better approach)
- Per-leg Pinnacle matching for combo boosts
- Auto-training trigger for calibrator
- Auto-settlement of boost bets (user settles manually)
