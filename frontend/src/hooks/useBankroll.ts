import { useState, useEffect, useCallback } from 'react';
import type { BankrollInfo, BankrollStats, BankrollExposure } from '@/types';
import { api } from '@/services/api';

export function useBankroll(refreshInterval = 30000) {
  const [bankroll, setBankroll] = useState<BankrollInfo>({
    total: 0,
    providers: [],
  });
  const [stats, setStats] = useState<BankrollStats>({
    total_bets: 0,
    wins: 0,
    losses: 0,
    voids: 0,
    total_deposited: 0,
    total_profit: 0,
    bet_profit: 0,
    freebet_profit: 0,
    bonus_profit: 0,
    roi_pct: 0,
    win_rate: 0,
    avg_clv: 0,
    clv_positive_pct: 0,
    clv_count: 0,
  });
  const [exposure, setExposure] = useState<BankrollExposure>({
    total_balance: 0,
    total_pending: 0,
    total_available: 0,
    providers: [],
  });
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [bankrollData, statsData, exposureData] = await Promise.all([
        api.getBankroll(),
        api.getBankrollStats(),
        api.getBankrollExposure(),
      ]);
      setBankroll(bankrollData);
      setStats(statsData);
      setExposure(exposureData);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load bankroll data');
    } finally {
      setIsLoading(false);
    }
  }, []);

  const setAllBalances = useCallback(
    async (balance: number, providerIds?: string[]) => {
      try {
        await api.setAllBalances(balance, providerIds);
        await refresh();
      } catch (err) {
        throw err instanceof Error ? err : new Error('Failed to set balances');
      }
    },
    [refresh]
  );

  const adjustBalance = useCallback(
    async (providerId: string, amount: number) => {
      try {
        await api.adjustBalance(providerId, amount);
        await refresh();
      } catch (err) {
        throw err instanceof Error ? err : new Error('Failed to adjust balance');
      }
    },
    [refresh]
  );

  const resetAllBalances = useCallback(async () => {
    try {
      await api.resetAllBalances();
      await refresh();
    } catch (err) {
      throw err instanceof Error ? err : new Error('Failed to reset balances');
    }
  }, [refresh]);

  useEffect(() => {
    refresh();

    if (refreshInterval > 0) {
      const interval = setInterval(refresh, refreshInterval);
      return () => clearInterval(interval);
    }
  }, [refresh, refreshInterval]);

  return {
    bankroll,
    stats,
    exposure,
    isLoading,
    error,
    refresh,
    setAllBalances,
    adjustBalance,
    resetAllBalances,
  };
}
