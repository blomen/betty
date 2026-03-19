# Reactive Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the entire frontend feel instant by replacing polling/manual-fetch patterns with React Query optimistic mutations and consistent cache invalidation.

**Architecture:** Two new hooks (`useBankrollQuery`, `useBetMutations`) centralize all data mutations with optimistic updates. Pages become thin consumers that call mutation functions and handle UI-specific feedback. All bankroll/bet queries use hierarchical keys (`['bankroll', 'info']`) so prefix invalidation works automatically.

**Tech Stack:** React 19, @tanstack/react-query, TypeScript

**Spec:** `docs/superpowers/specs/2026-03-19-reactive-frontend-design.md`

---

## File Structure

### New files
- `frontend/src/hooks/useBankrollQuery.ts` — React Query hook replacing `useBankroll.ts`. Three `useQuery` calls (info, stats, exposure) + six `useMutation` calls with optimistic updates.
- `frontend/src/hooks/useBetMutations.ts` — Centralized bet mutations: `placeBet` (wraps `api.createBet`), `placeBatchBets` (wraps `api.createBatchBets`), `editBet` (wraps `api.editBet`). Each mutation optimistically updates the bets cache and invalidates bankroll queries.

### Modified files
- `frontend/src/hooks/useBankroll.ts` — deleted
- `frontend/src/components/Terminal/pages/BankrollPage.tsx` — consume `useBankrollQuery` instead of internal `fetchData()`
- `frontend/src/components/Terminal/pages/ValuePage.tsx` — use `useBetMutations().placeBet`
- `frontend/src/components/Terminal/pages/DutchPage.tsx` — use `useBetMutations().placeBet` + `.placeBatchBets`
- `frontend/src/components/Terminal/pages/DrainPage.tsx` — use `useBetMutations().placeBet` + `.placeBatchBets`
- `frontend/src/components/Terminal/pages/ReversePage.tsx` — use `useBetMutations().placeBet`
- `frontend/src/components/Terminal/pages/PolymarketPage.tsx` — use `useBetMutations().placeBet`
- `frontend/src/components/Terminal/ManualBetForm.tsx` — use `useBetMutations().placeBet`
- `frontend/src/components/Terminal/MyBetsSection.tsx` — use `useBetMutations().editBet`
- `frontend/src/components/Terminal/pages/BetsPage.tsx` — use `useBetMutations().editBet`
- `frontend/src/components/Terminal/pages/StatsPage.tsx` — update query keys
- `frontend/src/hooks/index.ts` — update barrel export (useBankroll → useBankrollQuery)

### Unchanged
- `frontend/src/services/api.ts` — all API functions stay as-is
- `frontend/src/hooks/useOddsStream.ts` — already invalidates `['bankroll']` and `['bets']` prefixes
- `frontend/src/components/Terminal/TerminalWindow.tsx` — `<BankrollPage />` already rendered with no props

---

## Task 1: Create `useBankrollQuery` hook

**Files:**
- Create: `frontend/src/hooks/useBankrollQuery.ts`

- [ ] **Step 1: Create the hook file with queries**

