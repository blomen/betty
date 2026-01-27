import type { BettingContext } from '@/types';

interface TerminalHeaderProps {
  context: BettingContext;
  isLoading: boolean;
  onClear: () => void;
  onRefresh: () => void;
}

export function TerminalHeader({
  context,
  isLoading,
  onClear,
  onRefresh,
}: TerminalHeaderProps) {
  const hasData = context.arbitrage.length > 0 || context.valueBets.length > 0;

  return (
    <div className="flex items-center justify-between px-4 py-3 bg-terminal-surface border-b border-terminal-border">
      {/* Left: Title and status */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <span className="text-terminal-accent font-bold">[*]</span>
          <span className="font-semibold text-terminal-text">OddOpp</span>
        </div>

        {/* Status indicators */}
        <div className="flex items-center gap-3 text-xs text-terminal-muted">
          <div className="flex items-center gap-1.5">
            <span className={hasData ? 'text-terminal-green' : 'text-terminal-yellow'}>
              {hasData ? '[+]' : '[-]'}
            </span>
            <span>{hasData ? 'connected' : 'no data'}</span>
          </div>

          {context.arbitrage.length > 0 && (
            <span className="text-terminal-cyan">
              {context.arbitrage.length} arb
            </span>
          )}

          {context.valueBets.length > 0 && (
            <span className="text-terminal-green">
              {context.valueBets.length} value
            </span>
          )}
        </div>
      </div>

      {/* Right: Actions */}
      <div className="flex items-center gap-2">
        <button
          onClick={onRefresh}
          disabled={isLoading}
          className="px-2 py-1 rounded text-xs text-terminal-muted hover:text-terminal-text
                     hover:bg-terminal-border/50 transition-colors
                     disabled:opacity-50 disabled:cursor-not-allowed"
          title="Refresh data"
        >
          {isLoading ? '[...]' : '[refresh]'}
        </button>

        <button
          onClick={onClear}
          className="px-2 py-1 rounded text-xs text-terminal-muted hover:text-terminal-red
                     hover:bg-terminal-border/50 transition-colors"
          title="Clear chat"
        >
          [clear]
        </button>
      </div>
    </div>
  );
}
