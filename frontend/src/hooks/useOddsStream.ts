import { useEffect, useRef, useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';

interface OpportunitiesResponse {
  opportunities: any[];
  [key: string]: any;
}

export function useOddsStream() {
  const queryClient = useQueryClient();
  const esRef = useRef<EventSource | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const delayRef = useRef(1000);

  const connect = useCallback(() => {
    esRef.current?.close();
    const es = new EventSource('/api/extraction/stream');
    esRef.current = es;

    const resetDelay = () => { delayRef.current = 1000; };

    es.addEventListener('opportunity_update', (e) => {
      resetDelay();
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
      resetDelay();
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
      resetDelay();
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
      resetDelay();
      queryClient.invalidateQueries({ queryKey: ['opportunities'], refetchType: 'active' });
      queryClient.invalidateQueries({ queryKey: ['providers'], refetchType: 'active' });
      queryClient.invalidateQueries({ queryKey: ['specials'], refetchType: 'active' });
    });

    es.onerror = () => {
      es.close();
      queryClient.invalidateQueries({ queryKey: ['opportunities'], refetchType: 'active' });
      queryClient.invalidateQueries({ queryKey: ['providers'], refetchType: 'active' });
      retryRef.current = setTimeout(() => {
        delayRef.current = Math.min(delayRef.current * 2, 30000);
        connect();
      }, delayRef.current);
    };
  }, [queryClient]);

  useEffect(() => {
    connect();
    return () => {
      if (retryRef.current) clearTimeout(retryRef.current);
      esRef.current?.close();
    };
  }, [connect]);
}
