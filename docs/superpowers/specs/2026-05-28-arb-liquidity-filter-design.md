# Arb Liquidity Filter + Bonus Min-Odds Display

**Status:** Design
**Date:** 2026-05-28
**Context:** User got limited on Lodur (Altenar) — a 1000kr stake at 1.15 odds was cut to 11kr. Soft books limit aggressively on niche markets where they have low confidence in their own price. We need a way to filter the arb table to only games liquid enough that limited accounts will still honor full stake. Separately, several soft-book bonuses have a minimum-odds qualifier (`trigger_odds` in `providers.yaml`) that today is invisible to the operator.

## Problem

1. **Limiting on illiquid markets.** Soft books (Lodur, Betinia, CampoBet etc.) calibrate their max-stake-per-account using internal risk models that correlate strongly with Pinnacle's published `maxRiskStake`. High Pinnacle cap → soft books lay big; low Pinnacle cap → soft books cut you to a fraction. Today the operator can't see this signal at all when picking arbs from the cluster table, so they place a 1000kr anchor and discover at the betslip that the book will only accept 11kr.

2. **Hidden bonus thresholds.** Seven providers in `providers.yaml` have a `bonus.trigger_odds` field (e.g. Betinia `1.50`, Leovegas `1.80`). Today the only visible bonus hint in `BalanceCell` is `· deposit 500 kr` — the odds qualifier is invisible. Operators have to remember per-provider thresholds when choosing which leg to place a qualifying bet on.

## Goals

- Expose Pinnacle's per-line `maxRiskStake` in the arb table so the operator can filter / read liquidity before clicking Place.
- Operator-controlled threshold chip (cycle 0 / 2000 / 5000 / 10000 kr), default off, persisted.
- Display each provider's `bonus.trigger_odds` inline with the existing deposit hint when defined.

## Non-Goals

- Auto-block bet placement based on liquidity. The filter only affects arb-table display. `play_loop` still processes whatever opp the operator clicks. (Lowest blast radius — easy to dial up later if it proves valuable.)
- Apply the threshold to the value sub-tab. Value bets are placed at unlimited books (pinnacle / cloudbet / kalshi / polymarket) which don't limit.
- Backfill historical `Odds.max_stake`. Migration is additive nullable; first post-deploy Pinnacle cycle (~2 min cooldown) populates fresh rows.
- Surface non-Pinnacle providers' max-stake. Only Pinnacle's signal is reliable for this use case — soft-book caps reflect the limit the operator already has, not market liquidity.

## Design

### Data flow (Feature 1 — Pinnacle max-stake)

```
Pinnacle markets/straight response
  → market.limits[].amount  (USD, per-market-line)
  → _parse_moneyline/_parse_spread/_parse_total: read limits[0].amount
                                                  into outcome["max_stake_usd"]
  → pipeline/storage.py OddsBatchProcessor: persist max_stake on Odds row
  → /api/opportunities/arb-workflow:
        for each opp,
          pinnacle_max_stake_usd = min(leg.max_stake for leg where provider == pinnacle)
          pinnacle_max_stake_sek = pinnacle_max_stake_usd * SEK_PER_USD
        attached to the opp dict
  → PlayPage arb sub-tab:
        opps filtered by liqThresholdSek (0 default, off)
        per-row badge `liq Nk` next to TTK
```

### Data flow (Feature 2 — bonus min-odds)

```
providers.yaml  bonus.trigger_odds
  → backend/src/services/bankroll_service.py get_full(): response already exposes
        bonus_trigger_amount, bonus_currency
        → also expose bonus_trigger_odds  (None when not configured
          OR when bonus is no longer actionable)
  → ProviderBalanceInfo.bonus_trigger_odds?: number
  → getTrigger() returns { amount, currency, odds? }
  → BalanceCell deposit-hint string:
        "· deposit 500 kr"           (odds undefined)
        "· deposit 500 kr @ 1.50+"   (odds = 1.50)
```

### Backend changes

| File | Change |
|---|---|
| `backend/src/db/models.py` | Add `Odds.max_stake = Column(Float, nullable=True)`. USD. Pinnacle-only; null for everyone else. |
| `backend/src/db/migrations/<new>.py` | Alembic migration: `ALTER TABLE odds ADD COLUMN max_stake double precision NULL`. No backfill. |
| `backend/src/providers/pinnacle.py` | In `_parse_markets` (around line 444), extend `market_meta` to include `max_stake_usd = (market.get("limits") or [{}])[0].get("amount")`. Then in `_parse_moneyline / _parse_spread / _parse_total`, copy `market_meta["max_stake_usd"]` onto each outcome dict. |
| `backend/src/pipeline/storage.py` | `OddsBatchProcessor`: persist `max_stake` from the outcome payload onto the `Odds` row. Upsert path must include the new column so it updates on re-extraction. |
| `backend/src/api/routes/opportunities.py` (`arb_workflow`) | For each opp, compute `pinnacle_max_stake_sek = min(leg.max_stake for pinnacle legs) * SEK_PER_USD`. Surface as `pinnacle_max_stake_sek` on the opp dict. Returns `null` if no Pinnacle leg has a populated value. |
| `backend/src/services/bankroll_service.py` (`get_full` around line 70) | In the `provider_data.append({...})` block, add `"bonus_trigger_odds": cfg.get("trigger_odds") if trigger_actionable else None`. Tied to the same `trigger_actionable` gate as `bonus_trigger_amount` so it disappears after deposit (matching existing pattern). |

