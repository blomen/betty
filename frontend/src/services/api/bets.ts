import type { Bet, BonusArbResponse } from '@/types';
import { fetchJson, fetchWithRetry } from './client';

export const betsApi = {
  async getBets(
    status?: 'pending' | 'won' | 'lost' | 'void',
    limit = 50
  ): Promise<{ bets: Bet[]; count: number }> {
    const params = new URLSearchParams();
    if (status) params.set('status', status);
    params.set('limit', limit.toString());
    return fetchJson(`/bets?${params}`);
  },

  async createBet(data: {
    event_id?: string;
    provider_id: string;
    market?: string;
    outcome?: string;
    odds: number;
    stake: number;
    point?: number | null;
    is_bonus?: boolean;
    bonus_type?: string;
    utility_score?: number;
    selection_probability?: number;
    fair_odds_at_placement?: number;
    boost_event?: string;
    boost_title?: string;
    bet_type?: string;
    start_time?: string;
  }): Promise<{ success: boolean; bet_id: number }> {
    return fetchWithRetry('/bets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }, 2, 30000);
    // 2 retries + 30s timeout: backend retries SQLite locks internally (up to ~5s),
    // and duplicate-bet check prevents double-placing on frontend retry.
  },

  async createBatchBets(legs: {
    event_id?: string;
    provider_id: string;
    market?: string;
    outcome?: string;
    odds: number;
    stake: number;
    point?: number | null;
    is_bonus?: boolean;
    utility_score?: number;
    selection_probability?: number;
    bet_type?: string;
  }[]): Promise<{
    success: boolean;
    placed_count: number;
    total_legs: number;
    total_staked: number;
    results: {
      leg_index: number;
      provider_id: string;
      outcome: string;
      success: boolean;
      error?: string;
      bet_id?: number;
      stake?: number;
      odds?: number;
    }[];
  }> {
    return fetchWithRetry('/bets/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ legs }),
    }, 2, 30000);
  },

  async editBet(
    betId: number,
    data: { stake?: number; odds?: number; result?: string; payout?: number }
  ): Promise<{ success: boolean; stake: number; odds: number; result: string; payout: number; profit: number; balance_adjustment: number }> {
    return fetchJson(`/bets/${betId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  async getAnalytics(
    providerId?: string,
    days = 90
  ): Promise<{
    provider_id: string | null;
    days: number;
    cutoff: string;
    overall: AnalyticsBucket | null;
    by_sport: Record<string, AnalyticsBucket>;
    by_edge_bucket: Record<string, AnalyticsBucket>;
    by_sport_and_bucket: Record<string, AnalyticsBucket>;
    by_sport_and_market: Record<string, AnalyticsBucketWithMultiplier>;
    bucket_confidence_enabled: boolean;
  }> {
    const params = new URLSearchParams();
    if (providerId) params.set('provider_id', providerId);
    params.set('days', String(days));
    return fetchJson(`/bets/analytics?${params}`);
  },

  async getBonusArbs(
    window: 'today' | 'week' | '30d' = 'week'
  ): Promise<BonusArbResponse> {
    const params = new URLSearchParams();
    params.set('window', window);
    return fetchJson(`/bets/bonus-arbs?${params}`);
  },
};

export type AnalyticsBucket = {
  n: number;
  won: number;
  lost: number;
  void: number;
  win_pct: number | null;
  implied_pct: number | null;
  avg_displayed_edge_pct: number | null;
  staked: number;
  profit: number;
  roi_pct: number | null;
  avg_clv_pct: number | null;
};

export type AnalyticsBucketWithMultiplier = AnalyticsBucket & {
  confidence_multiplier: number;
};
