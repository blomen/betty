import { displayTeamName } from '@/utils/formatters';

/**
 * Convert a market key like "spread_-1.5" or "total_2.5" to a display label.
 * moneyline → "ML", 1x2 → "1X2", moneyline_m1 → "Map 1", total_m2 → "T Map 2".
 */
export function marketLabel(market: string): string {
  if (market === 'moneyline') return 'ML';
  if (market === 'spread') return 'SP';
  if (market === 'total') return 'TOT';
  if (market === '1x2') return '1X2';
  // Esports map markets: moneyline_m1 → "Map 1", total_m2 → "T Map 2"
  const mapMatch = market.match(/^(moneyline|total)_m(\d)$/);
  if (mapMatch) {
    const prefix = mapMatch[1] === 'total' ? 'T ' : '';
    return `${prefix}Map ${mapMatch[2]}`;
  }
  return market.toUpperCase();
}

/**
 * Resolve an outcome identifier to a human-readable display name.
 *
 * @param outcome - "home", "away", "draw", "over", "under", or raw string
 * @param event   - Object with home_team, away_team and optional display variants
 * @param point   - Spread/total point value (appended after team name for spread, after Over/Under for totals)
 * @param withTag - If true, append " [ML]", " [SPREAD]", etc. based on event.market
 */
export function resolveOutcome(
  outcome: string,
  event: {
    home_team?: string | null;
    away_team?: string | null;
    display_home?: string | null;
    display_away?: string | null;
    prov_home?: string | null;
    prov_away?: string | null;
    market?: string | null;
  },
  point?: number | null,
  withTag = false,
): string {
  const p = point != null ? ` ${point}` : '';
  const tag = withTag && event.market ? ` [${marketLabel(event.market)}]` : '';
  if (outcome === 'home') return `${displayTeamName(event.home_team, event.display_home ?? event.prov_home)}${p}${tag}`;
  if (outcome === 'away') return `${displayTeamName(event.away_team, event.display_away ?? event.prov_away)}${p}${tag}`;
  if (outcome === 'draw') return `Draw${tag}`;
  if (outcome === 'over') return `Over${p}${tag}`;
  if (outcome === 'under') return `Under${p}${tag}`;
  return `${outcome}${tag}`;
}

/**
 * Format an amount in its native currency ($ for USD/USDC, kr for SEK).
 */
export function fmtAmount(amount: number, currency: string, decimals?: number): string {
  if (currency === 'USD' || currency === 'USDC') {
    return `$${amount.toFixed(decimals ?? 2)}`;
  }
  return `${amount.toFixed(decimals ?? 0)} kr`;
}

/**
 * Format profit with +/- prefix in native currency.
 */
export function fmtProfit(amount: number, currency: string): string {
  const prefix = amount >= 0 ? '+' : '-';
  if (currency === 'USD' || currency === 'USDC') {
    return `${prefix}$${Math.abs(amount).toFixed(2)}`;
  }
  return `${prefix}${Math.abs(amount).toFixed(0)} kr`;
}

/**
 * Typical sport durations in milliseconds — used for categorizing
 * live/finished bets when no explicit match_status is available.
 */
export const SPORT_DURATION: Record<string, number> = {
  football: 2.5 * 3600000,
  basketball: 3 * 3600000,
  ice_hockey: 3 * 3600000,
  tennis: 4 * 3600000,
  esports: 4 * 3600000,
  handball: 2.5 * 3600000,
  mma: 3 * 3600000,
};

export const DEFAULT_DURATION = 3 * 3600000;
