import { useState, useEffect, useCallback } from 'react';
import type { ExtractionStatus } from '@/types';
import { api } from '@/services/api';

export function useExtraction(pollInterval = 5000) {
  const [status, setStatus] = useState<ExtractionStatus>({
    running: false,
    last_run: null,
    start_time: null,
    elapsed_seconds: 0,
    progress_pct: 0,
    total_events: 0,
    total_odds: 0,
    current_provider: null,
    completed_providers: 0,
    total_providers: 0,
    providers: {},
  });
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.getExtractionProgress();
      setStatus(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch extraction status');
    } finally {
      setIsLoading(false);
    }
  }, []);

  const runExtraction = useCallback(
    async (providers?: string) => {
      try {
        await api.runExtraction(providers);
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
