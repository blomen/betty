import { useState, useEffect } from 'react';

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
 * Polls /api/extraction/freshness — if it fails, shows the banner.
 */
const STARTUP_GRACE_MS = 30_000; // Don't show error banner during first 30s

export function ConnectionErrorBar() {
  const [offline, setOffline] = useState(false);
  const [lastError, setLastError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(true);

  useEffect(() => {
    let mounted = true;
    const mountTime = Date.now();
    let everConnected = false;
    let consecutiveFails = 0;
    let intervalId: ReturnType<typeof setInterval>;

    async function check() {
      try {
        const controller = new AbortController();
        const tid = setTimeout(() => controller.abort('Health check timeout'), 5000);
        const res = await fetch('/health', { signal: controller.signal });
        clearTimeout(tid);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        if (mounted) {
          everConnected = true;
          consecutiveFails = 0;
          setOffline(false);
          setConnecting(false);
          setLastError(null);
          clearInterval(intervalId);
          intervalId = setInterval(check, 30_000);
        }
      } catch (err) {
        if (mounted) {
          consecutiveFails++;
          const inGrace = !everConnected && Date.now() - mountTime < STARTUP_GRACE_MS;
          if (inGrace) {
            setConnecting(true);
            setOffline(false);
            setLastError(null);
          } else if (consecutiveFails >= 3) {
            setConnecting(false);
            setOffline(true);
            setLastError(err instanceof Error ? err.message : 'Connection failed');
          }
          // else: transient failure, don't change state yet
          clearInterval(intervalId);
          intervalId = setInterval(check, 5_000);
        }
      }
    }

    check();
    intervalId = setInterval(check, 30_000);
    return () => { mounted = false; clearInterval(intervalId); };
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