```typescript
// frontend/src/hooks/useBankrollQuery.ts
import { useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import type { BankrollInfo, BankrollStats, BankrollExposure } from '@/types';
import { api } from '@/services/api';

export function useBankrollQuery() {
  const queryClient = useQueryClient();

  // ─── Queries ───
  const { data: bankroll, isLoading: infoLoading, error: infoError } = useQuery({
    queryKey: ['bankroll', 'info'],
    queryFn: () => api.getBankroll(),
    staleTime: 5_000,
  });

  const { data: stats, isLoading: statsLoading } = useQuery({
    queryKey: ['bankroll', 'stats'],
    queryFn: () => api.getBankrollStats(),
    staleTime: 30_000,
  });

  const { data: exposure, isLoading: exposureLoading } = useQuery({
    queryKey: ['bankroll', 'exposure'],
    queryFn: () => api.getBankrollExposure(),
    staleTime: 5_000,
  });

  // ─── Helper: optimistic balance update for info + exposure ───
  const optimisticBalanceUpdate = (
    providerId: string,
    transform: (balance: number) => number,
  ) => {
    // Update info cache
    queryClient.setQueryData<BankrollInfo>(['bankroll', 'info'], (old) => {
      if (!old) return old;
      let newTotal = 0;
      const providers = old.providers.map((p) => {
        const newBalance = p.id === providerId ? transform(p.balance) : p.balance;
        newTotal += newBalance;
        return { ...p, balance: newBalance };
      });
      return { ...old, total: newTotal, providers };
    });
    // Update exposure cache
    queryClient.setQueryData<BankrollExposure>(['bankroll', 'exposure'], (old) => {
      if (!old) return old;
      let totalBalance = 0;
      const providers = old.providers.map((p) => {
        if (p.provider_id !== providerId) {
          totalBalance += p.total_balance;
          return p;
        }
        const newBalance = transform(p.total_balance);
        const newAvailable = newBalance - p.pending_exposure;
        totalBalance += newBalance;
        return { ...p, total_balance: newBalance, available: newAvailable };
      });
      const totalAvailable = providers.reduce((s, p) => s + p.available, 0);
      return { ...old, total_balance: totalBalance, total_available: totalAvailable, providers };
    });
  };

  // ─── Mutations ───
  const adjustBalanceMutation = useMutation({
    mutationFn: ({ providerId, amount }: { providerId: string; amount: number }) =>
      api.adjustBalance(providerId, amount),
    retry: false,
    onMutate: async ({ providerId, amount }) => {
      await queryClient.cancelQueries({ queryKey: ['bankroll'] });
      const prevInfo = queryClient.getQueryData<BankrollInfo>(['bankroll', 'info']);
      const prevExposure = queryClient.getQueryData<BankrollExposure>(['bankroll', 'exposure']);
      optimisticBalanceUpdate(providerId, (b) => b + amount);
      return { prevInfo, prevExposure };
    },
    onError: (_err, _vars, context) => {
      if (context?.prevInfo) queryClient.setQueryData(['bankroll', 'info'], context.prevInfo);
      if (context?.prevExposure) queryClient.setQueryData(['bankroll', 'exposure'], context.prevExposure);
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['bankroll'] });
      queryClient.invalidateQueries({ queryKey: ['opportunities'] });
    },
  });

  const setBalanceMutation = useMutation({
    mutationFn: ({ providerId, balance }: { providerId: string; balance: number }) =>
      api.setBalance(providerId, balance),
    retry: false,
    onMutate: async ({ providerId, balance }) => {
      await queryClient.cancelQueries({ queryKey: ['bankroll'] });
      const prevInfo = queryClient.getQueryData<BankrollInfo>(['bankroll', 'info']);
      const prevExposure = queryClient.getQueryData<BankrollExposure>(['bankroll', 'exposure']);
      optimisticBalanceUpdate(providerId, () => balance);
      return { prevInfo, prevExposure };
    },
    onError: (_err, _vars, context) => {
      if (context?.prevInfo) queryClient.setQueryData(['bankroll', 'info'], context.prevInfo);
      if (context?.prevExposure) queryClient.setQueryData(['bankroll', 'exposure'], context.prevExposure);
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['bankroll'] });
      queryClient.invalidateQueries({ queryKey: ['opportunities'] });
    },
  });

  const transferFundsMutation = useMutation({
    mutationFn: ({ fromProviderId, toProviderId, amount, withBonus }: {
      fromProviderId: string; toProviderId: string; amount: number; withBonus?: boolean;
    }) => api.transferFunds(fromProviderId, toProviderId, amount, withBonus),
    retry: false,
    onMutate: async ({ fromProviderId, toProviderId, amount }) => {
      await queryClient.cancelQueries({ queryKey: ['bankroll'] });
      const prevInfo = queryClient.getQueryData<BankrollInfo>(['bankroll', 'info']);
      const prevExposure = queryClient.getQueryData<BankrollExposure>(['bankroll', 'exposure']);
      // Check if cross-currency — skip optimistic if so
      const exp = queryClient.getQueryData<BankrollExposure>(['bankroll', 'exposure']);
      const fromProv = exp?.providers.find((p) => p.provider_id === fromProviderId);
      const toProv = exp?.providers.find((p) => p.provider_id === toProviderId);
      const sameCurrency = fromProv?.currency && toProv?.currency && fromProv.currency === toProv.currency;
      if (sameCurrency) {
        optimisticBalanceUpdate(fromProviderId, (b) => b - amount);
        optimisticBalanceUpdate(toProviderId, (b) => b + amount);
      }
      return { prevInfo, prevExposure };
    },
    onError: (_err, _vars, context) => {
      if (context?.prevInfo) queryClient.setQueryData(['bankroll', 'info'], context.prevInfo);
      if (context?.prevExposure) queryClient.setQueryData(['bankroll', 'exposure'], context.prevExposure);
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['bankroll'] });
      queryClient.invalidateQueries({ queryKey: ['opportunities'] });
    },
  });

  const setAllBalancesMutation = useMutation({
    mutationFn: ({ balance, providerIds }: { balance: number; providerIds?: string[] }) =>
      api.setAllBalances(balance, providerIds),
    retry: false,
    onMutate: async ({ balance, providerIds }) => {
      await queryClient.cancelQueries({ queryKey: ['bankroll'] });
      const prevInfo = queryClient.getQueryData<BankrollInfo>(['bankroll', 'info']);
      const prevExposure = queryClient.getQueryData<BankrollExposure>(['bankroll', 'exposure']);
      queryClient.setQueryData<BankrollInfo>(['bankroll', 'info'], (old) => {
        if (!old) return old;
        const providers = old.providers.map((p) => {
          if (providerIds && !providerIds.includes(p.id)) return p;
          return { ...p, balance };
        });
        return { ...old, total: providers.reduce((s, p) => s + p.balance, 0), providers };
      });
      return { prevInfo, prevExposure };
    },
    onError: (_err, _vars, context) => {
      if (context?.prevInfo) queryClient.setQueryData(['bankroll', 'info'], context.prevInfo);
      if (context?.prevExposure) queryClient.setQueryData(['bankroll', 'exposure'], context.prevExposure);
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['bankroll'] });
      queryClient.invalidateQueries({ queryKey: ['opportunities'] });
    },
  });

  const resetAllBalancesMutation = useMutation({
    mutationFn: () => api.resetAllBalances(),
    retry: false,
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey: ['bankroll'] });
      const prevInfo = queryClient.getQueryData<BankrollInfo>(['bankroll', 'info']);
      const prevExposure = queryClient.getQueryData<BankrollExposure>(['bankroll', 'exposure']);
      queryClient.setQueryData<BankrollInfo>(['bankroll', 'info'], (old) => {
        if (!old) return old;
        return { ...old, total: 0, providers: old.providers.map((p) => ({ ...p, balance: 0 })) };
      });
      return { prevInfo, prevExposure };
    },
    onError: (_err, _vars, context) => {
      if (context?.prevInfo) queryClient.setQueryData(['bankroll', 'info'], context.prevInfo);
      if (context?.prevExposure) queryClient.setQueryData(['bankroll', 'exposure'], context.prevExposure);
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['bankroll'] });
      queryClient.invalidateQueries({ queryKey: ['opportunities'] });
    },
  });

  const depositWithBonusMutation = useMutation({
    mutationFn: ({ providerId, amount }: { providerId: string; amount: number }) =>
      api.depositWithBonus(providerId, amount),
    retry: false,
    // No optimistic update — bonus amount unknown until server responds
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['bankroll'] });
      queryClient.invalidateQueries({ queryKey: ['opportunities'] });
      queryClient.invalidateQueries({ queryKey: ['providers'] });
    },
  });

  const refresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['bankroll'] });
  }, [queryClient]);

  return {
    // Data
    bankroll: bankroll ?? { total: 0, providers: [] } as BankrollInfo,
    stats: stats ?? {
      total_bets: 0, wins: 0, losses: 0, voids: 0,
      total_deposited: 0, total_withdrawn: 0, net_deposited: 0,
      total_staked: 0, total_profit: 0, bet_profit: 0,
      freebet_profit: 0, bonus_profit: 0,
      roi_pct: 0, win_rate: 0, avg_clv: 0, clv_positive_pct: 0, clv_count: 0,
    } as BankrollStats,
    exposure: exposure ?? {
      total_balance: 0, total_pending: 0, total_available: 0,
      total_free: 0, total_locked: 0, providers: [],
    } as BankrollExposure,
    isLoading: infoLoading || statsLoading || exposureLoading,
    error: infoError ? (infoError instanceof Error ? infoError.message : 'Failed to load bankroll data') : null,
    // Mutations (expose mutateAsync for try/catch usage in pages)
    adjustBalance: adjustBalanceMutation,
    setBalance: setBalanceMutation,
    transferFunds: transferFundsMutation,
    setAllBalances: setAllBalancesMutation,
    resetAllBalances: resetAllBalancesMutation,
    depositWithBonus: depositWithBonusMutation,
    refresh,
  };
}
```

