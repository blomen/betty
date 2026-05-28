# Open-Position Re-Hedge Scanner — Design Sketch

**Status:** Proposal, not implemented.
**Maps to:** `profitable-strategies-survey.md` §3b + roadmap gap #4b.
**Distinct from:** §3 middling scanner (which starts from `current_market` not from `bets`).

## Problem

Betty fires-and-forgets. Once a bet lands in `bets` with `result='pending'`, no scanner re-evaluates it against the live market until settlement. This misses three EV/variance events:

1. **Post-placement middle.** Line crosses a key number after we bet → free middle window opens.
2. **CLV inversion.** Sharp odds drift hard against us → bet is now −EV; the opposite side at any book where combined return > 0 turns the position into a small guaranteed loss instead of a bigger expected loss.
3. *(Skipped from automation: favourable-drift lock — case 2 in §3b. Acting on it usually throws away live EV. Leave as a manual UI hint, not a scanner action.)*

## Non-goals

- Not a middling scanner over fresh market data — that's #4.
- Not a parlay hedger — Betty doesn't place parlays.
- Not a live in-play hedger — `bets.start_time > now` filter excludes started events.
- Not a cash-out feature — Betty places opposite-side bets at *other* providers, never interacts with a provider's cash-out button.

## Data flow

```
                       every N minutes (cron-tick in pending_loop scheduler)
                                          │
                                          ▼
                  ┌──────────────────────────────────────────┐
                  │  SELECT * FROM bets                      │
                  │  WHERE result='pending'                  │
                  │    AND start_time > now()                │
                  │    AND event_id IS NOT NULL              │   ← skips boost / unknown-event bets
                  │    AND bet_type IN ('value','arb')       │   ← already-hedged arbs need only case-1 check
                  └─────────────────────┬────────────────────┘
                                        │
                                        ▼
                  For each bet B:
                  ┌──────────────────────────────────────────┐
                  │  Load current market state for B.event_id│
                  │    - sharp: latest Pinnacle Odds row     │
                  │    - softs: all Odds for                 │
                  │        (event_id, market, scope)         │
                  │    convert all to ONE base currency      │
                  │    (use bankroll_service.to_sek)         │
                  └─────────────────────┬────────────────────┘
                                        │
                                        ▼
                  ┌──────────────────────────────────────────┐
                  │  Run case classifiers in order:          │
                  │                                          │
                  │  case1_post_placement_middle(B, mkt)     │
                  │    → middle_candidate | None             │
                  │                                          │
                  │  case3_clv_inversion_salvage(B, mkt)     │
                  │    → salvage_candidate | None            │
                  │                                          │
                  │  (case2 deliberately skipped)            │
                  └─────────────────────┬────────────────────┘
                                        │
                                        ▼
                  ┌──────────────────────────────────────────┐
                  │  If a candidate passes its gate:         │
                  │    emit RehedgeOpportunity to            │
                  │    `opportunities` table with            │
                  │    bet_type='rehedge' and surface it     │
                  │    on the Sports tab.                    │
                  │                                          │
                  │  Bettor confirms → arb_runner places     │
                  │  side B at the target provider, links    │
                  │  via arb_group_id to bet A.              │
                  └──────────────────────────────────────────┘
```

**Where it lives:** new module `backend/src/analysis/rehedge_scanner.py`. Triggered from `pipeline/scheduler.py` on a 5-min tick (cheap query — pending count is tens, not thousands). Output rows land in the existing `opportunities` table with a new `bet_type='rehedge'` so the existing Sports-tab UI surfaces them without a new page.

## Decision rules (the gates that prevent churn)

### Case 1 — post-placement middle

Only fires for NFL spreads/totals (`key_numbers.is_nfl(B.event.sport)` AND `B.market` ∈ {spread, total}).

Inputs:
- B.outcome, B.point, B.odds, B.stake (in B.currency)
- current best odds on the *opposite* side across all providers we hold an account at, joined to current `point`

Gate:
1. `key_numbers.annotate(B.event.sport, B.market, B.point)` returned `straddles_key=True` at placement time (we can check by re-annotating — pure function, no I/O).
2. There exists a provider P where the opposite side is now offered with a `point` value such that `(B.point, P.point)` brackets at least one key number `k` ∈ NFL_SPREAD_KEY_NUMBERS / NFL_TOTAL_KEY_NUMBERS. E.g. B = `home -2.5`, P offers `away +3.5` → brackets 3.
3. The wing-loss is ≤ `MAX_WING_LOSS_PCT` (default 2.5%) of total stake-base. Wing loss = the worst-case loss when the result doesn't middle. Computed in base currency:
   ```
   stake_b   = solve_for_stake_b(stake_a_base, odds_a, odds_b)   # equalise non-middle payouts
   wing_loss = (stake_a_base + stake_b) − min(stake_a_base * odds_a, stake_b * odds_b)
   wing_pct  = wing_loss / (stake_a_base + stake_b)
   ```
4. Provider P is not currently flagged limited in `providers` table for this market.

If gate passes: emit candidate with `(bet_id=B.id, hedge_provider=P, hedge_outcome, hedge_point, hedge_odds, recommended_stake=stake_b, expected_middle_pct=key_number_landing_freq[k], wing_loss_pct=wing_pct)`.

### Case 3 — CLV-inversion salvage

Fires for any pending bet with a current Pinnacle quote.

