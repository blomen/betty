# Polymarket True Edge Tracking

## Problem

Polymarket bet P&L and CLV metrics are unreliable:

1. **CLV measures the wrong thing** — `clv_pct` compares Polymarket odds to Pinnacle closing odds. This measures the structural spread between two different markets, not whether you got a good entry on Polymarket. A +17% average CLV should produce massive profits, but instead we busted — suggesting the metric is misleading.

2. **Bet tracking has data integrity issues** — Pinnacle has 4 ghost bets (test data, never placed). Known trigger bets (unibet 1000kr id=70, dbet 500kr id=65) aren't flagged as bonus, inflating P&L by ~2,600 kr. Polymarket shows +$24 in bet math but actual balance went -$110 (bust).

3. **P&L reporting doesn't exclude triggers/freebets** — Every analysis session requires manual SQL filtering to get real numbers.

## Solution

### 1. Dual CLV: Provider Closing Odds

Add two columns to `Bet`:

```
provider_closing_odds  FLOAT  -- Same-provider odds at event start
provider_clv_pct       FLOAT  -- (bet.odds / provider_closing_odds - 1) * 100
```

Existing `closing_odds` / `clv_pct` remain as Pinnacle cross-market reference.

**Snapshot logic** (`BetService.snapshot_closing_odds()`): For Polymarket bets, also query the `odds` table for `provider_id='polymarket'` matching the bet's event/market/outcome. Store in `provider_closing_odds` and compute `provider_clv_pct`.

Query for provider closing odds:

```python
# After existing Pinnacle snapshot, for Polymarket bets:
if bet.provider_id == "polymarket" and bet.provider_closing_odds is None:
    provider_query = db.query(Odds).filter(
        Odds.event_id == bet.event_id,
        Odds.provider_id == "polymarket",
        Odds.market == bet.market,
        Odds.outcome == bet.outcome,
    )
    if bet.market in ("spread", "total") and bet.point is not None:
        provider_query = provider_query.filter(Odds.point == bet.point)
    poly_odds = provider_query.first()
    if poly_odds and poly_odds.odds > 1.0:
        bet.provider_closing_odds = poly_odds.odds
        bet.provider_clv_pct = round((bet.odds / poly_odds.odds - 1) * 100, 2)
```

**Precedence:** If `snapshot_closing_odds()` already set `provider_closing_odds`, `_calculate_clv()` at settlement skips it (same pattern as existing `closing_odds` check on line 315).

**Known limitation:** `provider_closing_odds` represents "last known Polymarket price before event start" — whatever was in the DB from the most recent extraction, typically within 15-30 minutes of event start. This is not the exact closing price but is the best we can capture without a dedicated pre-kickoff extraction.

For non-Polymarket bets, `provider_closing_odds` stays null (soft book closing odds aren't meaningful — they limit sharp bettors, so the closing line isn't a fair benchmark).

**What this enables:**
- **Polymarket CLV** (`provider_clv_pct`): Did you buy at a good price vs where Poly closed? This is true CLV.
- **Edge vs Pinnacle** (`clv_pct`): Is the cross-market signal real? Still useful but now properly labeled.

### 2. Data Cleanup

One-time operations (identified via manual DB audit on 2026-03-18):

- **Delete pinnacle ghost bets** (ids 6, 15, 24, 64) — test data, never placed. Verification query:
  ```sql
  SELECT id, provider_id, event_id, stake, result FROM bets WHERE id IN (6, 15, 24, 64);
  -- All should be provider_id='pinnacle'
  ```
- **Flag trigger bets** — Set `is_bonus=True, bonus_type='trigger'` on ids 70 (unibet 1000kr) and 65 (dbet 500kr)
- **Fix polymarket currency** — All polymarket bets should be `currency='USDC'` (some are incorrectly 'USD')

The -$110 vs +$24 discrepancy likely comes from:
- Void payouts returning less than stake (Polymarket fees)
- Missing bets not recorded in DB
- Fee slippage on USDC trades

This needs manual investigation rather than automated correction.

### 3. P&L Reporting: Exclude Bonus by Default

**Backend:** Add `exclude_bonus: bool = False` query param to `GET /api/bets`. When true, filter out `is_bonus=True` rows. Default false to avoid breaking existing consumers — frontend opts in with `?exclude_bonus=true`.

Also add `exclude_bonus` to `/api/polymarket/mybets` aggregate stats so those numbers are consistent.

**Frontend:** P&L aggregation passes `exclude_bonus=true`. Add a toggle "Include bonus/trigger bets" for when you want the full picture.

**API response fields added:**
- `provider_closing_odds` (Float | null)
- `provider_clv_pct` (Float | null)

## Files Changed

| File | Change |
|------|--------|
| `db/models.py` | Add `provider_closing_odds`, `provider_clv_pct` columns to `Bet` |
| `services/bet_service.py` | `snapshot_closing_odds()` also captures Polymarket provider odds |
| `services/bet_service.py` | `_calculate_clv()` also computes `provider_clv_pct` at settlement (if not already set) |
| `api/routes/bets.py` | Return `provider_closing_odds`, `provider_clv_pct`; add `exclude_bonus` param |
| `api/routes/polymarket.py` | Return `provider_clv_pct` in mybets; add `exclude_bonus` to stats |
| DB migration | `ALTER TABLE bets ADD COLUMN provider_closing_odds FLOAT` + `provider_clv_pct FLOAT` |
| One-time cleanup | Delete pinnacle ghosts (with verification), flag triggers, fix polymarket currency |

## Assumptions

- Cleanup at `_do_cleanup()` preserves odds for events that have bets, so Polymarket odds rows survive long enough for the snapshot. This is true per current logic (scheduler.py line 862-870) but future cleanup changes must not break this.
- Polymarket extraction cadence (sharp tier, ~15s) means odds are refreshed frequently enough that the "last known price" is a reasonable proxy for closing price.

## Out of Scope

- Backfilling `provider_closing_odds` for existing bets (Polymarket odds already cleaned from DB)
- Fixing the -$110 discrepancy (needs manual Polymarket transaction history comparison)
- Changing Kelly/stake sizing for Polymarket (separate concern)
- Frontend UI changes beyond adding the new fields to the bets response
