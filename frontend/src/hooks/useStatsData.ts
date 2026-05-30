import { useQuery } from '@tanstack/react-query';
import { api } from '@/services/api';

export type StatsRange = 'all' | '90d' | '30d' | '7d';
export const RANGE_DAYS: Record<StatsRange, number> = { all: 3650, '90d': 90, '30d': 30, '7d': 7 };

/** Single source of truth for the Stats page, keyed on (profileId, range).
 *  KPIs + equity curve are all-time; analytics + history honor the range. */
export function useStatsData(profileId: number | undefined, range: StatsRange) {
  const days = RANGE_DAYS[range];
  const enabled = profileId != null;

  const stats = useQuery({
    queryKey: ['bankroll', 'stats', profileId],
    queryFn: () => api.getBankrollStats(profileId), staleTime: 30_000, enabled,
  });
  const equity = useQuery({
    queryKey: ['bets', 'equity-curve', profileId],
    queryFn: () => api.getEquityCurve(profileId), staleTime: 30_000, enabled,
  });
  const analytics = useQuery({
    queryKey: ['bets', 'analytics', profileId, days],
    queryFn: () => api.getAnalytics(undefined, days, profileId), staleTime: 60_000, enabled,
  });
  const bets = useQuery({
    queryKey: ['bets', 'all', profileId],
    queryFn: () => api.getBets(undefined, 500, profileId), staleTime: 30_000, enabled,
  });

  return { stats, equity, analytics, bets };
}
