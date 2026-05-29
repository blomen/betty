# Multi-Book Sharp Blend — Design Spec

**Date:** 2026-05-29
**Status:** Approved design — pending implementation plan
**Sub-project 1 of 5** in the "profit-lever gap" program (see gap analysis below).

## Background

Deep research on professional sports-betting profit levers (2026) found that
the durable edges are market-structure and execution edges, not predictive
models — two model-building ROI claims were *refuted* under adversarial
verification. One verified, high-confidence finding directly implicates Betty's
core design:

> Market-maker books (Pinnacle, BetCRIS/Circa) originate lines via price
> discovery. **Pinnacle leads only the highly efficient markets (NFL, EPL);
> BetCRIS/Circa sets the line for most other US sports.** The sharpest tools
> use a *speed-weighted blend* of multiple market-makers, not a single source.

Betty's fair line is currently **Pinnacle-ONLY** (`SHARP_PROVIDERS =
frozenset({"pinnacle"})`). For everything outside NFL/EPL this is a laggier
baseline than best-in-class, which directly degrades **CLV** — the metric the
whole strategy validates on (also a high-confidence research finding:
positive-CLV bettors are "almost universally profitable regardless of
short-term variance").

This spec upgrades the **primary** fair line from Pinnacle-only to a
per-sport weighted blend of the sharp sources Betty already bets into.

## Goal

Replace the Pinnacle-only primary fair line with a **per-sport weighted
harmonic blend** of `pinnacle + cloudbet + kalshi + polymarket`, validated by
forward CLV shadow-collection, and flipped on per-sport only where it
measurably beats Pinnacle-only.

**Non-goals (explicitly out of scope):**
- Sourcing BetCRIS/Circa data (not obtainable for a platform in Betty's
  position; the chosen members are Betty's existing unlimited pool).
- Adding Smarkets / Marathon / Stake to the blend (considered, deliberately
  excluded — user trusts the books actually bet into).
- Producing fair lines where Pinnacle is absent (Pinnacle-required, to keep the
  CLV A/B comparison clean). Noted as a future relaxation.
- Learned/auto-tuned weights — that is the documented v2 evolution (Approach B),
  which this design's forward shadow data makes possible.

## Chosen approach

**Approach A — Per-sport weighted harmonic blend, forward-shadow validated.**
Pinnacle stays the anchor; Cloudbet + Kalshi/Polymarket refine it within a
per-sport leash. Structured so the weighting function can later be swapped for
CLV-learned weights (Approach B) once shadow data exists. The conservative
"bounded nudge" (Approach C) is folded in as a per-sport `max_dev_pct`
guardrail rather than a separate path.

### Why forward-shadow and not retrospective backtest

The user chose "CLV backtest first." However, the `odds` table is **upserted in
place** — one current row per outcome, overwritten every extraction
(`updated_at` only). There is **no historical time-series** of per-source odds.
`odds_movements` is append-only but only writes when `STEAM_DETECTOR_ENABLED=1`
and only logs >0.5pp deltas — not a usable snapshot series. `OppSnapshot`
freezes the **Pinnacle** fair + CLV per opportunity but never froze the
individual Cloudbet/Kalshi odds.

→ A retrospective replay is impossible (the raw data was never stored). The
chosen CLV methodology is therefore implemented as **forward shadow
collection**: freeze the blended fair now, backfill its closing CLV alongside
Pinnacle's, compare per-sport after enough samples accumulate.

## Architecture

### Component 1 — `compute_blended_sharp_fair()` (pure function)

Location: `backend/src/analysis/devig.py`, beside the existing devig helpers.
No I/O; fully unit-testable.

Signature (final names TBD in plan):
```python
def compute_blended_sharp_fair(
    outcome: str,
    odds_by_outcome: dict[str, list[dict]],   # {outcome: [{provider, odds, depth_usd, ...}]}
    sport: str,
    weights: dict[str, float],                # per-sport member weights (incl. max_dev_pct)
    liquidity_min_usd: float,
    min_sources: int = 1,
) -> BlendedFair | None
```

Algorithm:
1. For each blend member with a *complete* market at the matching scope, devig
   it (power for 3-way, multiplicative for 2-way — same selection as
   `compute_consensus_fair_odds`) → one fair-odds value for `outcome`.
2. **Liquidity gate:** drop a Kalshi/Polymarket contribution when its
   `depth_usd` < `liquidity_min_usd`. Null `depth_usd` → treated as below
   threshold (fail safe).
3. **Weighted harmonic mean** of surviving per-source fair odds, per-sport
   weights.
4. **Guardrail (Approach C folded in):** if blend deviates from Pinnacle's own
   fair by more than the sport's `max_dev_pct`, clamp back toward Pinnacle.
5. **Fallback:** no qualifying non-Pinnacle member → return Pinnacle fair
   unchanged. **Guarantees the blend is never strictly worse than Pinnacle-only.**

Returns blended fair odds **plus metadata**: contributing sources, n_sources,
the raw Pinnacle fair (for the guardrail and the shadow record).

**Currency:** not applicable — devig operates on implied probabilities, which
are currency-agnostic. (The one place CLAUDE.md's currency discipline does not
apply; called out to prevent a spurious "did we mix currencies?" review.)

### Component 2 — Configuration (`providers.yaml`)

New `sharp_blend` block (single source of truth):
```yaml
sharp_blend:
  members: [pinnacle, cloudbet, kalshi, polymarket]
  liquidity_min_usd: 500
  per_sport:
    default:               {pinnacle: 1.0, cloudbet: 0.6, kalshi: 0.5, polymarket: 0.5, max_dev_pct: 8}
    americanfootball_nfl:  {pinnacle: 1.0, cloudbet: 0.2, kalshi: 0.3, polymarket: 0.3, max_dev_pct: 4}
    soccer_epl:            {pinnacle: 1.0, cloudbet: 0.3, kalshi: 0.2, polymarket: 0.2, max_dev_pct: 4}
  use_blended_fair_for_edge:    # per-sport flip; default false = shadow only
    default: false