- [ ] **Step 2: Verify it compiles**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: No errors from `useBankrollQuery.ts`

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useBankrollQuery.ts
git commit -m "feat: add useBankrollQuery hook with optimistic mutations"
```

---

## Task 2: Create `useBetMutations` hook

**Files:**
- Create: `frontend/src/hooks/useBetMutations.ts`

- [ ] **Step 1: Create the hook file**

```typescript
// frontend/src/hooks/useBetMutations.ts
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { Bet } from '@/types';

interface BetsResponse {
  bets: Bet[];
  count: number;
}

// Type matching api.createBet parameter
type CreateBetData = Parameters<typeof api.createBet>[0];

// Type matching api.createBatchBets parameter
type BatchBetLeg = Parameters<typeof api.createBatchBets>[0][number];

// Type matching api.editBet parameters
type EditBetData = Parameters<typeof api.editBet>[1];

export function useBetMutations() {
  const queryClient = useQueryClient();

  const invalidateBankrollAndOpps = () => {
    queryClient.invalidateQueries({ queryKey: ['bankroll', 'info'] });
    queryClient.invalidateQueries({ queryKey: ['bankroll', 'exposure'] });
    queryClient.invalidateQueries({ queryKey: ['opportunities'] });
  };

  const placeBet = useMutation({
    mutationFn: (data: CreateBetData) => api.createBet(data),
    retry: false,
    onSuccess: (_result, data) => {
      // Insert placeholder into pending bets cache (replaced on refetch with real data)
      queryClient.setQueryData<BetsResponse>(['bets', 'pending'], (old) => {
        if (!old) return old;
        const newBet: Partial<Bet> = {
          id: Date.now(), // temporary ID until refetch
          provider: data.provider_id,
          market: data.market ?? null,
          outcome: data.outcome ?? null,
          odds: data.odds,
          stake: data.stake,
          point: data.point ?? null,
          is_bonus: data.is_bonus ?? false,
          bonus_type: data.bonus_type ?? null,
          result: 'pending',
          payout: 0,
          profit: 0,
          roi_pct: 0,
          event_id: data.event_id ?? null,
          bet_type: data.bet_type ?? null,
          currency: data.provider_id === 'polymarket' ? 'USDC' : 'SEK',
          placed_at: new Date().toISOString(),
        };
        return {
          ...old,
          bets: [newBet as Bet, ...old.bets],
          count: old.count + 1,
        };
      });
      invalidateBankrollAndOpps();
      // Refetch bets to get server-assigned IDs and full data
      queryClient.invalidateQueries({ queryKey: ['bets'] });
    },
  });

  const placeBatchBets = useMutation({
    mutationFn: (legs: BatchBetLeg[]) => api.createBatchBets(legs),
    retry: false,
    onSuccess: () => {
      invalidateBankrollAndOpps();
      queryClient.invalidateQueries({ queryKey: ['bets'] });
    },
  });

  const editBet = useMutation({
    mutationFn: ({ betId, data }: { betId: number; data: EditBetData }) =>
      api.editBet(betId, data),
    retry: false,
    onSuccess: (_result, { betId, data }) => {
      if (data.result && data.result !== 'pending') {
        // Settling — remove from pending cache
        queryClient.setQueryData<BetsResponse>(['bets', 'pending'], (old) => {
          if (!old) return old;
          return {
            ...old,
            bets: old.bets.filter((b) => b.id !== betId),
            count: old.count - 1,
          };
        });
        queryClient.invalidateQueries({ queryKey: ['bankroll', 'stats'] });
        queryClient.invalidateQueries({ queryKey: ['bankroll', 'exposure'] });
      } else if (data.odds !== undefined || data.stake !== undefined) {
        // Editing odds/stake (not settling) — update in cache
        queryClient.setQueryData<BetsResponse>(['bets', 'pending'], (old) => {
          if (!old) return old;
          return {
            ...old,
            bets: old.bets.map((b) =>
              b.id === betId
                ? { ...b, ...(data.odds !== undefined && { odds: data.odds }), ...(data.stake !== undefined && { stake: data.stake }) }
                : b
            ),
          };
        });
      }
      queryClient.invalidateQueries({ queryKey: ['bets'] });
    },
  });

  return { placeBet, placeBatchBets, editBet };
}
```

- [ ] **Step 2: Verify it compiles**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: No errors from `useBetMutations.ts`

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useBetMutations.ts
git commit -m "feat: add useBetMutations hook with optimistic cache updates"
```

