import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { Bet } from '@/types';

interface BetsResponse {
  bets: Bet[];
  count: number;
}

type CreateBetData = Parameters<typeof api.createBet>[0];
type BatchBetLeg = Parameters<typeof api.createBatchBets>[0][number];
type EditBetData = Parameters<typeof api.editBet>[1];

export function useBetMutations() {
  const queryClient = useQueryClient();

  const invalidateBankrollAndOpps = () => {
    queryClient.invalidateQueries({ queryKey: ['bankroll', 'info'] });
    queryClient.invalidateQueries({ queryKey: ['bankroll', 'exposure'] });
    queryClient.invalidateQueries({ queryKey: ['opportunities'] });
    queryClient.invalidateQueries({ queryKey: ['cluster-summary'] });
    queryClient.invalidateQueries({ queryKey: ['clusters'] });
  };

  const placeBet = useMutation({
    mutationFn: (data: CreateBetData) => api.createBet(data),
    retry: false,
    onSuccess: (_result, data) => {
      queryClient.setQueryData<BetsResponse>(['bets', 'pending'], (old) => {
        if (!old) return old;
        const newBet: Partial<Bet> = {
          id: Date.now(),
          provider: data.provider_id,
          market: data.market ?? null,
          outcome: data.outcome ?? null,
          odds: data.odds,
          stake: data.stake,
          point: data.point ?? null,
          is_bonus: data.is_bonus ?? false,
          bonus_type: data.bonus_type ?? null,
          result: 'pending',
          payout: 0,
          profit: 0,
          roi_pct: 0,
          event_id: data.event_id ?? null,
          bet_type: data.bet_type ?? null,
          currency: data.provider_id === 'polymarket' ? 'USDC' : 'SEK',
          placed_at: new Date().toISOString(),
        };
        return {
          ...old,
          bets: [newBet as Bet, ...old.bets],
          count: old.count + 1,
        };
      });
      invalidateBankrollAndOpps();
      // Defer heavy bets list refetch so UI stays responsive after optimistic update
      setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ['bets'] });
      }, 500);
    },
  });

  const placeBatchBets = useMutation({
    mutationFn: (legs: BatchBetLeg[]) => api.createBatchBets(legs),
    retry: false,
    onSuccess: () => {
      invalidateBankrollAndOpps();
      setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ['bets'] });
      }, 500);
    },
  });

  const editBet = useMutation({
    mutationFn: ({ betId, data }: { betId: number; data: EditBetData }) =>
      api.editBet(betId, data),
    retry: false,
    onSuccess: (_result, { betId, data }) => {
      if (data.result && data.result !== 'pending') {
        queryClient.setQueryData<BetsResponse>(['bets', 'pending'], (old) => {
          if (!old) return old;
          return {
            ...old,
            bets: old.bets.filter((b) => b.id !== betId),
            count: old.count - 1,
          };
        });
        queryClient.invalidateQueries({ queryKey: ['bankroll', 'stats'] });
        queryClient.invalidateQueries({ queryKey: ['bankroll', 'info'] });
        queryClient.invalidateQueries({ queryKey: ['bankroll', 'exposure'] });
      } else if (data.odds !== undefined || data.stake !== undefined) {
        queryClient.setQueryData<BetsResponse>(['bets', 'pending'], (old) => {
          if (!old) return old;
          return {
            ...old,
            bets: old.bets.map((b) =>
              b.id === betId
                ? { ...b, ...(data.odds !== undefined && { odds: data.odds }), ...(data.stake !== undefined && { stake: data.stake }) }
                : b
            ),
          };
        });
      }
      // Only invalidate specific bet queries — avoid broad ['bets'] which
      // triggers refetches on every page (ValuePage, DutchPage, StatsPage, etc.)
      // BetsPage handles its own refresh via manual fetchBets() calls.
      queryClient.invalidateQueries({ queryKey: ['bets', 'pending'] });
      queryClient.invalidateQueries({ queryKey: ['bets', 'all'] });
    },
  });

  return { placeBet, placeBatchBets, editBet };
}
