import type { BettingContext, BankrollExposure } from '@/types';
import { useBets } from '@/hooks/useBets';

interface WelcomeMessageProps {
  context: BettingContext;
  exposure: BankrollExposure;
  onShowOpportunities: () => void;
  onShowBets: () => void;
}

export function WelcomeMessage({
  context,
  exposure,
  onShowOpportunities,
  onShowBets,
}: WelcomeMessageProps) {
  const { count: totalBetsCount } = useBets(undefined, 0);
  const suggestions = [
    'Show me arbitrage opportunities',
    'What value bets do you see?',
    'Explain Kelly criterion staking',
    'Compare odds for NBA games',
  ];

  return (
    <div className="flex-1 flex items-center justify-center p-8">
      <div className="max-w-lg text-center">
        {/* ASCII Logo */}
        <div className="flex justify-center mb-6">
          <div className="w-16 h-16 rounded-xl bg-terminal-accent/10 border border-terminal-accent/20
                          flex items-center justify-center">
            <span className="text-3xl font-bold text-terminal-accent">[*]</span>
          </div>
        </div>

        {/* Title */}
        <h1 className="text-2xl font-bold text-terminal-text mb-2">
          OddOpp Terminal
        </h1>
        <p className="text-terminal-muted mb-8">
          AI-powered betting analytics. Find arbitrage and value across bookmakers.
        </p>

        {/* Stats */}
        <div className="grid grid-cols-4 gap-4 mb-6">
          <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
            <div className="text-terminal-cyan text-lg font-bold mb-1">[%]</div>
            <div className="text-lg font-semibold text-terminal-text">
              {context.opportunities.filter(o => o.type === 'arbitrage').length}
            </div>
            <div className="text-xs text-terminal-muted">Arbitrage</div>
          </div>

          <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
            <div className="text-terminal-green text-lg font-bold mb-1">[+]</div>
            <div className="text-lg font-semibold text-terminal-text">
              {context.opportunities.filter(o => o.type === 'value').length}
            </div>
            <div className="text-xs text-terminal-muted">Value</div>
          </div>

          <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
            <div className="text-terminal-yellow text-lg font-bold mb-1">[$]</div>
            <div className="text-lg font-semibold text-terminal-text">
              ${context.bankroll.total.toFixed(0)}
            </div>
            <div className="text-xs text-terminal-muted">Bankroll</div>
          </div>

          <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
            <div className="text-terminal-purple text-lg font-bold mb-1">[~]</div>
            <div className="text-lg font-semibold text-terminal-text">
              {exposure.total_pending.toFixed(0)}
            </div>
            <div className="text-xs text-terminal-muted">Pending</div>
          </div>
        </div>

        {/* Quick Actions */}
        <div className="mb-6">
          <p className="text-xs text-terminal-muted uppercase tracking-wide mb-3">
            Quick Actions
          </p>
          <div className="flex justify-center gap-3">
            <button
              onClick={onShowOpportunities}
              className="px-4 py-2 bg-terminal-accent/10 border border-terminal-accent/30
                         rounded text-terminal-accent hover:bg-terminal-accent/20
                         transition-colors font-mono text-sm"
            >
              View Opportunities <span className="text-xs opacity-60">(Cmd+O)</span>
            </button>
            <button
              onClick={onShowBets}
              className="px-4 py-2 bg-terminal-surface border border-terminal-border
                         rounded text-terminal-text hover:border-terminal-accent
                         transition-colors font-mono text-sm"
            >
              Manage Bets <span className="text-xs opacity-60">({totalBetsCount}) (Cmd+B)</span>
            </button>
          </div>
        </div>

        {/* Keyboard Shortcuts */}
        <div className="mb-8 bg-terminal-surface/50 border border-terminal-border rounded-lg p-3">
          <p className="text-xs text-terminal-muted uppercase tracking-wide mb-2">
            Keyboard Shortcuts
          </p>
          <div className="grid grid-cols-2 gap-2 text-xs font-mono">
            <div className="text-terminal-muted">
              <span className="text-terminal-accent">Cmd+O</span> - Opportunities
            </div>
            <div className="text-terminal-muted">
              <span className="text-terminal-accent">Cmd+B</span> - Bets
            </div>
            <div className="text-terminal-muted">
              <span className="text-terminal-accent">Cmd+L</span> - Clear Chat
            </div>
            <div className="text-terminal-muted">
              <span className="text-terminal-accent">F5</span> - Refresh
            </div>
          </div>
        </div>

        {/* Suggestions */}
        <div className="space-y-2">
          <p className="text-xs text-terminal-muted uppercase tracking-wide mb-3">
            Try asking
          </p>
          <div className="flex flex-wrap justify-center gap-2">
            {suggestions.map((suggestion) => (
              <button
                key={suggestion}
                onClick={() => {
                  // Find the input and set its value
                  const input = document.querySelector('textarea');
                  if (input) {
                    input.value = suggestion;
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.focus();
                  }
                }}
                className="px-3 py-1.5 text-sm bg-terminal-surface border border-terminal-border
                           rounded text-terminal-muted hover:text-terminal-text
                           hover:border-terminal-accent transition-colors"
              >
                {suggestion}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
