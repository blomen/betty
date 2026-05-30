# Shading-Aware Edge Diagnostic (v1) — Design Spec

**Date:** 2026-05-30
**Status:** Approved design — pending implementation plan
**Sub-project 4 of 5** in the "profit-lever gap" program.

## Background & premise correction

The original gap framing was "even a devigged sharp line carries directional
shading, so some edges are artifacts." A grounded, adversarially-verified
research pass (workflow `understand-shading-gap`, 2026-05-30) substantially
**narrowed** this:

- **Pinnacle largely does NOT shade toward the public.** Levitt-style public
  shading is a *recreational-book* behavior; Pinnacle is the passive, low-margin
  (~2-3%) sharp reference. Building a "Pinnacle un-shading" offset would *add*
  error. (Levitt 2004; Paul-Weinbach: NFL-yes/NBA-no, all soft-book findings.)
- The real residual effect is the **favorite-longshot bias (FLB)**, which is a
  **devig-METHOD artifact**, not mispricing — and Betty **already half-handles
  it**: `get_fair_odds_for_outcome` (`devig.py:147`) auto-switches to
  `devig_power` for 3-way (1x2) markets and keeps multiplicative only for 2-way
  (moneyline/spread/total). The FLB EV gap is **≲1pp on near-even lines
  (35-65%)** — where most matched lines sit — and only material at extremes.
- **Over-correction is the dominant, empirically-documented risk.** Bias-aware
  devig models manufactured *phantom* positive blind-bet yields on longshots.
- Several specific magnitudes from the first research pass were **refuted**
  (the "5pp gap at 80/20, 3% margin"; the "2pp → +6.63% ROI" threshold).
  We deliberately do NOT design around any of those numbers.

**Conclusion:** #4 is scoped as a **diagnostic + CLV-bucket validation** layer,
NOT a live edge correction. It measures a conservative per-outcome shading-RISK
signal and records realized CLV by `(odds_bucket × shading_risk)` so we can
*later* decide — from Betty's own data — whether any live correction (the
research's preferred mechanism is a stake throttle, not an edge haircut) is
warranted. Acting live is a separate future plan, gated on this data.

## Goal

Surface a conservative per-outcome **shading-risk** signal (low/elevated/high)
as a diagnostic badge, and record it + an odds bucket on each opportunity
snapshot so the Shadow-CLV surface can answer: *do high-shading-risk bets in a
given odds bucket realize systematically worse CLV than low-risk bets in the
same bucket?* **Zero edge/stake change.**

## Decisions (from brainstorming)

- **Direction:** diagnostic + CLV-bucket validation (Option A). No live edge or
  stake change.
- **Signal basis:** consensus-lean **spine** + **FLB flag (2-way markets only)**.
  - Spine = the existing `consensus_lean` signal (soft-consensus vs Pinnacle
    divergence) — already a direct shading proxy. `lean == "stale_outlier"`
    (softs say the outcome is MORE likely than Pinnacle → the Pinnacle price may
    be shaded/stale on this side) is the primary risk driver.
  - FLB flag fires ONLY on 2-way markets (where multiplicative devig leaves FLB
    un-neutralized) when `fair_probability` is extreme. NEVER on 1x2 (power
    devig already handles it → avoids double-counting).
- All thresholds are **named, tunable constants** documented as "starting
  hypotheses to backtest, not laws."

## Architecture

### Component 1 — `backend/src/analysis/shading.py` (pure classifier)

```python
@dataclass(frozen=True)
class ShadingSignal:
    risk: str                    # "low" | "elevated" | "high"
    favorite_side: bool          # is this outcome the market favorite?
    fav_prob: float              # the outcome's devigged fair probability
    divergence_pp: float | None  # carried from consensus_lean (spine)
    flb_contrib: bool            # FLB extremity flag fired (2-way only)
    reason: str                  # human-readable "why"

    def to_dict(self) -> dict: ...

def compute_shading(
    fair_probability: float,
    market: str,
    consensus_lean: dict | None,   # ConsensusLean.to_dict() or None
    *,
    fav_extreme_prob: float = SHADING_FAV_EXTREME_PROB,   # tunable
    elevated_divergence_pp: float = SHADING_ELEVATED_PP,   # tunable
    high_divergence_pp: float = SHADING_HIGH_PP,           # tunable
) -> ShadingSignal | None:
    ...
```

Logic (conservative, additive, no mutation):
1. If `consensus_lean` is None (fewer than `MIN_SOFT_BOOKS` soft books) →
   return `None` (degrade safely; no annotation, no badge).
2. Spine: read `consensus_lean["lean"]` and `["divergence_pp"]`. A
   `stale_outlier` lean with divergence beyond `elevated_divergence_pp` →
   `elevated`; beyond `high_divergence_pp` → `high`. `sharp_value`/`market_lag`
   → spine contributes `low`.
3. FLB flag (2-way only): `is_two_way = market in {"moneyline","spread","total"}`.
   If `is_two_way` AND `fair_probability ≥ fav_extreme_prob` (heavy favorite) OR
   `fair_probability ≤ 1 - fav_extreme_prob` (longshot) → `flb_contrib = True`,
   which can lift `low → elevated` (never alone to `high`).
