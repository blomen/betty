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
  MetricsRun,
  ProviderMetrics,
  DetailedMetricsRun,
  CircuitBreakerStatus,
  CacheStats,
  ProviderCacheStats,
  HealthCheckStatus,
  ProviderHealth,
  BonusMatchRequest,
  BonusMatch,
  PolymarketMatchedResponse,
  PolymarketValueResponse,
  PolymarketStats,
  BonusArbResponse,
  ProviderRiskProfile,
  AllRiskResponse,
  RiskConfig,
  RiskConfigUpdate,
  OpportunityInput,
  SelectOpportunityResponse,
  RiskAwareStake,
  StakeNoiseResult,
} from '@/types';

// ============ Oddsboost Types ============

export interface SpecialItem {
  provider: string;
  title: string;
  description: string;
  original_odds: number | null;
  boosted_odds: number | null;
  boost_pct: number | null;
  max_stake: number | null;
  category: string;
  sport: string;
  league: string;
  event: string;
  event_time: string | null;
  expires_at: string | null;
  url: string;
  scraped_at: string;
  source: string;
  market_label: string;
  shared_providers: string[] | null;
}

export interface SpecialsFilters {
  sports: string[];
  providers: string[];
  categories: string[];
}

export interface SpecialsResponse {
  specials: SpecialItem[];
  count: number;
  scraped_at: string | null;
  filters?: SpecialsFilters;
}

export interface StakePreviewResult {
  recommended_stake: number;
  kelly_fraction: number;
  edge_raw: number;
  edge_used: number;
  bankroll: number;
  raw_kelly_stake: number;
  single_bet_cap: number;
  was_capped_single: boolean;
  was_capped_event: boolean;
  was_capped_daily: boolean;
  skip_reason: string | null;
  bonus_cleared: boolean;
  min_odds_applied: number;
}

const API_BASE = '/api';

// Configuration for fetch with retry
const DEFAULT_TIMEOUT_MS = 30000; // 30 seconds
const DEFAULT_RETRIES = 3;
const INITIAL_BACKOFF_MS = 1000; // 1 second

// Structured error classes
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public statusText: string,
    public endpoint: string,
    public isRetryable: boolean = false
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export class NetworkError extends Error {
  constructor(
    message: string,
    public endpoint: string,
    public isRetryable: boolean = true
  ) {
    super(message);
    this.name = 'NetworkError';
  }
}

export class TimeoutError extends Error {
  constructor(
    message: string = 'Request timed out',
    public endpoint: string,
    public timeoutMs: number
  ) {
    super(message);
    this.name = 'TimeoutError';
  }
}

// Determine if an error is retryable
function isRetryableStatus(status: number): boolean {
  // Retry on server errors and rate limits
  return status >= 500 || status === 429 || status === 408;
}

// Sleep helper for retry backoff
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchWithRetry<T>(
  endpoint: string,
  options?: RequestInit,
  retries: number = DEFAULT_RETRIES,
  timeoutMs: number = DEFAULT_TIMEOUT_MS
): Promise<T> {
  let lastError: Error | null = null;

  for (let attempt = 0; attempt <= retries; attempt++) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

    try {
      const response = await fetch(`${API_BASE}${endpoint}`, {
        ...options,
        signal: controller.signal,
      });

      clearTimeout(timeoutId);

      if (!response.ok) {
        const isRetryable = isRetryableStatus(response.status);

        // If not retryable or last attempt, throw immediately
        if (!isRetryable || attempt === retries) {
          // Try to extract error detail from response body
          let errorDetail = '';
          try {
            const errorBody = await response.json();
            errorDetail = errorBody.detail || errorBody.message || errorBody.error || '';
          } catch {
            // Ignore JSON parse errors
          }

          const errorMessage = errorDetail
            ? `${errorDetail}`
            : `API error: ${response.status} ${response.statusText}`;

          throw new ApiError(
            errorMessage,
            response.status,
            response.statusText,
            endpoint,
            isRetryable
          );
        }

        // Retryable error - calculate backoff and retry
        const backoffMs = INITIAL_BACKOFF_MS * Math.pow(2, attempt);
        console.warn(
          `API request failed (attempt ${attempt + 1}/${retries + 1}): ${response.status}, retrying in ${backoffMs}ms`
        );
        await sleep(backoffMs);
        continue;
      }

      return response.json();
    } catch (error) {
      clearTimeout(timeoutId);

      // Handle abort/timeout
      if (error instanceof DOMException && error.name === 'AbortError') {
        lastError = new TimeoutError(
          `Request to ${endpoint} timed out after ${timeoutMs}ms`,
          endpoint,
          timeoutMs
        );

        // Retry on timeout
        if (attempt < retries) {
          const backoffMs = INITIAL_BACKOFF_MS * Math.pow(2, attempt);
          console.warn(
            `Request timed out (attempt ${attempt + 1}/${retries + 1}), retrying in ${backoffMs}ms`
          );
          await sleep(backoffMs);
          continue;
        }
      }

      // Handle network errors
      if (error instanceof TypeError && error.message.includes('fetch')) {
        lastError = new NetworkError(
          `Network error: ${error.message}`,
          endpoint
        );

        // Retry on network errors
        if (attempt < retries) {
          const backoffMs = INITIAL_BACKOFF_MS * Math.pow(2, attempt);
          console.warn(
            `Network error (attempt ${attempt + 1}/${retries + 1}), retrying in ${backoffMs}ms`
          );
          await sleep(backoffMs);
          continue;
        }
      }

      // Re-throw ApiError or save for later
      if (error instanceof ApiError) {
        lastError = error;
        if (!error.isRetryable) {
          throw error;
        }
      } else {
        lastError = error instanceof Error ? error : new Error(String(error));
      }
    }
  }

  // All retries exhausted
  throw lastError || new Error(`Request failed after ${retries + 1} attempts`);
}