```
Introduce a **new** `SHARP_BLEND_MEMBERS` concept (config-driven) rather than
overloading `SHARP_PROVIDERS` (wired into storage retention + scanner
exclusions across ~15 files; repurposing it has wide blast radius). The blend
is a fair-line concern only.

Initial weights are informed guesses; they become data-driven after shadow
collection (v2 / Approach B).

### Component 3 — Shadow capture (`OppSnapshot` + `OppSnapshotService` + scanner)

Extend `OppSnapshot` with blend-parallel columns (all nullable):
- `blended_fair1_at_detection` — frozen at first sighting
- `blended_closing_fair` — backfilled at event start
- `blended_clv_pct` — backfilled, computed identically to `pinnacle_clv_pct`
- `blend_n_sources_at_detection` (Int), `blend_sources` (JSON)

During shadow, the scanner computes the blend *in addition to* the Pinnacle
fair and passes it to `OppSnapshotService.upsert_from_opportunity`, which
freezes it. **Edge math is unchanged during shadow** — Pinnacle still drives
stakes.

### Component 4 — Closing backfill

Reuse the existing CLV backfill mechanism ("latest odds row at-or-before
`start_time`", which works because Betty skips live events so each member's
last pre-match odds persists as its closing line). Compute `blended_closing_fair`
over all blend members' closing odds, then `blended_clv_pct`.

### Component 5 — Comparison surface

Extend `/api/opp-snapshots/stats` + the Shadow CLV sub-tab to show, per sport:
mean `blended_clv_pct` vs mean `pinnacle_clv_pct`, sample size n, delta. This is
the dashboard that drives flip decisions.

### Component 6 — Rollout / flip

- `use_blended_fair_for_edge` per-sport flag (default false). When true for a
  sport, scanner feeds the blended fair into the edge formula; else Pinnacle
  drives edge and the blend is shadow-only.
- **Flip criterion (manual, documented):** turn a sport on only when its blended
  CLV mean beats Pinnacle's by a meaningful margin over adequate sample
  (target `n ≥ 200` opps, sustained positive delta).
- `max_dev_pct` guardrail stays active post-flip.
- Flipping is pure `providers.yaml` config — no deploy, instantly reversible.

## Data flow

```
Extraction (existing) → Odds table (pinnacle, cloudbet, kalshi, polymarket rows)
    ↓
