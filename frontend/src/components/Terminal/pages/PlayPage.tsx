import { useState, useCallback, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import { NetworkError, TimeoutError } from '@/services/api/client';
import type { BatchResult } from '@/types';
import { SettlePanel } from './play/SettlePanel';
import { CapitalPlanPanel } from './play/CapitalPlanPanel';
import { SessionBatchPanel } from './play/SessionBatchPanel';
import { ExecutionPanel } from './play/ExecutionPanel';
import { TabIcon, TAB_COLORS } from '../TabBar';

// ---------------------------------------------------------------------------
// Step definitions
// ---------------------------------------------------------------------------

type Step = 'settle' | 'batch' | 'capital' | 'execute';

const STEPS: { id: Step; label: string }[] = [
  { id: 'settle', label: 'Settle' },
  { id: 'batch', label: 'Batch' },
  { id: 'capital', label: 'Capital' },
  { id: 'execute', label: 'Fire' },
];

// ---------------------------------------------------------------------------
// PlayPage
// ---------------------------------------------------------------------------

export function PlayPage() {
  const queryClient = useQueryClient();
  const [step, setStep] = useState<Step | null>(null);
  const [excludedBets, setExcludedBets] = useState<string[]>([]);
  const [batchLocked, setBatchLocked] = useState(false);
  const [pendingCount, setPendingCount] = useState(0);
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

  // Fetch batch (only when past settle step)
  const {
    data: batchData,
    isLoading,
    error: batchError,
  } = useQuery<BatchResult>({
    queryKey: ['play-batch', excludedBets],
    queryFn: () => api.getPlayBatch(excludedBets.length > 0 ? excludedBets : undefined),
    staleTime: 60_000,
    refetchInterval: batchLocked ? false : 120_000,
    enabled: step !== null && step !== 'settle',
  });

  function batchErrorMessage(): string {
    if (!batchError) return 'No batch data available.';
    if (batchError instanceof NetworkError) return 'Backend is not reachable. Start the backend server first.';
    if (batchError instanceof TimeoutError) return 'Batch request timed out. Backend may be overloaded.';
    return batchError.message || 'Failed to load batch data.';
  }

  // Confirm capital — just rebuilds batch with mirror-synced balances
  const confirmCapital = useMutation({
    mutationFn: () => api.confirmCapital(),
    onSuccess: () => {
      setExcludedBets([]);
      queryClient.invalidateQueries({ queryKey: ['play-batch'] });
      queryClient.invalidateQueries({ queryKey: ['bankroll'] });
      setStep('execute');
    },
  });

  const handleRemoveBet = useCallback((betKey: string) => {
    setExcludedBets(prev => [...prev, betKey]);
  }, []);

  const handleConfirmCapital = useCallback(() => {
    confirmCapital.mutate();
  }, [confirmCapital]);

  const handleSkipCapital = useCallback(() => {
    setStep('execute');
  }, []);

  const handleLockBatch = useCallback(() => {
    setBatchLocked(true);
    setStep('capital');
  }, []);

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

      {/* Sub-tab selector — only show settle and batch */}
      <div className="flex gap-1 border-b border-border">
        {STEPS.filter(s => s.id === 'settle' || s.id === 'batch').map((s) => {
          const isActive = step === s.id;
          // Hide settle tab when no pending bets and not on settle step
          if (s.id === 'settle' && pendingCount === 0 && step !== 'settle') return null;
          return (
            <button
              key={s.id}
              onClick={() => setStep(s.id)}
              className={`px-3 py-1.5 text-xs font-medium transition-colors border-b-2 -mb-[1px] ${
                isActive
                  ? 'border-tabPlay text-tabPlay'
                  : 'border-transparent text-muted hover:text-text'
              }`}
            >
              {s.label}
              {s.id === 'settle' && pendingCount > 0 && (
                <span className="ml-1 text-muted">({pendingCount})</span>
              )}
              {s.id === 'batch' && batchData && (
                <span className="ml-1 text-muted">({batchData.batch.length})</span>
              )}
            </button>
          );
        })}
      </div>

      {/* Step content */}
      {step === 'settle' && (
        <SettlePanel
          onContinue={() => setStep('batch')}
          pendingCount={pendingCount}
          setPendingCount={setPendingCount}
        />
      )}

      {step === 'batch' && (
        <>
          {isLoading ? (
            <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
              Building batch...
            </div>
          ) : !batchData ? (
            <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
              {batchErrorMessage()}
            </div>
          ) : (
            <>
              <SessionBatchPanel
                batch={batchData.batch}
                summary={batchData.summary}
                wageringProjections={batchData.wagering_projections || []}
                onRemoveBet={handleRemoveBet}
              />

              <div className="flex items-center justify-end px-1 py-1">
                {batchData.batch.length > 0 && (
                  <button
                    className="px-4 py-1.5 text-xs bg-tabPlay text-bg font-medium hover:opacity-90 transition-opacity"
                    onClick={handleLockBatch}
                  >
                    Lock Batch ({batchData.batch.length} bets) →
                  </button>
                )}
              </div>
            </>
          )}
        </>
      )}

      {step === 'capital' && (
        <>
          {isLoading ? (
            <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
              Building batch...
            </div>
          ) : !batchData ? (
            <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
              {batchErrorMessage()}
            </div>
          ) : (
            <CapitalPlanPanel
              capitalPlan={batchData.capital_plan}
              balanceStatus={batchData.balance_status}
              onConfirm={handleConfirmCapital}
              onSkip={handleSkipCapital}
              isLoading={confirmCapital.isPending}
            />
          )}
        </>
      )}

      {step === 'execute' && batchData && (
        <ExecutionPanel
          batch={batchData.batch}
          wageringProjections={batchData.wagering_projections || []}
        />
      )}
    </div>
  );
}
