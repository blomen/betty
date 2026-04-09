import { useState, useEffect, useRef, useCallback } from 'react';

export interface PendingBet {
  id: number;
  event_id: string;
  market: string;
  outcome: string;
  odds: number;
  stake: number;
}

export interface Settlement {
  id: number;
  bet_id: number | null;
  result: 'won' | 'lost' | 'void';
  payout: number;
  detected_at: string;
}

interface SyncState {
  balance: { amount: number; currency: string; updatedAt: string | null };
  pendingBets: PendingBet[];
  settlements: Settlement[];
  notifications: { email: boolean; sms: boolean; push: boolean };
  connected: boolean;
}

const DEFAULT_STATE: SyncState = {
  balance: { amount: 0, currency: '', updatedAt: null },
  pendingBets: [],
  settlements: [],
  notifications: { email: false, sms: false, push: false },
  connected: false,
};

export function useSyncStream(providerId: string | null): SyncState {
  const [state, setState] = useState<SyncState>(DEFAULT_STATE);
  const esRef = useRef<EventSource | null>(null);

  const fetchBootstrap = useCallback(async (id: string) => {
    try {
      const res = await fetch(`/api/mirror/state/${id}`);
      if (!res.ok) return;
      const data = await res.json();
      setState(prev => ({
        ...prev,
        balance: data.balance ?? prev.balance,
        pendingBets: data.pending_bets ?? prev.pendingBets,
        notifications: data.notifications ?? prev.notifications,
      }));
    } catch (err) {
      console.error('[sync-stream] bootstrap fetch failed', err);
    }
  }, []);

  // Bootstrap + SSE setup — re-runs when providerId changes
  useEffect(() => {
    // Reset state on provider change
    setState(DEFAULT_STATE);

    if (!providerId) return;

    fetchBootstrap(providerId);

    // Close any existing EventSource before opening a new one
    esRef.current?.close();

    const es = new EventSource('/api/mirror/stream/sync');
    esRef.current = es;

    es.onopen = () => {
      setState(prev => ({ ...prev, connected: true }));
    };

    es.onerror = () => {
      setState(prev => ({ ...prev, connected: false }));
      es.close();
    };

    es.addEventListener('balance_update', (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      if (data.provider_id !== providerId) return;
      setState(prev => ({
        ...prev,
        balance: {
          amount: data.amount,
          currency: data.currency,
          updatedAt: data.updated_at ?? new Date().toISOString(),
        },
      }));
    });

    es.addEventListener('history_update', (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      if (data.provider_id !== providerId) return;
      fetchBootstrap(providerId);
    });

    es.addEventListener('settlement_pending', (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      if (data.provider_id !== providerId) return;
      const settlement: Settlement = {
        id: data.id,
        bet_id: data.bet_id ?? null,
        result: data.result,
        payout: data.payout,
        detected_at: data.detected_at ?? new Date().toISOString(),
      };
      setState(prev => ({
        ...prev,
        settlements: [...prev.settlements, settlement],
      }));
    });

    es.addEventListener('settlement_confirmed', (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      if (data.provider_id !== providerId) return;
      setState(prev => ({ ...prev, settlements: [] }));
      fetchBootstrap(providerId);
    });

    es.addEventListener('notification_status', (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      if (data.provider_id !== providerId) return;
      setState(prev => ({
        ...prev,
        notifications: {
          email: data.email ?? prev.notifications.email,
          sms: data.sms ?? prev.notifications.sms,
          push: data.push ?? prev.notifications.push,
        },
      }));
    });

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [providerId, fetchBootstrap]);

  return state;
}
