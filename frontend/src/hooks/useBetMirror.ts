import { useState, useEffect, useCallback } from 'react';

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

export function useBetMirror() {
  const [toasts, setToasts] = useState<MirroredBet[]>([]);

  const dismiss = useCallback((id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id));
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

    return () => es.close();
  }, []);

  return { toasts, dismiss };
}
