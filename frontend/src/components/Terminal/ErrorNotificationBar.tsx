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
export function ConnectionErrorBar() {
  const [offline, setOffline] = useState(false);
  const [lastError, setLastError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    // Poll faster when offline (5s) to detect recovery quickly, slower when online (30s)
    let intervalId: ReturnType<typeof setInterval>;

    async function check() {
      try {
        const controller = new AbortController();
        const tid = setTimeout(() => controller.abort(), 3000);
        const res = await fetch('/health', { signal: controller.signal });
        clearTimeout(tid);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        if (mounted) {
          setOffline(false);
          setLastError(null);
          // Switch to slow polling once recovered
          clearInterval(intervalId);
          intervalId = setInterval(check, 30_000);
        }
      } catch (err) {
        if (mounted) {
          setOffline(true);
          setLastError(err instanceof Error ? err.message : 'Connection failed');
          // Switch to fast polling to detect recovery
          clearInterval(intervalId);
          intervalId = setInterval(check, 5_000);
        }
      }
    }

    check();
    intervalId = setInterval(check, 30_000);
    return () => { mounted = false; clearInterval(intervalId); };
  }, []);

  if (!offline) return null;

  return (
    <div
      className="mx-3 mt-2 border border-error/30 bg-gradient-to-br from-error/12 to-error/4 text-xs font-mono px-3 py-2 flex items-center gap-2 shadow-[0_0_20px_rgba(239,83,80,0.08),0_4px_12px_rgba(0,0,0,0.3)]"
      style={{ borderLeftWidth: 3, borderLeftColor: '#EF5350' }}
    >
      <span className="text-error font-bold text-sm">!</span>
      <span className="text-error">Backend unreachable</span>
      <span className="text-muted">{lastError}</span>
    </div>
  );
}
