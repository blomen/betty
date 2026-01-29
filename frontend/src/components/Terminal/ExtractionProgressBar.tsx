import { useEffect, useState } from 'react';
import { api } from '@/services/api';
import type { ExtractionStatus } from '@/types';

interface ExtractionProgressBarProps {
  isExtracting: boolean;
  onComplete?: () => void;
}

export function ExtractionProgressBar({
  isExtracting,
  onComplete,
}: ExtractionProgressBarProps) {
  const [status, setStatus] = useState<ExtractionStatus | null>(null);
  const [startTime] = useState(Date.now());
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!isExtracting) return;

    // Poll extraction status
    const pollInterval = setInterval(async () => {
      try {
        const extractionStatus = await api.getExtractionStatus();
        setStatus(extractionStatus);

        // Check if complete
        if (!extractionStatus.running) {
          clearInterval(pollInterval);
          if (onComplete) onComplete();
        }
      } catch (err) {
        console.error('Error polling extraction:', err);
      }
    }, 500);

    // Update elapsed time
    const elapsedInterval = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startTime) / 1000));
    }, 1000);

    return () => {
      clearInterval(pollInterval);
      clearInterval(elapsedInterval);
    };
  }, [isExtracting, startTime, onComplete]);

  if (!isExtracting || !status) return null;

  // Calculate progress percentage (estimate based on events)
  const progress = status.events > 0 ? Math.min(100, (status.events / 50) * 100) : 0;

  // ASCII progress bar
  const barWidth = 50;
  const filled = Math.floor((progress / 100) * barWidth);
  const empty = barWidth - filled;
  const progressBar = '\u2588'.repeat(filled) + '\u2591'.repeat(empty);

  // Format time
  const mins = Math.floor(elapsed / 60);
  const secs = elapsed % 60;
  const timeStr = `${mins}:${secs.toString().padStart(2, '0')}`;

  return (
    <div className="fixed bottom-0 left-0 right-0 bg-terminal-surface/95 border-t-2 border-terminal-accent z-50 font-mono backdrop-blur-sm">
      <div className="max-w-7xl mx-auto px-4 py-2">
        <div className="flex items-center justify-between text-xs">
          {/* Left: Status */}
          <div className="flex items-center gap-2 text-terminal-accent">
            <span className="animate-pulse">[*]</span>
            <span>EXTRACTION RUNNING</span>
          </div>

          {/* Center: Progress bar */}
          <div className="flex-1 mx-4 flex items-center gap-2">
            <span className="text-terminal-muted">[</span>
            <span className="text-terminal-accent">{progressBar}</span>
            <span className="text-terminal-muted">]</span>
            <span className="text-terminal-text">{progress.toFixed(0)}%</span>
          </div>

          {/* Right: Stats */}
          <div className="flex items-center gap-4 text-terminal-text">
            <span>Events: <span className="text-terminal-accent">{status.events}</span></span>
            <span>Odds: <span className="text-terminal-accent">{status.odds}</span></span>
            <span>Time: <span className="text-terminal-yellow">{timeStr}</span></span>
          </div>
        </div>
      </div>
    </div>
  );
}
