import { useState, useEffect, useCallback } from 'react';
import type { ProviderHealth } from '@/types';
import { api } from '@/services/api';

export function useProviderMonitor(refreshInterval = 60000) {
  const [providers, setProviders] = useState<Record<string, ProviderHealth>>({});
  const [summary, setSummary] = useState({
    total_providers: 0,
    healthy: 0,
    unhealthy: 0,
    critical: 0,
  });
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.monitorAllProviders();
      setProviders(data.providers);
      setSummary(data.summary);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch provider health');
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

  return {
    providers,
    summary,
    isLoading,
    error,
    refresh,
  };
}
