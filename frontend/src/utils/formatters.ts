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

export function formatDateTime(isoString: string | null | undefined): string {
  if (!isoString) return 'N/A';
  const date = new Date(isoString);
  return date.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/**
 * Title-case a normalized team name: "odense bulldogs" → "Odense Bulldogs"
 */
function toTitleCase(s: string): string {
  return s.replace(/\b\w/g, c => c.toUpperCase());
}

/**
 * Get display name for a team, preferring the original cased name from the API
 * and falling back to title-cased normalized name.
 */
export function displayTeamName(normalized: string | undefined | null, display: string | undefined | null): string {
  if (display) return display;
  if (normalized) return toTitleCase(normalized);
  return '';
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

import type { BankrollExposure, OpportunityWithEvent, Bet, BonusArbOpportunity } from '@/types';

function truncate(str: string, maxLen: number): string {
  if (str.length <= maxLen) return str;
  return str.slice(0, maxLen - 1) + '…';
}

function pad(str: string, width: number, align: 'left' | 'right' = 'left'): string {
  if (align === 'right') {
    return str.padStart(width);
  }
  return str.padEnd(width);
}

function formatShortDateTime(isoString: string | null | undefined): string {
  if (!isoString) return '-';
  const date = new Date(isoString);
  const day = date.getDate();
  const month = date.toLocaleDateString('en-US', { month: 'short' });
  const time = date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
  return `${day} ${month} ${time}`;
}

export function outcomeToTeam(outcome: string | undefined, homeTeam?: string, awayTeam?: string): string {
  if (!outcome) return '-';
  if (outcome === 'home') return homeTeam || 'home';
  if (outcome === 'away') return awayTeam || 'away';
  if (outcome === 'draw') return 'draw';
  return outcome;
}

export function formatBankrollTable(exposure: BankrollExposure): string {
  if (!exposure || exposure.providers.length === 0) {
    return 'No bankroll data. Configure providers first.';
  }

  const fmt = (n: number) => n % 1 === 0 ? `${n} kr` : `${n.toFixed(0)} kr`;

  const maxNameLen = Math.max(
    8,
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
      const homeTeam = opp.home_team || opp.event?.home_team;
      const awayTeam = opp.away_team || opp.event?.away_team;
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
