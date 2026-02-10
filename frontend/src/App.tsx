import { useCallback } from 'react';
import { TerminalWindow } from '@/components/Terminal';
import { useBettingContext } from '@/hooks/useBettingContext';
import { useUpdateChecker } from '@/hooks/useUpdateChecker';
import { useExtractionStatus, useExtractionProgress } from '@/hooks/useExtractionStatus';
import { formatProviderName } from '@/utils/formatters';

export default function App() {
  const { context, refresh } = useBettingContext();
  const { updateAvailable, latestVersion, downloadUrl } = useUpdateChecker();

  // Monitor extraction — refresh all data when extraction completes
  const onExtractionComplete = useCallback(() => {
    refresh();
  }, [refresh]);
  const { running: extractionRunning } = useExtractionStatus(onExtractionComplete);
  const progress = useExtractionProgress();

  return (
    <div className="h-screen w-screen overflow-hidden flex flex-col">
      {updateAvailable && downloadUrl && (
        <div className="bg-blue-600 text-white px-4 py-1.5 text-center text-sm flex-shrink-0">
          Update available: v{latestVersion}
          <a
            href={downloadUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="underline ml-2 font-medium"
          >
            Download
          </a>
        </div>
      )}
      {extractionRunning && progress && (
        <div className="bg-panel2 border-b border-border text-text px-4 py-1 flex-shrink-0 font-mono text-xs flex items-center gap-3">
          <span className="text-emerald-400 animate-blink">●</span>
          <span className="text-emerald-400">EXTRACTING</span>
          <span className="text-emerald-400/60">
            {'█'.repeat(Math.round((progress.progress_pct / 100) * 12))}
            {'░'.repeat(12 - Math.round((progress.progress_pct / 100) * 12))}
          </span>
          <span className="text-muted">{progress.progress_pct.toFixed(0)}%</span>
          {progress.current_provider && (
            <span className="text-muted2">
              {formatProviderName(progress.current_provider)}
            </span>
          )}
          <span className="text-muted2 ml-auto">
            {progress.completed_providers}/{progress.total_providers}
          </span>
        </div>
      )}
      <div className="flex-1 min-h-0">
        <TerminalWindow context={context} onRefresh={refresh} />
      </div>
    </div>
  );
}
