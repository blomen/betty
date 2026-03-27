import { useState, useEffect, useRef, useCallback } from 'react';

export interface BackendHealth {
  status: 'ok' | 'slow' | 'down' | 'checking';
  latencyMs: number | null;
  lastCheck: number | null;
  streamConnected: boolean;
  streamLastTick: number | null;
  message: string;
}

const POLL_OK_MS = 10_000;     // Poll every 10s when healthy
const POLL_DOWN_MS = 3_000;    // Poll every 3s when down — detect recovery fast
const SLOW_THRESHOLD_MS = 2000;

export function useBackendHealth(sseConnected: boolean, lastTickTs: number | null): BackendHealth {
  const [health, setHealth] = useState<BackendHealth>({
    status: 'checking',
    latencyMs: null,
    lastCheck: null,
    streamConnected: sseConnected,
    streamLastTick: lastTickTs,
    message: 'Checking...',
  });
  const mountedRef = useRef(true);
  const statusRef = useRef<BackendHealth['status']>('checking');
  const intervalRef = useRef<ReturnType<typeof setInterval>>();

  const check = useCallback(async () => {
    const t0 = performance.now();
    let newStatus: BackendHealth['status'] = 'down';
    try {
      const controller = new AbortController();
      const tid = setTimeout(() => controller.abort(), 3000);
      const res = await fetch('/health', { signal: controller.signal });
      clearTimeout(tid);
      const latency = Math.round(performance.now() - t0);

      if (!mountedRef.current) return;

      if (!res.ok) {
        newStatus = 'down';
        setHealth(h => ({ ...h, status: 'down', latencyMs: latency, lastCheck: Date.now(), message: `API ${res.status}` }));
      } else if (latency > SLOW_THRESHOLD_MS) {
        newStatus = 'slow';
        setHealth(h => ({ ...h, status: 'slow', latencyMs: latency, lastCheck: Date.now(), message: `Event loop slow (${latency}ms)` }));
      } else {
        newStatus = 'ok';
        setHealth(h => ({ ...h, status: 'ok', latencyMs: latency, lastCheck: Date.now(), message: '' }));
      }
    } catch {
      const latency = Math.round(performance.now() - t0);
      if (!mountedRef.current) return;
      newStatus = 'down';
      setHealth(h => ({ ...h, status: 'down', latencyMs: latency, lastCheck: Date.now(), message: 'Backend unreachable' }));
    }

    // Adjust polling interval when status changes
    if (newStatus !== statusRef.current) {
      statusRef.current = newStatus;
      clearInterval(intervalRef.current);
      intervalRef.current = setInterval(check, newStatus === 'down' ? POLL_DOWN_MS : POLL_OK_MS);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    check();
    intervalRef.current = setInterval(check, POLL_OK_MS);
    return () => { mountedRef.current = false; clearInterval(intervalRef.current); };
  }, [check]);

  // Update stream fields reactively
  useEffect(() => {
    setHealth(h => ({ ...h, streamConnected: sseConnected, streamLastTick: lastTickTs }));
  }, [sseConnected, lastTickTs]);

  return health;
}
