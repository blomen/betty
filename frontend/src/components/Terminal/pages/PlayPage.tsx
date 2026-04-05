import { useState, useCallback, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/services/api';
import { NetworkError, TimeoutError } from '@/services/api/client';
import type { ClusterBatchResult } from '@/types';
import { SessionBatchPanel } from './play/SessionBatchPanel';
import { TabIcon, TAB_COLORS } from '../TabBar';

export function PlayPage() {
  const [excludedBets, setExcludedBets] = useState<string[]>([]);

  // Lazy-start mirror on mount
  useEffect(() => {
    api.ensureMirrorStarted().catch(() => {});
  }, []);

  // Fetch batch — always refreshes every 10s
  const {
    data: batchData,
    isLoading,
    error: batchError,
  } = useQuery<ClusterBatchResult>({
    queryKey: ['play-batch', excludedBets],
    queryFn: () => api.getPlayBatch(
      excludedBets.length > 0 ? excludedBets : undefined,
    ),
    staleTime: 5_000,
    refetchInterval: 10_000,
    enabled: true,
  });

  function batchErrorMessage(): string {
    if (!batchError) return 'No batch data available.';
    if (batchError instanceof NetworkError) return 'Backend is not reachable.';
    if (batchError instanceof TimeoutError) return 'Batch request timed out.';
    return batchError.message || 'Failed to load batch data.';
  }

  const handleRemoveBet = useCallback((betKey: string) => {
    setExcludedBets(prev => [...prev, betKey]);
  }, []);

  return (
    <div className="flex flex-col flex-1 min-h-0 gap-2">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="play" color={TAB_COLORS.play} size={16} />
          Play
        </h2>
      </div>

      {isLoading ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
          Building batch...
        </div>
      ) : !batchData ? (
        <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
          {batchErrorMessage()}
        </div>
      ) : (
        <SessionBatchPanel
          batch={batchData.batch}
          summary={batchData.summary}
          providerBalances={(batchData as any).provider_balances}
          onRemoveBet={handleRemoveBet}
        />
      )}
    </div>
  );
}
