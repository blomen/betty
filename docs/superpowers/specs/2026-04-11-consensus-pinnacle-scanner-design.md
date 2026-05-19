# Consensus-Based Pinnacle Value Scanner — Design Spec

**Date:** 2026-04-11
**Status:** Draft
**Scope:** New scanner mode that finds value bets ON Pinnacle by comparing its odds against consensus of all soft providers

## Problem

The current scanner uses Pinnacle as the sharp source and finds soft books priced above Pinnacle fair. This generates "bet on unibet/betinia/etc." opportunities — books that limit winning players.

Pinnacle doesn't limit. We now have 14+ independent price sources. When the market consensus says an outcome is worth 2.44 and Pinnacle offers 2.55, that's a value bet on Pinnacle — the one venue where we can bet unlimited, tax-free.

## How It Works

```
For each event where Pinnacle has odds:
  1. Collect odds from all soft providers for the same market+outcome
  2. Deduplicate by platform (one vote per independent odds engine)
  3. Compute consensus fair odds = mean of deduplicated platform odds
  4. Edge = (pinnacle_odds / consensus_fair - 1) × 100
  5. If edge > 3% AND platforms >= 3 → CONSENSUS VALUE BET on Pinnacle
```

## Platform Deduplication

Providers sharing the same odds engine get one vote (highest odds from the group).

| Platform key | Providers | Note |
|-------------|-----------|------|
| `kambi` | unibet, leovegas, expekt, betmgm, speedybet, x3000, goldenbull, 1x2 | 100% identical |
| `altenar` | betinia, campobet, lodur, quickcasino, swiper, dbet | ~99.7% identical |
| `gecko_betsson` | betsson, nordicbet, betsafe | Shared odds |
| `gecko_bethard` | bethard | Independent (45% differ from betsson) |
| `gecko_spelklubben` | spelklubben | Independent |
| `comeon` | comeon, hajper, lyllo, snabbare | 100% identical |
| `spectate` | 888sport, mrgreen | 100% identical |
| `vbet` | vbet | Standalone |
| `tipwin` | tipwin | Standalone |
| `coolbet` | coolbet | Standalone |
| `10bet` | 10bet | Standalone |
| `marathon` | marathon | International signal |
| `cloudbet` | cloudbet | International signal+play |

**~14 independent platform votes** per outcome.

Uses existing `PLATFORM_MAP` from `constants.py` to deduplicate. For each platform, take the **best (highest) odds** from its members — this is the most conservative consensus (hardest to beat).

## Edge Calculation

```python
# Consensus fair = mean of deduplicated platform odds
consensus_fair = mean(platform_odds.values())

# Edge vs consensus
edge = (pinnacle_odds / consensus_fair - 1) * 100
```

No separate juice adjustment needed. Pinnacle's odds already include their ~2% juice. If the consensus says the true price is 2.44 and Pinnacle offers 2.55, the edge (4.5%) already exceeds the juice embedded in 2.55.

## Quality Filters

- **Minimum 3 platforms** must have odds (avoid thin/unreliable consensus)
- **Mismatch filter**: skip outcome if any platform odds differ by >50% from median (wrong event match)
- **Market types**: 1x2, moneyline, spread, total (same as existing scanner)
- **Pre-match only**: skip live events

## Implementation

### New method on OpportunityScanner

`scan_consensus_value(min_edge_pct=3.0, min_platforms=3) -> list[ValueBet]`

Located in `backend/src/analysis/scanner.py`. Follows the same pattern as existing `scan_value()`.

### Data flow

```
OpportunityScanner.scan_consensus_value()
  ↓
For each event with Pinnacle odds:
  → group_odds(event) → odds_by_outcome
  → _deduplicate_by_platform(odds_by_outcome) → platform_odds
  → consensus_fair = mean(platform_odds)
  → edge = pinnacle / consensus_fair - 1
  → if edge > threshold: yield ValueBet(provider="pinnacle", ...)
```

### Helper function

`_deduplicate_by_platform(odds_list: list[dict]) -> dict[str, float]`

Takes a list of `{provider, odds}` dicts, maps each provider to its platform via `PLATFORM_MAP`, and returns one odds value per platform (highest odds = most conservative).

### Opportunity storage

Stored in existing `opportunities` table with:
- `type = "consensus_value"`
- `outcomes` JSON: `[{provider: "pinnacle", outcome: "home", odds: 2.55, edge_pct: 4.5}, {provider: "consensus", odds: 2.44, is_fair_odds: true, platforms: 11}]`
- Existing fields: `event_id`, `market`, `edge_pct`, `is_active`

### Integration with pipeline

Called from `OpportunityAnalyzer.run()` (in `backend/src/pipeline/analyzer.py`) after the existing `scan_value()` call. Same trigger — runs after each extraction tier completes.

### Frontend

Consensus value bets appear in the existing Value tab on the server dashboard. They show `provider=pinnacle` which is already a valid provider in the UI. No frontend changes needed — the opportunities table renders any opportunity type.

## What This Does NOT Change

- Existing `scan_value()` continues to run (finds soft book value for Swedish-licensed providers)
- Existing `scan_dutch()` continues to run
- Pinnacle devig logic unchanged
- No changes to extraction, matching, or storage pipeline

## Success Criteria

- Consensus scanner produces opportunities with `type=consensus_value`
- Only Pinnacle appears as the betting provider
- Edge calculation is correct: `pinnacle_odds / consensus_mean - 1`
- Platform deduplication uses `PLATFORM_MAP`
- Minimum 3 platforms required
- Mismatch filtering works (>50% deviation from median = skip)
- Opportunities visible in frontend Value tab