---

## Task 3: Migrate BankrollPage to `useBankrollQuery`

**Files:**
- Modify: `frontend/src/components/Terminal/pages/BankrollPage.tsx`

The BankrollPage currently uses:
- Internal `useState<BankrollExposure>`, `fetchData()`, `useEffect` for data loading
- Direct `api.adjustBalance()`, `api.setBalance()`, etc. calls followed by `fetchData()` + `onRefresh?.()`
- A `providers` prop that is never passed (always `[]`)

Replace with `useBankrollQuery()` hook + `useQuery(['providers'])` for provider data.

- [ ] **Step 1: Replace imports and state**

At the top of `BankrollPage.tsx`:
- Remove: `import { api } from '@/services/api';` (bankroll calls move to hook)
- Add: `import { useBankrollQuery } from '@/hooks/useBankrollQuery';`
- Add: `import { useQuery } from '@tanstack/react-query';`
- Add: `import { api } from '@/services/api';` (keep for provider query + claimBonus)
- Remove: `BankrollPageProps` interface and `providers`/`onRefresh` props
- Change function signature: `export function BankrollPage()` (no props)
- Remove: `useState<BankrollExposure>`, `isLoading` state, `fetchData` callback, `useEffect` fetch-on-mount
- Add: `const { exposure, adjustBalance, setBalance, transferFunds, depositWithBonus, isLoading } = useBankrollQuery();`
- Add: `const { data: providersData } = useQuery({ queryKey: ['providers'], queryFn: () => api.getProviders() });`
- Add: `const providers = providersData?.providers ?? [];`

