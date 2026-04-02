import { useConnectionStatus } from '@/hooks/useConnectionStatus';

/**
 * Shows extraction errors as a dismissible banner above page content.
 * Currently a no-op since the tiers progress endpoint was removed.
 */
export function ErrorNotificationBar() {
  return null;
}

/**
 * Shows a connection error banner when the backend API is unreachable.
 * Reads from the unified ConnectionManager — no independent polling.
 *
 * States:
 * - connecting: initial page load, backend not yet reached
 * - restarting: was connected, lost connection (deploy/restart)
 * - down: extended outage (>60s unreachable)
 */
export function ConnectionErrorBar() {
  const { status, message } = useConnectionStatus();

  if (status === 'checking') return null;

  if (status === 'connecting') {
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

  if (status === 'restarting') {
    return (
      <div
        className="mx-3 mt-2 border border-yellow-500/30 bg-gradient-to-br from-yellow-500/12 to-yellow-500/4 text-xs font-mono px-3 py-2 flex items-center gap-2"
        style={{ borderLeftWidth: 3, borderLeftColor: '#EAB308' }}
      >
        <span className="text-yellow-400 animate-pulse">↻</span>
        <span className="text-yellow-400">Backend restarting...</span>
        <span className="text-muted">Data may be stale until reconnected</span>
      </div>
    );
  }

  if (status === 'down') {
    return (
      <div
        className="mx-3 mt-2 border border-error/30 bg-gradient-to-br from-error/12 to-error/4 text-xs font-mono px-3 py-2 flex items-center gap-2"
        style={{ borderLeftWidth: 3, borderLeftColor: '#EF5350' }}
      >
        <span className="text-error font-bold text-sm">!</span>
        <span className="text-error">Backend unreachable</span>
        <span className="text-muted">{message}</span>
      </div>
    );
  }

  return null;
}
