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
  onShowOpportunities: (args: string) => Promise<void>;
  onShowBets: (args: string) => Promise<void>;
  onShowBalanceBreakdown: () => Promise<void>;
  onShowStats: () => Promise<void>;
  onRefresh: () => void;
  onClear: () => void;
  onRunExtraction: (providers?: string) => Promise<void>;
  onShowProviders: () => void;
  onShowHealth: () => void;
  onSettleBet: (args: string) => Promise<void>;
  onPlaceBet: (args: string) => Promise<void>;
  onProfileCommand: (args: string) => Promise<void>;
}): CommandRegistry {
  return {
    // Data & Extraction
    extract: {
      name: 'extract',
      description: 'Extract ALL sports/leagues from configured providers (or specify: /extract unibet,leovegas)',
      category: 'Extraction',
      execute: async () => {
        await handlers.onRunExtraction();
      },
    },

    // Opportunities & Bets
    opportunities: {
      name: 'opportunities',
      description: 'List opportunities with filters (e.g., --type arb --sport football)',
      category: 'Betting',
      execute: async () => {
        await handlers.onShowOpportunities('');
      },
    },
    arb: {
      name: 'arb',
      description: 'Show arbitrage opportunities',
      category: 'Betting',
      execute: async () => {
        await handlers.onShowOpportunities('--type arb');
      },
    },
    value: {
      name: 'value',
      description: 'Show value bets',
      category: 'Betting',
      execute: async () => {
        await handlers.onShowOpportunities('--type value');
      },
    },
    bets: {
      name: 'bets',
      description: 'Show bets with filters (e.g., --status pending)',
      category: 'Betting',
      execute: async () => {
        await handlers.onShowBets('');
      },
    },

    // Bankroll
    bankroll: {
      name: 'bankroll',
      description: 'Show bankroll breakdown table',
      category: 'Bankroll',
      execute: async () => {
        await handlers.onShowBalanceBreakdown();
      },
    },
    balance: {
      name: 'balance',
      description: 'Show balance breakdown (alias for /bankroll)',
      category: 'Bankroll',
      execute: async () => {
        await handlers.onShowBalanceBreakdown();
      },
    },
    stats: {
      name: 'stats',
      description: 'Show betting statistics',
      category: 'Analytics',
      execute: async () => {
        await handlers.onShowStats();
      },
    },

    // Actions
    'place-bet': {
      name: 'place-bet',
      description: 'Place bet on opportunity (usage: /place-bet <opp#> <stake> [provider])',
      category: 'Actions',
      execute: async () => {
        // Will be handled with args
      },
    },
    'settle-bet': {
      name: 'settle-bet',
      description: 'Settle pending bet (usage: /settle-bet <id> won/lost/void)',
      category: 'Actions',
      execute: async () => {
        // Will be handled with args
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

    // Profile Management
    profile: {
      name: 'profile',
      description: 'Manage profiles (usage: /profile list|switch|create|delete <name>)',
      category: 'Profile',
      execute: async () => {
        // Will be handled with args
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
