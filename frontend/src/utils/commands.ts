/**
 * Slash Command System
 * Similar to Claude Code's command interface
 */

export interface Command {
  name: string;
  description: string;
  execute: () => Promise<void> | void;
  category?: string;
}

export interface CommandRegistry {
  [key: string]: Command;
}

export function createCommandRegistry(handlers: {
  onShowOpportunities: () => void;
  onShowBets: () => void;
  onShowBalanceBreakdown: () => void;
  onRefresh: () => void;
  onClear: () => void;
  onRunExtraction: (providers?: string) => Promise<void>;
  onShowProviders: () => void;
  onShowHealth: () => void;
}): CommandRegistry {
  return {
    // Data & Extraction
    extractall: {
      name: 'extractall',
      description: 'Run extraction on all providers',
      category: 'Extraction',
      execute: async () => {
        await handlers.onRunExtraction();
      },
    },
    extract: {
      name: 'extract',
      description: 'Run extraction (usage: /extract unibet,leovegas)',
      category: 'Extraction',
      execute: async () => {
        // Will be called with args
      },
    },

    // Opportunities & Bets
    opportunities: {
      name: 'opportunities',
      description: 'Show opportunities overlay',
      category: 'Betting',
      execute: () => {
        handlers.onShowOpportunities();
      },
    },
    arb: {
      name: 'arb',
      description: 'Show arbitrage opportunities',
      category: 'Betting',
      execute: () => {
        handlers.onShowOpportunities();
      },
    },
    value: {
      name: 'value',
      description: 'Show value bets',
      category: 'Betting',
      execute: () => {
        handlers.onShowOpportunities();
      },
    },
    bets: {
      name: 'bets',
      description: 'Show bets panel',
      category: 'Betting',
      execute: () => {
        handlers.onShowBets();
      },
    },

    // Bankroll
    bankroll: {
      name: 'bankroll',
      description: 'Show bankroll breakdown',
      category: 'Bankroll',
      execute: () => {
        handlers.onShowBalanceBreakdown();
      },
    },
    balance: {
      name: 'balance',
      description: 'Show balance breakdown (alias for /bankroll)',
      category: 'Bankroll',
      execute: () => {
        handlers.onShowBalanceBreakdown();
      },
    },

    // System
    providers: {
      name: 'providers',
      description: 'List all providers',
      category: 'System',
      execute: () => {
        handlers.onShowProviders();
      },
    },
    health: {
      name: 'health',
      description: 'Check system health',
      category: 'System',
      execute: () => {
        handlers.onShowHealth();
      },
    },
    refresh: {
      name: 'refresh',
      description: 'Refresh all data',
      category: 'System',
      execute: () => {
        handlers.onRefresh();
      },
    },
    clear: {
      name: 'clear',
      description: 'Clear chat history',
      category: 'System',
      execute: () => {
        handlers.onClear();
      },
    },

    // Help
    help: {
      name: 'help',
      description: 'Show all available commands',
      category: 'Help',
      execute: () => {
        // Will be handled specially to show command list
      },
    },
    commands: {
      name: 'commands',
      description: 'Show all available commands (alias for /help)',
      category: 'Help',
      execute: () => {
        // Will be handled specially
      },
    },
  };
}

export function parseCommand(input: string): { command: string; args: string } | null {
  const trimmed = input.trim();
  if (!trimmed.startsWith('/')) return null;

  const parts = trimmed.slice(1).split(/\s+/);
  const command = parts[0].toLowerCase();
  const args = parts.slice(1).join(' ');

  return { command, args };
}

export function filterCommands(query: string, commands: CommandRegistry): Command[] {
  const lowerQuery = query.toLowerCase();
  return Object.values(commands).filter(
    (cmd) =>
      cmd.name.toLowerCase().includes(lowerQuery) ||
      cmd.description.toLowerCase().includes(lowerQuery)
  );
}

export function formatCommandHelp(commands: CommandRegistry): string {
  const categories = new Map<string, Command[]>();

  Object.values(commands).forEach((cmd) => {
    const category = cmd.category || 'Other';
    if (!categories.has(category)) {
      categories.set(category, []);
    }
    categories.get(category)!.push(cmd);
  });

  let help = '**Available Commands:**\n\n';

  categories.forEach((cmds, category) => {
    help += `**[${category}]**\n`;
    cmds.forEach((cmd) => {
      help += `- \`/${cmd.name}\` - ${cmd.description}\n`;
    });
    help += '\n';
  });

  return help;
}
