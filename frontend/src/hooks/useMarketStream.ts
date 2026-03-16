import { useState, useEffect, useRef } from 'react';
import type { StreamTickEvent, StreamBookEvent, CandleData } from '@/types/market';

export function useMarketStream(symbol: string = 'NQ') {
  const [lastTick, setLastTick] = useState<StreamTickEvent | null>(null);
  const [book, setBook] = useState<StreamBookEvent | null>(null);
  const [lastCandle, setLastCandle] = useState<CandleData | null>(null);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const tickBuffer = useRef<StreamTickEvent[]>([]);

  useEffect(() => {
    const es = new EventSource(`/api/trading/market/stream?symbol=${symbol}`);
    esRef.current = es;

    es.addEventListener('tick', (e) => {
      tickBuffer.current.push(JSON.parse(e.data));
    });

    es.addEventListener('book', (e) => {
      setBook(JSON.parse(e.data));
    });

    es.addEventListener('candle', (e) => {
      setLastCandle(JSON.parse(e.data));
    });

    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);

    const flushId = setInterval(() => {
      if (tickBuffer.current.length > 0) {
        setLastTick(tickBuffer.current[tickBuffer.current.length - 1]);
        tickBuffer.current = [];
      }
    }, 200);

    return () => {
      es.close();
      esRef.current = null;
      clearInterval(flushId);
      tickBuffer.current = [];
      setConnected(false);
    };
  }, [symbol]);

  return { lastTick, book, lastCandle, connected, esRef };
}
