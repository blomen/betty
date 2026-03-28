# Capital Allocation ↔ Mirror Wiring Design

**Date:** 2026-03-28
**Status:** Draft
**Builds on:** Play v3 Session Manager (2026-03-24), Bet Mirror (2026-03-19), mirror-wiring.md

## Problem

The capital plan panel recommends deposits/withdrawals, but the user must manually click "done" after completing each action in the mirror browser. The mirror already auto-syncs balances via intercepted traffic, but the capital plan doesn't use this data to auto-confirm actions. The `balance_synced` SSE listener naively marks any balance change as "done" without matching the recommended amount.

## Strategy

Wire the mirror's balance/deposit detection into the capital plan panel so that when the user deposits in the mirror browser, the corresponding capital plan action auto-checks as "done" — matched by provider AND amount (±10% tolerance).

Two detection tiers:
1. **Primary (exact):** Intercept deposit-specific endpoints (e.g. Gecko V2 `payment-stats`) that report exact deposit amounts.
2. **Fallback (delta):** For providers without deposit endpoints, compute `delta = new_balance - old_balance` from the existing balance sync. Match the delta against pending capital plan actions.

Matching happens **client-side** — the frontend already has both the capital plan state and the SSE events. No need to push plan state to the backend.

## Detection Signals

### Tier 1: Deposit Endpoint (Exact)

Gecko V2 providers expose `GET cloud-api.{domain}/player/payment-stats` which returns cumulative deposit/withdraw totals. By comparing with the last-seen totals, we can detect exact deposit amounts.

**New interceptor keyword:** `payment-stats` (already in `_FINANCIAL_KEYWORDS`)

**New handler:** `MirrorService._handle_payment_stats(url, response_body)`:
- Parse cumulative deposit total from response
- Compare with last-seen total (cached in-memory per provider)
- If deposit total increased: `delta = new_total - old_total`
- Emit SSE: `deposit_detected {provider, amount, method: "payment_stats"}`
- Update cache

### Tier 2: Balance Delta (Fallback)

For all providers, enrich the existing `balance_synced` SSE event:
- Add `delta` field: `new_balance - old_balance`
- Keep existing fields: `provider`, `balance`, `previous`

The frontend uses `delta` to match against capital plan actions when no `deposit_detected` event fires.

### SSE Event Shapes

```json
// Tier 1: Exact deposit detection (Gecko V2)
{
  "event": "deposit_detected",
  "data": {
    "provider": "spelklubben",
    "amount": 500.0,
    "method": "payment_stats"
  }
}

// Tier 2: Enriched balance sync (all providers)
{
  "event": "balance_synced",
  "data": {
    "provider": "unibet",
    "balance": 1410.0,
    "previous": 1000.0,
    "delta": 410.0
  }
}
```

## Frontend Matching Logic

`CapitalPlanPanel` SSE listener update:

```
on deposit_detected OR balance_synced (where delta > 0):
  for each pending capital plan action where action.type == "deposit":
    if action.provider_id == event.provider:
      if |event.amount_or_delta - action.amount| / action.amount <= 0.10:
        auto-mark action as "done"
        break
```

- `deposit_detected` uses `amount` field
- `balance_synced` uses `delta` field
- Tolerance: ±10% of recommended amount (covers rounding, fees)
- Only matches `deposit` actions, not `withdraw` (out of scope for now)
- If no match within tolerance, ignore (could be a winning bet)

## Provider Wiring Order

Wire providers incrementally, in order of signal quality:

| Phase | Provider(s) | Platform | Detection | Signal |
|-------|------------|----------|-----------|--------|
| 1 | spelklubben, betsson, betsafe, nordicbet, bethard | Gecko V2 | `payment-stats` endpoint | Exact deposit amount |
| 2 | campobet, quickcasino, betinia, swiper, lodur, dbet | Altenar | Balance delta | `delta = new - old` |
| 3 | unibet | Kambi | Balance delta | `delta = new - old` |
| 4 | remaining Kambi providers | Kambi | Wire balance first, then delta | Incremental |

Phase 2 and 3 work immediately via delta fallback — their balance sync is already live. Phase 1 adds the exact signal on top.

## Backend Changes

### `backend/src/mirror/service.py`

1. **Add `_payment_stats_cache`** — `dict[str, float]` mapping provider_id → last-seen cumulative deposit total. Initialized empty, populated on first intercept.

2. **Add `_handle_payment_stats(url, response_body)`**:
   - Parse Gecko V2 payment-stats response (extract cumulative deposit total)
   - Compare with cache → compute delta
   - If delta > 0: emit `deposit_detected` SSE
   - Update cache
   - Store trace for audit

3. **Enrich `_sync_balance`** — add `delta` to the `balance_synced` notification payload. Already computes `old_balance` and `balance` — just add the subtraction.

4. **Route inside `_handle_financial_data`** — check if URL contains `payment-stats`. If yes, call `_handle_payment_stats` (deposit detection). Then continue with existing balance extraction flow (both can fire for the same provider — one gives deposit signal, the other syncs the actual balance).

### `backend/src/mirror/interceptor.py`

No changes needed — `payment-stats` is already in `_FINANCIAL_KEYWORDS`. The `_handle_financial_data` callback already receives these responses. Routing happens inside `MirrorService`, not the interceptor.

## Frontend Changes

### `frontend/src/components/Terminal/pages/play/CapitalPlanPanel.tsx`

Replace the naive SSE listener (lines 150-167) with amount-matching logic:

1. Listen for both `balance_synced` and `deposit_detected` events
2. Extract `delta` (from `balance_synced`) or `amount` (from `deposit_detected`)
3. Match against pending deposit actions by provider + amount tolerance
4. Auto-mark matched action as "done"

## Data Flow

```
User deposits 500kr at spelklubben in mirror browser
    ↓
[Interceptor] catches GET payment-stats response
    ↓
[MirrorService._handle_payment_stats()]
  → cache: 2000 → 2500, delta = 500
  → SSE: deposit_detected {provider: "spelklubben", amount: 500}
    ↓
[Interceptor] also catches GET /wallets response
    ↓
[MirrorService._sync_balance()]
  → ProfileProviderBalance updated: 400 → 900
  → SSE: balance_synced {provider: "spelklubben", balance: 900, previous: 400, delta: 500}
    ↓
[CapitalPlanPanel] SSE listener
  → deposit_detected for spelklubben, amount=500
  → capital plan has: deposit spelklubben 500kr
  → |500 - 500| / 500 = 0% ≤ 10% → match!
  → auto-mark "done" ✓
    ↓
User sees checkmark, clicks "Recalc Batch →"
```

## Out of Scope

- **Withdraw detection** — same pattern, wire later when needed
- **Auto-triggering "Recalc Batch"** — user still clicks manually after reviewing
- **Deposit automation** — Phase 3 mirror (placing deposits programmatically)
- **Non-mirror deposits** — deposits done outside the mirror browser won't be detected
