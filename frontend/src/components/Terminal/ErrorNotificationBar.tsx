import { useState, useEffect, useRef } from 'react';

/**
 * Shows extraction errors as a dismissible banner above page content.
 * Currently a no-op since the tiers progress endpoint was removed.
 * Will be re-wired to a new error source in a future iteration.
 */
export function ErrorNotificationBar() {
  return null;
}

/**
 * Shows a connection error banner when the backend API is unreachable.
 * Polls /health with exponential backoff. Recovers on first success.
 */
const STARTUP_GRACE_MS = 30_000;

export function ConnectionErrorBar() {
  const [offline, setOffline] = useState(false);
  const [connecting, setConnecting] = useState(true);
  const [lastError, setLastError] = useState<string | null>(null);
  const mountedRef = useRef(true);
  const mountTimeRef = useRef(Date.now());
  const everConnectedRef = useRef(false);
  const consecutiveFailsRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    mountedRef.current = true;
    mountTimeRef.current = Date.now();

    async function check() {
      try {
        const controller = new AbortController();
        const tid = setTimeout(() => controller.abort(), 5000);
        const res = await fetch('/health', { signal: controller.signal });
        clearTimeout(tid);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        if (!mountedRef.current) return;
        everConnectedRef.current = true;
        consecutiveFailsRef.current = 0;
        setOffline(false);
        setConnecting(false);
        setLastError(null);
        // Healthy: poll infrequently
        timerRef.current = setTimeout(check, 30_000);
      } catch (err) {
        if (!mountedRef.current) return;
        consecutiveFailsRef.current++;
        const inGrace = !everConnectedRef.current && Date.now() - mountTimeRef.current < STARTUP_GRACE_MS;
        if (inGrace) {
          setConnecting(true);
          setOffline(false);
        } else if (consecutiveFailsRef.current >= 3) {
          setConnecting(false);
          setOffline(true);
          setLastError(err instanceof Error ? err.message : 'Connection failed');
        }
        // Unhealthy: poll more frequently
        timerRef.current = setTimeout(check, 3_000);
      }
    }

    check();
    return () => { mountedRef.current = false; clearTimeout(timerRef.current); };
  }, []);

  if (connecting) {
    return (
      <div
        className="mx-3 mt-2 border border-orange-500/30 bg-gradient-to-br from-orange-500/12 to-orange-500/4 text-xs font-mono px-3 py-2 flex items-center gap-2"
        style={{ borderLeftWidth: 3, borderLeftColor: '#F97316' }}
      >
        <span className="text-orange-400 animate-pulse">●</span>
        <span className="text-orange-400">Connecting to backend...</span>
      </div>
    );
  }

  if (!offline) return null;

  return (
    <div
      className="mx-3 mt-2 border border-error/30 bg-gradient-to-br from-error/12 to-error/4 text-xs font-mono px-3 py-2 flex items-center gap-2 "
      style={{ borderLeftWidth: 3, borderLeftColor: '#EF5350' }}
    >
      <span className="text-error font-bold text-sm">!</span>
      <span className="text-error">Backend unreachable</span>
      <span className="text-muted">{lastError}</span>
    </div>
  );
}