- [ ] **Step 2: Replace mutation calls**

In each handler, replace `await api.X(...)` + `fetchData()` + `onRefresh?.()` with the mutation:

`confirmDeposit`:
```typescript
// Before:
await api.adjustBalance(providerId, amount);
fetchData();
onRefresh?.();
// After:
await adjustBalance.mutateAsync({ providerId, amount });
```

For `depositWithBonus`:
```typescript
// Before:
const result = await api.depositWithBonus(providerId, amount);
fetchData();
onRefresh?.();
// After:
const result = await depositWithBonus.mutateAsync({ providerId, amount });
```

`handleWithdraw`:
```typescript
// Before:
await api.adjustBalance(providerId, -amount);
fetchData();
onRefresh?.();
// After:
await adjustBalance.mutateAsync({ providerId, amount: -amount });
```

`handleSetBalance`:
```typescript
// Before:
const result = await api.setBalance(providerId, balance);
fetchData();
onRefresh?.();
// After:
const result = await setBalance.mutateAsync({ providerId, balance });
```

`handleTransfer`:
```typescript
// Before:
const result = await api.transferFunds(from, to, amount, withBonus);
fetchData();
onRefresh?.();
// After:
const result = await transferFunds.mutateAsync({ fromProviderId: from, toProviderId: to, amount, withBonus });
```

Remove all `fetchData()` and `onRefresh?.()` calls — the mutations handle cache invalidation.

Also fix the inline `claimBonus` button handler (line ~371) which only calls `onRefresh?.()`:
```typescript
// Before:
await api.claimBonus(provider.provider_id);
onRefresh?.();
// After:
await api.claimBonus(provider.provider_id);
queryClient.invalidateQueries({ queryKey: ['providers'] });
queryClient.invalidateQueries({ queryKey: ['bankroll'] });
```
This requires adding `const queryClient = useQueryClient();` at the top of the component.

- [ ] **Step 3: Fix `getProviderBonus` to use query data instead of prop**

The `getProviderBonus` function references `providers.find(...)`. Since `providers` now comes from `useQuery(['providers'])` instead of a prop, it should just work. Verify the variable name matches.

- [ ] **Step 4: Verify it compiles**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: No errors. Fix any type mismatches between mutation arg shapes and old direct-call shapes.

- [ ] **Step 5: Test in browser**

Start dev server: `cd frontend && npm run dev`

Test each flow:
1. Navigate to Bankroll tab → data loads
2. Click a provider → expand → deposit 100 kr → balance updates instantly
3. Withdraw 50 kr → balance decreases instantly
4. Set balance to 500 → balance snaps to 500
5. Transfer between providers → both update instantly
6. If any mutation fails, balance should roll back

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Terminal/pages/BankrollPage.tsx
git commit -m "refactor: migrate BankrollPage to useBankrollQuery with optimistic updates"
```

---

## Task 4: Delete `useBankroll.ts` and update barrel export

**Files:**
- Delete: `frontend/src/hooks/useBankroll.ts`
- Modify: `frontend/src/hooks/index.ts`

- [ ] **Step 1: Update barrel export in `hooks/index.ts`**

```typescript
// Before (line 2):
export { useBankroll } from './useBankroll';
// After:
export { useBankrollQuery } from './useBankrollQuery';
```

- [ ] **Step 2: Verify no other imports of `useBankroll` remain**

Run: `grep -r "useBankroll" frontend/src/ --include="*.ts" --include="*.tsx" | grep -v "useBankrollQuery" | grep -v "node_modules"`

Expected: No results (zero consumers). If any results appear, update those files first.

- [ ] **Step 3: Delete the file**

```bash
rm frontend/src/hooks/useBankroll.ts
```

- [ ] **Step 4: Verify it compiles**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: No errors.

- [ ] **Step 5: Commit**

```bash
git add -u frontend/src/hooks/useBankroll.ts
git add frontend/src/hooks/index.ts
git commit -m "chore: delete unused useBankroll hook, update barrel export"
```

---

## Task 5: Update StatsPage query keys

**Files:**
- Modify: `frontend/src/components/Terminal/pages/StatsPage.tsx`

- [ ] **Step 1: Update query keys**

At line 22, change:
```typescript
// Before:
queryKey: ['bankroll-stats'],
// After:
queryKey: ['bankroll', 'stats'],
```

At line 46, change:
```typescript
// Before:
queryKey: ['bankroll-status'],
// After:
queryKey: ['bankroll', 'status'],
```

Also update the stale time on the status query:
```typescript
// Before:
staleTime: 60_000,
// After:
staleTime: 30_000,
```

- [ ] **Step 2: Verify it compiles**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -20`

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Terminal/pages/StatsPage.tsx
git commit -m "refactor: align StatsPage query keys to bankroll prefix hierarchy"
```

---

## Task 6: Migrate ValuePage to `useBetMutations`

**Files:**
- Modify: `frontend/src/components/Terminal/pages/ValuePage.tsx`

ValuePage has two bet placement flows:
1. `confirmPlaceBet()` — regular value bet (line ~661)
2. `confirmBoostPlaceBet()` — boost bet (line ~601)

Both currently call `api.createBet()` directly with a `try/catch`.

- [ ] **Step 1: Add import and hook call**

```typescript
import { useBetMutations } from '@/hooks/useBetMutations';
// Inside the component:
const { placeBet } = useBetMutations();
```

- [ ] **Step 2: Replace `confirmPlaceBet` (value bets)**

Replace the `api.createBet(...)` call inside `confirmPlaceBet` with `placeBet.mutateAsync(...)`. Keep all surrounding UI logic (pending state, success toast, placedKeys, myBetsCount). Remove the manual `queryClient.invalidateQueries({ queryKey: ['bets', 'pending'] })` — the mutation handles it.

```typescript
// Before:
await api.createBet({ ... });
// ...UI logic...
queryClient.invalidateQueries({ queryKey: ['bets', 'pending'] });

