/**
 * Format utilities for displaying betting data
 */

export function formatCurrency(value: number, currency = '$'): string {
  return `${currency}${value.toFixed(2)}`;
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

// ============ Markdown Table Formatters for Terminal Output ============

import type { BankrollExposure, OpportunityWithEvent, Bet, BankrollStats } from '@/types';

/**
 * Format bankroll exposure as markdown table
 */
export function formatBankrollTable(exposure: BankrollExposure): string {
  if (!exposure || exposure.providers.length === 0) {
    return '**No bankroll data available.** Make sure providers are configured.';
  }

  const rows = exposure.providers
    .map((p) => {
      const provider = p.provider_name.padEnd(12);
      const balance = `$${p.total_balance.toFixed(2)}`.padStart(9);
      const pending = `$${p.pending_exposure.toFixed(2)}`.padStart(9);
      const available = `$${p.available.toFixed(2)}`.padStart(9);
      const active = String(p.pending_bets_count).padStart(6);
      return `| ${provider} | ${balance} | ${pending} | ${available} | ${active} |`;
    })
    .join('\n');

  const total = `$${exposure.total_balance.toFixed(2)}`;
  const totalPending = `$${exposure.total_pending.toFixed(2)}`;
  const totalAvailable = `$${exposure.total_available.toFixed(2)}`;

  return `**BANKROLL BREAKDOWN**

Total: ${total} | Pending: ${totalPending} | Available: ${totalAvailable}

| Provider     | Balance   | Pending   | Available | Active |
|--------------|-----------|-----------|-----------|--------|
${rows}

*Use /bets to see pending bets*`;
}

/**
 * Format opportunities list as markdown cards
 */
export function formatOpportunitiesList(
  opportunities: OpportunityWithEvent[],
  count: number
): string {
  if (opportunities.length === 0) {
    return '**No opportunities found.** Try adjusting filters or run /extractall to update data.';
  }

  const cards = opportunities
    .slice(0, 20) // Limit to 20 for readability
    .map((opp, idx) => {
      const typeIcon =
        opp.type === 'arbitrage' ? 'ARB' : opp.type === 'value' ? 'VALUE' : 'BONUS';
      const value = opp.type === 'arbitrage' ? opp.profit_pct : opp.edge_pct;
      const valueLabel = opp.type === 'arbitrage' ? 'profit' : 'edge';

      const eventName = opp.event
        ? `${opp.event.home_team} vs ${opp.event.away_team}`
        : 'Unknown Event';
      const eventDetails = opp.event
        ? `${opp.event.sport} - ${opp.event.league} | ${formatDateTime(opp.event.start_time)}`
        : 'No details';

      const line2 = opp.provider2
        ? `└─ ${opp.provider2}: ${opp.outcome2} @ ${opp.odds2?.toFixed(2)}`
        : '';

      return `**[${idx + 1}] ${typeIcon}** - ${value?.toFixed(2)}% ${valueLabel}
${eventName}
${eventDetails}
├─ ${opp.provider1}: ${opp.outcome1} @ ${opp.odds1.toFixed(2)}
${line2}`.trim();
    })
    .join('\n\n');

  const countNote = opportunities.length < count ? `\n\n*Showing ${opportunities.length} of ${count} opportunities*` : '';

  return `**OPPORTUNITIES** (${count} found)

${cards}${countNote}

*To place a bet, type: "Place bet on #<number>" or "Bet $<amount> on #<number>"*`;
}

/**
 * Format bets list as markdown table
 */
export function formatBetsTable(bets: Bet[], status?: string): string {
  if (bets.length === 0) {
    return `**No ${status || 'bets'} found.**`;
  }

  const rows = bets
    .slice(0, 20) // Limit to 20 for readability
    .map((bet) => {
      const statusIcon =
        bet.result === 'pending'
          ? '...'
          : bet.result === 'won'
          ? '+'
          : bet.result === 'lost'
          ? '-'
          : '~';
      const id = String(bet.id).padStart(4);
      const res = bet.result.padEnd(8);
      const provider = bet.provider.padEnd(10);
      const outcome = (bet.outcome || 'N/A').padEnd(15);
      const odds = bet.odds.toFixed(2).padStart(5);
      const stake = `$${bet.stake.toFixed(2)}`.padStart(8);
      const profit = bet.profit ?? 0;
      const profitStr =
        profit >= 0 ? `+$${profit.toFixed(2)}`.padStart(9) : `-$${Math.abs(profit).toFixed(2)}`.padStart(9);

      return `| ${id} | ${statusIcon} ${res} | ${provider} | ${outcome} | ${odds} | ${stake} | ${profitStr} |`;
    })
    .join('\n');

  const statusLabel = status ? ` (${status})` : '';
  const countNote = bets.length === 20 ? '\n\n*Showing first 20 bets. Use filters to narrow results.*' : '';

  return `**BETS${statusLabel}**

| ID   | Status    | Provider   | Outcome         | Odds  | Stake    | Profit    |
|------|-----------|------------|-----------------|-------|----------|-----------|
${rows}${countNote}

*To settle a pending bet, type: "/settle-bet <id> won" or "/settle-bet <id> lost"*`;
}

/**
 * Format betting statistics report
 */
export function formatStatsReport(stats: BankrollStats): string {
  const winRate = stats.total_bets > 0 ? ((stats.wins / stats.total_bets) * 100).toFixed(1) : '0.0';
  const totalStaked = stats.total_staked || 0;
  const totalProfit = stats.total_profit || 0;
  const totalReturns = totalStaked + totalProfit;
  const roi = stats.roi_pct || 0;

  return `**BETTING STATISTICS**

Total Bets: ${stats.total_bets}
Win Rate: ${winRate}% (${stats.wins}/${stats.total_bets})
ROI: ${roi.toFixed(2)}%

Total Staked: $${totalStaked.toFixed(2)}
Total Returns: $${totalReturns.toFixed(2)}
Net Profit: $${totalProfit.toFixed(2)}

By Status:
- Won: ${stats.wins} bets
- Lost: ${stats.losses} bets
- Void: ${stats.voids} bets`;
}
