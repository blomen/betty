# Reactive Frontend Design

**Date:** 2026-03-19
**Goal:** Make the entire frontend feel instant by replacing polling/manual-fetch patterns with React Query optimistic mutations and consistent cache invalidation.

## Problem

The trading window (L1) feels real-time because SSE pushes data. The rest of the app feels sluggish because:

1. **BankrollPage uses raw `useState` + manual `fetchData()`** — not React Query, no shared cache, no optimistic updates
2. **Bankroll mutations (deposit/withdraw/transfer) wait for two sequential round trips** before updating UI: `await api.adjustBalance()` then `await fetchData()` which re-fetches exposure
3. **Bet placement invalidates `['bets', 'pending']` but not bankroll** — balance stays stale until next page visit
4. **Stale times are too high** — 60s for bets, no automatic refetch for bankroll

Note: `useBankroll` hook exists but has zero active consumers. The real problem is BankrollPage's isolated fetch pattern and the lack of optimistic mutations across all pages.

## Approach

Surgical optimistic updates using React Query patterns already proven in `TradingBankrollPage`. No backend changes. No new SSE streams. Optimistic updates are faster than SSE for user-initiated actions because they update before the server processes the request.

## Design

### New: `useBankrollQuery` hook

Replaces `useBankroll` (unused) and BankrollPage's internal fetch logic. Three `useQuery` hooks with shared query keys:

```typescript
// Query keys
['bankroll', 'info']      // api.getBankroll() — balances per provider
['bankroll', 'stats']     // api.getBankrollStats() — win rate, ROI, CLV
['bankroll', 'exposure']  // api.getBankrollExposure() — pending, available, locked

// Stale times
info/exposure: 5s    // balance-critical, cheap endpoints
stats: 30s           // slow-changing aggregates
```

Optimistic mutations:

| Mutation | Optimistic update | Invalidates on success |
|----------|------------------|----------------------|
| `adjustBalance(id, amt)` | ±amount in info + exposure cache | `opportunities.*` |
| `setBalance(id, bal)` | Set balance in info + exposure | `opportunities.*` |
| `transferFunds(from, to, amt)` | Subtract source, add dest (same-currency only; cross-currency skips optimistic, waits for server) | `opportunities.*` |
| `setAllBalances(bal)` | Set all providers | `opportunities.*` |
| `resetAllBalances()` | Zero all providers | `opportunities.*` |
| `depositWithBonus(id, amt)` | None (bonus unknown) | `bankroll.*`, `opportunities.*`, `providers` |

All mutations use `retry: false` — financial operations should fail clearly, not silently retry with confusing optimistic→rollback→re-optimistic flicker.

Optimistic update pattern:
```typescript
onMutate: async ({ providerId, amount }) => {
  await queryClient.cancelQueries({ queryKey: ['bankroll', 'info'] });
  const previous = queryClient.getQueryData(['bankroll', 'info']);
  queryClient.setQueryData(['bankroll', 'info'], (old) => ({
    ...old,
    providers: old.providers.map(p =>
      p.id === providerId ? { ...p, balance: p.balance + amount } : p
    ),
    total: old.total + amount,
  }));
  return { previous };
},
onError: (_err, _vars, context) => {
  queryClient.setQueryData(['bankroll', 'info'], context.previous);
},
onSettled: () => {
  queryClient.invalidateQueries({ queryKey: ['bankroll'] });
}
```

Return shape extends current `useBankroll` interface:
```typescript
{
  bankroll, stats, exposure,
  isLoading, error,
  adjustBalance, setBalance, transferFunds,
  setAllBalances, resetAllBalances, depositWithBonus,
  refresh  // manual invalidation for edge cases
}
```

### New: `useBetMutations` hook

Centralizes bet placement across all pages.

```typescript
// Mutations
placeBet(data)       // api.createBet() — single bet
editBet(id, data)    // api.editBet() — settle (win/loss/void) or edit odds/stake
```

Note: DutchPage places legs one-by-one in a loop (no batch API exists). Each call uses `placeBet` — the loop stays in the page component.

| Mutation | Optimistic update | Invalidates on success |
|----------|------------------|----------------------|
| `placeBet` | Insert into `['bets', 'pending']` | `bankroll.info`, `bankroll.exposure`, `opportunities.*` |
| `editBet` | Update bet in `['bets', *]` cache | `bankroll.stats` (when settling) |

