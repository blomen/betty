import { useState, useEffect, useCallback } from 'react';
import { api } from '../services/api';

export interface MirroredBet {
  id: number;
  status: string;
  confirmation_id?: string;
  provider: string;
  event: string;
  market: string | null;
  outcome: string | null;
  odds: number;
  stake: number;
  matched: boolean;
  error?: string;
  timestamp: number;
}

export interface PendingSettlement {
  bet_id: number;
  provider: string;
  event: string;
  odds: number;
  stake: number;
  result: string;
  payout: number;
}

export interface SettlementSummary {
  provider: string;
  count: number;
  wins: number;
  losses: number;
  total_staked: number;
  total_payout: number;
  net: number;
  settlements: PendingSettlement[];
}

export interface SyncAvailable {
  provider: string;
  balance: number;
  pending_bets: number;
  pending_stake: number;
}

export function useBetMirror() {
  const [toasts, setToasts] = useState<MirroredBet[]>([]);
  const [pendingSettlements, setPendingSettlements] = useState<SettlementSummary | null>(null);
  const [syncAvailable, setSyncAvailable] = useState<SyncAvailable | null>(null);

  const dismiss = useCallback((id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  const confirmSettlements = useCallback(async () => {
    try {
      await api.confirmMirrorSettlements();
      setPendingSettlements(null);
    } catch (err) {
      console.error('[mirror] confirm failed', err);
    }
  }, []);

  const rejectSettlements = useCallback(async () => {
    try {
      await api.rejectMirrorSettlements();
      setPendingSettlements(null);
    } catch (err) {
      console.error('[mirror] reject failed', err);
    }
  }, []);

  useEffect(() => {
    const es = new EventSource('/api/extraction/stream');

    const addToast = (data: Partial<MirroredBet>) => {
      const toast: MirroredBet = {
        id: Date.now() + Math.random(),
        status: 'ok',
        provider: '',
        event: '',
        market: null,
        outcome: null,
        odds: 0,
        stake: 0,
        matched: false,
        timestamp: Date.now(),
        ...data,
      };
      setToasts(prev => [...prev, toast]);
      setTimeout(() => {
        setToasts(prev => prev.filter(t => t.id !== toast.id));
      }, 5000);
    };

    es.addEventListener('bet_mirrored', (e: MessageEvent) => {
      addToast(JSON.parse(e.data));
    });

    es.addEventListener('bet_rejected', (e: MessageEvent) => {
      addToast({ ...JSON.parse(e.data), status: 'rejected' });
    });

    es.addEventListener('settlements_pending', (e: MessageEvent) => {
      setSyncAvailable(null); // Replace sync banner with settlement breakdown
      setPendingSettlements(JSON.parse(e.data));
    });

    es.addEventListener('sync_available', (e: MessageEvent) => {
      setSyncAvailable(JSON.parse(e.data));
    });

    es.addEventListener('balance_synced', (e: MessageEvent) => {
      // Update sync banner with new balance if still showing
      const data = JSON.parse(e.data);
      setSyncAvailable(prev => prev && prev.provider === data.provider
        ? { ...prev, balance: data.balance }
        : prev
      );
    });

    return () => es.close();
  }, []);

  const dismissSync = useCallback(() => setSyncAvailable(null), []);

  return { toasts, dismiss, pendingSettlements, confirmSettlements, rejectSettlements, syncAvailable, dismissSync };
}
