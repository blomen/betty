import type { PolymarketValueResponse } from '@/types';
import { fetchJson } from './client';
import type { SpecialsResponse, StakePreviewResult } from './client';

export const specialsApi = {
  // ============ Oddsboost ============
  async getSpecials(filters?: {
    sport?: string;
    provider?: string;
    category?: string;
    sort?: string;
    order?: string;
    measurable_only?: boolean;
  }): Promise<SpecialsResponse> {
    const params = new URLSearchParams();
    if (filters?.sport) params.set('sport', filters.sport);
    if (filters?.provider) params.set('provider', filters.provider);
    if (filters?.category) params.set('category', filters.category);
    if (filters?.sort) params.set('sort', filters.sort);
    if (filters?.order) params.set('order', filters.order);
    if (filters?.measurable_only !== undefined) params.set('measurable_only', String(filters.measurable_only));
    const qs = params.toString();
    return fetchJson(`/specials${qs ? `?${qs}` : ''}`);
  },

  async getBoostStakePreview(data: {
    edge_pct: number;
    odds: number;
    event_id?: string;
    provider_id?: string;
  }): Promise<StakePreviewResult> {
    return fetchJson('/bankroll/stake-preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  // ============ Polymarket ============
  async getPolymarketValue(
    minEdge = 3.0,
    sport?: string,
    limit = 50
  ): Promise<PolymarketValueResponse> {
    const params = new URLSearchParams();
    params.set('min_edge', minEdge.toString());
    if (sport) params.set('sport', sport);
    params.set('limit', limit.toString());
    return fetchJson(`/polymarket/value?${params}`);
  },

  async getPolymarketRewards(
    minDailyRate = 0,
    sport?: string,
    limit = 100
  ): Promise<import('@/types').PolymarketRewardsResponse> {
    const params = new URLSearchParams();
    if (minDailyRate > 0) params.set('min_daily_rate', minDailyRate.toString());
    if (sport) params.set('sport', sport);
    params.set('limit', limit.toString());
    return fetchJson(`/polymarket/rewards?${params}`, undefined, 60_000);
  },
};
