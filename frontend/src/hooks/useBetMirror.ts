import { useState, useEffect, useCallback, useRef } from 'react';
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
  const cooldownRef = useRef(false);

  const dismiss = useCallback((id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  const confirmSettlements = useCallback(async () => {
    try {
      await api.confirmMirrorSettlements();
      const summary = pendingSettlements;
      setPendingSettlements(null);
      cooldownRef.current = true;
      setTimeout(() => { cooldownRef.current = false; }, 5000);
      if (summary) {
        const net = summary.total_payout - summary.total_staked;
        const toast: MirroredBet = {
          id: Date.now() + Math.random(),
          status: 'settled',
          provider: summary.provider,
          event: `${summary.count} bet${summary.count !== 1 ? 's' : ''} settled: ${summary.wins}W ${summary.losses}L = ${net >= 0 ? '+' : ''}${net.toFixed(0)} kr`,
          market: null, outcome: null, odds: 0, stake: 0, matched: false,
          timestamp: Date.now(),
        };
        setToasts(prev => [...prev, toast]);
        setTimeout(() => setToasts(prev => prev.filter(t => t.id !== toast.id)), 5000);
      }
    } catch (err) {
      console.error('[mirror] confirm failed', err);
    }
  }, [pendingSettlements]);

  const rejectSettlements = useCallback(async () => {
    try {
      await api.rejectMirrorSettlements();
      setPendingSettlements(null);
    } catch (err) {
      console.error('[mirror] reject failed', err);
    }
  }, []);

  const esRef = useRef<EventSource | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const delayRef = useRef(1000);

  const connectMirror = useCallback(() => {
    esRef.current?.close();
    const es = new EventSource('/api/extraction/stream');
    esRef.current = es;

    const resetDelay = () => { delayRef.current = 1000; };

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
      resetDelay();
      addToast(JSON.parse(e.data));
    });

    es.addEventListener('bet_rejected', (e: MessageEvent) => {
      resetDelay();
      addToast({ ...JSON.parse(e.data), status: 'rejected' });
    });

    es.addEventListener('settlements_pending', (e: MessageEvent) => {
      resetDelay();
      if (cooldownRef.current) return;
      setSyncAvailable(null);
      setPendingSettlements(JSON.parse(e.data));
    });

    const shownProviders = new Set<string>();
    es.addEventListener('sync_available', (e: MessageEvent) => {
      resetDelay();
      const data = JSON.parse(e.data);
      // Only show toast once per provider per session
      if (shownProviders.has(data.provider)) return;
      shownProviders.add(data.provider);
      setSyncAvailable(data);
    });

    es.addEventListener('balance_synced', (e: MessageEvent) => {
      resetDelay();
      const data = JSON.parse(e.data);
      setSyncAvailable(prev => prev && prev.provider === data.provider
        ? { ...prev, balance: data.balance }
        : prev
      );
    });

    es.onerror = () => {
      es.close();
      retryRef.current = setTimeout(() => {
        delayRef.current = Math.min(delayRef.current * 2, 30000);
        connectMirror();
      }, delayRef.current);
    };
  }, []);

  useEffect(() => {
    connectMirror();
    return () => {
      if (retryRef.current) clearTimeout(retryRef.current);
      esRef.current?.close();
    };
  }, [connectMirror]);

  const dismissSync = useCallback(() => setSyncAvailable(null), []);

  return { toasts, dismiss, pendingSettlements, confirmSettlements, rejectSettlements, syncAvailable, dismissSync };
}