Inputs:
- B.odds, B.stake (in base currency)
- current Pinnacle de-vigged fair odds for `(B.event_id, B.market, B.outcome, B.point, B.scope)` — call this `fair_now`
- best opposite-side odds across providers — call this `opp_odds`

Gate:
1. `(B.odds / fair_now − 1) < INVERSION_THRESHOLD_PCT` (default `−2.0%` — the bet is now 2pp+ −EV).
2. There exists a sum-of-implied combination where `1/B.odds + 1/opp_odds < 1 + MAX_SALVAGE_VIG_PCT` (default `+1.5%` — i.e. we accept paying up to 1.5% of total stake for the salvage; below this we're locking a loss bigger than just holding the original bet).
3. `opp_odds` provider is not limited.
4. Time-to-event > `MIN_TIME_TO_EVENT_MIN` (default 30 min) — too close to game and the salvage will move against us before we can place.

If gate passes: emit candidate with `(bet_id=B.id, hedge_provider, hedge_outcome, hedge_odds, recommended_stake, locked_loss_base, original_expected_loss_base)`. UI shows the comparison: "Holding: expected −$24. Salvaging: locked −$8."

### Sizing math (one helper, reused for both cases)

```python
def equalise_payouts(stake_a_base: float, odds_a: float, odds_b: float) -> float:
    """Stake for side B that makes both winning-outcome payouts equal in base currency.

    Returns stake in base currency. Currency conversion to provider-B native
    currency happens at the placement layer, not here.
    """
    return stake_a_base * odds_a / odds_b
```

For case 1 (middling), we explicitly DO NOT equalise — we accept a small wing loss in exchange for the middle upside. So:

```python
def middle_size(stake_a_base: float, odds_a: float, odds_b: float, target_wing_pct: float) -> float:
    """Stake for side B such that wing_loss / total_stake == target_wing_pct.

    Smaller stake_b → bigger wing loss but bigger middle payout.
    Larger stake_b → smaller wing loss but smaller middle payout.
    Tune via target_wing_pct (e.g. 1.0% accepts a 1% loss on wings to keep
    the middle upside large).
    """
    # Solve: (S_a + S_b − min(S_a*o_a, S_b*o_b)) / (S_a + S_b) = w
    # Two cases depending on which side has the larger payout.
    # ... (standard middling stake calc; ~10 lines)
```

These belong in `local/mirror/arb_math.py` (next to the existing arb sizing).

## What gets written where

| File | New code |
|---|---|
| `backend/src/analysis/rehedge_scanner.py` | New. `scan_open_positions(db) → list[RehedgeOpportunity]`. ~150 LOC. |
| `local/mirror/arb_math.py` | Add `equalise_payouts`, `middle_size` helpers. ~30 LOC. |
| `backend/src/pipeline/scheduler.py` | Add 5-min tick that calls `rehedge_scanner.scan_open_positions` and upserts to `opportunities`. ~20 LOC. |
| `backend/src/db/models.py` | No schema change — reuses `opportunities` with `bet_type='rehedge'` and a new `metadata` JSON column (already present per recent migrations — verify). |
| `frontend/src/pages/PlayPage.tsx` | New sub-tab or section under Sports → Arbitrage showing rehedge candidates with the side-by-side comparison block. ~80 LOC. |
| `local/mirror/play_loop.py` | One branch: when a rehedge opp is confirmed, route through `arb_runner.place_single_leg` with `arb_group_id` linking back to the original bet. ~10 LOC. |

## Risks / open questions

1. **Provider-limit awareness.** We don't currently maintain a clean "this provider is limited on this market" table. The case-3 gate needs it, otherwise we'll keep emitting opps that can't be placed. Cheapest fix: extend `limit_service` to record "last refusal" timestamps per `(provider, market)`.
2. **Currency drift between detection and placement.** A 0.5% FX move during the human confirmation window can flip a salvage from −0.5% locked loss to −1.5%. Mitigation: snapshot FX rate at scan time, re-check at placement, abort if drift > 0.3%.
3. **Same-bet re-emission.** If the scanner runs every 5 min and a candidate isn't placed for an hour, we don't want 12 duplicate rows in `opportunities`. Dedup key: `(bet_id, hedge_provider, hedge_market, hedge_point)` with upsert.
4. **Already-hedged arbs.** A `bet_type='arb'` row already has its hedge leg. Case 1 (post-placement middle) might still fire if the line moved through a key after the arb was struck. Case 3 should NEVER fire on arb bets (the combined position is already delta-neutral). Gate case 3 with `B.bet_type != 'arb'`.
5. **NFL margin-landing freq numbers.** Case 1's `expected_middle_pct` value needs a real source. Initial values from public Wong/Boyd charts; revisit after the first ~50 emitted candidates have settled.

## Sequencing

1. Ship `arb_math` helpers + unit tests on the sizing math (1 day).
2. Ship `rehedge_scanner` + case-1-only emit (2 days). Surface read-only on the UI, no placement, just observe what would have been opportunities.
3. After 1-2 weeks of read-only observation, validate the rate / quality matches expectations, then wire placement (1 day).
4. Ship case-3 (CLV-inversion salvage) only after #3 — case 3 has higher false-positive risk because Pinnacle's fair odds bounce around and a transient inversion can revert before placement.

Total realistic timeline: 1 week if everything goes smoothly, 2 weeks accounting for the read-only observation period in step 2.
