import { useEffect, useRef } from 'react';
import { fireWindowApi } from '@/services/api/fireWindow';
import type { BatchBet, WageringProjection } from '@/types';
import { useProviderQueue } from '../../../../hooks/useProviderQueue';
import { SyncLane } from './SyncLane';
import { BettingLane } from './BettingLane';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Props {
  batch: BatchBet[];
  wageringProjections: WageringProjection[];
  onComplete: () => void;
  onBack: () => void;
  onNewBatch: () => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function dotClass(state: 'active' | 'syncing' | 'queued' | 'done'): string {
  if (state === 'active') return 'bg-green-500';
  if (state === 'syncing') return 'bg-blue-500 animate-pulse';
  if (state === 'done') return 'bg-zinc-600';
  return 'bg-zinc-700';
}

// ---------------------------------------------------------------------------
// FireWindow Component
// ---------------------------------------------------------------------------

export function FireWindow({ batch, wageringProjections: _wp, onComplete, onBack, onNewBatch }: Props) {
  const closedRef = useRef(false);
  const { providers, activeProvider } = useProviderQueue();

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      closedRef.current = true;
      fireWindowApi.close().catch(() => {});
    };
  }, []);

  // Open fire window on mount
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await fireWindowApi.open(batch);
      } catch (err) {
        if (!cancelled) console.error('[FireWindow] open failed', err);
      }
    })();
    return () => { cancelled = true; };
  }, [batch]);

  // Settlement confirmation
  const handleConfirmSettlements = async () => {
    try {
      await fetch('/api/mirror/settlements/confirm-queue', { method: 'POST' });
    } catch (err) {
      console.error('[FireWindow] confirm-queue failed', err);
    }
  };

  const allDone = providers.length > 0 && providers.every(p => p.state === 'done');

  return (
    <div className="flex flex-col flex-1 min-h-0">

      {/* Provider Queue Bar */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-zinc-800 bg-zinc-950 flex-shrink-0 flex-wrap">
        {providers.length === 0 ? (
          <span className="text-xs text-zinc-600 animate-pulse">Opening providers...</span>
        ) : (
          providers.map(p => (
            <div
              key={p.id}
              className={`flex items-center gap-1.5 px-2 py-1 border text-xs font-medium uppercase ${
                p.state === 'active'
                  ? 'border-green-700/60 bg-green-950/30 text-green-300'
                  : p.state === 'syncing'
                    ? 'border-blue-700/60 bg-blue-950/30 text-blue-300'
                    : p.state === 'done'
                      ? 'border-zinc-800 bg-zinc-900/30 text-zinc-600'
                      : 'border-zinc-800 bg-zinc-900/20 text-zinc-500'
              }`}
            >
              <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${dotClass(p.state)}`} />
              <span>{p.id}</span>
              {p.betsRemaining > 0 && (
                <span className="text-[10px] text-zinc-500 ml-0.5">{p.betsRemaining}</span>
              )}
            </div>
          ))
        )}

        {/* Actions in top bar */}
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={onBack}
            className="px-3 py-1 text-xs bg-zinc-800 text-zinc-300 hover:bg-zinc-700 transition-colors"
          >
            Back
          </button>
          <button
            onClick={() => { fireWindowApi.close().catch(() => {}); onNewBatch(); }}
            className="px-3 py-1 text-xs text-zinc-500 hover:text-zinc-300 transition-colors"
          >
            New Batch
          </button>
          {allDone && (
            <button
              onClick={onComplete}
              className="px-4 py-1 text-xs bg-green-700 text-white font-medium hover:bg-green-600 transition-colors"
            >
              Done
            </button>
          )}
        </div>
      </div>

      {/* Two-Lane Layout */}
      <div className="flex flex-1 min-h-0">
        <SyncLane
          providerId={activeProvider}
          onConfirmSettlements={handleConfirmSettlements}
        />
        <BettingLane providerId={activeProvider} />
      </div>

    </div>
  );
}
