import { useState, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { BatchResult, CapitalAction } from '@/types';
import { CapitalPlanPanel } from './play/CapitalPlanPanel';
import { SessionBatchPanel } from './play/SessionBatchPanel';
import { ExecutionPanel } from './play/ExecutionPanel';

export function PlayPage() {
  const queryClient = useQueryClient();
  const [excludedBets, setExcludedBets] = useState<string[]>([]);
  const [capitalDismissed, setCapitalDismissed] = useState(false);

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
    },
  });

  const handleRemoveBet = useCallback((betKey: string) => {
    setExcludedBets(prev => [...prev, betKey]);
  }, []);

  const handleConfirmCapital = useCallback((actions: CapitalAction[]) => {
    confirmCapital.mutate(actions);
  }, [confirmCapital]);

  if (isLoading) {
    return <div className="p-4 text-dark-400 text-sm">Building batch...</div>;
  }

  if (!batchData) {
    return <div className="p-4 text-dark-400 text-sm">No batch data available. Run extraction first.</div>;
  }

  const { batch, summary, capital_plan, wagering_projections } = batchData;

  return (
    <div className="p-3 space-y-2 overflow-y-auto flex-1">
      {/* Panel 1: Capital Plan */}
      {!capitalDismissed && capital_plan && capital_plan.actions?.length > 0 && (
        <CapitalPlanPanel
          capitalPlan={capital_plan}
          onConfirm={handleConfirmCapital}
          onDismissAll={() => setCapitalDismissed(true)}
          isLoading={confirmCapital.isPending}
        />
      )}

      {/* Panel 2: Session Batch */}
      <SessionBatchPanel
        batch={batch}
        summary={summary}
        wageringProjections={wagering_projections || []}
        onRemoveBet={handleRemoveBet}
      />

      {/* Panel 3: Execution */}
      {batch.length > 0 && (
        <ExecutionPanel
          batch={batch}
          wageringProjections={wagering_projections || []}
        />
      )}

      {/* Rebuild button */}
      <div className="flex justify-end pt-2">
        <button
          className="px-3 py-1 text-xs bg-dark-700 text-dark-300 border border-dark-600 rounded hover:bg-dark-600"
          onClick={() => { setExcludedBets([]); rebuildBatch(); }}
          disabled={isFetching}
        >
          {isFetching ? 'Rebuilding...' : 'Rebuild Batch'}
        </button>
      </div>
    </div>
  );
}
