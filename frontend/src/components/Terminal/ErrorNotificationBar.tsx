import { useState, useEffect, useRef } from 'react';
import { useTiersProgress } from '@/hooks/useExtractionStatus';
import { formatProviderName } from '@/utils/formatters';

interface FailedProvider {
  provider: string;
  tier: string;
  error: string;
}

/**
 * Shows extraction errors as a dismissible banner above page content.
 * Auto-appears when providers fail during extraction.
 * Collapsible to show/hide error details.
 */
export function ErrorNotificationBar() {
  const tiersProgress = useTiersProgress();
  const [dismissed, setDismissed] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const prevErrorKeyRef = useRef<string>('');

  // Collect failed providers across all tiers
  const failures: FailedProvider[] = [];
  if (tiersProgress?.tiers) {
    for (const [tierName, tier] of Object.entries(tiersProgress.tiers)) {
      for (const [pid, prov] of Object.entries(tier.providers || {})) {
        if (prov.status === 'failed' && prov.error) {
          failures.push({ provider: pid, tier: tierName, error: prov.error });
        }
      }
    }
  }

  // Auto-show when new errors appear (reset dismissed state)
  const errorKey = failures.map(f => `${f.provider}:${f.tier}`).sort().join(',');
  useEffect(() => {
    if (errorKey && errorKey !== prevErrorKeyRef.current) {
      setDismissed(false);
    }
    prevErrorKeyRef.current = errorKey;
  }, [errorKey]);

  if (failures.length === 0 || dismissed) return null;

  return (
    <div className="mx-3 mt-2 border border-error/30 bg-error/10 text-xs font-mono animate-fadeIn">
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="text-error font-bold">!</span>
        <button
          onClick={() => setExpanded(e => !e)}
          className="text-error hover:text-text transition-colors flex items-center gap-1"
        >
          <span>{failures.length} extraction error{failures.length > 1 ? 's' : ''}</span>
          <span className="text-[10px] text-muted">{expanded ? '[-]' : '[+]'}</span>
        </button>
        <span className="text-muted">
          ({failures.map(f => formatProviderName(f.provider)).join(', ')})
        </span>
        <button
          onClick={() => setDismissed(true)}
          className="ml-auto text-muted hover:text-error transition-colors"
          title="Dismiss"
        >
          x
        </button>
      </div>
      {expanded && (
        <div className="border-t border-error/20 px-3 py-1.5 space-y-1">
          {failures.map(f => (
            <div key={`${f.tier}-${f.provider}`} className="flex gap-2">
              <span className="text-muted shrink-0">[{f.tier}]</span>
              <span className="text-error shrink-0">{formatProviderName(f.provider)}</span>
              <span className="text-muted truncate" title={f.error}>{f.error}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * Shows a connection error banner when the backend API is unreachable.
 * Polls /api/extraction/progress — if it fails, shows the banner.
 */
export function ConnectionErrorBar() {
  const [offline, setOffline] = useState(false);
  const [lastError, setLastError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;

    async function check() {
      try {
        const res = await fetch('/api/extraction/progress');
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
    const id = setInterval(check, 10_000);
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
