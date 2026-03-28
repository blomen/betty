import { useState, useCallback, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import { NetworkError, TimeoutError } from '@/services/api/client';
import type { BatchResult } from '@/types';
import { SettlePanel } from './play/SettlePanel';
import { CapitalPlanPanel } from './play/CapitalPlanPanel';
import { SessionBatchPanel } from './play/SessionBatchPanel';
import { ExecutionPanel } from './play/ExecutionPanel';

// ---------------------------------------------------------------------------
// Step definitions
// ---------------------------------------------------------------------------

type Step = 'settle' | 'capital' | 'batch' | 'execute';

const STEPS: { key: Step; label: string }[] = [
  { key: 'settle', label: 'Settle' },
  { key: 'batch', label: 'Session Batch' },
  { key: 'capital', label: 'Capital Plan' },
  { key: 'execute', label: 'Execute' },
];

function StepIndicator({
  current,
  onNavigate,
  pendingCount,
  mirrorRunning,
}: {
  current: Step;
  onNavigate: (s: Step) => void;
  pendingCount: number;
  mirrorRunning: boolean;
}) {
  const visibleSteps = pendingCount > 0 ? STEPS : STEPS.filter((s) => s.key !== 'settle');

  return (
    <div className="flex items-center gap-1 px-3 py-2 border-b border-border bg-dark-900">
      {/* Mirror status dot */}
      <div
        className={`w-2 h-2 rounded-full mr-2 ${mirrorRunning ? 'bg-success' : 'bg-dark-600'}`}
        title={mirrorRunning ? 'Mirror running' : 'Mirror not running'}
      />

      {visibleSteps.map((step, i) => {
        const isActive = step.key === current;
        const currentIdx = visibleSteps.findIndex((s) => s.key === current);
        const isPast = i < currentIdx;

        const label = step.key === 'settle' && pendingCount > 0
          ? `${step.label} (${pendingCount})`
          : step.label;

        return (
          <div key={step.key} className="flex items-center gap-1">
            {i > 0 && (
              <span className={`text-[10px] mx-1 ${isPast ? 'text-success' : 'text-dark-600'}`}>→</span>
            )}
            <button
              onClick={() => onNavigate(step.key)}
              className={`text-[11px] px-2 py-0.5 transition-colors ${
                isActive
                  ? 'text-success font-bold border-b border-success'
                  : isPast
                  ? 'text-success/60 hover:text-success cursor-pointer'
                  : 'text-dark-500 hover:text-dark-300 cursor-pointer'
              }`}
            >
              {isPast ? '✓ ' : isActive ? '● ' : '○ '}
              {label}
            </button>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// PlayPage
// ---------------------------------------------------------------------------

export function PlayPage() {
  const queryClient = useQueryClient();
  const [step, setStep] = useState<Step | null>(null);
  const [excludedBets, setExcludedBets] = useState<string[]>([]);
  const [pendingCount, setPendingCount] = useState(0);
  const [mirrorRunning, setMirrorRunning] = useState(false);

  // Check pending bets on mount to decide initial step
  useEffect(() => {
    api.getPendingBets()
      .then((res) => {
        const count = res.total_pending ?? 0;
        setPendingCount(count);
        setStep(count > 0 ? 'settle' : 'capital');
      })
      .catch(() => setStep('capital'));
  }, []);

  // Lazy-start mirror on mount
  useEffect(() => {
    api.ensureMirrorStarted()
      .then(() => setMirrorRunning(true))
      .catch(() => setMirrorRunning(false));
  }, []);

  // Fetch batch (only when past settle step)
  const {
    data: batchData,
    isLoading,
    error: batchError,
    refetch: rebuildBatch,
  } = useQuery<BatchResult>({
    queryKey: ['play-batch', excludedBets],
    queryFn: () => api.getPlayBatch(excludedBets.length > 0 ? excludedBets : undefined),
    staleTime: 60_000,
    refetchInterval: 120_000,
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
      setStep('batch');  // Back to batch to see updated allocation
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
    setStep('execute');
  }, []);

  if (!step) {
    return <div className="p-4 text-dark-400 text-sm">Loading...</div>;
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <StepIndicator
        current={step}
        onNavigate={setStep}
        pendingCount={pendingCount}
        mirrorRunning={mirrorRunning}
      />

      <div className="flex-1 min-h-0 overflow-y-auto">
        {step === 'settle' && (
          <SettlePanel
            onContinue={() => setStep('batch')}
            pendingCount={pendingCount}
            setPendingCount={setPendingCount}
          />
        )}

        {step === 'capital' && (
          <>
            {isLoading ? (
              <div className="p-4 text-dark-400 text-sm">Building batch...</div>
            ) : !batchData ? (
              <div className="p-4 text-dark-400 text-sm">{batchErrorMessage()}</div>
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

        {step === 'batch' && (
          <div className="flex flex-col flex-1 min-h-0">
            {isLoading ? (
              <div className="p-4 text-dark-400 text-sm">Building batch...</div>
            ) : !batchData ? (
              <div className="p-4 text-dark-400 text-sm">{batchErrorMessage()}</div>
            ) : (
              <>
                <SessionBatchPanel
                  batch={batchData.batch}
                  summary={batchData.summary}
                  wageringProjections={batchData.wagering_projections || []}
                  onRemoveBet={handleRemoveBet}
                />

                <div className="flex items-center justify-between px-3 py-2 border-t border-border bg-dark-900">
                  {pendingCount > 0 ? (
                    <button
                      className="px-3 py-1 text-xs text-dark-400 border border-dark-600 hover:bg-dark-800 transition-colors"
                      onClick={() => setStep('settle')}
                    >
                      ← Settle
                    </button>
                  ) : (
                    <div />
                  )}
                  <div className="flex items-center gap-2">
                    {batchData.capital_plan.actions.length > 0 && (
                      <button
                        className="px-4 py-1 text-xs bg-amber-500 text-black font-bold hover:opacity-90 transition-opacity"
                        onClick={() => setStep('capital')}
                      >
                        Capital Plan →
                      </button>
                    )}
                    {batchData.batch.length > 0 && (
                      <button
                        className="px-4 py-1 text-xs bg-success text-black font-bold hover:opacity-90 transition-opacity"
                        onClick={handleLockBatch}
                      >
                        Execute ({batchData.batch.length} bets) →
                      </button>
                    )}
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {step === 'execute' && batchData && (
          <div className="flex flex-col flex-1 min-h-0">
            <ExecutionPanel
              batch={batchData.batch}
              wageringProjections={batchData.wagering_projections || []}
            />

            <div className="flex items-center px-3 py-2 border-t border-border bg-dark-900">
              <button
                className="px-3 py-1 text-xs text-dark-400 border border-dark-600 hover:bg-dark-800 transition-colors"
                onClick={() => setStep('batch')}
              >
                ← Back to Batch
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
