import { useState, useEffect, useCallback } from 'react';
import type { BettingContext } from '@/types';
import { api } from '@/services/api';

const EMPTY_CONTEXT: BettingContext = {
  opportunities: [],
  events: [],
  providers: [],
  bankroll: {
    total: 0,
    providers: [],
  },
};

export function useBettingContext(refreshInterval = 30000) {
  const [context, setContext] = useState<BettingContext>(EMPTY_CONTEXT);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [opportunitiesRes, eventsRes, providersRes, bankrollRes] = await Promise.all([
        api.getOpportunities(),
        api.getEvents(),
        api.getProviders(),
        api.getBankroll(),
      ]);

      setContext({
        opportunities: opportunitiesRes.opportunities,
        events: eventsRes.events,
        providers: providersRes.providers,
        bankroll: bankrollRes,
      });
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
