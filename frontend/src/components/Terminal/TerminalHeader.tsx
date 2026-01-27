import type { BettingContext, BankrollExposure } from '@/types';

interface TerminalHeaderProps {
  context: BettingContext;
  exposure: BankrollExposure;
  isLoading: boolean;
  onClear: () => void;
  onRefresh: () => void;
  onShowBalanceBreakdown: () => void;
}

export function TerminalHeader({
  context,
  exposure,
  isLoading,
  onClear,
  onRefresh,
  onShowBalanceBreakdown,
}: TerminalHeaderProps) {
  const arbCount = context.opportunities.filter(o => o.type === 'arbitrage').length;
  const valueCount = context.opportunities.filter(o => o.type === 'value').length;
  const hasData = context.opportunities.length > 0;
  const hasPending = exposure.total_pending > 0;

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

          {arbCount > 0 && (
            <span className="text-terminal-cyan">
              {arbCount} arb
            </span>
          )}

          {valueCount > 0 && (
            <span className="text-terminal-green">
              {valueCount} value
            </span>
          )}

          {context.bankroll.total > 0 && (
            <button
              onClick={onShowBalanceBreakdown}
              className="text-terminal-yellow hover:text-terminal-accent transition-colors"
              title="Click for balance breakdown"
            >
              ${context.bankroll.total.toFixed(0)}
              {hasPending && (
                <span className="ml-1 text-xs text-yellow-500">
                  ({exposure.total_pending.toFixed(0)} pending)
                </span>
              )}
            </button>
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
