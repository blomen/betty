import { useState, useEffect, useCallback } from 'react';
import type { BettingContext } from '@/types';
import { api } from '@/services/api';

const EMPTY_CONTEXT: BettingContext = {
  arbitrage: [],
  valueBets: [],
  events: [],
  providers: [],
};

export function useBettingContext(refreshInterval = 30000) {
  const [context, setContext] = useState<BettingContext>(EMPTY_CONTEXT);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [arbitrage, valueBets, events, providers] = await Promise.all([
        api.getArbitrage(),
        api.getValueBets(),
        api.getEvents(),
        api.getProviders(),
      ]);

      setContext({ arbitrage, valueBets, events, providers });
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load betting data');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();

    if (refreshInterval > 0) {
      const interval = setInterval(refresh, refreshInterval);
      return () => clearInterval(interval);
    }
  }, [refresh, refreshInterval]);

  return { context, isLoading, error, refresh };
}
