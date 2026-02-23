/**
 * Format utilities for displaying betting data
 */

/**
 * Join array items with markdown hard line breaks.
 * Markdown collapses single \n to spaces - use two trailing spaces for hard breaks.
 */
export function joinLines(items: string[]): string {
  return items.join('  \n');
}

/**
 * Strip domain suffixes (.se, .com, .no, .dk, etc.) from provider names
 */
export function formatProviderName(name: string): string {
  return name.replace(/\.(se|com|no|dk|fi|de|uk|eu|net|org|io|co)$/i, '');
}

export function formatCurrency(value: number, currency = 'kr'): string {
  return `${value.toFixed(2)} ${currency}`;
}

export function formatPercentage(value: number, decimals = 2): string {
  return `${value.toFixed(decimals)}%`;
}

export function formatOdds(odds: number): string {
  return odds.toFixed(2);
}

export function formatDateTime(isoString: string | null): string {
  if (!isoString) return 'N/A';
  const date = new Date(isoString);
  return date.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatDate(isoString: string | null): string {
  if (!isoString) return 'N/A';
  const date = new Date(isoString);
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

export function formatTime(isoString: string | null): string {
  if (!isoString) return 'N/A';
  const date = new Date(isoString);
  return date.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
}

export function formatHealthScore(
  score: 'EXCELLENT' | 'GOOD' | 'FAIR' | 'POOR' | 'CRITICAL'
): { text: string; color: string } {
  switch (score) {
    case 'EXCELLENT':
      return { text: 'EXCELLENT', color: 'text-green-400' };
    case 'GOOD':
      return { text: 'GOOD', color: 'text-green-500' };
    case 'FAIR':
      return { text: 'FAIR', color: 'text-yellow-400' };
    case 'POOR':
      return { text: 'POOR', color: 'text-orange-400' };
    case 'CRITICAL':
      return { text: 'CRITICAL', color: 'text-red-400' };
    default:
      return { text: 'UNKNOWN', color: 'text-gray-400' };
  }
}

export function formatCircuitState(
  state: 'CLOSED' | 'OPEN' | 'HALF_OPEN'
): { text: string; color: string } {
  switch (state) {
    case 'CLOSED':
      return { text: 'CLOSED', color: 'text-green-400' };
    case 'HALF_OPEN':
      return { text: 'HALF_OPEN', color: 'text-yellow-400' };
    case 'OPEN':
      return { text: 'OPEN', color: 'text-red-400' };
    default:
      return { text: 'UNKNOWN', color: 'text-gray-400' };
  }
}

export function formatSeverity(
  severity: 'info' | 'warning' | 'critical'
): { symbol: string; color: string } {
  switch (severity) {
    case 'info':
      return { symbol: 'i', color: 'text-blue-400' };
    case 'warning':
      return { symbol: '!', color: 'text-yellow-400' };
    case 'critical':
      return { symbol: 'X', color: 'text-red-400' };
    default:
      return { symbol: '?', color: 'text-gray-400' };
  }
}

export function formatBetResult(
  result: 'pending' | 'won' | 'lost' | 'void'
): { text: string; color: string; symbol: string } {
  switch (result) {
    case 'pending':
      return { text: 'PENDING', color: 'text-yellow-400', symbol: '...' };
    case 'won':
      return { text: 'WON', color: 'text-green-400', symbol: '+' };
    case 'lost':
      return { text: 'LOST', color: 'text-red-400', symbol: '-' };
    case 'void':
      return { text: 'VOID', color: 'text-gray-400', symbol: '~' };
    default:
      return { text: 'UNKNOWN', color: 'text-gray-400', symbol: '?' };
  }
}

export function formatOpportunityType(
  type: 'arbitrage' | 'value' | 'bonus'
): { text: string; color: string; symbol: string } {
  switch (type) {
    case 'arbitrage':
      return { text: 'ARB', color: 'text-cyan-400', symbol: '<<>>' };
    case 'value':
      return { text: 'VALUE', color: 'text-purple-400', symbol: '<$>' };
    case 'bonus':
      return { text: 'BONUS', color: 'text-yellow-400', symbol: '<*>' };
    default:
      return { text: 'UNKNOWN', color: 'text-gray-400', symbol: '<?>' };
  }
}

export function formatEventName(homeTeam: string, awayTeam: string): string {
  return `${homeTeam} vs ${awayTeam}`;
}

export function formatTrend(
  direction: 'IMPROVING' | 'STABLE' | 'DEGRADING'
): { symbol: string; color: string } {
  switch (direction) {
    case 'IMPROVING':
      return { symbol: '^', color: 'text-green-400' };
    case 'STABLE':
      return { symbol: '=', color: 'text-gray-400' };
    case 'DEGRADING':
      return { symbol: 'v', color: 'text-red-400' };
    default:
      return { symbol: '-', color: 'text-gray-400' };
  }
}

export function formatNumber(value: number, decimals = 0): string {
  return value.toFixed(decimals);
}

export function formatBoolean(value: boolean): string {
  return value ? 'YES' : 'NO';
}

export function formatRetention(retention: number): { text: string; color: string } {
  if (retention >= 90) return { text: `${retention.toFixed(1)}%`, color: 'text-green-400' };
  if (retention >= 80) return { text: `${retention.toFixed(1)}%`, color: 'text-yellow-400' };
  return { text: `${retention.toFixed(1)}%`, color: 'text-red-400' };
}

// ============ TTK (Time-to-Kickoff) Utilities ============

/** Hours from now until event start. Returns null if no start time. */
export function getTTKFromNow(startTime: string | null | undefined): number | null {
  if (!startTime) return null;
  try {
    const start = new Date(startTime).getTime();
    const now = Date.now();
    return Math.max(0, (start - now) / (1000 * 60 * 60));
  } catch { return null; }
}

/** Format TTK hours to compact label: 45m, 5h, 4d 2h */
export function formatTTKLabel(hours: number | null): string {
  if (hours === null) return '-';
  if (hours < 1) return `${Math.round(hours * 60)}m`;
  if (hours < 24) return `${Math.round(hours)}h`;
  const days = Math.floor(hours / 24);
  const remainHours = Math.round(hours % 24);
  if (remainHours === 0) return `${days}d`;
  return `${days}d ${remainHours}h`;
}

/** Get color class for TTK tier: lower TTK with edge = higher confidence it's real */
export function getTTKColor(hours: number | null): string {
  if (hours === null) return 'text-muted';
  if (hours <= 6) return 'text-success';   // Near kickoff — highest confidence
  if (hours <= 12) return 'text-yellow';   // Sweet spot — sharp line stable, soft books lag
  if (hours <= 24) return 'text-warning';  // Decent — line mostly settled
  if (hours <= 48) return 'text-error';    // Early — edge may shift
  return 'text-muted2';                    // Too early — edge may evaporate
}

// ============ Markdown Table Formatters for Terminal Output ============

import type { BankrollExposure, OpportunityWithEvent, Bet, BankrollStats, BonusArbOpportunity } from '@/types';

/**
 * Format bankroll exposure as compact table
 */
export function formatBankrollTable(exposure: BankrollExposure): string {
  if (!exposure || exposure.providers.length === 0) {
    return 'No bankroll data. Configure providers first.';
  }

  const fmt = (n: number) => n % 1 === 0 ? `${n} kr` : `${n.toFixed(0)} kr`;

  // Find max provider name length for dynamic column width (after stripping suffixes)
  const maxNameLen = Math.max(
    8, // minimum "Provider" header length
    ...exposure.providers.map((p) => formatProviderName(p.provider_name).length)
  );

  const rows = exposure.providers
    .map((p) => {
      const name = formatProviderName(p.provider_name).padEnd(maxNameLen);
      const bal = fmt(p.total_balance).padStart(7);
      const pend = fmt(p.pending_exposure).padStart(6);
      const free = fmt(p.available).padStart(7);
      return `${name} | ${bal} | ${pend} | ${free}`;
    })
    .join('\n');

  const headerLine = 'Provider'.padEnd(maxNameLen);
  const separatorLine = '-'.repeat(maxNameLen);

  return `Total: ${fmt(exposure.total_balance)} | Pending: ${fmt(exposure.total_pending)} | Free: ${fmt(exposure.total_available)}

\`\`\`
${headerLine} | Bal     | Pend  | Free
${separatorLine}-|---------|-------|-------
${rows}
\`\`\``;
}

/**
 * Truncate string to max length with ellipsis
 */
function truncate(str: string, maxLen: number): string {
  if (str.length <= maxLen) return str;
  return str.slice(0, maxLen - 1) + '…';
}

/**
 * Pad string to fixed width (left or right aligned)
 */
function pad(str: string, width: number, align: 'left' | 'right' = 'left'): string {
  if (align === 'right') {
    return str.padStart(width);
  }
  return str.padEnd(width);
}

/**
 * Format short datetime for table display (e.g., "3 Feb 18:30")
 */
function formatShortDateTime(isoString: string | null | undefined): string {
  if (!isoString) return '-';
  const date = new Date(isoString);
  const day = date.getDate();
  const month = date.toLocaleDateString('en-US', { month: 'short' });
  const time = date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
  return `${day} ${month} ${time}`;
}

/**
 * Convert outcome code to team name
 */
export function outcomeToTeam(outcome: string | undefined, homeTeam?: string, awayTeam?: string): string {
  if (!outcome) return '-';
  if (outcome === 'home') return homeTeam || 'home';
  if (outcome === 'away') return awayTeam || 'away';
  if (outcome === 'draw') return 'draw';
  return outcome; // Return as-is if already a team name
}

/**
 * Format opportunities list as compact ASCII table
 * Now includes: Bet (team name), Sport, Time, Fair odds columns
 * Sorted by edge_pct descending (backend handles sorting)
 */
export function formatOpportunitiesList(
  opportunities: OpportunityWithEvent[],
  _count: number
): string {
  if (opportunities.length === 0) {
    return 'No opportunities found.';
  }

  const rows = opportunities
    .slice(0, 20)
    .map((opp, idx) => {
      const num = pad(String(idx + 1), 2, 'right');
      const value = opp.type === 'arbitrage' ? opp.profit_pct : opp.edge_pct;
      const edge = pad(`${value?.toFixed(1)}%`, 5, 'right');
      // Get team names
      const homeTeam = opp.home_team || opp.event?.home_team;
      const awayTeam = opp.away_team || opp.event?.away_team;
      // Convert outcome to team name
      const betOn = outcomeToTeam(opp.outcome1, homeTeam, awayTeam);
      const bet = pad(truncate(betOn, 18), 18);
      const provider = pad(truncate(opp.provider1, 10), 10);
      const odds = opp.odds1.toFixed(2).padStart(5);
      const fair = opp.fair_odds ? opp.fair_odds.toFixed(2).padStart(5) : '    -';
      const sport = pad(opp.sport || opp.event?.sport || '-', 10);
      const time = pad(formatShortDateTime(opp.starts_at || opp.event?.start_time), 12);
      return `${num} | ${edge} | ${bet} | ${provider} | ${odds} | ${fair} | ${sport} | ${time}`;
    })
    .join('\n');

  return `\`\`\`
# |   %   | Bet on             | Provider   | Odds  | Fair  | Sport      | Time
--|-------|--------------------|-----------:|------:|------:|------------|-------------
${rows}
\`\`\``;
}

/**
 * Format bets list as compact table
 */
export function formatBetsTable(bets: Bet[], status?: string): string {
  if (bets.length === 0) {
    return `No ${status || 'bets'} found.`;
  }

  const rows = bets
    .slice(0, 20)
    .map((bet) => {
      const st = bet.result === 'pending' ? '...' : bet.result === 'won' ? '+' : bet.result === 'lost' ? '-' : '~';
      const id = String(bet.id).padStart(2);
      const provider = truncate(bet.provider, 15).padEnd(15);
      const odds = bet.odds.toFixed(2).padStart(5);
      const stake = `${bet.stake.toFixed(0)}kr`.padStart(6);
      const pl = bet.result === 'pending' ? '-'.padStart(7)
        : bet.profit >= 0 ? `+${bet.profit.toFixed(0)}kr`.padStart(7)
        : `-${Math.abs(bet.profit).toFixed(0)}kr`.padStart(7);
      return `${id} | ${st}  | ${provider} | ${odds} | ${stake} | ${pl}`;
    })
    .join('\n');

  return `\`\`\`
# | St  | Provider        | Odds  | Stake  | P/L
--|-----|-----------------|-------|--------|-------
${rows}
\`\`\``;
}

/**
 * Format betting statistics report - compact 2-line summary
 */
export function formatStatsReport(stats: BankrollStats): string {
  const winRate = stats.total_bets > 0 ? ((stats.wins / stats.total_bets) * 100).toFixed(0) : '0';
  const profit = stats.total_profit || 0;
  const profitStr = profit >= 0 ? `+${profit.toFixed(0)} kr` : `-${Math.abs(profit).toFixed(0)} kr`;
  const roi = stats.roi_pct || 0;

  return `Bets: ${stats.total_bets} | W/L: ${stats.wins}/${stats.losses} | Win: ${winRate}%
Staked: ${(stats.total_staked || 0).toFixed(0)} kr | Profit: ${profitStr} | ROI: ${roi.toFixed(1)}%`;
}


/**
 * Format bonus arbitrage opportunities - compact (true arb with hedges)
 */
export function formatBonusArbitrage(
  opportunities: BonusArbOpportunity[],
  anchorProvider: string,
  totalBankroll: number,
  anchorBalance: number
): string {
  if (opportunities.length === 0) {
    return `No bonus arb for ${anchorProvider}. Run /extract first.`;
  }

  const rows = opportunities
    .slice(0, 20)
    .map((opp, idx) => {
      const num = pad(String(idx + 1), 2, 'right');
      const profitSign = opp.profit_pct >= 0 ? '+' : '';
      const profit = pad(`${profitSign}${opp.profit_pct.toFixed(1)}%`, 7, 'right');
      const eventName = opp.home_team && opp.away_team
        ? `${opp.home_team} vs ${opp.away_team}`
        : 'Unknown';
      const match = pad(truncate(eventName, 30), 30);
      // Show anchor bet with team name
      const anchorLeg = opp.legs.find(l => l.is_anchor);
      const betOn = anchorLeg ? outcomeToTeam(anchorLeg.outcome, opp.home_team || undefined, opp.away_team || undefined) : '-';
      const bet = pad(truncate(betOn, 15), 15);
      return `${num} | ${profit} | ${match} | ${bet}`;
    })
    .join('\n');

  return `${anchorProvider.toUpperCase()} bonus arbitrage | Bankroll: ${totalBankroll.toFixed(0)} kr | Balance: ${anchorBalance.toFixed(0)} kr

\`\`\`
# | Profit  | Match                          | Bet on
--|---------|--------------------------------|----------------
${rows}
\`\`\``;
}
