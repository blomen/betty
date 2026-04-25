# PlayPage Arb Section — Visibility & Signal Display

**Date:** 2026-04-25
**Scope:** `arnold/frontend/src/pages/PlayPage.tsx` — Arbitrage sub-tab rendering rules.

## Strategy Context

The bet flow is: place on the soft book, hedge on an unlimited book (Pinnacle / Polymarket / Cloudbet / Kalshi), repeat until the soft book balance bleeds out via variance landing on the hedge side. Bonus money is irrelevant to this loop — whether a soft-book balance is cash or restricted bonus money, we bet it down the same way. **Bonus state is not tracked or displayed in this UI.**

## Problem

The Arbitrage sub-tab currently renders every canonical cluster (`SOFT_CLUSTER_MEMBERS`) and every standalone (`SOFT_STANDALONES`) regardless of whether the user has any actionable signal. Result: bare cluster headers like `SPECTATE`, `COMEON_GROUP`, `10BET`, `BETHARD`, `COOLBET` clutter the page when fully drained.

## Goal

Render only what the user can act on: clusters with a member that has cash balance or pending bets, or clusters with a "massive arb" worth a fresh deposit.

## Constants

Replace at top of `PlayPage.tsx`:

```ts
// Provider is "drained" when balance falls below this threshold (SEK).
// Below this, no meaningful bet can be placed after odds rounding / min stakes.
const DRAIN_THRESHOLD_SEK = 20            // was 1

// Minimum guaranteed profit % an arb must show for a fully-drained cluster
// to be surfaced as a deposit hint. Tuned to clear realistic execution costs:
// ~0.5–1.5% on Pinnacle-hedged arbs, ~1.5–4% on Kalshi/Polymarket-hedged
// arbs (slippage + spread + per-contract fees).
const DEPOSIT_HINT_MIN_PROFIT_PCT = 2.5
```

## Per-provider state classification

```ts
const isFunded = (pid: string) =>
  (providerBalances[pid] ?? 0) >= DRAIN_THRESHOLD_SEK ||
  (pendingByProvider[pid]?.length ?? 0) > 0
```

Bonus state is not consulted. The previous `nonFunded` / "done" / bonus-pill logic is removed.

## Cluster visibility rule

For each cluster in `softByCluster`:

1. Partition members into `funded` and `unfunded` using `isFunded`.
2. If `funded.length > 0` → **show cluster (normal mode)**.
3. Else compute `qualifyingOpps = oppsByCluster[cluster].filter(o => o.guaranteed_profit_pct >= DEPOSIT_HINT_MIN_PROFIT_PCT)`.
   - If `qualifyingOpps.length > 0` → **show cluster (deposit-hint mode)**.
   - Else → **hide cluster entirely**.

## Per-state rendering

| State | Render |
|---|---|
| Funded (balance ≥ 20 OR pending > 0) | Provider card with green balance chip + pending list (if any) + arb table — unchanged from today |
| Unfunded | Omitted from the cluster body |

### Arb table content

- **Normal mode:** unchanged — top 10 by edge, negative edges allowed if no positives exist (today's behavior).
- **Deposit-hint mode:** top 10 by edge from `qualifyingOpps` (already filtered to ≥ 2.5%). Cluster header replaces provider cards with a small "Deposit to play — qualifying arb opportunities below" note. No per-provider funded cards rendered.

## Cluster header changes

- Existing label + opp count + "siblings share odds" tail line preserved for normal mode.
- The amber italic `nonFunded` pill row is **removed entirely** (was the bonus indicator).
- Deposit-hint mode tail text: `deposit to play · {n} qualifying arbs ≥ 2.5%` instead of today's `no funded siblings`.

## Bonus state removal

These are deleted (no replacement, not tracked any more in this UI):

- `providerBonuses` state, `setProviderBonuses` setter ([PlayPage.tsx:79](arnold/frontend/src/pages/PlayPage.tsx#L79))
- The `for (const entry of result.balance_status ?? [])` extraction loop ([PlayPage.tsx:158-164](arnold/frontend/src/pages/PlayPage.tsx#L158-L164))
- `Object.keys(providerBonuses)` in the `allKnownPids` set ([PlayPage.tsx:793](arnold/frontend/src/pages/PlayPage.tsx#L793))
- `(providerBonuses[pid] ?? 0)` reference in the old `isDone` ([PlayPage.tsx:822](arnold/frontend/src/pages/PlayPage.tsx#L822))
- `const bonus = providerBonuses[pid] ?? 0` line and any consumer ([PlayPage.tsx:875](arnold/frontend/src/pages/PlayPage.tsx#L875))
- The `nonFunded.map(pid => ...)` pill rendering ([PlayPage.tsx:858-866](arnold/frontend/src/pages/PlayPage.tsx#L858-L866))

The backend's `balance_status[i].bonus_amount` field is left in place — other consumers (Bankroll page, capital plan) may still use it. We just stop reading it here.

## Files touched

- `arnold/frontend/src/pages/PlayPage.tsx` — only file in scope. Constants, classifier, cluster filter + deposit-hint branch, removal of bonus state.

## Out of scope

- Stake-cap feasibility check on deposit-hint arbs.
- Hedge-leg funding feasibility (counter providers having enough to cover).
- Per-hedge-venue profit tiers (e.g., 1.5% for Pinnacle, 3% for Kalshi).
- Tracking provider unclaimed bonus offers (not modeled by backend; future work if ever needed).

## Success criteria

1. Empty cluster headers (no funded members AND no qualifying arb) no longer render.
2. Soft cluster pills (the amber bonus indicators) no longer render anywhere.
3. A drained cluster with a ≥ 2.5% arb opp still appears, marked as a deposit hint with no provider cards but with the qualifying arb rows.
4. Existing funded-cluster behavior (top 10 by edge, negative-edge fallback, pending list, arb table layout, provider start button, settlement UI) unchanged.
5. `providerBonuses` state and all dependent code paths fully removed from `PlayPage.tsx`.
