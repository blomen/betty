import { useState, useEffect, useRef } from 'react';
import type { StreamTickEvent, StreamBookEvent } from '@/types/market';

export function useMarketStream(symbol: string = 'NQ') {
  const [lastTick, setLastTick] = useState<StreamTickEvent | null>(null);
  const [book, setBook] = useState<StreamBookEvent | null>(null);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    const es = new EventSource(`/api/trading/market/stream?symbol=${symbol}`);
    esRef.current = es;

    es.addEventListener('tick', (e) => {
      setLastTick(JSON.parse(e.data));
    });
    es.addEventListener('book', (e) => {
      setBook(JSON.parse(e.data));
    });
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);

    return () => {
      es.close();
      esRef.current = null;
      setConnected(false);
    };
  }, [symbol]);

  return { lastTick, book, connected };
}
