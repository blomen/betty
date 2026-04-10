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
    staleTime: 30_000,
  });

  const { data: stats, isLoading: statsLoading } = useQuery({
    queryKey: ['bankroll', 'stats'],
    queryFn: () => api.getBankrollStats(),
    staleTime: 60_000,
  });

  const { data: exposure, isLoading: exposureLoading } = useQuery({
    queryKey: ['bankroll', 'exposure'],
    queryFn: () => api.getBankrollExposure(),
    staleTime: 30_000,
  });

  // ─── Helper: optimistic balance update for info + exposure ───
  const optimisticBalanceUpdate = (
    providerId: string,
    transform: (balance: number) => number,
  ) => {
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
  const allocateMutation = useMutation({
    mutationFn: (liquidAmount: number) => api.allocate(liquidAmount),
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
    isLoading: infoLoading || statsLoading || exposureLoading,
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
