# Plan: Period Market Scanning (PR 2 + PR 3)

**Date:** 2026-05-26
**Predecessor:** PR 1 (this same date) added `f5`/`f3` to `VALID_SCOPES` and wired Pinnacle MLB period 1/3 extraction to emit `scope="f5"`/`"f3"` rows. Detailed audit: [docs/knowledge/pinnacle-period-codes.md](../../knowledge/pinnacle-period-codes.md).

**Why now.** Pinnacle F5 rows will land in the DB on next deploy, but the scanner drops them. To surface F5 opportunities to the user, the scanner must scan per-scope, and at least one soft book must extract F5 markets so there's a comparison surface.

**Why this is the next step (not a different one).** Audit on 2026-05-26 found that 5 of the 9 improvement proposals in [anon-sports-consulting-insights.md](../../knowledge/anon-sports-consulting-insights.md) are already shipped (CLV tracking, steam detector, bucket-confidence Kelly throttle, drawdown circuit, specialization auto-weighting). Of the remainder, period market scanning has the cleanest infrastructure path (extraction infra already half-done by PR 1) and the highest expected EV per dev-day. Item #5 (key-number stake math) is a deliberate non-decision; #6 (opening-line freshness) is lower priority; #9 (full placement JSONB) is partially shipped via `fair_odds_at_placement`/`edge_at_placement`.

---

## Design

### Core change: `SPORT_CANONICAL_SCOPE` → `SPORT_SCANNABLE_SCOPES`

Today:
```python
SPORT_CANONICAL_SCOPE: dict[str, str] = {"baseball": "ft", ...}
def canonical_scope_for(sport) -> str: ...
```

After PR 2:
```python
# Single primary scope per sport (kept for callers that just need one — e.g., the
# pipeline analyzer's opportunity-write code path, which writes the "canonical"
# opportunity row when there's a single primary market scope per sport).
SPORT_CANONICAL_SCOPE: dict[str, str] = {"baseball": "ft", ...}

# Set of scopes the scanner should iterate per sport. Defaults to {canonical} per
# sport — only sports with explicit period coverage list more.
SPORT_SCANNABLE_SCOPES: dict[str, frozenset[str]] = {
    "baseball": frozenset({"ft", "f5"}),  # f5 only when soft-book F5 ships (PR 3)
    # all other sports inherit {canonical_scope_for(sport)} from the helper
}

def scannable_scopes_for(sport) -> frozenset[str]:
    if sport in SPORT_SCANNABLE_SCOPES:
        return SPORT_SCANNABLE_SCOPES[sport]
    return frozenset({canonical_scope_for(sport)})
```

`canonical_scope_for` keeps current behaviour. The new helper `scannable_scopes_for` returns a superset.

**Important:** F3 stays out of `SPORT_SCANNABLE_SCOPES["baseball"]` for PR 2/3. F5 alone is the first new lane. F3 ships when we know F5 works.

### Scanner change: accept `scope` parameter

`group_odds(event)` keeps current behaviour by default. Add `scope: str | None = None` parameter:

```python
def group_odds(
    self,
    event: Event,
    exclude_providers: set[str] = None,
    check_staleness: bool = True,
    scope: str | None = None,
) -> dict:
    target_scope = scope if scope is not None else canonical_scope_for(event.sport)
    # ... existing filter changes `row_scope != canonical` → `row_scope != target_scope`
```

Backward-compat for the 7+ call sites that don't pass `scope`: they continue to scan canonical only. Only the analyzer's outer loop and the value scanner entry points need to iterate.

### Outer-loop iteration

In [pipeline/analyzer.py](../../../backend/src/pipeline/analyzer.py) where opportunities are detected, wrap the per-event scan in a scope loop:

```python
for event in events:
    for scope in scannable_scopes_for(event.sport):
        odds_grouped = self.scanner.group_odds(event, scope=scope)
        opportunities = self.scanner.scan_value_for_grouped(
            event, odds_grouped, scope=scope, ...
        )
        # opportunity rows tagged with scope
```

The same pattern goes in `scanner.scan_value()` and the other scan_* entry points that currently iterate events.

### Opportunity tagging with scope

