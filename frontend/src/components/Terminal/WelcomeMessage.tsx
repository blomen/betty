import type { BettingContext } from '@/types';

interface WelcomeMessageProps {
  context: BettingContext;
}

export function WelcomeMessage({ context }: WelcomeMessageProps) {
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
        <div className="grid grid-cols-3 gap-4 mb-8">
          <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
            <div className="text-terminal-cyan text-lg font-bold mb-1">[%]</div>
            <div className="text-lg font-semibold text-terminal-text">
              {context.arbitrage.length}
            </div>
            <div className="text-xs text-terminal-muted">Arbitrage</div>
          </div>

          <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
            <div className="text-terminal-green text-lg font-bold mb-1">[+]</div>
            <div className="text-lg font-semibold text-terminal-text">
              {context.valueBets.length}
            </div>
            <div className="text-xs text-terminal-muted">Value Bets</div>
          </div>

          <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
            <div className="text-terminal-purple text-lg font-bold mb-1">[#]</div>
            <div className="text-lg font-semibold text-terminal-text">
              {context.events.length}
            </div>
            <div className="text-xs text-terminal-muted">Events</div>
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
