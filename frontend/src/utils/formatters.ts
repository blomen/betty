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
