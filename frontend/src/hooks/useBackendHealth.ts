import { useState, useEffect, useRef, useCallback } from 'react';

export interface BackendHealth {
  status: 'ok' | 'slow' | 'down' | 'connecting' | 'checking';
  latencyMs: number | null;
  lastCheck: number | null;
  streamConnected: boolean;
  streamLastTick: number | null;
  message: string;
}

const POLL_OK_MS = 10_000;     // Poll every 10s when healthy
const POLL_DOWN_MS = 3_000;    // Poll every 3s when down — detect recovery fast
const SLOW_THRESHOLD_MS = 3000; // Windows asyncio has occasional spikes — 3s avoids false alarms
const STARTUP_GRACE_MS = 30_000; // First 30s: show "connecting" not "down"

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
  const intervalRef = useRef<ReturnType<typeof setInterval>>(undefined);
  const mountTimeRef = useRef(Date.now());
  const everConnectedRef = useRef(false);
  const consecutiveFailsRef = useRef(0);

  const check = useCallback(async () => {
    const t0 = performance.now();
    let newStatus: BackendHealth['status'] = 'down';
    let message = '';
    try {
      const controller = new AbortController();
      const tid = setTimeout(() => controller.abort('Health check timeout'), 5000);
      const res = await fetch('/health', { signal: controller.signal });
      clearTimeout(tid);
      const latency = Math.round(performance.now() - t0);

      if (!mountedRef.current) return;

      if (!res.ok) {
        consecutiveFailsRef.current += 1;
        // Require 2+ consecutive failures to avoid false alarms from transient spikes
        if (consecutiveFailsRef.current >= 2) {
          newStatus = 'down';
          message = `API ${res.status}`;
        } else {
          newStatus = statusRef.current === 'checking' ? 'checking' : 'ok';
          message = '';
        }
      } else if (latency > SLOW_THRESHOLD_MS) {
        consecutiveFailsRef.current += 1;
        // During startup grace period, slow responses are normal — treat as connecting
        const inGrace = !everConnectedRef.current && Date.now() - mountTimeRef.current < STARTUP_GRACE_MS;
        if (inGrace) {
          newStatus = 'connecting';
          message = `Starting up (${latency}ms)`;
        } else if (consecutiveFailsRef.current >= 2) {
          newStatus = 'slow';
          message = `Event loop slow (${latency}ms)`;
        } else {
          // Single slow response — don't alarm, keep previous status
          newStatus = statusRef.current === 'checking' ? 'checking' : 'ok';
          message = '';
        }
      } else {
        newStatus = 'ok';
        consecutiveFailsRef.current = 0;
        everConnectedRef.current = true;
      }
      setHealth(h => ({ ...h, status: newStatus, latencyMs: latency, lastCheck: Date.now(), message }));
    } catch {
      const latency = Math.round(performance.now() - t0);
      if (!mountedRef.current) return;
      consecutiveFailsRef.current += 1;
      // During startup grace period, unreachable = still connecting
      const inGrace = !everConnectedRef.current && Date.now() - mountTimeRef.current < STARTUP_GRACE_MS;
      if (inGrace) {
        newStatus = 'connecting';
        message = 'Waiting for backend...';
      } else if (consecutiveFailsRef.current >= 2) {
        newStatus = 'down';
        message = 'Backend unreachable';
      } else {
        // Single timeout — don't alarm yet, retry on next poll
        newStatus = statusRef.current === 'checking' ? 'checking' : statusRef.current;
        message = '';
      }
      setHealth(h => ({ ...h, status: newStatus, latencyMs: latency, lastCheck: Date.now(), message }));
    }

    // Adjust polling interval when status changes
    if (newStatus !== statusRef.current) {
      statusRef.current = newStatus;
      clearInterval(intervalRef.current);
      intervalRef.current = setInterval(check, newStatus === 'down' || newStatus === 'connecting' ? POLL_DOWN_MS : POLL_OK_MS);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    mountTimeRef.current = Date.now();
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
