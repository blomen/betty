import { useState, useEffect, useRef, useCallback } from 'react';
import type { StreamTickEvent, StreamBookEvent, CandleData } from '@/types/market';

export function useMarketStream(symbol: string = 'NQ') {
  const [lastTick, setLastTick] = useState<StreamTickEvent | null>(null);
  const [book, setBook] = useState<StreamBookEvent | null>(null);
  const [lastCandle, setLastCandle] = useState<CandleData | null>(null);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const tickBuffer = useRef<StreamTickEvent[]>([]);
  const retryRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const retryDelayRef = useRef(500);
  const mountedRef = useRef(true);
  const consecutiveErrorsRef = useRef(0);

  const connect = useCallback(() => {
    if (!mountedRef.current) return;

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

    es.onopen = () => {
      setConnected(true);
      retryDelayRef.current = 500; // reset backoff on success
      consecutiveErrorsRef.current = 0;
    };

    es.onerror = () => {
      consecutiveErrorsRef.current += 1;
      // Only show disconnected after 2+ consecutive errors (skip transient blips)
      if (consecutiveErrorsRef.current >= 2) {
        setConnected(false);
      }
      es.close();
      esRef.current = null;
      // Reconnect with exponential backoff (500ms → 1s → 2s → 4s → cap 8s)
      if (mountedRef.current) {
        retryRef.current = setTimeout(() => {
          retryDelayRef.current = Math.min(retryDelayRef.current * 2, 8_000);
          connect();
        }, retryDelayRef.current);
      }
    };
  }, [symbol]);

  useEffect(() => {
    mountedRef.current = true;
    connect();

    const flushId = setInterval(() => {
      if (tickBuffer.current.length > 0) {
        setLastTick(tickBuffer.current[tickBuffer.current.length - 1]);
        tickBuffer.current = [];
      }
    }, 500);

    return () => {
      mountedRef.current = false;
      clearTimeout(retryRef.current);
      esRef.current?.close();
      esRef.current = null;
      clearInterval(flushId);
      tickBuffer.current = [];
      setConnected(false);
    };
  }, [connect]);

  return { lastTick, book, lastCandle, connected, esRef };
}
