import { useState, useCallback, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import { NetworkError, TimeoutError } from '@/services/api/client';
import type { ClusterBatchResult, AllocationResult } from '@/types';
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
  { id: 'capital', label: 'Capital Allocation' },
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
  const [skipSiblings, setSkipSiblings] = useState<string[]>([]);
  const [lockedAt, setLockedAt] = useState<number | null>(null);   // epoch ms
  const [lockTtl, setLockTtl] = useState<number>(1800);            // seconds
  const [budgetSek, setBudgetSek] = useState<number | undefined>(undefined);
  const [budgetUsdc, setBudgetUsdc] = useState<number | undefined>(undefined);
  // Track whether user has edited the budget (null = use default shortfall)
  const [budgetCommitted, setBudgetCommitted] = useState(false);

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

  // Fetch cluster-level batch (only when past settle step and not locked)
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
    refetchInterval: batchLocked ? false : 10_000,
    enabled: step !== null && step !== 'settle',
  });

  // Track committed skips (sent to backend) vs pending skips (local UI)
  const [committedSkips, setCommittedSkips] = useState<string[]>([]);

  // Fetch allocation (only when on capital step with locked batch)
  // Only refetches when committedSkips changes (via Recalc), not on every skip click
  const {
    data: allocationData,
    isLoading: allocLoading,
    error: allocError,
  } = useQuery<AllocationResult>({
    queryKey: ['play-allocate', committedSkips, budgetCommitted ? budgetSek : undefined, budgetCommitted ? budgetUsdc : undefined],
    queryFn: () => api.allocateCapital(
      committedSkips.length > 0 ? committedSkips : undefined,
      budgetCommitted ? budgetSek : undefined,
      budgetCommitted ? budgetUsdc : undefined,
    ),
    staleTime: 3_000,
    refetchInterval: committedSkips.length === skipSiblings.length && !budgetCommitted ? 5_000 : false,
    enabled: step === 'capital' && batchLocked,
  });

  function batchErrorMessage(): string {
    if (!batchError) return 'No batch data available.';
    if (batchError instanceof NetworkError) return 'Backend is not reachable. Start the backend server first.';
    if (batchError instanceof TimeoutError) return 'Batch request timed out. Backend may be overloaded.';
    return batchError.message || 'Failed to load batch data.';
  }

  // Lock batch on backend then move to capital step.
  // Set batchLocked=true on mutate to freeze refetch before the request fires.
  const lockBatch = useMutation({
    mutationFn: () => {
      if (!batchData) throw new Error('No batch to lock');
      return api.lockBatch(batchData.batch);
    },
    onMutate: () => {
      setBatchLocked(true);  // freeze refetch immediately
    },
    onSuccess: (data) => {
      setLockedAt(Date.now());
      if (data.ttl_seconds) setLockTtl(data.ttl_seconds);
      setStep('capital');
    },
    onError: () => {
      setBatchLocked(false);  // unfreeze on failure
    },
  });

  const handleRemoveBet = useCallback((betKey: string) => {
    setExcludedBets(prev => [...prev, betKey]);
  }, []);

  const handleLockBatch = useCallback(() => {
    lockBatch.mutate();
  }, [lockBatch]);

  const handleExecute = useCallback(() => {
    setStep('execute');
  }, []);

  const handleBackToBatch = useCallback(() => {
    // Clear locked batch on backend
    api.unlockBatch().catch(() => {});
    setBatchLocked(false);
    setLockedAt(null);
    setSkipSiblings([]);
    setCommittedSkips([]);
    setExcludedBets([]);
    setBudgetSek(undefined);
    setBudgetUsdc(undefined);
    setBudgetCommitted(false);
    queryClient.invalidateQueries({ queryKey: ['play-batch'] });
    setStep('batch');
  }, [queryClient]);

  const handleSkipSibling = useCallback((providerId: string) => {
    setSkipSiblings(prev => [...prev, providerId]);
  }, []);

  const handleUnskipSibling = useCallback((providerId: string) => {
    setSkipSiblings(prev => prev.filter(id => id !== providerId));
  }, []);

  // Commit pending skips → re-allocate same locked batch across fewer siblings.
  // Clear local skipSiblings after committing — the backend owns skip state
  // via committedSkips. UI starts fresh for any further skips.
  const handleRecalc = useCallback(() => {
    setCommittedSkips(prev => [...new Set([...prev, ...skipSiblings])]);
    setSkipSiblings([]);
    queryClient.invalidateQueries({ queryKey: ['play-allocate'] });
  }, [skipSiblings, queryClient]);

  // Budget recalc: user edited the deposit budget and pressed recalc
  const handleBudgetRecalc = useCallback((sek: number | undefined, usdc: number | undefined) => {
    setBudgetSek(sek);
    setBudgetUsdc(usdc);
    setBudgetCommitted(true);
    // Budget replaces manual skips — clear them so budget is the only constraint
    setSkipSiblings([]);
    setCommittedSkips([]);
    queryClient.invalidateQueries({ queryKey: ['play-allocate'] });
  }, [queryClient]);

  const hasPendingSkips = skipSiblings.length !== committedSkips.length
    || skipSiblings.some(s => !committedSkips.includes(s));

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
                    setBatchLocked(false);
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
            <>
              <SessionBatchPanel
                batch={batchData.batch}
                summary={batchData.summary}
                onRemoveBet={handleRemoveBet}
              />

              {batchData.batch.length > 0 && (
                <div className="flex items-center justify-end px-1 py-1 shrink-0">
                  <button
                    className="px-4 py-1.5 text-xs bg-tabPlay text-bg font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
                    onClick={handleLockBatch}
                    disabled={lockBatch.isPending}
                  >
                    {lockBatch.isPending ? 'Locking...' : `Lock Batch (${batchData.batch.length} bets) →`}
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {step === 'capital' && (
        <>
          {allocLoading && !allocationData ? (
            <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
              Allocating capital...
            </div>
          ) : allocError ? (
            <div className="text-muted text-sm py-8 text-center border border-border bg-panel">
              {allocError instanceof Error ? allocError.message : 'Failed to allocate capital.'}
            </div>
          ) : allocationData ? (
            <CapitalPlanPanel
              allocation={allocationData}
              onExecute={handleExecute}
              onBack={handleBackToBatch}
              onSkipSibling={handleSkipSibling}
              onUnskipSibling={handleUnskipSibling}
              onRecalc={handleRecalc}
              onBudgetRecalc={handleBudgetRecalc}
              hasPendingSkips={hasPendingSkips}
              skippedSiblings={skipSiblings}
              isLoading={allocLoading}
              lockedAt={lockedAt}
              lockTtlSeconds={lockTtl}
              onLockExpired={handleBackToBatch}
            />
          ) : null}
        </>
      )}

      {step === 'execute' && allocationData && (
        <ExecutionPanel
          batch={allocationData.allocated_batch}
          wageringProjections={allocationData.wagering_projections || []}
          onBack={() => setStep('capital')}
          onNewBatch={handleBackToBatch}
        />
      )}
    </div>
  );
}
