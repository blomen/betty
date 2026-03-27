import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { TerminalWindow } from '@/components/Terminal';
import { useUpdateChecker } from '@/hooks/useUpdateChecker';
import { useOddsStream } from '@/hooks/useOddsStream';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 30_000,
      gcTime: 5 * 60_000, // 5 min — keep cached data across tab switches
    },
  },
});

/** Inner component — lives inside QueryClientProvider so useOddsStream can call useQueryClient. */
function AppInner({ updateAvailable, latestVersion, downloadUrl }: {
  updateAvailable: boolean;
  latestVersion: string | null;
  downloadUrl: string | null;
}) {
  useOddsStream();

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
        <TerminalWindow />
      </div>
    </div>
  );
}

export default function App() {
  const { updateAvailable, latestVersion, downloadUrl } = useUpdateChecker();

  return (
    <QueryClientProvider client={queryClient}>
      <AppInner
        updateAvailable={updateAvailable}
        latestVersion={latestVersion}
        downloadUrl={downloadUrl}
      />
    </QueryClientProvider>
  );
}
