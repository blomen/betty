import { useState, useEffect, useRef, useCallback } from 'react';

export interface BetDetails {
  bet_id: number;
  event_id: string;
  display_home: string;
  display_away: string;
  sport: string;
  league?: string;
  start_time?: string;
  market: string;
  outcome: string;
  odds: number;
  fair_odds: number;
  edge_pct: number;
  stake: number;
  kelly_pct?: number;
  point?: number;
}

export type BettingLaneStatus = 'idle' | 'navigating' | 'filling' | 'ready' | 'placing';

export interface BettingLaneState {
  currentBet: BetDetails | null;
  upNext: BetDetails[];
  status: BettingLaneStatus;
  placeBet: () => Promise<void>;
  skipBet: () => Promise<void>;
}

export function useBettingLane(providerId: string | null): BettingLaneState {
  const [currentBet, setCurrentBet] = useState<BetDetails | null>(null);
  const [upNext, setUpNext] = useState<BetDetails[]>([]);
  const [status, setStatus] = useState<BettingLaneStatus>('idle');
  const esRef = useRef<EventSource | null>(null);

  const fetchNextBet = useCallback(async () => {
    if (!providerId) return;
    try {
      const res = await fetch(`/api/fire-window/next-bet?provider_id=${providerId}`);
      if (!res.ok) return;
      const data = await res.json();
      setCurrentBet(data.current_bet ?? null);
      setUpNext(data.up_next ?? []);
      setStatus(data.current_bet ? 'navigating' : 'idle');
    } catch (err) {
      console.error('[betting-lane] fetchNextBet failed', err);
    }
  }, [providerId]);

  const placeBet = useCallback(async () => {
    if (!currentBet) return;
    setStatus('placing');
    try {
      await fetch(`/api/fire-window/place-bet/${currentBet.bet_id}`, { method: 'POST' });
    } catch (err) {
      console.error('[betting-lane] placeBet failed', err);
      setStatus('ready');
    }
  }, [currentBet]);

  const skipBet = useCallback(async () => {
    if (!currentBet) return;
    try {
      await fetch(`/api/fire-window/skip-bet/${currentBet.bet_id}`, { method: 'POST' });
    } catch (err) {
      console.error('[betting-lane] skipBet failed', err);
    }
  }, [currentBet]);

  useEffect(() => {
    setCurrentBet(null);
    setUpNext([]);
    setStatus('idle');

    if (!providerId) return;

    fetchNextBet();

    esRef.current?.close();

    const es = new EventSource('/api/mirror/stream/actions');
    esRef.current = es;

    es.addEventListener('navigated', (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      if (data.provider_id !== providerId) return;
      setStatus('filling');
    });

    es.addEventListener('autofilled', (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      if (data.provider_id !== providerId) return;
      setStatus('ready');
    });

    es.addEventListener('bet_placed', (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      if (data.provider_id !== providerId) return;
      fetchNextBet();
    });

    es.addEventListener('bet_skipped', (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      if (data.provider_id !== providerId) return;
      fetchNextBet();
    });

    es.onerror = () => {
      es.close();
      esRef.current = null;
    };

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [providerId, fetchNextBet]);

  return { currentBet, upNext, status, placeBet, skipBet };
}
