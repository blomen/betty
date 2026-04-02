import type { ProviderLimit, BettingSnapshot } from '@/types';
import { fetchJson, fetchWithRetry, API_BASE } from './client';
import type { ExtractionSettingsResponse } from './client';

export const settingsApi = {
  // ============ Limits ============
  async getLimits(providerId?: string): Promise<ProviderLimit[]> {
    const params = providerId ? `?provider_id=${providerId}` : '';
    return fetchJson<ProviderLimit[]>(`/limits${params}`);
  },

  async createLimit(data: {
    provider_id: string;
    limit_type: string;
    limit_level: number;
    notes?: string;
  }): Promise<{ success: boolean; id: number; betting_snapshot: BettingSnapshot }> {
    return fetchJson('/limits', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  async updateLimit(id: number, data: {
    limit_level?: number;
    notes?: string;
  }): Promise<{ success: boolean }> {
    return fetchJson(`/limits/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  async deleteLimit(id: number): Promise<{ success: boolean }> {
    return fetchJson(`/limits/${id}`, { method: 'DELETE' });
  },

  async updateProviderLimitRisk(providerId: string, data: {
    limit_risk: string;
    limit_notes?: string;
  }): Promise<{ success: boolean }> {
    return fetchJson(`/providers/${providerId}/limit-risk`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  // ============ Settings ============

  async getExtractionSettings(): Promise<ExtractionSettingsResponse> {
    return fetchJson('/settings/extraction', { cache: 'no-store' });
  },

  async toggleExtractionProvider(providerId: string, enabled: boolean): Promise<{ success: boolean }> {
    return fetchJson('/settings/extraction/provider', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider_id: providerId, enabled }),
    });
  },

  async toggleExtractionBatch(providerIds: string[], enabled: boolean): Promise<{ success: boolean }> {
    return fetchJson('/settings/extraction/batch', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider_ids: providerIds, enabled }),
    });
  },

  async getExtractionAnalytics() {
    return fetchWithRetry<{
      provider_roi: Array<{
        provider_id: string;
        total_opportunities: number;
        avg_edge: number;
        total_bets: number;
        win_rate: number | null;
        net_pnl: number;
      }>;
      coverage_gaps: Array<{
        provider_id: string;
        sport: string;
        pinnacle_events: number;
        matched_events: number;
        event_coverage_pct: number;
        missing_events: number;
        spread_count: number;
        total_count: number;
        pinnacle_spread_count: number;
        pinnacle_total_count: number;
      }>;
      scheduling: Record<string, {
        runs: number;
        avg_duration: number;
        avg_events: number;
        events_per_sec: number;
      }>;
    }>(`${API_BASE}/extraction/analytics`);
  },

  async getExtractionRecommendations() {
    return fetchWithRetry<Array<{
      id: number;
      provider_id: string;
      category: string;
      severity: string;
      message: string;
      status: string;
      before_metric: number | null;
      after_metric: number | null;
      source: string;
      created_at: string | null;
    }>>(`${API_BASE}/extraction/recommendations`);
  },

  async getMlStatus(): Promise<Record<string, { loaded: boolean; data_ready: boolean; min_samples: number }>> {
    return fetchJson('/extraction/ml/status');
  },

  async triggerMlTraining(): Promise<Record<string, string>> {
    return fetchJson('/extraction/ml/train', { method: 'POST' });
  },

  async updateRecommendation(id: number, status: string, afterMetric?: number) {
    const params = new URLSearchParams({ status });
    if (afterMetric !== undefined) params.set("after_metric", String(afterMetric));
    return fetchWithRetry<{ id: number; status: string }>(
      `${API_BASE}/extraction/recommendations/${id}?${params}`,
      { method: "PATCH" }
    );
  },

  // ============ Postmortem ============

  async getPostmortemBets(filters?: { classification?: string; market?: string; provider?: string }): Promise<{ postmortems: any[]; count: number }> {
    const params = new URLSearchParams();
    if (filters?.classification) params.set('classification', filters.classification);
    if (filters?.market) params.set('market', filters.market);
    if (filters?.provider) params.set('provider', filters.provider);
    return fetchJson(`/postmortem/bets?${params}`);
  },

  async getPostmortemBetsSummary(): Promise<{ summary: any[]; total: number }> {
    return fetchJson('/postmortem/bets/summary');
  },

  async getPostmortemBetsPatterns(): Promise<{ patterns: any[] }> {
    return fetchJson('/postmortem/bets/patterns');
  },

  async getPostmortemTradesSummary(accountId: number): Promise<{ summary: any[]; total: number }> {
    return fetchJson(`/postmortem/trades/summary?account_id=${accountId}`);
  },

  async getPostmortemTradesPatterns(accountId: number): Promise<{ patterns: any[] }> {
    return fetchJson(`/postmortem/trades/patterns?account_id=${accountId}`);
  },

  async recomputePostmortems(profileId?: number, accountId?: number): Promise<{ bets_recomputed: number; trades_recomputed: number }> {
    const params = new URLSearchParams();
    if (profileId) params.set('profile_id', String(profileId));
    if (accountId) params.set('account_id', String(accountId));
    return fetchJson(`/postmortem/recompute?${params}`, { method: 'POST' });
  },

  // ============ Mirror ============

  async getMirrorStatus(): Promise<{ running: boolean; status: string; since: string | null }> {
    return fetchJson('/mirror/status');
  },

  async startMirror(): Promise<{ running: boolean; status: string; since: string }> {
    return fetchJson('/mirror/start', { method: 'POST' });
  },

  async stopMirror(): Promise<any> {
    return fetchJson('/mirror/stop', { method: 'POST' });
  },

  async getMirrorSettlements(): Promise<{ settlements: any[] }> {
    return fetchJson('/mirror/settlements');
  },

  async confirmMirrorSettlements(): Promise<any> {
    return fetchJson('/mirror/settlements/confirm', { method: 'POST' });
  },

  async rejectMirrorSettlements(): Promise<any> {
    return fetchJson('/mirror/settlements/reject', { method: 'POST' });
  },

  async getLiveEdge(bets: { event_id: string; market: string; outcome: string; odds: number; stake: number }[]): Promise<any> {
    return fetchJson('/mirror/live-edge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bets }),
    }, 120_000); // 2 min — opening tabs + loading pages is slow
  },

  async fireLive(bets: { event_id: string; market: string; outcome: string; odds: number; stake: number }[]): Promise<any> {
    return fetchJson('/mirror/fire-live', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bets }),
    }, 300_000); // 5 min — reads live prices + places each bet sequentially
  },
};