4. `favorite_side = fair_probability ≥ 0.5`. `risk` = max of spine tier and FLB
   contribution. `reason` explains which signals fired.

Constants live at module top, each annotated as a backtest hypothesis.

### Component 2 — Annotation wiring (reuse existing pipe, no schema change)

In `backend/src/pipeline/analyzer.py` (the `annotations = {...}` dict, ~412-418),
add `"shading": signal.to_dict()` when `compute_shading(...)` is non-None. Flows
through the existing `Opportunity.annotations` JSON column →
`opportunity_service.py` → `/api/opportunities/play/batch` → frontend. The live
scan path (`scanner.py`) computes `consensus_lean` already; `compute_shading`
reads that same dict — no recomputation of consensus.

### Component 3 — Frontend badge

`renderAnnotationBadges()` in `frontend/src/pages/PlayPage.tsx` (~2699-2735)
gets a `shade` pill: amber for `elevated`, red for `high`, hidden for `low`/None.
Tooltip = `ShadingSignal.reason`. Consistent with the existing sharp/stale/
steam/key pills. (Frontend-only; ships via `betty.bat`.)

### Component 4 — CLV-bucket validation (reuses #1 `opp_snapshots` infra)

- Add two nullable columns to `OppSnapshot` (`db/models.py`) via the idempotent
  `_run_pg_migrations` ADD COLUMN IF NOT EXISTS pattern (same as #1's blend
  columns):
  - `shading_risk` (String, nullable)
  - `odds_bucket` (String, nullable)
- In `OppSnapshotService.upsert_from_opportunity`, freeze both at detection:
  `shading_risk` from the opp's `annotations["shading"]["risk"]` (None if
  absent); `odds_bucket` from `odds1_at_detection` via the EXISTING
  `patterns._odds_range` buckets (`<1.5 | 1.5-2.5 | 2.5-4.0 | 4.0+`) — one
  source of truth, no divergent bucketing.
- Extend `GET /api/opp-snapshots/stats` with a `shading_clv_breakdown` section:
  group by `(odds_bucket, shading_risk)` → mean `pinnacle_clv_pct` + n, gated by
  the existing min-sample `having(count >= 3)` pattern.
- Render a small table in the Stats → Shadow CLV sub-tab.

**The analysis question it answers:** within an odds bucket, does `high`
shading-risk realize worse CLV than `low`? If yes → signal is real, a throttle
is justified (future plan). If no → signal is noise; we correctly never touched
stakes.

## Data flow

```
scanner: consensus_lean (existing) ─┐
fair_probability + market ──────────┴─> compute_shading() → ShadingSignal | None
    ↓ (analyzer.py annotations dict)
Opportunity.annotations["shading"]  ──> /play/batch ──> PlayPage shade badge
    ↓ (OppSnapshotService.upsert_from_opportunity)
OppSnapshot.shading_risk + odds_bucket  (frozen at detection)
    ↓ (existing #1 CLV backfill sets pinnacle_clv_pct)
/api/opp-snapshots/stats → shading_clv_breakdown (bucket × risk → mean CLV, n)
    ↓
Shadow CLV sub-tab table  →  [future, gated] stake throttle plan
```

## Error handling & edge cases

| Case | Behavior |
|---|---|
| `consensus_lean` absent (<3 soft books) | `compute_shading` → None; no annotation/badge/risk |
| 1x2 market | FLB flag never fires (power devig handles it); only lean-spine can raise risk |
| Old snapshot rows | columns nullable; backfill-neutral; breakdown filters non-null |
| Odds bucketing | reuse `patterns._odds_range` exactly (no divergent buckets) |
| `shading` annotation present but risk=low | no badge; still recorded for CLV baseline |
| No edge/stake anywhere | `compute_shading` is pure read-only; never mutates edge_pct/stake |

## Testing

- **Unit (`compute_shading`):** stale_outlier + high divergence → `high`;
  neutral/market_lag lean → `low`; 2-way heavy favorite → `flb_contrib=True`;
  1x2 heavy favorite → `flb_contrib=False`; None consensus_lean → None; tier
  boundaries at the tunable thresholds.
- **Snapshot service:** `shading_risk` + `odds_bucket` frozen from the
  annotation + `odds1_at_detection`; null when no shading annotation; bucket
  matches `patterns._odds_range`.
- **Stats endpoint:** `shading_clv_breakdown` groups by (bucket, risk), correct
  means/n on a seeded fixture, min-sample gate.
- Pure-classifier tests need no DB; service/endpoint use existing fixtures.

## Deployment note

Backend rebuild (analysis + analyzer + models + snapshot service + endpoint);
frontend badge/table via `betty.bat`. Migration = 2 nullable columns (safe, no
backfill). **Zero betting-path change** — pure diagnostic. Shadow-safe to deploy
immediately.

## The 5-gap program (context)

Sub-project 4 of 5: 1. multi-book sharp blend (shipped) → 2. liquidity-aware
sizing (shipped) → 3. steam alert UX (shipped) → **4. shading-aware diagnostic
(this spec)** → 5. bonus-play behavior shaping. Deferred within #4: any live
edge/stake adjustment (a stake throttle), gated on the CLV-bucket data this
sub-project collects.
