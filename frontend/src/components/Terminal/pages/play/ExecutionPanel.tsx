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
}

// ---------------------------------------------------------------------------
// ExecutionPanel
// ---------------------------------------------------------------------------

export function ExecutionPanel({ batch, wageringProjections, onBack }: Props) {
  const [completed, setCompleted] = useState(false);

  if (completed) {
    return (
      <div className="border border-border bg-panel px-4 py-3 flex flex-col gap-3">
        <div className="text-sm text-foreground font-medium">
          All providers processed. Session complete.
        </div>
        <button
          onClick={onBack}
          className="px-3 py-1 text-xs bg-border text-foreground hover:opacity-90 transition-opacity w-fit"
        >
          Back
        </button>
      </div>
    );
  }

  return (
    <FireWindow
      batch={batch}
      wageringProjections={wageringProjections}
      onComplete={() => setCompleted(true)}
      onBack={onBack}
    />
  );
}