// After:
await placeBet.mutateAsync({ ... });
// ...UI logic stays the same, remove the manual invalidateQueries call...
```

- [ ] **Step 3: Replace `confirmBoostPlaceBet` (boost bets)**

Same pattern — replace `api.createBet(...)` with `placeBet.mutateAsync(...)`, remove manual `queryClient.invalidateQueries`.

- [ ] **Step 4: Clean up unused imports**

If `api` is no longer used directly in ValuePage (check for other calls like `api.getStakePreview`, `api.getOpportunities`, etc.), keep the import. Only remove if truly unused.

Remove `queryClient` usage if no other direct `invalidateQueries` calls remain. Check that `useQueryClient` import can be removed too.

- [ ] **Step 5: Update bets query stale time**

Find the `useQuery` for `['bets', 'pending']` and change staleTime:
```typescript
// Before:
staleTime: 60_000,
// After:
staleTime: 10_000,
```

- [ ] **Step 6: Verify it compiles**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -20`

- [ ] **Step 7: Test in browser**

1. Value tab → expand a row → Place Bet → Confirm → success toast shows, bet appears in My Bets
2. Boosts tab → expand → Place Bet → same flow
3. Check Bankroll tab — balance should reflect the new bet's stake deduction within 5s

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/Terminal/pages/ValuePage.tsx
git commit -m "refactor: migrate ValuePage bet placement to useBetMutations"
```

---

## Task 7: Migrate DutchPage to `useBetMutations`

**Files:**
- Modify: `frontend/src/components/Terminal/pages/DutchPage.tsx`

DutchPage has two bet flows:
1. `handlePlaceLeg()` — single leg placement (line ~471), uses `api.createBet()`
2. `handlePlaceAll()` — batch placement (line ~521), uses `api.createBatchBets()`

- [ ] **Step 1: Add import and hook call**

```typescript
import { useBetMutations } from '@/hooks/useBetMutations';
const { placeBet, placeBatchBets } = useBetMutations();
```

- [ ] **Step 2: Replace `handlePlaceLeg`**

```typescript
// Before:
await api.createBet({ ... });
queryClient.invalidateQueries({ queryKey: ['opportunities', 'dutch'] });
queryClient.invalidateQueries({ queryKey: ['bets', 'pending'] });

// After:
await placeBet.mutateAsync({ ... });
queryClient.invalidateQueries({ queryKey: ['opportunities', 'dutch'] });
// Remove the bets invalidation — mutation handles it
```

Keep the `queryClient.invalidateQueries({ queryKey: ['opportunities', 'dutch'] })` — this is Dutch-specific (refetches the dutch workflow to update remaining legs).

- [ ] **Step 3: Replace `handlePlaceAll`**

```typescript
// Before:
const res = await api.createBatchBets(batchLegs);
// ...handle results...
queryClient.invalidateQueries({ queryKey: ['opportunities', 'dutch'] });
queryClient.invalidateQueries({ queryKey: ['bets', 'pending'] });

