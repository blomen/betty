import { useState, useCallback } from 'react';
import { api } from '@/services/api';
import type { BonusMatchRequest, BonusMatch } from '@/types';

/**
 * Hook for bonus mode functionality
 *
 * Provides state management and API calls for finding the best hedge
 * when placing bonus bets or qualifying bets.
 */
export function useBonusMode() {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BonusMatch | null>(null);

  const findHedge = useCallback(async (request: BonusMatchRequest) => {
    setIsLoading(true);
    setError(null);
    try {
      const match = await api.findBestHedge(request);
      setResult(match);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to find hedge');
      setResult(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const clearResult = useCallback(() => {
    setResult(null);
    setError(null);
  }, []);

  return {
    findHedge,
    clearResult,
    result,
    isLoading,
    error,
  };
}
