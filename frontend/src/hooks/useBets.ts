import { useState, useEffect, useCallback } from 'react';
import type { Bet } from '@/types';
import { api } from '@/services/api';

export function useBets(status?: 'pending' | 'won' | 'lost' | 'void', refreshInterval = 30000) {
  const [bets, setBets] = useState<Bet[]>([]);
  const [count, setCount] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.getBets(status);
      setBets(data.bets);
      setCount(data.count);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load bets');
    } finally {
      setIsLoading(false);
    }
  }, [status]);

  const createBet = useCallback(
    async (betData: {
      event_id?: string;
      provider_id: string;
      market?: string;
      outcome?: string;
      odds: number;
      stake: number;
      is_bonus?: boolean;
      bonus_type?: string;
    }) => {
      try {
        await api.createBet(betData);
        await refresh();
      } catch (err) {
        throw err instanceof Error ? err : new Error('Failed to create bet');
      }
    },
    [refresh]
  );

  const settleBet = useCallback(
    async (betId: number, result: 'won' | 'lost' | 'void', payout: number) => {
      try {
        await api.settleBet(betId, { result, payout });
        await refresh();
      } catch (err) {
        throw err instanceof Error ? err : new Error('Failed to settle bet');
      }
    },
    [refresh]
  );

  useEffect(() => {
    refresh();

    if (refreshInterval > 0) {
      const interval = setInterval(refresh, refreshInterval);
      return () => clearInterval(interval);
    }
  }, [refresh, refreshInterval]);

  return {
    bets,
    count,
    isLoading,
    error,
    refresh,
    createBet,
    settleBet,
  };
}
