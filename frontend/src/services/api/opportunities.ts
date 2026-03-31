import type { Opportunity, ClusterInfo, ClusterSummary, PlaySession, BatchResult, ClusterBatchResult, ClusterBet, AllocationResult, PendingBetsResponse, SettleBetResult } from '@/types';
import { fetchJson } from './client';

export const opportunitiesApi = {
  // ============ Events ============
  async getEvents(sport?: string, limit = 50): Promise<{ events: import('@/types').EventSummary[]; count: number }> {
    const params = new URLSearchParams();
    if (sport) params.set('sport', sport);
    params.set('limit', limit.toString());
    return fetchJson(`/events?${params}`);
  },

  // ============ Opportunities ============
  async getOpportunities(
    type?: 'arbitrage' | 'value' | 'bonus' | 'dutch' | 'reverse' | 'reverse_value',
    activeOnly = true,
    provider1?: string,
    provider2?: string,
    providers?: string,
    market?: string,
    sport?: string,
    minValue?: number
  ): Promise<{ opportunities: Opportunity[]; count: number }> {
    const params = new URLSearchParams();
    if (type) params.set('type', type);
    params.set('active_only', activeOnly.toString());
    if (provider1) params.set('provider1', provider1);
    if (provider2) params.set('provider2', provider2);
    if (providers) params.set('providers', providers);
    if (market) params.set('market', market);
    if (sport) params.set('sport', sport);
    if (minValue !== undefined) params.set('min_value', minValue.toString());
    return fetchJson(`/opportunities?${params}`);
  },

  async getDutchWorkflow(
    providers: string[],
    majorOnly: boolean,
    limit: number = 50,
    counterpartProviders?: string[],
  ): Promise<{
    opportunities: unknown[];
    count: number;
    anchor_providers: string[];
    anchor_wagering?: Record<string, {
      status: string; wagered: number; requirement: number; remaining: number;
      progress_pct: number; min_odds: number; bonus_amount: number;
      bonus_type: string | null; days_remaining: number | null;
    }>;
  }> {
    const params = new URLSearchParams();
    params.set('providers', providers.join(','));
    params.set('major_only', String(majorOnly));
    params.set('limit', String(limit));
    if (counterpartProviders && counterpartProviders.length > 0) {
      params.set('counterpart_providers', counterpartProviders.join(','));
    }
    params.set('_t', String(Date.now())); // Cache-bust: live scan, never cache
    return fetchJson(`/opportunities/dutch-workflow?${params}`);
  },

  // ============ Clusters ============

  async getClusters(): Promise<{ clusters: ClusterInfo[] }> {
    return fetchJson('/opportunities/clusters');
  },

  async getClusterSummary(cluster: string): Promise<ClusterSummary> {
    return fetchJson(`/opportunities/cluster-summary?cluster=${encodeURIComponent(cluster)}`);
  },

  async getPlaySession(): Promise<PlaySession> {
    return fetchJson('/opportunities/play/session');
  },

  async getPlayBatch(exclude?: string[], skipSiblings?: string[]): Promise<ClusterBatchResult> {
    const payload: Record<string, string[]> = {};
    if (exclude?.length) payload.exclude = exclude;
    if (skipSiblings?.length) payload.skip_siblings = skipSiblings;
    return fetchJson('/opportunities/play/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: Object.keys(payload).length > 0 ? JSON.stringify(payload) : undefined,
    });
  },

  async lockBatch(batch: ClusterBet[]): Promise<{ locked: boolean; count: number; locked_at: string; ttl_seconds: number }> {
    return fetchJson('/opportunities/play/lock-batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ batch }),
    });
  },

  async unlockBatch(): Promise<{ unlocked: boolean }> {
    return fetchJson('/opportunities/play/unlock-batch', { method: 'POST' });
  },

  async allocateCapital(skipSiblings?: string[], budgetSek?: number, budgetUsdc?: number): Promise<AllocationResult> {
    const payload: Record<string, unknown> = {};
    if (skipSiblings?.length) payload.skip_siblings = skipSiblings;
    if (budgetSek !== undefined) payload.budget_sek = budgetSek;
    if (budgetUsdc !== undefined) payload.budget_usdc = budgetUsdc;
    return fetchJson('/opportunities/play/allocate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: Object.keys(payload).length > 0 ? JSON.stringify(payload) : undefined,
    });
  },

  async confirmCapital(): Promise<BatchResult> {
    return fetchJson('/opportunities/play/confirm-capital', {
      method: 'POST',
    });
  },

  async getPendingBets(): Promise<PendingBetsResponse> {
    return fetchJson('/opportunities/play/pending-bets');
  },

  async settleBet(betId: number, result: 'won' | 'lost' | 'void'): Promise<SettleBetResult> {
    return fetchJson('/opportunities/play/settle-bet', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bet_id: betId, result }),
    });
  },

  async ensureMirrorStarted(): Promise<{ running: boolean; status: string }> {
    return fetchJson('/mirror/ensure-started', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
  },
};
