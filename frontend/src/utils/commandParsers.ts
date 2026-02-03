/**
 * Command argument parsers for terminal commands
 */

export interface OpportunityFilters {
  type?: 'arbitrage' | 'value' | 'bonus';
  sport?: string;
  market?: string;
  minValue?: number;
}

/**
 * Parse filters from /opportunities command arguments
 * Examples:
 * - /opportunities --type arb
 * - /opportunities --sport football --min-edge 5
 * - /opportunities --type value --min-profit 3
 */
export function parseOpportunityFilters(args: string): OpportunityFilters {
  const filters: OpportunityFilters = {};

  // Parse --type arb | --type value | --type bonus
  const typeMatch = args.match(/--type\s+(arb|arbitrage|value|bonus)/i);
  if (typeMatch) {
    const typeArg = typeMatch[1].toLowerCase();
    filters.type = typeArg === 'arb' ? 'arbitrage' : (typeArg as 'arbitrage' | 'value' | 'bonus');
  }

  // Parse --sport football
  const sportMatch = args.match(/--sport\s+(\w+)/i);
  if (sportMatch) {
    filters.sport = sportMatch[1];
  }

  // Parse --market match_winner
  const marketMatch = args.match(/--market\s+([\w_]+)/i);
  if (marketMatch) {
    filters.market = marketMatch[1];
  }

  // Parse --min-edge 5 or --min-profit 2
  const minMatch = args.match(/--min-(?:edge|profit)\s+([\d.]+)/i);
  if (minMatch) {
    filters.minValue = parseFloat(minMatch[1]);
  }

  return filters;
}

export interface BetFilters {
  status?: 'pending' | 'won' | 'lost' | 'void';
  limit?: number;
  offset?: number;
}

/**
 * Parse filters from /bets command arguments
 * Examples:
 * - /bets --status pending
 * - /bets --limit 10 --offset 20
 */
export function parseBetFilters(args: string): BetFilters {
  const filters: BetFilters = { limit: 50, offset: 0 };

  // Parse --status pending
  const statusMatch = args.match(/--status\s+(pending|won|lost|void)/i);
  if (statusMatch) {
    filters.status = statusMatch[1].toLowerCase() as 'pending' | 'won' | 'lost' | 'void';
  }

  // Parse --limit 20
  const limitMatch = args.match(/--limit\s+(\d+)/i);
  if (limitMatch) {
    filters.limit = parseInt(limitMatch[1], 10);
  }

  // Parse --offset 10
  const offsetMatch = args.match(/--offset\s+(\d+)/i);
  if (offsetMatch) {
    filters.offset = parseInt(offsetMatch[1], 10);
  }

  return filters;
}

export interface ProfileCommand {
  action: 'list' | 'switch' | 'create' | 'delete' | 'set';
  name?: string;
  setting?: string;
  value?: string;
}

/**
 * Parse /profile command arguments
 * Examples:
 * - /profile list - Show all profiles
 * - /profile switch aggressive - Switch to profile named "aggressive"
 * - /profile create conservative - Create new profile
 * - /profile delete test - Delete profile
 * - /profile set kelly_fraction 0.25 - Update active profile setting
 */
export function parseProfileCommand(args: string): ProfileCommand {
  const parts = args.trim().split(/\s+/);
  if (parts.length === 0 || !parts[0]) return { action: 'list' };

  const action = parts[0].toLowerCase();

  switch (action) {
    case 'list':
      return { action: 'list' };
    case 'switch':
      return { action: 'switch', name: parts.slice(1).join(' ') };
    case 'create':
      return { action: 'create', name: parts.slice(1).join(' ') };
    case 'delete':
      return { action: 'delete', name: parts.slice(1).join(' ') };
    case 'set':
      return { action: 'set', setting: parts[1], value: parts[2] };
    default:
      return { action: 'list' };
  }
}

