import type {
  Opportunity,
  EventSummary,
  EventDetail,
  ProvidersResponse,
  BankrollInfo,
  BankrollStats,
  BankrollExposure,
  Bet,
  Profile,
  ProfileCreate,
  ProfileUpdate,
  StakeCalculation,
  ExtractionStatus,
  MetricsRun,
  ProviderMetrics,
  CircuitBreakerStatus,
  CacheStats,
  ProviderCacheStats,
  HealthCheckStatus,
  ProviderHealth,
  BonusMatchRequest,
  BonusMatch,
} from '@/types';

const API_BASE = '/api';

async function fetchJson<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`, options);
  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`);
  }
  return response.json();
}

export const api = {
  // ============ Providers ============
  async getProviders(): Promise<ProvidersResponse> {
    return fetchJson<ProvidersResponse>('/providers');
  },

  async createProvider(data: {
    id: string;
    name: string;
    url?: string;
    balance?: number;
  }): Promise<{ success: boolean; provider_id: string }> {
    return fetchJson('/providers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  async updateProvider(
    providerId: string,
    data: {
      name?: string;
      url?: string;
      is_enabled?: boolean;
      balance?: number;
    }
  ): Promise<{ success: boolean; provider_id: string }> {
    return fetchJson(`/providers/${providerId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  // ============ Bankroll ============
  async getBankroll(): Promise<BankrollInfo> {
    return fetchJson<BankrollInfo>('/bankroll');
  },

  async getBankrollStats(): Promise<BankrollStats> {
    return fetchJson<BankrollStats>('/bankroll/stats');
  },

  async setAllBalances(
    balance: number,
    providerIds?: string[]
  ): Promise<{
    success: boolean;
    updated_count: number;
    balance_per_provider: number;
    total_balance: number;
  }> {
    return fetchJson('/bankroll/set-all', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        balance,
        provider_ids: providerIds,
      }),
    });
  },

  async adjustBalance(
    providerId: string,
    amount: number
  ): Promise<{
    success: boolean;
    provider_id: string;
    old_balance: number;
    adjustment: number;
    new_balance: number;
  }> {
    return fetchJson(`/bankroll/adjust/${providerId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ amount }),
    });
  },

  async resetAllBalances(): Promise<{
    success: boolean;
    reset_count: number;
    message: string;
  }> {
    return fetchJson('/bankroll/reset-all', { method: 'POST' });
  },

  async getBankrollExposure(): Promise<BankrollExposure> {
    return fetchJson<BankrollExposure>('/bankroll/exposure');
  },

  // ============ Events ============
  async getEvents(sport?: string, limit = 50): Promise<{ events: EventSummary[]; count: number }> {
    const params = new URLSearchParams();
    if (sport) params.set('sport', sport);
    params.set('limit', limit.toString());
    return fetchJson(`/events?${params}`);
  },

  async getEvent(eventId: string): Promise<EventDetail> {
    return fetchJson<EventDetail>(`/events/${eventId}`);
  },

  // ============ Opportunities ============
  async getOpportunities(
    type?: 'arbitrage' | 'value' | 'bonus',
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

  async findBestHedge(request: BonusMatchRequest): Promise<BonusMatch> {
    return fetchJson<BonusMatch>('/opportunities/bonus/match', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
  },

  // ============ Bets ============
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
    is_bonus?: boolean;
    bonus_type?: string;
  }): Promise<{ success: boolean; bet_id: number }> {
    return fetchJson('/bets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  async settleBet(
    betId: number,
    data: { result: 'won' | 'lost' | 'void'; payout: number }
  ): Promise<{ success: boolean; profit: number }> {
    return fetchJson(`/bets/${betId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  // ============ Stake Calculator ============
  async calculateStake(odds: number, fairOdds: number): Promise<StakeCalculation> {
    return fetchJson('/calculate/stake', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ odds, fair_odds: fairOdds }),
    });
  },

  // ============ Extraction ============
  async getExtractionStatus(): Promise<ExtractionStatus> {
    return fetchJson<ExtractionStatus>('/extraction/status');
  },

  async getExtractionProgress(): Promise<ExtractionStatus> {
    return fetchJson<ExtractionStatus>('/extraction/progress');
  },

  async runExtraction(
    providers?: string  // Optional: "unibet,leovegas" or undefined for all
  ): Promise<{ status: string; providers: string | string[] }> {
    const params = new URLSearchParams();
    if (providers) {
      params.append('providers', providers);
    }

    const url = params.toString()
      ? `/extraction/run?${params}`
      : '/extraction/run';

    return fetchJson(url, { method: 'POST' });
  },

  // ============ Metrics ============
  async getMetricsHistory(limit = 10): Promise<{ history: MetricsRun[]; count: number }> {
    return fetchJson(`/metrics/history?limit=${limit}`);
  },

  async getProviderMetrics(providerId: string, limit = 10): Promise<ProviderMetrics> {
    return fetchJson(`/metrics/provider/${providerId}?limit=${limit}`);
  },

  async getCurrentMetrics(): Promise<MetricsRun | { error: string }> {
    return fetchJson('/metrics/current');
  },

  // ============ Circuit Breaker ============
  async getCircuitBreakerStatus(): Promise<{
    statuses: Record<string, CircuitBreakerStatus>;
  }> {
    return fetchJson('/circuit-breaker/status');
  },

  async getProviderCircuitBreaker(providerId: string): Promise<{
    provider_id: string;
  } & CircuitBreakerStatus> {
    return fetchJson(`/circuit-breaker/status/${providerId}`);
  },

  async resetCircuitBreaker(providerId: string): Promise<{
    success: boolean;
    provider_id: string;
    message: string;
  }> {
    return fetchJson(`/circuit-breaker/reset/${providerId}`, { method: 'POST' });
  },

  // ============ Cache ============
  async getCacheStats(): Promise<CacheStats> {
    return fetchJson<CacheStats>('/cache/stats');
  },

  async getProviderCacheStats(providerId: string): Promise<{
    provider_id: string;
  } & ProviderCacheStats> {
    return fetchJson(`/cache/stats/${providerId}`);
  },

  async clearCache(providerId?: string): Promise<{ success: boolean; message: string }> {
    const params = providerId ? `?provider_id=${providerId}` : '';
    return fetchJson(`/cache/clear${params}`, { method: 'POST' });
  },

  async evictExpiredCache(): Promise<{ success: boolean; message: string }> {
    return fetchJson('/cache/evict-expired', { method: 'POST' });
  },

  // ============ Health Checks ============
  async getHealthCheckStatus(): Promise<{
    statuses: Record<string, HealthCheckStatus>;
  }> {
    return fetchJson('/health-check/status');
  },

  async runHealthCheck(
    providerId: string,
    force = false
  ): Promise<{
    provider_id: string;
  } & HealthCheckStatus> {
    const params = force ? '?force=true' : '';
    return fetchJson(`/health-check/run/${providerId}${params}`, { method: 'POST' });
  },

  async clearHealthCheckCache(providerId?: string): Promise<{
    success: boolean;
    message: string;
  }> {
    const params = providerId ? `?provider_id=${providerId}` : '';
    return fetchJson(`/health-check/clear-cache${params}`, { method: 'POST' });
  },

  // ============ Provider Monitoring ============
  async monitorAllProviders(limit = 20): Promise<{
    providers: Record<string, ProviderHealth>;
    summary: {
      total_providers: number;
      healthy: number;
      unhealthy: number;
      critical: number;
    };
  }> {
    return fetchJson(`/monitor/providers?limit=${limit}`);
  },

  async monitorProvider(providerId: string, limit = 20): Promise<{
    provider_id: string;
  } & ProviderHealth> {
    return fetchJson(`/monitor/providers/${providerId}?limit=${limit}`);
  },

  async getUnhealthyProviders(limit = 20): Promise<{
    unhealthy_providers: Array<{
      provider_id: string;
      health_score: string;
      score_value: number;
      issue_count: number;
      critical_issues: number;
    }>;
    count: number;
  }> {
    return fetchJson(`/monitor/unhealthy?limit=${limit}`);
  },

  async getCriticalProviders(limit = 20): Promise<{
    critical_providers: Array<{
      provider_id: string;
      health_score: string;
      score_value: number;
      critical_issues: Array<{ type: string; message: string }>;
    }>;
    count: number;
  }> {
    return fetchJson(`/monitor/critical?limit=${limit}`);
  },

  // ============ Health ============
  async getHealth(): Promise<{ status: string; time: string }> {
    return fetchJson('/health');
  },

  // ============ Profiles ============
  async getProfiles(): Promise<{ profiles: Profile[]; active: Profile | null }> {
    return fetchJson('/profiles');
  },

  async getActiveProfile(): Promise<Profile> {
    return fetchJson('/profiles/active');
  },

  async createProfile(data: ProfileCreate): Promise<{ success: boolean; profile: Profile }> {
    return fetchJson('/profiles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  async updateProfile(id: number, data: ProfileUpdate): Promise<{ success: boolean; profile: Profile }> {
    return fetchJson(`/profiles/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  async activateProfile(id: number): Promise<{ success: boolean; profile: Profile }> {
    return fetchJson(`/profiles/${id}/activate`, {
      method: 'POST',
    });
  },

  async deleteProfile(id: number): Promise<{ success: boolean }> {
    return fetchJson(`/profiles/${id}`, {
      method: 'DELETE',
    });
  },
};
