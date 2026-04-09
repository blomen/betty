import { useState, useEffect, useRef, useCallback } from 'react';

export interface ProviderQueueItem {
  id: string;
  state: 'active' | 'syncing' | 'queued' | 'done';
  betsRemaining: number;
}

export interface ProviderQueueState {
  providers: ProviderQueueItem[];
  activeProvider: string | null;
  preSyncing: string[];
}

const DEFAULT_STATE: ProviderQueueState = {
  providers: [],
  activeProvider: null,
  preSyncing: [],
};

export function useProviderQueue(): ProviderQueueState {
  const [state, setState] = useState<ProviderQueueState>(DEFAULT_STATE);
  const esRef = useRef<EventSource | null>(null);

  const fetchQueue = useCallback(async () => {
    try {
      const res = await fetch('/api/mirror/queue');
      if (!res.ok) return;
      const data = await res.json();
      const providers: ProviderQueueItem[] = (data.providers ?? []).map((p: any) => ({
        id: p.id,
        state: p.state,
        betsRemaining: p.bets_remaining ?? 0,
      }));
      const activeProvider = providers.find(p => p.state === 'active')?.id ?? null;
      const preSyncing = providers.filter(p => p.state === 'syncing').map(p => p.id);
      setState({ providers, activeProvider, preSyncing });
    } catch (err) {
      console.error('[provider-queue] fetchQueue failed', err);
    }
  }, []);

  useEffect(() => {
    fetchQueue();

    esRef.current?.close();

    const es = new EventSource('/api/mirror/stream/sync');
    esRef.current = es;

    es.addEventListener('provider_state', (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      setState(prev => {
        const updated = prev.providers.map(p =>
          p.id === data.provider_id
            ? { ...p, state: data.state, betsRemaining: data.bets_remaining ?? p.betsRemaining }
            : p
        );
        // If provider not yet in list, add it
        const exists = prev.providers.some(p => p.id === data.provider_id);
        const providers = exists
          ? updated
          : [
              ...prev.providers,
              {
                id: data.provider_id,
                state: data.state,
                betsRemaining: data.bets_remaining ?? 0,
              },
            ];
        const activeProvider = providers.find(p => p.state === 'active')?.id ?? null;
        const preSyncing = providers.filter(p => p.state === 'syncing').map(p => p.id);
        return { providers, activeProvider, preSyncing };
      });
    });

    es.onerror = () => {
      es.close();
      esRef.current = null;
    };

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [fetchQueue]);

  return state;
}
