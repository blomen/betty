import { useState } from 'react';
import { FireWindow } from './FireWindow';
import type { BatchBet, WageringProjection } from '@/types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Props {
  batch: BatchBet[];
  wageringProjections: WageringProjection[];
  onBack: () => void;
  onNewBatch: () => void;
}

// ---------------------------------------------------------------------------
// ExecutionPanel
// ---------------------------------------------------------------------------

export function ExecutionPanel({ batch, wageringProjections, onBack, onNewBatch }: Props) {
  const [completed, setCompleted] = useState(false);

  if (completed) {
    return (
      <div className="border border-border bg-panel px-4 py-3 flex flex-col gap-3">
        <div className="text-sm text-foreground font-medium">
          All providers processed. Session complete.
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={onNewBatch}
            className="px-3 py-1 text-xs bg-success text-bg font-medium hover:opacity-90 transition-opacity"
          >
            New Batch
          </button>
          <button
            onClick={onBack}
            className="px-3 py-1 text-xs bg-border text-foreground hover:opacity-90 transition-opacity"
          >
            Back
          </button>
        </div>
      </div>
    );
  }

  return (
    <FireWindow
      batch={batch}
      wageringProjections={wageringProjections}
      onComplete={() => setCompleted(true)}
      onBack={onBack}
      onNewBatch={onNewBatch}
    />
  );
}