Opportunities table already has implicit per-scope identity through `(market, point)` but no explicit scope column. Either:

- **Option A (recommended):** Add `Opportunity.scope` column with default `"ft"`. Tagged at creation time inside `scan_value_for_grouped`. Lets PR 2 ship without a UI change — opportunities for F5 just appear with scope="f5" in the DB and surface in any downstream consumer that filters/displays by scope.
- **Option B:** Encode scope into the opportunity's `market` field (e.g. `"total"` → `"total_f5"`). Avoids a schema change but breaks the existing scanner contract.

Picking **A**. One migration, clean separation, future-proof for 1H/Q1/etc.

### UI surface (deferred to PR 4)

PlayPage value-bet rows currently render with a market label. Adding a scope chip ("F5" beside "Total 8.5 over") is a UI-only change that can ship after the scanning side is verified. PR 4 — deferred.

---

## Bite-sized PR sequence

| PR | Scope | Effort | Risk |
|----|-------|--------|------|
| **PR 2.1** | Add `scannable_scopes_for` helper + `SPORT_SCANNABLE_SCOPES` dict (baseball gets {ft, f5}) + `scope` param on `group_odds`. No behaviour change yet because callers still don't pass scope. | ~30 min | Zero — purely additive |
| **PR 2.2** | Add `Opportunity.scope` column + migration. Tag scope at opportunity creation. Default "ft" for all existing rows. | ~1 hr | Low — schema additive |
| **PR 2.3** | Convert `analyzer.detect_opportunities` and the public `scanner.scan_value`/`scan_arb` entry points to iterate `scannable_scopes_for(sport)`. Each call passes `scope` to `group_odds`. | ~2-3 hr | Medium — touches the hot path. Mitigation: when `scannable_scopes_for` returns `{ft}` (every sport except baseball), behaviour is bit-for-bit identical. |
| **PR 3** | Wire Kambi MLB F5 extraction. Kambi exposes F5 via the `1st_5_innings` betOfferCategory. Add a parsing branch in `providers/kambi.py` that emits scope="f5" for matching markets. | ~3-4 hr | Low — Kambi extractor is well-understood, isolated change |
| **PR 4** | UI: scope chip on PlayPage value-bet rows; Stats view filtering by scope. | ~2-3 hr | Low — UI only |

Total: ~10 hours of focused work across 4 PRs. Each is independently shippable and reversible.

---

## Tests we need

For PR 2.1:
- `scannable_scopes_for("baseball") == {"ft", "f5"}`
- `scannable_scopes_for("football") == {"ft"}` (default)
- `group_odds(event, scope="f5")` returns only f5 rows; `group_odds(event)` unchanged

For PR 2.3:
- Integration test: one MLB event with both ft and f5 odds → analyzer produces two opportunities (one per scope)
- Regression: non-baseball events produce identical opportunity counts before/after

For PR 3:
- Kambi MLB F5 fixture → produces scope="f5" odds rows
- Kambi MLB full-game fixture → still produces scope="ft" (no regression)

---

## What this does NOT change

- Soft-book extraction for sports other than MLB (no soccer 1H, no NBA Q1) — those wait until they have explicit demand
- The `MAX_ODDS_RATIO` and `MIN_VALID_PROB_SUM` scanner guards — they apply per-scope unchanged
- Cluster dedup in `play_loop.py` — different scopes on the same event are different bet identities, no dedup issue
- Kelly sizing — F5 opportunities go through the same `get_kelly_fraction` + `bucket_confidence` machinery; F5 will start showing up as its own (sport, market) bucket once enough placements accumulate
- CLV calculation — already uses (event_id, market, outcome, point) and SAME pinnacle row at close. F5 CLV will work the same way once F5 odds rows exist for both Pinnacle and the bet's provider.

---

## Sequencing decision: ship PR 2.x first or wait for Kambi?

PR 2 (the scanner changes) is safe to ship without PR 3 — it just means baseball events with f5 scope have no soft-book counterpart in `odds_by_outcome`, so no F5 opportunities surface, but no regression on existing ft opportunities. That's the right increment: ship PR 2.x to derisk the scanner change, then ship PR 3 (Kambi) to start producing F5 opportunities.