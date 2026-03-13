import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';

interface OpportunitiesResponse {
  opportunities: any[];
  [key: string]: any;
}

export function useOddsStream() {
  const queryClient = useQueryClient();

  useEffect(() => {
    const es = new EventSource('/api/extraction/stream');

    es.addEventListener('opportunity_update', (e) => {
      const update = JSON.parse(e.data);
      const queryKey = ['opportunities', update.type];
      queryClient.setQueryData<OpportunitiesResponse>(queryKey, (old) => {
        if (!old) return old;
        return {
          ...old,
          opportunities: old.opportunities.map((opp: any) =>
            opp.id === update.id ? { ...opp, ...update } : opp
          ),
        };
      });
    });

    es.addEventListener('opportunity_added', (e) => {
      const opp = JSON.parse(e.data);
      const queryKey = ['opportunities', opp.type];
      queryClient.setQueryData<OpportunitiesResponse>(queryKey, (old) => {
        if (!old) return { opportunities: [opp] };
        return {
          ...old,
          opportunities: [...old.opportunities, opp],
        };
      });
    });

    es.addEventListener('opportunity_removed', (e) => {
      const { id, type } = JSON.parse(e.data);
      const queryKey = ['opportunities', type];
      queryClient.setQueryData<OpportunitiesResponse>(queryKey, (old) => {
        if (!old) return old;
        return {
          ...old,
          opportunities: old.opportunities.filter((opp: any) => opp.id !== id),
        };
      });
    });

    es.addEventListener('tier_complete', () => {
      // Full refetch on tier completion to catch anything missed
      queryClient.invalidateQueries({ queryKey: ['opportunities'] });
      queryClient.invalidateQueries({ queryKey: ['providers'] });
      queryClient.invalidateQueries({ queryKey: ['bankroll'] });
      queryClient.invalidateQueries({ queryKey: ['bets'] });
    });

    es.onerror = () => {
      // SSE disconnected — invalidate to trigger refetch
      queryClient.invalidateQueries({ queryKey: ['opportunities'] });
      queryClient.invalidateQueries({ queryKey: ['providers'] });
    };

    return () => es.close();
  }, [queryClient]);
}
