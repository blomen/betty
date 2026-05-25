# Scanner Trust Gates — Design

**Date:** 2026-05-26
**Status:** Draft, awaiting user review
**Trigger:** Audit on 2026-05-26 (one day after the period-scope fix shipped) found three additional bug classes producing phantom opportunities. The UI currently surfaces 427 active spread "value bets" at edge ≥15%, multiple home/away inverted spreads, and 64 cross-currency "arbs" that don't account for USDC/SEK/USD/GBP differences. None of these can be trusted for high-stake play.

## Problem

The scanner makes pairwise comparisons (provider odds vs Pinnacle fair odds, leg A vs leg B for arbs) without validating that the compared rows are semantically aligned. Yesterday's period-scope fix added one alignment dimension (scope). Today's audit revealed three more independent failure modes, each producing the same observable symptom — phantom edges large enough to be obviously wrong but with no current code path to refuse them:

| Bug class | Current state | Live exposure today |
|---|---|---|
| **Cross-currency arb math** | Scanner treats `USDC × decimal_odds` and `SEK × decimal_odds` as comparable units. Provider currencies declared in `providers.yaml` but never plumbed to the scanner. | 64 active "arbs" pair non-SEK with SEK providers |
| **Home/away inversion not caught for non-favorite matchups** | `detect_and_fix_inversion` exists ([backend/src/pipeline/storage.py:138](backend/src/pipeline/storage.py#L138)) but only triggers when Pinnacle shows a clear favorite (`odds ratio > 1.5`). Near-coinflip matchups slip through. | SSG Landers v Samsung Lions (KBO): Pinnacle ratio 1.06, books disagree on favored side by 30pp, scanner emits 45% phantom spread edges |
| **Spread Asian-handicap convention mismatch** | Different providers store quarter handicaps at different boundary values. Pinnacle uses midpoint (`+0.25` = the +0/+0.5 quarter). Kambi/Altenar/etc. store some lines at the half value. Scanner's `spread_{point}` bucketing conflates them. | 427 active "value bets" at edge ≥15% on spreads — almost all phantom |
| **No upper-bound edge sanity gate in batch builder** | `batch_builder.build()` filters by per-provider minimum edge but has no maximum. A 45% phantom edge passes Kelly sizing untouched and is surfaced as a large recommended stake. | Top recommended plays include 45% "edges" today |
| **Stale opportunity cleanup leak** | Opportunities are inserted on each scan and only deleted when re-scanned. Off-season events (NFL) carry stale opps in `is_active=true` state for weeks. | NFL "arbs" with start_time 8 days out still active in the table |

Each bug independently produces opportunities the user could place real money against — none of them detectable without per-bet manual audit. The user's stated trust goal ("I want to trust we coded everything right") requires structural refusal, not patches.

## Approach

**Hybrid: tag where the bug is data, gate where the bug is comparison logic.** Five focused fixes, each in the layer that matches its bug class:

| Fix | Where | Mechanism |
|---|---|---|
| 1. Currency-aware arb math | scanner (arb detection) | Lookup provider currency from config, convert leg payouts to SEK base before margin computation; refuse arb if conversion rate unavailable |
| 2. Enhanced inversion detector | storage (per-event, at extract time) | Lower threshold + add 1x2 implied-prob agreement signal; mark event as "side-inverted" if disagreement persists after swap attempt; scanner skips inverted events |
| 3. Spread implied-prob disagreement gate | scanner (per-bucket, post-grouping) | For each spread bucket, devig both soft and sharp at the same outcome; if disagreement > 30pp (configurable), refuse to emit value bets for that bucket |
| 4. Upper-bound edge sanity gate | batch_builder (final safety net) | Reject any value bet with edge > 10% and any arb with profit > 5% from the surfaced batch; log dropped opps for visibility |
| 5. Stale opportunity cleanup | analyzer / cron | Delete opportunities for events past start_time + 1h, or with odds_age > MAX_ODDS_AGE_HOURS |

Each fix is independently testable and independently shippable. Fix 4 is load-bearing for trust — it ensures the user can never see a phantom edge even if all upstream layers fail. Fixes 1–3 are the proper structural answers; fix 4 is the safety net.

## Fix 1: Currency-aware arb math

### Problem details

Arb math: worst-case payout = `min_i(stake_i × odds_i)`. Total stake = `sum_i(stake_i)`. The arb is "guaranteed" iff `worst_payout > total_stake`. **Both quantities must be in the same currency** for this inequality to mean anything.

Today the scanner computes both in raw decimal-odds × raw stake, ignoring that:
- polymarket is USDC
- cloudbet is USDC (corrected — CLAUDE.md says SEK, but `providers.yaml:811` confirms USDC and the live `bets.currency` column agrees)
- kalshi is USD
- smarkets is GBP
- everything else (SEK-funded user accounts on Swedish/EU softs and Pinnacle) is SEK

A Pinnacle-vs-cloudbet "arb" pretends 10 USDC ≈ 10 SEK, off by ~10x.

### Mechanism

In the arb detection path ([backend/src/analysis/scanner.py](backend/src/analysis/scanner.py) — currently `_find_arb_in_market` per yesterday's audit):

1. For each pair of opposing-leg providers, lookup currency via `get_provider_currency(provider_id)` (already exists in `backend/src/config/__init__.py`).
2. If currencies differ, fetch the SEK conversion rate via `money.convert(amount=1.0, from_currency=X, to_currency='SEK')` from the existing `money/` package.
3. If `convert` raises or returns a stale/unavailable rate (per existing `money` package error handling), **refuse to emit this arb** and log at INFO.
4. Convert each leg's hypothetical stake and payout to SEK before computing `worst_payout > total_stake`.
5. Surface the result in the canonical SEK base on the `opportunities` row so downstream sizing is consistent.

No new schema. Reuses existing `money/` package and `providers.yaml` currency declarations.

### Test plan

- Unit test: synthetic two-leg arb (cloudbet USDC 1.75 + pinnacle SEK 2.56) with conversion rate 1 USDC = 10.5 SEK. Assert refused (margin negative after conversion).
- Unit test: same-currency arb (unibet SEK 1.95 + pinnacle SEK 2.10) — assert emitted with correct margin.
- Unit test: missing conversion rate → arb refused, log message emitted.

## Fix 2: Enhanced home/away inversion detector

### Problem details

`detect_and_fix_inversion` ([backend/src/pipeline/storage.py:138](backend/src/pipeline/storage.py#L138)) checks if the incoming provider's home/away odds are inverted vs Pinnacle. It triggers a swap only when Pinnacle shows a clear favorite (`odds_ratio > 1.5`). Near-coinflip matchups (1.06 in the SSG case) never trigger, so an inversion goes undetected.

### Mechanism

Two-layered detection:

1. **Lower the threshold** from `>1.5` to `>1.1` (any clear directional disagreement). A 1.1x favorite is still a real signal — Pinnacle thinks one side is ~52.5%+, the soft thinking 47.5%- is a clean inversion candidate.

2. **Add a second signal: 1x2 devig agreement.** Compute the soft book's devigged P(home) and Pinnacle's devigged P(home). If they disagree by > 25 percentage points, treat as inverted candidate even if the raw odds ratio doesn't trigger threshold #1.

3. **Verification step:** after attempting a swap, recheck. If post-swap disagreement is STILL > 15pp, the event is genuinely matched-wrong (probably a fuzzy-match false positive). Drop the soft odds for this event/provider entirely and log at WARNING. The scanner doesn't see them.

4. **Storage flag:** set `Event.home_away_validated` (new boolean, default false) to true only after successful swap or unambiguous no-swap-needed. Scanner skips opportunities where this is false. This prevents the scanner from emitting against unresolved events.

### Test plan

- Unit test: synthetic event with Pinnacle home @ 2.0 / away @ 1.85 (home slight favorite) and soft home @ 1.85 / away @ 2.0 (inverted). Assert swap happens.
- Unit test: same with Pinnacle ratio 1.06 (near-coinflip) and soft assigning different team as favorite. Assert swap.
- Unit test: synthetic event with no inversion (both books agree). Assert no swap, resolved=true.
- Unit test: event where post-swap still has 30pp disagreement (genuine match error). Assert soft odds dropped + WARNING logged.

## Fix 3: Spread implied-prob disagreement gate

### Problem details

The 0.5-line bug isn't an Asian-handicap convention bug specifically — it's the **symptom** of one. Unibet's `home @ +0.5 @ 1.91` implies 52% probability for "home doesn't lose"; Pinnacle's `home @ +0.5 @ 1.24` (devigged 1.31) implies 76% for the same logical bet. A 24pp disagreement on the same canonical outcome means the bet has been bucketed incorrectly — regardless of root cause (quarter handicap convention, DNB line, or anything else).

Rather than auditing each provider's convention, use the symptom as the gate.

### Mechanism

In `OpportunityScanner.find_value_in_market` or `_find_arb_in_market` ([backend/src/analysis/scanner.py](backend/src/analysis/scanner.py)), after spread bucketing but before edge emission:

1. For each outcome in the bucket, compute the implied probability per provider:
   - `p_implied = 1 / odds`
   - For 2-way devig: `p_fair = p_implied / (p_implied_home + p_implied_away)`
2. Compare each soft book's `p_fair` for the outcome against Pinnacle's `p_fair` for the same outcome.
3. If `|p_fair_soft − p_fair_sharp| > SPREAD_DISAGREEMENT_MAX_PP` (default 0.30 = 30 percentage points), refuse to emit value bets for that soft book in that bucket. Continue with the remaining soft books.
4. Log dropped buckets at DEBUG with counts. Surface aggregate count in `/health/extraction` as `spread_disagreement_drops` (new sibling to yesterday's `unscannable_markets`).

Threshold of 30pp is chosen because:
- Real edge ≤ 10% on decimal odds corresponds to ≤ ~5pp probability disagreement.
- Genuine line-move noise rarely exceeds 10pp.
- 30pp is "you're not even pricing the same bet" territory.

### Test plan

- Unit test: All Boys v Los Andes fixture (Unibet home@+0.5 @ 1.91 vs Pinnacle devig 1.31). Assert Unibet dropped from bucket, no value bet emitted.
- Unit test: legitimate small disagreement (Pinnacle home@-0.5 devig 2.10, Unibet home@-0.5 @ 2.20 = 5pp difference). Assert value bet IS emitted.
- Unit test: missing Pinnacle in bucket → no disagreement check possible, skip silently (no value bet anyway).

## Fix 4: Upper-bound edge sanity gate in batch_builder

### Problem details

`BatchBuilder.build()` in [backend/src/services/batch_builder.py](backend/src/services/batch_builder.py) filters by per-provider MIN edge but has no MAX. A 45% phantom edge gets sized large by Kelly and surfaced as a top recommended play. Real arbs are low single digits per CLAUDE.md ("Real arbs are low single digits"). Real value bets ≥10% are extreme outliers — virtually always a bug rather than a real edge.

### Mechanism

In `batch_builder.py`, add module constants:

```python
# Upper-bound sanity gates. Anything above is almost certainly a bug
# (currency mismatch, spread convention mismatch, inversion, scope mismatch),
# not a real edge. Refuse to surface — log instead so we can monitor.
MAX_BATCH_VALUE_EDGE_PCT = 10.0
MAX_BATCH_ARB_PROFIT_PCT = 5.0
```

In `_build_value_bet` (where the per-provider min-edge gate is): add a parallel max-edge gate. Return `None` (skip) if `opp.edge_pct > MAX_BATCH_VALUE_EDGE_PCT`. Same for arbs against `profit_pct`.

Log dropped opps at WARNING with `[suspect_phantom]` prefix so they show up in `docker logs`. Counter surfaced in `/health/extraction` as `phantom_suspect_drops`.

This is a backstop, not a substitute for fixes 1–3. With all three structural fixes in place, this gate should drop zero or near-zero opportunities per cycle. A non-zero count is the signal that a new bug class has emerged.

### Test plan

- Unit test: opportunity with edge_pct = 12 → returns None from `_build_value_bet`.
- Unit test: opportunity with edge_pct = 8 → returns BatchBet normally.
- Unit test: arb opportunity with profit_pct = 6 → returns None.
- Unit test: arb opportunity with profit_pct = 3 → returns BatchBet normally.

## Fix 5: Stale opportunity cleanup

### Problem details

The scanner inserts opportunities on each cycle and only deletes them when the next scan re-evaluates the event. Off-season events (NFL during summer) don't get scanned because no fresh odds arrive, so stale `is_active=true` opportunities linger for weeks. Today's NFL Steelers v Falcons "arb" with start_time 8 days out is one example.

### Mechanism

Two cheap queries added to a periodic cleanup task (cron or end-of-scan hook):

```sql
-- Hard expire: any opp for an event that already started + 1h grace
UPDATE opportunities SET is_active = false
WHERE is_active = true
  AND event_id IN (SELECT id FROM events WHERE start_time < NOW() - INTERVAL '1 hour');

-- Soft expire: any opp where the underlying provider odds are older than 4h
UPDATE opportunities SET is_active = false
WHERE is_active = true
  AND id IN (
    SELECT op.id FROM opportunities op
    JOIN odds o ON o.event_id = op.event_id AND o.provider_id = op.provider1_id
    WHERE op.is_active = true
      AND o.updated_at < NOW() - INTERVAL '4 hours'
  );
```

Run on every scan cycle (cheap — indexed columns, ~50ms). Surface `expired_opps_count` in `/health/extraction`.

### Test plan

- Integration test: seed opp for event with start_time = NOW() - 2h. Run cleanup. Assert is_active = false.
- Integration test: seed opp linked to odds with updated_at = NOW() - 6h. Run cleanup. Assert is_active = false.
- Integration test: seed opp linked to fresh odds and future event. Assert is_active = true (untouched).

## Out of scope

- **Per-provider Asian-handicap convention audit.** Fix 3 uses the symptom (probability disagreement) as the gate, which is enough to refuse phantom edges. Properly normalizing each provider's convention at extract time is a future improvement that would restore visibility into spread opps that fix 3 drops. Not blocking trust today.
- **Live in-play opps.** Already filtered.
- **Polymarket / Kalshi liquidity-driven longshot pricing.** Existing `PREDICTION_MARKETS` exemption in `_has_odds_discrepancy` continues. Fix 1 (currency) covers their main risk; their longshot noise is a separate calibration concern.
- **Provider currency tag on the `odds` table.** Considered but rejected — currency is per-provider, not per-row. Lookup at scan time via existing `get_provider_currency` is sufficient.
- **Frontend-side caps.** Frontend trusts the API. Fix 4 in batch_builder ensures the API never surfaces a phantom.

## Files affected (estimated)

- `backend/src/analysis/scanner.py` — fixes 1, 3
- `backend/src/pipeline/storage.py` — fix 2 (`detect_and_fix_inversion`)
- `backend/src/db/models.py` — fix 2 (new `Event.home_away_validated` column + migration)
- `backend/src/services/batch_builder.py` — fix 4
- `backend/src/analysis/analyzer.py` or scheduler hook — fix 5 (cleanup query)
- `backend/src/api/__init__.py` — extend `/health/extraction` with new counters
- `backend/tests/analysis/test_currency_arb.py` — new (fix 1)
- `backend/tests/pipeline/test_inversion_detection.py` — extend (fix 2)
- `backend/tests/analysis/test_spread_disagreement.py` — new (fix 3)
- `backend/tests/services/test_batch_phantom_gate.py` — new (fix 4)
- `backend/tests/test_opportunity_cleanup.py` — new (fix 5)

## Rollout

1. Single deploy via `server-deploy.sh rebuild backend` after merge
2. Migration runs at startup (adds `Event.home_away_validated` column)
3. Next extraction cycle runs enhanced inversion detection; existing events left at default false until next scan
4. Verification queries post-deploy:
   - `SELECT COUNT(*) FROM opportunities WHERE is_active=true AND edge_pct > 10` → expect dramatic decrease
   - `SELECT COUNT(*) FROM opportunities WHERE is_active=true AND profit_pct > 5` → expect 0 within minutes
   - `/health/extraction` returns all 4 new counters: `phantom_suspect_drops`, `spread_disagreement_drops`, `expired_opps_count`, `currency_mismatch_arb_drops`
5. Smoke check: re-audit top 10 opps on UI; confirm no obvious phantoms remain
