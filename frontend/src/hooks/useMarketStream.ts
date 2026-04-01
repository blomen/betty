import { useState, useEffect, useRef, useCallback } from 'react';
import { connectionManager } from '@/services/connectionManager';
import type { StreamTickEvent, StreamBookEvent, CandleData, StatisticsEvent } from '@/types/market';

export function useMarketStream(symbol: string = 'NQ') {
  const [lastTick, setLastTick] = useState<StreamTickEvent | null>(null);
  const [book, setBook] = useState<StreamBookEvent | null>(null);
  const [lastCandle, setLastCandle] = useState<CandleData | null>(null);
  const [statistics, setStatistics] = useState<StatisticsEvent | null>(null);
  const [connected, setConnected] = useState(false);
  const [connectionId, setConnectionId] = useState(0);
  const esRef = useRef<EventSource | null>(null);
  const tickBuffer = useRef<StreamTickEvent[]>([]);
  const retryRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const retryDelayRef = useRef(500);
  const mountedRef = useRef(true);

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

    es.addEventListener('statistics', (e) => {
      setStatistics(JSON.parse(e.data));
    });

    es.onopen = () => {
      setConnected(true);
      setConnectionId(id => id + 1);
      retryDelayRef.current = 500;
    };

    es.onerror = () => {
      setConnected(false);
      es.close();
      esRef.current = null;

      // Wait for backend health confirmation before reconnecting
      connectionManager.waitForUp().then(() => {
        if (!mountedRef.current) return;
        retryRef.current = setTimeout(() => {
          retryDelayRef.current = Math.min(retryDelayRef.current * 2, 8_000);
          connect();
        }, retryDelayRef.current);
      });
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

  return { lastTick, book, lastCandle, statistics, connected, esRef, connectionId };
}
