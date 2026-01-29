import { useEffect, useState, useRef } from 'react';
import { api } from '@/services/api';
import type { ExtractionStatus } from '@/types';

interface ExtractionProgressMessageProps {
  onComplete: () => void;
}

export function ExtractionProgressMessage({ onComplete }: ExtractionProgressMessageProps) {
  const [status, setStatus] = useState<ExtractionStatus | null>(null);
  const [progress, setProgress] = useState(0);
  const onCompleteRef = useRef(onComplete);
  const hasCompletedRef = useRef(false);

  // Update ref when callback changes
  useEffect(() => {
    onCompleteRef.current = onComplete;
  }, [onComplete]);

  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const data = await api.getExtractionProgress();
        setStatus(data);

        // Use REAL progress percentage from backend
        if (data.running) {
          setProgress(data.progress_pct);
        } else {
          setProgress(100);
          // Only call onComplete once
          if (!hasCompletedRef.current) {
            hasCompletedRef.current = true;
            onCompleteRef.current();
          }
          clearInterval(interval);
        }
      } catch (err) {
        console.error('Failed to poll extraction status:', err);
      }
    }, 500);

    return () => clearInterval(interval);
  }, []); // No dependencies - interval only created once

  // Generate ASCII progress bar
  const generateProgressBar = (percent: number) => {
    const filled = Math.floor(percent / 5); // 20 chars total, 5% each
    const empty = 20 - filled;
    return `[${'='.repeat(filled)}${'-'.repeat(empty)}]`;
  };

  if (!status) {
    return (
      <div className="whitespace-pre font-mono text-sm text-terminal-muted">
        Initializing extraction...
      </div>
    );
  }

  // Get status icon
  const getStatusIcon = (providerStatus: string) => {
    switch (providerStatus) {
      case 'pending': return '\u25CB'; // ○
      case 'running': return '\u25D0'; // ◐
      case 'completed': return '\u25CF'; // ●
      case 'failed': return '\u2715'; // ✕
      default: return '?';
    }
  };

  return (
    <div className="whitespace-pre font-mono text-sm">
      {/* Overall progress bar */}
      <div className="text-terminal-accent mb-2">
        {generateProgressBar(progress)} {Math.round(progress)}%
        {status.current_provider && (
          <span className="text-terminal-muted ml-2">
            → Processing: {status.current_provider}
          </span>
        )}
      </div>

      {/* Stats */}
      <div className="text-terminal-muted mb-3">
        Providers: {status.completed_providers}/{status.total_providers} |
        Events: {status.total_events} |
        Odds: {status.total_odds} |
        {Math.floor(status.elapsed_seconds)}s
      </div>

      {/* Provider breakdown */}
      {Object.keys(status.providers).length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
          {Object.entries(status.providers).map(([id, provider]) => (
            <div
              key={id}
              className={`border border-terminal-border/30 p-2 rounded ${
                provider.status === 'running' ? 'border-terminal-accent' :
                provider.status === 'completed' ? 'border-green-500/50' :
                provider.status === 'failed' ? 'border-red-500/50' : ''
              }`}
            >
              <div className="font-medium">
                {getStatusIcon(provider.status)} {id}
              </div>
              <div className="text-terminal-muted mt-1">
                {provider.events} ev | {provider.odds} odds
              </div>
              {provider.sports_total > 0 && (
                <div className="text-terminal-muted text-xs">
                  {provider.sports_completed}/{provider.sports_total} sports
                </div>
              )}
              {provider.duration_seconds > 0 && (
                <div className="text-terminal-muted text-xs">
                  {provider.duration_seconds.toFixed(1)}s
                </div>
              )}
              {provider.error && (
                <div className="text-red-500 text-xs truncate" title={provider.error}>
                  Error: {provider.error}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