The hook does NOT own page-specific UI state (pendingBet, isPlacing, toasts, two-step confirm flow, freebet popups). Pages pass `onSuccess`/`onError` callbacks per-call for their UI logic.

Consumer pages: ValuePage, DutchPage, ReversePage, PolymarketPage, ManualBetForm, MyBetsSection (for settlement via editBet).

### Unchanged: `useOddsStream`

The `tier_complete` handler already invalidates `['bankroll']` and `['bets']` at lines 57-58. React Query prefix-matches by default, so `{ queryKey: ['bankroll'] }` already matches `['bankroll', 'info']`, `['bankroll', 'stats']`, `['bankroll', 'exposure']`. No changes needed.

### Modified: `BankrollPage`

- Drop internal `useState<BankrollExposure>`, `fetchData()`, `useEffect` fetch-on-mount
- Consume `useBankrollQuery()` for exposure data and mutations
- Remove `onRefresh` prop — cache invalidation handles cross-page sync
- Deposit/withdraw/transfer handlers call mutation functions directly
- Optimistic updates make balance changes instant
- Fetch providers from React Query cache (`useQuery(['providers'])`) instead of broken `providers` prop that is never passed

### Modified: Value/Dutch/Reverse/Polymarket pages + ManualBetForm

Replace inline bet placement `try/catch` blocks with `useBetMutations().placeBet.mutateAsync()`. Keep page-specific UI flow (pending bet state, confirm step, toasts) in callbacks:

```typescript
const { placeBet } = useBetMutations();

const confirmPlaceBet = async () => {
  try {
    await placeBet.mutateAsync(betData, {
      onSuccess: () => {
        setBetSuccess(`Recorded: ...`);
        setPlacedKeys(prev => new Set(prev).add(key));
      },
    });
  } catch (err) {
    setBetError(err.message);
  }
};
```

### Modified: `MyBetsSection`

Replace inline `api.editBet()` calls with `useBetMutations().editBet.mutateAsync()` for settlement (win/loss/void). This ensures bankroll stats are invalidated on settlement.

### Modified: `StatsPage`

Update query keys for cache consistency:
- `['bankroll-stats']` → `['bankroll', 'stats']`
- `['bankroll-status']` → `['bankroll', 'status']`

This ensures both are invalidated when `['bankroll']` is prefix-invalidated.

### Stale time changes

| Query key | Current | New |
|-----------|---------|-----|
| `bankroll.info` | manual fetch | 5s stale |
| `bankroll.exposure` | manual fetch | 5s stale |
| `bankroll.stats` | 30s | 30s (unchanged) |
| `bankroll.status` | 60s | 30s |
| `bets.pending` | 60s | 10s |
| `bets.all` | 30s | 30s (unchanged) |
| `opportunities.*` | 30s | 30s (unchanged, SSE-driven) |

## Files

### New
- `frontend/src/hooks/useBankrollQuery.ts`
- `frontend/src/hooks/useBetMutations.ts`

### Modified
- `frontend/src/hooks/useBankroll.ts` — deleted (zero consumers, replaced by useBankrollQuery)
- `frontend/src/components/Terminal/pages/BankrollPage.tsx` — consume useBankrollQuery, fetch providers from React Query
- `frontend/src/components/Terminal/pages/ValuePage.tsx` — use useBetMutations
- `frontend/src/components/Terminal/pages/DutchPage.tsx` — use useBetMutations
- `frontend/src/components/Terminal/pages/ReversePage.tsx` — use useBetMutations
- `frontend/src/components/Terminal/pages/PolymarketPage.tsx` — use useBetMutations
- `frontend/src/components/Terminal/ManualBetForm.tsx` — use useBetMutations
- `frontend/src/components/Terminal/MyBetsSection.tsx` — use editBet mutation
- `frontend/src/components/Terminal/pages/StatsPage.tsx` — update query keys
- `frontend/src/components/Terminal/TerminalWindow.tsx` — remove BankrollPage providers prop if threaded

### Unchanged
- Backend APIs — no changes
- `api.ts` — all functions stay, mutations wrap them
- `useOddsStream.ts` — already invalidates correct prefixes
- `useMarketStream` / `useLevelMonitor` — trading SSE unchanged
- Odds/stake local editing — already instant (dynamicEdge)

## Implementation sequence

1. **PR 1: `useBankrollQuery` + BankrollPage migration** — no existing consumers to break, validates the pattern
2. **PR 2: `useBetMutations` + all page migrations** — larger scope, builds on validated pattern
