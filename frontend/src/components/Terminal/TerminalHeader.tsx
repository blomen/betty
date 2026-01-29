import type { BettingContext, BankrollExposure, Profile } from '@/types';

interface TerminalHeaderProps {
  context: BettingContext;
  exposure: BankrollExposure;
  isLoading: boolean;
  activeProfile: Profile | null;
}

export function TerminalHeader({
  context,
  exposure,
  isLoading,
  activeProfile,
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
            <span className="text-terminal-yellow">
              ${context.bankroll.total.toFixed(0)}
              {hasPending && (
                <span className="ml-1 text-xs text-yellow-500">
                  ({exposure.total_pending.toFixed(0)} pending)
                </span>
              )}
            </span>
          )}
        </div>
      </div>

      {/* Right: Profile and status */}
      <div className="flex items-center gap-3">
        {/* Active Profile Display */}
        {activeProfile && (
          <div className="flex items-center gap-2 px-2 py-1 text-xs text-terminal-muted">
            <span className="text-terminal-accent">[@]</span>
            <span className="text-terminal-text">{activeProfile.name}</span>
          </div>
        )}

        {/* Loading indicator */}
        {isLoading && (
          <span className="text-xs text-terminal-muted">
            [...]
          </span>
        )}
      </div>
    </div>
  );
}