### Frontend changes (`frontend/src/pages/PlayPage.tsx`)

| Symbol | Change |
|---|---|
| `ProviderBalanceInfo` type | Add `bonus_trigger_odds?: number`. |
| `getTrigger()` helper | Return `{ amount, currency, odds? }`. |
| `BalanceCell` deposit-hint span | When `odds` present, append ` @ ${odds.toFixed(2)}+`. |
| `load()` provider-balance mapper (~line 754) | Pass through `bonus_trigger_odds: p.bonus_trigger_odds` into the local map. |
| New state `liqThresholdSek` | `useState<number>(() => parseInt(localStorage.getItem('betty:arbLiqThreshold:v1') ?? '0'))`. Persist on change. |
| New chip in arb sub-tab header | Right of the `pos only` / `showing neg` toggle. Label: `liq off` (0) / `liq ≥ 2k` / `liq ≥ 5k` / `liq ≥ 10k`. Click cycles 0 → 2000 → 5000 → 10000 → 0. |
| Arb-row rendering | Filter `opps` by `(o.pinnacle_max_stake_sek ?? Infinity) >= liqThresholdSek`. When threshold = 0, no rows hidden. Pre-backfill rows (max_stake null) treated as "unknown" — they pass when threshold = 0 and hide when threshold > 0, so the filter is strict. |
| Arb-row badge | When `pinnacle_max_stake_sek` is set, render a small badge `liq ${formatK(value)}` next to TTK. Tooltip: "Pinnacle max stake — soft books typically cap stakes proportionally to this". |

### Currency

Pinnacle's `maxRiskStake` is USD. We convert to SEK with the existing `SEK_PER_USD = 10.5` constant on the backend (server-side conversion so the frontend filter compares SEK-to-SEK). No need for per-currency UI.

### Filter semantics

- Threshold 0: no filter applied. All opps visible.
- Threshold > 0: opps with `pinnacle_max_stake_sek == null` are hidden (unknown == not high-liq); opps with value < threshold are hidden.
- Filter applies *after* the existing `showNegativeArbs` filter and within each cluster's top-N. It does not change the per-cluster fetch limit on the backend.

### Error handling

- Pinnacle response missing `limits`: outcome dict has no `max_stake_usd` key → Odds.max_stake stays null. Filter treats as unknown.
- Pinnacle response has empty `limits` array: same as above.
- All Pinnacle legs in an opp have null max_stake: opp's `pinnacle_max_stake_sek` is null; filter treats as unknown.
- Opp has no Pinnacle legs (rare — would only happen for unlimited-unlimited arbs like polymarket+kalshi): null. Same handling.

### Testing

- `backend/tests/providers/test_pinnacle.py`: assert `max_stake_usd` is parsed when `limits[].amount` is present; assert null when `limits` is missing or empty.
- `backend/tests/pipeline/test_storage_scope.py` (or sibling): assert `Odds.max_stake` roundtrips through `OddsBatchProcessor.upsert`.
- Manual: deploy → wait one Pinnacle cycle → arb table renders `liq Nk` badges on Pinnacle-cluster rows → cycle threshold chip, confirm rows hide/show → confirm `· deposit 500 kr @ 1.50+` renders for Betinia / Lodur (none — cumulative without trigger_odds) / Leovegas.

### Rollout

1. PR 1: backend schema + Pinnacle extractor + arb-workflow plumbing + bankroll endpoint. Deploys atomically; column is nullable so no immediate UI dependency.
2. PR 2: frontend filter chip + BalanceCell extension. Ships after PR 1 has had at least one Pinnacle cycle on the server so the data is live.

Could be merged as a single PR with the frontend bits behind the assumption that null max_stake is "unknown" — backfill happens within minutes.

### Risk

- Migration: additive nullable column, no backfill. Safe.
- Filter default off. Operator opts in via the chip.
- `BalanceCell` change is a string append, only fires when `trigger_odds` is defined. No behavior change for the 11 providers without `trigger_odds`.
- No impact on `play_loop`, settlement, value-bet placement, or any auto-place flow.