// After:
const res = await placeBatchBets.mutateAsync(batchLegs);
// ...handle results stays the same...
queryClient.invalidateQueries({ queryKey: ['opportunities', 'dutch'] });
// Remove the bets invalidation
```

- [ ] **Step 4: Update bets stale time**

Change `['bets', 'pending']` staleTime from `60_000` to `10_000`.

- [ ] **Step 5: Verify + commit**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -20`

```bash
git add frontend/src/components/Terminal/pages/DutchPage.tsx
git commit -m "refactor: migrate DutchPage bet placement to useBetMutations"
```

---

## Task 8: Migrate ReversePage to `useBetMutations`

**Files:**
- Modify: `frontend/src/components/Terminal/pages/ReversePage.tsx`

ReversePage has one bet flow: `confirmPlaceBet()` (line ~285) using `api.createBet()`.

- [ ] **Step 1: Add import and hook, replace `api.createBet`**

```typescript
import { useBetMutations } from '@/hooks/useBetMutations';
const { placeBet } = useBetMutations();

// In confirmPlaceBet:
// Before:
await api.createBet({ ... });
queryClient.invalidateQueries({ queryKey: ['opportunities', 'reverse'] });
queryClient.invalidateQueries({ queryKey: ['bets', 'pending'] });
// After:
await placeBet.mutateAsync({ ... });
queryClient.invalidateQueries({ queryKey: ['opportunities', 'reverse'] });
```

- [ ] **Step 2: Update bets stale time to 10_000**

- [ ] **Step 3: Verify + commit**

```bash
git add frontend/src/components/Terminal/pages/ReversePage.tsx
git commit -m "refactor: migrate ReversePage bet placement to useBetMutations"
```

---

## Task 9: Migrate PolymarketPage to `useBetMutations`

**Files:**
- Modify: `frontend/src/components/Terminal/pages/PolymarketPage.tsx`

PolymarketPage has one bet flow using `api.createBet()` (line ~339).

- [ ] **Step 1: Add import and hook, replace `api.createBet`**

Same pattern as ReversePage. Keep `queryClient.invalidateQueries({ queryKey: ['opportunities', 'polymarket'] })`.

- [ ] **Step 2: Update bets stale time to 10_000**

- [ ] **Step 3: Verify + commit**

```bash
git add frontend/src/components/Terminal/pages/PolymarketPage.tsx
git commit -m "refactor: migrate PolymarketPage bet placement to useBetMutations"
```

---

## Task 10: Migrate DrainPage to `useBetMutations`

**Files:**
- Modify: `frontend/src/components/Terminal/pages/DrainPage.tsx`

DrainPage has two bet flows (same pattern as DutchPage):
1. `handlePlaceLeg` (line ~246) — single leg, uses `api.createBet()`
2. `handlePlaceAll` (line ~320) — batch, uses `api.createBatchBets()`

- [ ] **Step 1: Add import and hook, replace both `api.createBet` and `api.createBatchBets`**

```typescript
import { useBetMutations } from '@/hooks/useBetMutations';
const { placeBet, placeBatchBets } = useBetMutations();
```

Replace `api.createBet(...)` with `placeBet.mutateAsync(...)` in `handlePlaceLeg`.
Replace `api.createBatchBets(...)` with `placeBatchBets.mutateAsync(...)` in `handlePlaceAll`.
Keep any drain-specific invalidation (e.g. `['opportunities', 'drain']`).

- [ ] **Step 2: Update bets stale time to 10_000 if applicable**

- [ ] **Step 3: Verify + commit**

```bash
git add frontend/src/components/Terminal/pages/DrainPage.tsx
git commit -m "refactor: migrate DrainPage bet placement to useBetMutations"
```

---

## Task 11: Migrate ManualBetForm to `useBetMutations`

**Files:**
- Modify: `frontend/src/components/Terminal/ManualBetForm.tsx`

ManualBetForm calls `api.createBet()` at line 39 inside `handleSubmit`.

- [ ] **Step 1: Add import and hook, replace `api.createBet`**

```typescript
import { useBetMutations } from '@/hooks/useBetMutations';

// Inside component:
const { placeBet } = useBetMutations();

// In handleSubmit:
// Before:
await api.createBet({ ... });
// After:
await placeBet.mutateAsync({ ... });
```

The `onSuccess` and `onError` callbacks from the parent component are called around the mutation, not changed.

- [ ] **Step 2: Remove `import { api } from '@/services/api'` if no other api calls remain**

- [ ] **Step 3: Verify + commit**

```bash
git add frontend/src/components/Terminal/ManualBetForm.tsx
git commit -m "refactor: migrate ManualBetForm to useBetMutations"
```

---

## Task 12: Migrate MyBetsSection to `useBetMutations`

**Files:**
- Modify: `frontend/src/components/Terminal/MyBetsSection.tsx`

