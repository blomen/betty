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

    async function check() {
      try {
        const res = await fetch('/api/extraction/freshness');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        if (mounted) {
          setOffline(false);
          setLastError(null);
        }
      } catch (err) {
        if (mounted) {
          setOffline(true);
          setLastError(err instanceof Error ? err.message : 'Connection failed');
        }
      }
    }

    check();
    const id = setInterval(check, 30_000);
    return () => { mounted = false; clearInterval(id); };
  }, []);

  if (!offline) return null;

  return (
    <div className="mx-3 mt-2 border border-error/30 bg-error/10 text-xs font-mono px-3 py-1.5 flex items-center gap-2">
      <span className="text-error font-bold">!</span>
      <span className="text-error">Backend unreachable</span>
      <span className="text-muted">{lastError}</span>
    </div>
  );
}
