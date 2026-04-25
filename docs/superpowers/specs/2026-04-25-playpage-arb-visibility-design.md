# PlayPage Arb Section — Visibility & Signal Display

**Date:** 2026-04-25
**Scope:** `arnold/frontend/src/pages/PlayPage.tsx` — Arbitrage sub-tab rendering rules.

## Problem

The Arbitrage sub-tab currently shows every canonical cluster (`SOFT_CLUSTER_MEMBERS`) and every standalone (`SOFT_STANDALONES`) regardless of whether the user has any actionable signal on those providers. Result: bare cluster headers like `SPECTATE`, `COMEON_GROUP`, `10BET`, `BETHARD`, `COOLBET` clutter the page when fully drained.

Additionally, multi-signal providers (e.g. funded with both balance and unclaimed bonus) only surface one signal — the bonus is invisible once a provider is funded.

## Goal

Render only what the user can act on, and clearly indicate which signals (balance, pending, unclaimed bonus) are present per provider. For drained clusters, surface only the rare "massive arb" deposit hints worth refilling for.

## Constants

Add/replace at top of `PlayPage.tsx`:

```ts
// Provider is "drained" when balance falls below this threshold (SEK).
// Below this, no meaningful bet can be placed after odds rounding / min stakes.
const DRAIN_THRESHOLD_SEK = 20            // was 1

// Minimum guaranteed profit % an arb must show for a fully-drained cluster
// to be surfaced as a deposit hint. Tuned to clear realistic execution costs:
// ~0.5–1.5% on Pinnacle-hedged arbs, ~1.5–4% on Kalshi/Polymarket-hedged
// arbs (slippage + spread + per-contract fees). 2.5% gives margin for the
// former and is borderline-acceptable for the latter.
const DEPOSIT_HINT_MIN_PROFIT_PCT = 2.5
```

## Per-provider state classification

```ts
const isFunded = (pid: string) =>
  (providerBalances[pid] ?? 0) >= DRAIN_THRESHOLD_SEK ||
  (pendingByProvider[pid]?.length ?? 0) > 0

// "Bonus" here means UNCLAIMED bonus money — a deposit-match offer or free
// bet token the user has not yet activated. Once claimed, it is merged into
// the cash balance and no longer appears in providerBonuses.
const hasUnclaimedBonus = (pid: string) =>
  (providerBonuses[pid] ?? 0) >= DRAIN_THRESHOLD_SEK

const isDone = (pid: string) => !isFunded(pid) && !hasUnclaimedBonus(pid)
```

The bonus threshold mirrors `DRAIN_THRESHOLD_SEK` so a residual <20 kr unclaimed-bonus dust does not trigger a header pill.

## Cluster visibility rule

For each cluster in `softByCluster`:

1. Partition members into `funded`, `bonusOnly`, `done` using the classifiers above.
2. If `funded.length > 0` OR `bonusOnly.length > 0` → **show cluster (normal mode)**.
3. Else (all members done):
   - Compute `qualifyingOpps = oppsByCluster[cluster].filter(o => o.guaranteed_profit_pct >= DEPOSIT_HINT_MIN_PROFIT_PCT)`.
   - If `qualifyingOpps.length > 0` → **show cluster (deposit-hint mode)**.
   - Else → **hide cluster entirely**.

## Per-state rendering

| State | Render |
|---|---|
| Funded, no unclaimed bonus | Provider card with green balance chip + arb table (existing) |
| Funded, with unclaimed bonus | Provider card with green balance chip + amber `+B {amount} unclaimed` chip + arb table |
| Bonus-only (unclaimed) | Header pill: `{PID} +B {amount} unclaimed` (amber italic) |
| Pending-only (no balance, has pending bets) | Provider card (same as funded — folds into `isFunded` because pending count > 0). Pending list UI is required for settlement. |
| Done | Omitted from the cluster body |

### Arb table content

- **Normal mode:** unchanged — top 10 by edge, negative edges allowed if no positives exist (today's behavior).
- **Deposit-hint mode:** top 10 by edge from `qualifyingOpps` (already filtered to ≥ 2.5%). Cluster header replaces provider cards with a small "Deposit to play — qualifying arb opportunities below" note. No per-provider funded cards rendered.

## Cluster header changes

- Existing label + opp count + "siblings share odds" tail line preserved.
- `nonFunded` pills (today: bare `{PID}` text) updated to show the unclaimed bonus amount: `{PID} +B {amount} unclaimed`.
- For deposit-hint mode clusters, the right-side tail text becomes `deposit to play · {n} qualifying arbs ≥ {threshold}%` instead of `no funded siblings`.

## Backend dependency check

The spec assumes `providerBonuses[pid]` contains **unclaimed bonus only** — claimed bonus must already be merged into `providerBalances[pid]`. Before implementing the UI:

1. Trace where `providerBonuses` is populated (likely a `/api/bankroll/*` endpoint reading from `provider_profiles` or a dedicated bonus table).
2. Verify the field's semantics. If claimed bonus is present in this map, the data path needs a fix (filter to unclaimed only) before the UI surfaces a misleading "unclaimed" label.

If the backend currently does not distinguish claimed vs unclaimed, surface that as a blocker and resolve before merging the UI changes.

## Files touched

- `arnold/frontend/src/pages/PlayPage.tsx`
  - Constants block (line ~6–14)
  - `isFunded` / new `hasUnclaimedBonus` / new `isDone` (replace existing inline logic at line ~817–823)
  - Cluster filter inside `subTab === 'arb'` block (line ~775–811): add the deposit-hint qualifier and filter step
  - Cluster header pill render (line ~858–866): show bonus amount
  - Deposit-hint branch (new): cluster header with note instead of funded provider cards
  - Funded provider card (line ~880–902): conditional `+B {amount} unclaimed` chip when `hasUnclaimedBonus(pid)`

- Possibly `backend/src/services/*` — only if bonus-semantics trace shows the field needs a fix. TBD after step 1 of backend dependency check.

## Out of scope

- Stake-cap feasibility check on deposit-hint arbs (anchor stake fits within `stakeCaps[pid]`).
- Hedge-leg funding feasibility (counter providers having enough balance to cover hedge stakes).
- Per-hedge-venue profit tiers (e.g., 1.5% for Pinnacle, 3% for Kalshi).

These were considered but deferred — single global 2.5% floor first, refine if surfacing produces too many duds or never fires.

## Success criteria

1. Empty cluster headers (no funded, no bonus-only, no qualifying arb) no longer render.
2. Funded provider with unclaimed bonus visibly indicates both signals on the provider card.
3. Bonus-only cluster pills show the unclaimed amount, not just the provider name.
4. A drained cluster with a ≥ 2.5% arb opp still appears, marked as a deposit hint with no provider cards.
5. Existing funded-cluster behavior (top 10 by edge, negative-edge fallback, pending list, arb table layout) unchanged.
