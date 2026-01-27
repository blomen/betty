import { useState, useEffect, useCallback } from 'react';
import type { ExtractionStatus } from '@/types';
import { api } from '@/services/api';

export function useExtraction(pollInterval = 5000) {
  const [status, setStatus] = useState<ExtractionStatus>({
    running: false,
    last_run: null,
    events: 0,
    odds: 0,
  });
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.getExtractionStatus();
      setStatus(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch extraction status');
    } finally {
      setIsLoading(false);
    }
  }, []);

  const runExtraction = useCallback(
    async (providers?: string, sport?: string, maxGroups?: number) => {
      try {
        await api.runExtraction(providers, sport, maxGroups);
        await refresh();
      } catch (err) {
        throw err instanceof Error ? err : new Error('Failed to run extraction');
      }
    },
    [refresh]
  );

  useEffect(() => {
    refresh();

    if (pollInterval > 0) {
      const interval = setInterval(refresh, pollInterval);
      return () => clearInterval(interval);
    }
  }, [refresh, pollInterval]);

  return {
    status,
    isLoading,
    error,
    refresh,
    runExtraction,
  };
}
