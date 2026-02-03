/**
 * Slash Command System
 * Similar to Claude Code's command interface
 */

export interface Command {
  name: string;
  aliases?: string[];
  description: string;
  execute: () => Promise<void> | void;
  category?: string;
}

export interface CommandRegistry {
  [key: string]: Command;
}

export function createCommandRegistry(handlers: {
  onShowStats: () => Promise<void>;
  onClear: () => void;
  onBonusCommand: () => void;
  onExtractWorkflow: () => void;
  onArbWorkflow: () => void;
  onValueWorkflow: () => void;
  onBetsWorkflow: () => void;
  onBankrollWorkflow: () => void;
}): CommandRegistry {
  return {
    extract: {
      name: 'extract',
      description: 'Run extraction',
      execute: () => handlers.onExtractWorkflow(),
    },
    arb: {
      name: 'arb',
      description: 'Find arbitrage',
      execute: () => handlers.onArbWorkflow(),
    },
    value: {
      name: 'value',
      description: 'Find value bets',
      execute: () => handlers.onValueWorkflow(),
    },
    bonus: {
      name: 'bonus',
      description: 'Bonus arbitrage',
      execute: () => handlers.onBonusCommand(),
    },
    bets: {
      name: 'bets',
      description: 'View/settle bets',
      execute: () => handlers.onBetsWorkflow(),
    },
    bankroll: {
      name: 'bankroll',
      description: 'Bankroll management',
      execute: () => handlers.onBankrollWorkflow(),
    },
    stats: {
      name: 'stats',
      description: 'Betting stats',
      execute: async () => await handlers.onShowStats(),
    },
    clear: {
      name: 'clear',
      description: 'Clear terminal',
      execute: () => handlers.onClear(),
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
    (cmd) => cmd.name.toLowerCase().includes(lowerQuery)
  );
}

export function formatCommandHelp(commands: CommandRegistry): string {
  return Object.values(commands)
    .map((cmd) => `/${cmd.name}`)
    .join('  ');
}
