import { useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import type { BankrollInfo, BankrollStats, BankrollExposure } from '@/types';
import { api } from '@/services/api';

export function useBankrollQuery() {
  const queryClient = useQueryClient();

  // ─── Queries ───
  // Single combined fetch — one tunnel round-trip instead of three.
  // Per-card loading flags below derive from the same query so the page can
  // render Total Capital the moment the response lands.
  const { data: full, isLoading: fullLoading, error: infoError } = useQuery({
    queryKey: ['bankroll', 'full'],
    queryFn: () => api.getBankrollFull(),
    staleTime: 30_000,
  });

  const bankroll = full?.info;
  const stats = full?.stats;
  const exposure = full?.exposure;
  const infoLoading = fullLoading;
  const statsLoading = fullLoading;
  const exposureLoading = fullLoading;

  type FullPayload = { info: BankrollInfo; exposure: BankrollExposure; stats: BankrollStats };
  const FULL_KEY = ['bankroll', 'full'] as const;

  // ─── Helper: optimistic balance update on the combined payload ───
  const optimisticBalanceUpdate = (
    providerId: string,
    transform: (balance: number) => number,
  ) => {
    queryClient.setQueryData<FullPayload>(FULL_KEY, (old) => {
      if (!old) return old;
      let infoTotal = 0;
      const infoProviders = old.info.providers.map((p) => {
        const newBalance = p.id === providerId ? transform(p.balance) : p.balance;
        infoTotal += newBalance;
        return { ...p, balance: newBalance };
      });
      let exposureTotal = 0;
      const exposureProviders = old.exposure.providers.map((p) => {
        if (p.provider_id !== providerId) {
          exposureTotal += p.total_balance;
          return p;
        }
        const newBalance = transform(p.total_balance);
        const newAvailable = newBalance - p.pending_exposure;
        exposureTotal += newBalance;
        return { ...p, total_balance: newBalance, available: newAvailable };
      });
      const totalAvailable = exposureProviders.reduce((s, p) => s + p.available, 0);
      return {
        ...old,
        info: { ...old.info, total: infoTotal, providers: infoProviders },
        exposure: {
          ...old.exposure,
          total_balance: exposureTotal,
          total_available: totalAvailable,
          providers: exposureProviders,
        },
      };
    });
  };

  // ─── Mutations ───
  const allocateMutation = useMutation({
    mutationFn: (liquidAmount: number | null) => api.allocate(liquidAmount),
    retry: false,
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['bankroll'] });
    },
  });

  const { data: liquidData } = useQuery({
    queryKey: ['bankroll', 'liquid'],
    queryFn: () => api.getLiquidBalance(),
    staleTime: 60_000,
  });

  const setBalanceMutation = useMutation({
    mutationFn: ({ providerId, balance }: { providerId: string; balance: number }) =>
      api.setBalance(providerId, balance),
    retry: false,
    onMutate: async ({ providerId, balance }) => {
      await queryClient.cancelQueries({ queryKey: ['bankroll'] });
      const prevFull = queryClient.getQueryData<FullPayload>(FULL_KEY);
      optimisticBalanceUpdate(providerId, () => balance);
      return { prevFull };
    },
    onError: (_err, _vars, context) => {
      if (context?.prevFull) queryClient.setQueryData(FULL_KEY, context.prevFull);
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
      const prevFull = queryClient.getQueryData<FullPayload>(FULL_KEY);
      queryClient.setQueryData<FullPayload>(FULL_KEY, (old) => {
        if (!old) return old;
        const infoProviders = old.info.providers.map((p) => {
          if (providerIds && !providerIds.includes(p.id)) return p;
          return { ...p, balance };
        });
        return {
          ...old,
          info: {
            ...old.info,
            total: infoProviders.reduce((s, p) => s + p.balance, 0),
            providers: infoProviders,
          },
        };
      });
      return { prevFull };
    },
    onError: (_err, _vars, context) => {
      if (context?.prevFull) queryClient.setQueryData(FULL_KEY, context.prevFull);
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
      const prevFull = queryClient.getQueryData<FullPayload>(FULL_KEY);
      queryClient.setQueryData<FullPayload>(FULL_KEY, (old) => {
        if (!old) return old;
        return {
          ...old,
          info: { ...old.info, total: 0, providers: old.info.providers.map((p) => ({ ...p, balance: 0 })) },
        };
      });
      return { prevFull };
    },
    onError: (_err, _vars, context) => {
      if (context?.prevFull) queryClient.setQueryData(FULL_KEY, context.prevFull);
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
    isLoading: fullLoading,
    isInfoLoading: infoLoading,
    isStatsLoading: statsLoading,
    isExposureLoading: exposureLoading,
    error: infoError ? (infoError instanceof Error ? infoError.message : 'Failed to load bankroll data') : null,
    allocate: allocateMutation,
    liquidBalance: liquidData?.liquid_balance ?? 0,
    setBalance: setBalanceMutation,
    setAllBalances: setAllBalancesMutation,
    resetAllBalances: resetAllBalancesMutation,
    depositWithBonus: depositWithBonusMutation,
    refresh,
  };
}
