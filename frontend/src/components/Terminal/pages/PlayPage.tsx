import { useState, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { BatchResult, CapitalAction } from '@/types';
import { CapitalPlanPanel } from './play/CapitalPlanPanel';
import { SessionBatchPanel } from './play/SessionBatchPanel';
import { ExecutionPanel } from './play/ExecutionPanel';

// ---------------------------------------------------------------------------
// Step definitions
// ---------------------------------------------------------------------------

type Step = 'capital' | 'batch' | 'execute';

const STEPS: { key: Step; label: string }[] = [
  { key: 'capital', label: 'Capital Plan' },
  { key: 'batch', label: 'Session Batch' },
  { key: 'execute', label: 'Execute' },
];

function StepIndicator({ current, onNavigate }: { current: Step; onNavigate: (s: Step) => void }) {
  return (
    <div className="flex items-center gap-1 px-3 py-2 border-b border-border bg-dark-900">
      {STEPS.map((step, i) => {
        const isActive = step.key === current;
        const currentIdx = STEPS.findIndex((s) => s.key === current);
        const isPast = i < currentIdx;

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
              {step.label}
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
  const [step, setStep] = useState<Step>('capital');
  const [excludedBets, setExcludedBets] = useState<string[]>([]);

  // Fetch batch
  const {
    data: batchData,
    isLoading,
    isFetching,
    refetch: rebuildBatch,
  } = useQuery<BatchResult>({
    queryKey: ['play-batch', excludedBets],
    queryFn: () => api.getPlayBatch(excludedBets.length > 0 ? excludedBets : undefined),
    staleTime: 60_000,
    refetchInterval: 120_000,
  });

  // Confirm capital mutation
  const confirmCapital = useMutation({
    mutationFn: (actions: CapitalAction[]) => api.confirmCapital(
      actions.map(a => ({
        type: a.type,
        provider_id: a.provider_id,
        from_provider_id: a.from_provider_id,
        to_provider_id: a.to_provider_id,
        amount: a.amount,
      }))
    ),
    onSuccess: () => {
      setExcludedBets([]);
      queryClient.invalidateQueries({ queryKey: ['play-batch'] });
      queryClient.invalidateQueries({ queryKey: ['bankroll'] });
      setStep('batch');
    },
  });

  const handleRemoveBet = useCallback((betKey: string) => {
    setExcludedBets(prev => [...prev, betKey]);
  }, []);

  const handleConfirmCapital = useCallback((actions: CapitalAction[]) => {
    confirmCapital.mutate(actions);
  }, [confirmCapital]);

  const handleSkipCapital = useCallback(() => {
    setStep('batch');
  }, []);

  const handleLockBatch = useCallback(() => {
    setStep('execute');
  }, []);

  if (isLoading) {
    return <div className="p-4 text-dark-400 text-sm">Building batch...</div>;
  }

  if (!batchData) {
    return <div className="p-4 text-dark-400 text-sm">No batch data available. Run extraction first.</div>;
  }

  const { batch, summary, capital_plan, wagering_projections } = batchData;

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {/* Step indicator */}
      <StepIndicator current={step} onNavigate={setStep} />

      {/* Step content */}
      <div className="flex-1 overflow-y-auto">
        {step === 'capital' && (
          <CapitalPlanPanel
            capitalPlan={capital_plan}
            onConfirm={handleConfirmCapital}
            onSkip={handleSkipCapital}
            isLoading={confirmCapital.isPending}
          />
        )}

        {step === 'batch' && (
          <div className="flex flex-col flex-1 min-h-0">
            <SessionBatchPanel
              batch={batch}
              summary={summary}
              wageringProjections={wagering_projections || []}
              onRemoveBet={handleRemoveBet}
            />

            {/* Action bar */}
            <div className="flex items-center justify-between px-3 py-2 border-t border-border bg-dark-900">
              <button
                className="px-3 py-1 text-xs text-dark-400 border border-dark-600 hover:bg-dark-800 transition-colors"
                onClick={() => setStep('capital')}
              >
                ← Capital Plan
              </button>
              <div className="flex items-center gap-2">
                <button
                  className="px-3 py-1 text-xs bg-dark-700 text-dark-300 border border-dark-600 hover:bg-dark-600"
                  onClick={() => { setExcludedBets([]); rebuildBatch(); }}
                  disabled={isFetching}
                >
                  {isFetching ? 'Rebuilding...' : 'Rebuild'}
                </button>
                {batch.length > 0 && (
                  <button
                    className="px-4 py-1 text-xs bg-success text-black font-bold hover:opacity-90 transition-opacity"
                    onClick={handleLockBatch}
                  >
                    Execute ({batch.length} bets) →
                  </button>
                )}
              </div>
            </div>
          </div>
        )}

        {step === 'execute' && (
          <div className="flex flex-col flex-1 min-h-0">
            <ExecutionPanel
              batch={batch}
              wageringProjections={wagering_projections || []}
            />

            {/* Back to batch */}
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
