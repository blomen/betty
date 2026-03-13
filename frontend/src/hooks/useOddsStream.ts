import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';

export function useOddsStream() {
  const queryClient = useQueryClient();

  useEffect(() => {
    const es = new EventSource('/api/extraction/stream');

    es.addEventListener('opportunity_update', (e) => {
      const update = JSON.parse(e.data);
      const queryKey = ['opportunities', update.type];
      queryClient.setQueryData(queryKey, (old: any[] | undefined) =>
        old?.map((opp: any) => opp.id === update.id ? { ...opp, ...update } : opp)
      );
    });

    es.addEventListener('opportunity_added', (e) => {
      const opp = JSON.parse(e.data);
      const queryKey = ['opportunities', opp.type];
      queryClient.setQueryData(queryKey, (old: any[] | undefined) =>
        old ? [...old, opp] : [opp]
      );
    });

    es.addEventListener('opportunity_removed', (e) => {
      const { id, type } = JSON.parse(e.data);
      const queryKey = ['opportunities', type];
      queryClient.setQueryData(queryKey, (old: any[] | undefined) =>
        old?.filter((opp: any) => opp.id !== id)
      );
    });

    es.addEventListener('tier_complete', () => {
      queryClient.invalidateQueries({ queryKey: ['providers'] });
      queryClient.invalidateQueries({ queryKey: ['bankroll'] });
      queryClient.invalidateQueries({ queryKey: ['bets'] });
    });

    es.onerror = () => {
      queryClient.invalidateQueries({ queryKey: ['opportunities'] });
      queryClient.invalidateQueries({ queryKey: ['providers'] });
    };

    return () => es.close();
  }, [queryClient]);
}