Scanner.scan_value (per soft provider odds)
    ↓
get_fair_odds_for_outcome(pinnacle)         ← still drives EDGE during shadow
compute_blended_sharp_fair(blend members)   ← NEW, shadow-recorded
    ↓
Opportunity → OppSnapshotService.upsert     ← freezes pinnacle + blended fair
    ↓
[event start] CLV backfill                  ← pinnacle_clv_pct + blended_clv_pct
    ↓
/api/opp-snapshots/stats → Shadow CLV sub-tab (per-sport A/B)
    ↓
[manual] flip use_blended_fair_for_edge[sport]=true once blend wins
    ↓
Scanner edge now uses blended fair for that sport (guardrail still active)
```

## Error handling & edge cases

| Case | Behavior |
|---|---|
| No qualifying non-Pinnacle member | Return Pinnacle fair unchanged (never worse) |
| Pinnacle absent | No blend, no value opp — same as today (Pinnacle-required) |
| Stale / odds-ratio outliers | **Shadow-phase decision (2026-05-30):** the blend does NOT pre-filter members on `staleness_minutes_for` / `MAX_ODDS_RATIO`. Outlier impact is bounded instead by (a) the per-sport `max_dev_pct` guardrail (clamps the blend toward Pinnacle) and (b) the `depth_usd` liquidity gate for Kalshi/Poly. Members are the frequently-extracted unlimited pool (pinnacle/cloudbet/kalshi/polymarket), where staleness is far less likely than for browser-soft books. A dedicated staleness filter can be added to `blended_fair_from_rows` later if shadow data shows stale-line contamination. Shadow-only → risk bounded to analytics, not stakes. |
| `depth_usd` null/below gate | Kalshi/Poly excluded (fail safe) |
| Incomplete market / `odds ≤ 1` | Skip that source (same guards as `compute_consensus_fair_odds`) |
| Only Pinnacle closed at backfill | `blended_closing_fair == pinnacle_closing_fair` → opp neutral in A/B delta (correct) |
| Migration | New `OppSnapshot` columns nullable; follow existing migration pattern (alembic vs create_all — confirm in plan) |

## Testing

- **Unit (`compute_blended_sharp_fair`):** weighted harmonic mean correctness;
  liquidity gate drops thin Kalshi/Poly; guardrail clamps outlier blend toward
  Pinnacle; 2-way vs 3-way devig path.
- **Regression/parity:** with only Pinnacle qualifying, blended fair ==
  `get_fair_odds_for_outcome` to float tolerance (proves "never worse").
- **Snapshot service:** blended fields frozen at detection, not overwritten on
  re-detection; backfill populates blended closing + CLV; neutral case handled.
- **Stats endpoint:** per-sport blended-vs-Pinnacle aggregation returns correct
  means/n/delta on a seeded fixture.
- Pure-math tests need no DB; service/endpoint tests use existing fixtures.

## Deployment note

This touches `backend/` (`analysis/`, `db/models.py`, `services/`, `api/`,
`config/providers.yaml`) → requires a **backend rebuild** via
`server-deploy.sh rebuild backend`. The frontend Shadow-CLV sub-tab change ships
via `betty.bat`. Shadow phase is safe to deploy immediately (no stake-path
change); the flip is config-only afterward.

## The 5-gap program (context)

This is sub-project 1. Full sequence:
1. **Multi-book sharp blend** (this spec) — foundation; better fair line lifts all downstream edges + CLV.
2. **Liquidity-aware sizing** (Gap 5) — cap prediction-market stakes by order-book depth. Independent quick win.
3. **Steam-execution latency pipeline** (Gap 1) — highest standalone ROI; depends on #1's blended trigger.
4. **Shading-aware edge adjustment** (Gap 3) — refine `consensus_lean`; builds on #1.
5. **Bonus-play behavior shaping** (Gap 4) — marginal, partly ToS-sensitive; optional/last.
