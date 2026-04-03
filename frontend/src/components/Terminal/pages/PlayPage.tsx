import { useState, useCallback, useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import { NetworkError, TimeoutError } from '@/services/api/client';
import type { ClusterBatchResult } from '@/types';
import { SettlePanel } from './play/SettlePanel';
import { SessionBatchPanel } from './play/SessionBatchPanel';
import { ExecutionPanel } from './play/ExecutionPanel';
import { TabIcon, TAB_COLORS } from '../TabBar';

// ---------------------------------------------------------------------------
// Step definitions
// ---------------------------------------------------------------------------

type Step = 'settle' | 'batch' | 'execute';

const STEPS: { id: Step; label: string }[] = [
  { id: 'settle', label: 'Settle' },
  { id: 'batch', label: 'Batch' },
  { id: 'execute', label: 'Fire' },
];

// ---------------------------------------------------------------------------
// PlayPage
// ---------------------------------------------------------------------------

export function PlayPage() {
  const queryClient = useQueryClient();
  const [step, setStep] = useState<Step | null>(null);
  const [excludedBets, setExcludedBets] = useState<string[]>([]);
  const [pendingCount, setPendingCount] = useState(0);
  const [skipSiblings, setSkipSiblings] = useState<string[]>([]);
  // Snapshot of batch at fire time
  const [fireBatch, setFireBatch] = useState<any[] | null>(null);

  // Check pending bets on mount to decide initial step
  useEffect(() => {
    api.getPendingBets()
      .then((res) => {
        const count = res.total_pending ?? 0;
        setPendingCount(count);
        setStep(count > 0 ? 'settle' : 'batch');
      })
      .catch(() => setStep('batch'));
  }, []);

  // Lazy-start mirror on mount
  useEffect(() => {
    api.ensureMirrorStarted().catch(() => {});
  }, []);

  // Fetch cluster-level batch — always refreshes every 10s (no lock)
  const {
    data: batchData,
    isLoading,
    error: batchError,
  } = useQuery<ClusterBatchResult>({
    queryKey: ['play-batch', excludedBets, skipSiblings],
    queryFn: () => api.getPlayBatch(
      excludedBets.length > 0 ? excludedBets : undefined,
      skipSiblings.length > 0 ? skipSiblings : undefined,
    ),
    staleTime: 5_000,
    refetchInterval: step === 'batch' ? 10_000 : false,
    enabled: step !== null && step !== 'settle',
  });

  function batchErrorMessage(): string {
    if (!batchError) return 'No batch data available.';
    if (batchError instanceof NetworkError) return 'Backend is not reachable. Start the backend server first.';
    if (batchError instanceof TimeoutError) return 'Batch request timed out. Backend may be overloaded.';
    return batchError.message || 'Failed to load batch data.';
  }

  const handleRemoveBet = useCallback((betKey: string) => {
    setExcludedBets(prev => [...prev, betKey]);
  }, []);

  const handleFire = useCallback(() => {
    if (!batchData) return;
    // Send all bets — fire window streams all odds, fires what balance allows
    setFireBatch(batchData.batch);
    setStep('execute');
  }, [batchData]);

  const handleBackToBatch = useCallback(() => {
    setFireBatch(null);
    setSkipSiblings([]);
    setExcludedBets([]);
    queryClient.invalidateQueries({ queryKey: ['play-batch'] });
    setStep('batch');
  }, [queryClient]);

  if (!step) {
    return <div className="p-4 text-muted text-sm">Loading...</div>;
  }

  return (
    <div className="flex flex-col flex-1 min-h-0 gap-2">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="play" color={TAB_COLORS.play} size={16} />
          Play
        </h2>
      </div>

      {/* Sub-tab selector — only show when settle has pending bets */}
      {step === 'settle' && pendingCount > 0 && (
        <div className="flex gap-1 border-b border-border">
          {STEPS.filter(s => s.id === 'settle' || s.id === 'batch').map((s) => {
            const isActive = step === s.id;
            return (
              <button
                key={s.id}
                onClick={() => {
                  if (s.id === 'batch') {
                    queryClient.invalidateQueries({ queryKey: ['play-batch'] });
                  }
                  setStep(s.id);
                }}
                className={`px-3 py-1.5 text-xs font-medium transition-colors border-b-2 -mb-[1px] ${
                  isActive
                    ? 'border-tabPlay text-tabPlay'
                    : 'border-transparent text-muted hover:text-text'
                }`}
              >
                {s.label}
                {s.id === 'settle' && (
                  <span className="ml-1 text-muted">({pendingCount})</span>
                )}
              </button>
            );
          })}
        </div>
      )}

      {/* Step content */}
      {step === 'settle' && (
        <SettlePanel
          onContinue={() => setStep('batch')}
          pendingCount={pendingCount}
          setPendingCount={setPendingCount}
        />
      )}

      {step === 'batch' && (
        <div className="flex flex-col flex-1 min-h-0">
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
              onFire={handleFire}
            />
          )}
        </div>
      )}

      {step === 'execute' && fireBatch && (
        <ExecutionPanel
          batch={fireBatch}
          wageringProjections={batchData?.wagering_projections || []}
          onBack={() => setStep('batch')}
          onNewBatch={handleBackToBatch}
        />
      )}
    </div>
  );
}
