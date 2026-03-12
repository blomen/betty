import { useState, useEffect, useRef } from 'react';
import type { StreamTickEvent } from '@/types/market';

export function useMarketStream(symbol: string = 'NQ') {
  const [lastTick, setLastTick] = useState<StreamTickEvent | null>(null);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    const es = new EventSource(`/api/trading/market/stream?symbol=${symbol}`);
    esRef.current = es;

    es.addEventListener('tick', (e) => {
      setLastTick(JSON.parse(e.data));
    });
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);

    return () => {
      es.close();
      esRef.current = null;
      setConnected(false);
    };
  }, [symbol]);

  return { lastTick, connected };
}