// Legacy fetchJson for backward compatibility (uses retry internally)
async function fetchJson<T>(endpoint: string, options?: RequestInit): Promise<T> {
  return fetchWithRetry<T>(endpoint, options);
}

// ============ Extraction Progress Types ============

export interface ExtractionProgress {
  running: boolean;
  last_run: string | null;
  start_time: string | null;
  elapsed_seconds: number;
  progress_pct: number;
  total_events: number;
  total_odds: number;
  current_provider: string | null;
  completed_providers: number;
  total_providers: number;
  providers: Record<string, {
    status: string;
    events: number;
    odds: number;
    duration_seconds: number;
    error: string | null;
    sports_completed: number;
    sports_total: number;
  }>;
}

// Per-tier progress
export interface TierProgress {
  running: boolean;
  last_run: string | null;
  elapsed_seconds: number;
  progress_pct: number;
  total_events: number;
  total_odds: number;
  current_provider: string | null;
  completed_providers: number;
  total_providers: number;
}

export interface TiersProgressResponse {
  any_running: boolean;
  tiers: Record<string, TierProgress>;
}

export const api = {
  // ============ Extraction ============
  async getExtractionProgress(): Promise<ExtractionProgress> {
    return fetchJson<ExtractionProgress>('/extraction/progress');
  },

  async getTiersProgress(): Promise<TiersProgressResponse> {
    return fetchJson<TiersProgressResponse>('/extraction/tiers/progress');
  },

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

  async getProviderBonuses(): Promise<Record<string, {
    type: string;
    amount: number;
    wagering_multiplier: number;
    min_odds: number;
  }>> {
    return fetchJson('/bankroll/bonuses');
  },

  async getBankrollStatus(): Promise<{
    profile_id: number;
    profile_name: string;
    bankroll: number;
    bonus_progress: Record<string, {
      status: string;
      bonus_amount: number;
      wagering_requirement: number;
      wagered_amount: number;
      min_odds: number;
      progress_pct: number;
      is_cleared: boolean;
    }>;
  }> {
    return fetchJson('/bankroll/status');
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

  async depositWithBonus(
    providerId: string,
    amount: number
  ): Promise<{
    success: boolean;
    provider_id: string;
    deposit: number;
    bonus_claimed: number;
    total_added: number;
    old_balance: number;
    new_balance: number;
    bonus_status: string | null;
    bonus_limit: number | null;
  }> {
    return fetchJson(`/bankroll/deposit/${providerId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ amount }),
    });
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

  async getBonusArbitrage(
    anchorProvider: string,
    limit = 50
  ): Promise<BonusArbResponse> {
    const params = new URLSearchParams();
    params.set('anchor_provider', anchorProvider);
    params.set('limit', limit.toString());
    return fetchJson<BonusArbResponse>(`/opportunities/bonus/arbitrage?${params}`);
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

  // ============ Metrics ============
  async getMetricsHistory(limit = 10): Promise<{ history: MetricsRun[]; count: number }> {
    return fetchJson(`/metrics/history?limit=${limit}`);
  },

  async getProviderMetrics(providerId: string, limit = 10): Promise<ProviderMetrics> {
    return fetchJson(`/metrics/provider/${providerId}?limit=${limit}`);
  },

  async getCurrentMetrics(): Promise<DetailedMetricsRun | { error: string }> {
    return fetchJson('/metrics/current');
  },

  async getDetailedHistory(limit = 10): Promise<{ history: DetailedMetricsRun[]; count: number }> {
    return fetchJson(`/metrics/history?limit=${limit}`);
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

  // ============ Polymarket ============
  async getPolymarketMatched(
    sport?: string,
    limit = 50
  ): Promise<PolymarketMatchedResponse> {
    const params = new URLSearchParams();
    if (sport) params.set('sport', sport);
    params.set('limit', limit.toString());
    return fetchJson(`/polymarket/matched?${params}`);
  },

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

  async getPolymarketStats(): Promise<PolymarketStats> {
    return fetchJson<PolymarketStats>('/polymarket/stats');
  },

  // ============ Risk Management ============
  async getProviderRisk(providerId: string): Promise<ProviderRiskProfile> {
    return fetchJson<ProviderRiskProfile>(`/risk/provider/${providerId}`);
  },

  async getAllRiskProfiles(): Promise<AllRiskResponse> {
    return fetchJson<AllRiskResponse>('/risk/all');
  },

  async getRiskConfig(): Promise<RiskConfig> {
    return fetchJson<RiskConfig>('/risk/config');
  },

  async updateRiskConfig(config: RiskConfigUpdate): Promise<RiskConfig> {
    return fetchJson<RiskConfig>('/risk/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    });
  },

  async selectOpportunity(
    opportunities: OpportunityInput[],
    stake: number,
    options?: { temperature?: number; deterministic?: boolean }
  ): Promise<SelectOpportunityResponse> {
    return fetchJson<SelectOpportunityResponse>('/risk/select', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        opportunities,
        stake,
        temperature: options?.temperature,
        deterministic: options?.deterministic ?? false,
      }),
    });
  },

  async setProviderCooldown(
    providerId: string,
    durationHours: number,
    reason?: string
  ): Promise<{ success: boolean; provider_id: string; cooldown_until: string; reason: string }> {
    return fetchJson(`/risk/cooldown/${providerId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        duration_hours: durationHours,
        reason,
      }),
    });
  },

  async clearProviderCooldown(
    providerId: string
  ): Promise<{ success: boolean; provider_id: string; message: string }> {
    return fetchJson(`/risk/cooldown/${providerId}`, {
      method: 'DELETE',
    });
  },

  async calculateRiskAwareStake(
    odds: number,
    fairOdds: number,
    providerId: string,
    force = false
  ): Promise<RiskAwareStake> {
    const params = new URLSearchParams();
    params.set('odds', odds.toString());
    params.set('fair_odds', fairOdds.toString());
    params.set('provider_id', providerId);
    params.set('force', force.toString());
    return fetchJson<RiskAwareStake>(`/risk/calculate-stake?${params}`, {
      method: 'POST',
    });
  },

  async calculateStakeNoise(
    stake: number,
    providerId: string
  ): Promise<StakeNoiseResult> {
    return fetchJson<StakeNoiseResult>('/risk/stake-noise', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        stake,
        provider_id: providerId,
      }),
    });
  },

  // ============ Oddsboost ============
  async getSpecials(filters?: {
    sport?: string;
    provider?: string;
    category?: string;
    sort?: string;
    order?: string;
  }): Promise<SpecialsResponse> {
    const params = new URLSearchParams();
    if (filters?.sport) params.set('sport', filters.sport);
    if (filters?.provider) params.set('provider', filters.provider);
    if (filters?.category) params.set('category', filters.category);
    if (filters?.sort) params.set('sort', filters.sort);
    if (filters?.order) params.set('order', filters.order);
    const qs = params.toString();
    return fetchJson(`/specials${qs ? `?${qs}` : ''}`);
  },

  async scrapeSpecials(): Promise<SpecialsResponse> {
    return fetchJson('/specials/scrape', { method: 'POST' });
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