MyBetsSection has four `api.editBet()` calls:
1. `handleSettle` (line 68) — settle bet as won/lost/void
2. `saveInlineEdit` (line 92) — edit odds/stake
3. `handleRaise` (line 209) — raise stake at current odds
4. `confirmCashout` (line 235) — cashout (void with payout)

It also has its own `fetchBets()` + `useState` pattern for loading bets.

- [ ] **Step 1: Add import and hook**

```typescript
import { useBetMutations } from '@/hooks/useBetMutations';
const { editBet } = useBetMutations();
```

- [ ] **Step 2: Replace `handleSettle`**

```typescript
// Before:
await api.editBet(bet.id, { result });
setExpandedId(null);
await fetchBets();

// After:
await editBet.mutateAsync({ betId: bet.id, data: { result } });
setExpandedId(null);
await fetchBets(); // Keep this — MyBetsSection manages its own filtered list
```

Note: MyBetsSection maintains a local `bets` state filtered by the `filter` prop. The `fetchBets()` call should stay because the mutation's cache update happens on the unfiltered `['bets', 'pending']` cache, but MyBetsSection needs to re-fetch and re-filter. The mutation still adds value by invalidating `bankroll.stats`.

- [ ] **Step 3: Replace `saveInlineEdit`**

```typescript
// Before:
await api.editBet(inlineEdit.id, changes);
cancelInlineEdit();
fetchBets();

// After:
await editBet.mutateAsync({ betId: inlineEdit.id, data: changes });
cancelInlineEdit();
fetchBets();
```

- [ ] **Step 4: Replace `handleRaise`**

```typescript
// Before:
await api.editBet(bet.id, { stake: newStake, odds: parseFloat(newAvgOdds.toFixed(2)) });
fetchBets();

// After:
await editBet.mutateAsync({ betId: bet.id, data: { stake: newStake, odds: parseFloat(newAvgOdds.toFixed(2)) } });
fetchBets();
```

- [ ] **Step 5: Replace `confirmCashout`**

```typescript
// Before:
await api.editBet(betId, { result: 'void', payout: amount });

// After:
await editBet.mutateAsync({ betId, data: { result: 'void', payout: amount } });
```

- [ ] **Step 6: Verify + commit**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -20`

```bash
git add frontend/src/components/Terminal/MyBetsSection.tsx
git commit -m "refactor: migrate MyBetsSection to useBetMutations for settlement/edit"
```

---

## Task 13: Migrate BetsPage to `useBetMutations`

**Files:**
- Modify: `frontend/src/components/Terminal/pages/BetsPage.tsx`

BetsPage has two `api.editBet()` calls:
1. Inline edit (line ~507) — edit odds/stake
2. `confirmCashout` (line ~530) — cashout (void with payout)

- [ ] **Step 1: Add import and hook**

```typescript
import { useBetMutations } from '@/hooks/useBetMutations';
const { editBet } = useBetMutations();
```

- [ ] **Step 2: Replace both `api.editBet` calls**

Same pattern as MyBetsSection — replace `api.editBet(betId, changes)` with `editBet.mutateAsync({ betId, data: changes })`. Keep `fetchBets()` and `fetchStats()` calls since BetsPage manages its own local state.

- [ ] **Step 3: Verify + commit**

```bash
git add frontend/src/components/Terminal/pages/BetsPage.tsx
git commit -m "refactor: migrate BetsPage to useBetMutations for edit/cashout"
```

---

## Task 14: Final verification

- [ ] **Step 1: Full type check**

Run: `cd frontend && npx tsc --noEmit`
Expected: Clean compile, zero errors.

- [ ] **Step 2: Search for any remaining direct `api.createBet` or `api.editBet` calls**

Run: `grep -rn "api\.createBet\|api\.editBet\|api\.createBatchBets" frontend/src/components/ frontend/src/hooks/ --include="*.tsx" --include="*.ts" | grep -v node_modules | grep -v useBetMutations`

Expected: No results. All bet mutations should go through the hook now.

- [ ] **Step 3: Search for any remaining `fetchData` or `onRefresh` in BankrollPage**

Run: `grep -n "fetchData\|onRefresh" frontend/src/components/Terminal/pages/BankrollPage.tsx`

Expected: No results.

- [ ] **Step 4: Verify old query keys are gone**

Run: `grep -rn "bankroll-stats\|bankroll-status" frontend/src/ --include="*.tsx" --include="*.ts"`

Expected: No results.

- [ ] **Step 5: Build**

Run: `cd frontend && npm run build`
Expected: Clean build, no errors.

- [ ] **Step 6: Commit if any cleanup was needed**

```bash
git add -A frontend/src/
git commit -m "chore: final cleanup for reactive frontend migration"
```
