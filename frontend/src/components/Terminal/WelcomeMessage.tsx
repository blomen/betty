import type { BettingContext, BankrollExposure, Profile } from '@/types';
import { useBets } from '@/hooks/useBets';

interface WelcomeMessageProps {
  context: BettingContext;
  exposure: BankrollExposure;
  activeProfile: Profile | null;
  onShowOpportunities: () => void;
  onShowBets: () => void;
}

export function WelcomeMessage({
  context,
  exposure,
  activeProfile,
  onShowOpportunities,
  onShowBets,
}: WelcomeMessageProps) {
  const { count: totalBetsCount } = useBets(undefined, 0);
  const commandCategories = [
    {
      title: 'Opportunities',
      commands: [
        'Show me all arbitrage opportunities',
        'What are the best value bets right now?',
        'Find bonus bet hedging opportunities',
        'Show opportunities with >5% edge',
      ],
    },
    {
      title: 'Bankroll & Bets',
      commands: [
        'Show my current bankroll breakdown',
        'What are my pending bets?',
        'Calculate Kelly stake for 2.5 odds at 3% edge',
        'Show bet history from last 7 days',
      ],
    },
    {
      title: 'Providers',
      commands: [
        'Which providers are healthy?',
        'Show provider performance metrics',
        'Check extraction status',
        'Run extraction for football',
      ],
    },
    {
      title: 'Analytics',
      commands: [
        'Explain Kelly criterion staking',
        'What is arbitrage betting?',
        'How do you calculate implied probability?',
        'Compare odds across all providers',
      ],
    },
  ];

  return (
    <div className="flex-1 flex items-center justify-center p-8">
      <div className="max-w-4xl text-center">
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

        {/* Active Profile */}
        {activeProfile && (
          <div className="mb-6 px-4 py-2 bg-terminal-surface border border-terminal-border rounded-lg inline-flex items-center gap-2">
            <span className="text-terminal-accent">[@]</span>
            <span className="text-terminal-text font-medium">{activeProfile.name}</span>
          </div>
        )}

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
              View Opportunities <span className="text-xs opacity-60">(Ctrl+O)</span>
            </button>
            <button
              onClick={onShowBets}
              className="px-4 py-2 bg-terminal-surface border border-terminal-border
                         rounded text-terminal-text hover:border-terminal-accent
                         transition-colors font-mono text-sm"
            >
              Manage Bets <span className="text-xs opacity-60">({totalBetsCount}) (Ctrl+B)</span>
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
              <span className="text-terminal-accent">Ctrl+O</span> - Opportunities
            </div>
            <div className="text-terminal-muted">
              <span className="text-terminal-accent">Ctrl+B</span> - Bets
            </div>
            <div className="text-terminal-muted">
              <span className="text-terminal-accent">Ctrl+L</span> - Clear Chat
            </div>
            <div className="text-terminal-muted">
              <span className="text-terminal-accent">F5</span> - Refresh
            </div>
          </div>
        </div>

        {/* Command Categories */}
        <div className="space-y-4">
          <p className="text-xs text-terminal-muted uppercase tracking-wide mb-3">
            Available Commands
          </p>
          <div className="grid grid-cols-2 gap-4 text-left">
            {commandCategories.map((category) => (
              <div key={category.title} className="bg-terminal-surface/50 border border-terminal-border rounded-lg p-3">
                <h3 className="text-xs text-terminal-accent uppercase tracking-wide mb-2 font-bold">
                  [{category.title}]
                </h3>
                <div className="space-y-1.5">
                  {category.commands.map((command) => (
                    <button
                      key={command}
                      onClick={() => {
                        const input = document.querySelector('textarea');
                        if (input) {
                          input.value = command;
                          input.dispatchEvent(new Event('input', { bubbles: true }));
                          input.focus();
                        }
                      }}
                      className="block w-full text-left px-2 py-1 text-xs font-mono text-terminal-muted
                                 hover:text-terminal-accent hover:bg-terminal-accent/10 rounded transition-colors"
                    >
                      <span className="text-terminal-accent">[{'>'}]</span> {command}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
