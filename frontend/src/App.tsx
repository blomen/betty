import { useCallback } from 'react';
import { TerminalWindow } from '@/components/Terminal';
import { useBettingContext } from '@/hooks/useBettingContext';
import { useUpdateChecker } from '@/hooks/useUpdateChecker';
import { useExtractionStatus } from '@/hooks/useExtractionStatus';

export default function App() {
  const { context, refresh } = useBettingContext();
  const { updateAvailable, latestVersion, downloadUrl } = useUpdateChecker();

  // Monitor extraction — refresh all data when extraction completes
  const onExtractionComplete = useCallback(() => {
    refresh();
  }, [refresh]);
  useExtractionStatus(onExtractionComplete);

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
      <div className="flex-1 min-h-0">
        <TerminalWindow context={context} onRefresh={refresh} />
      </div>
    </div>
  );
}
