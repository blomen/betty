import type {
  Opportunity,
  ProvidersResponse,
  BankrollInfo,
  BankrollStats,
  BankrollExposure,
  Bet,
  Profile,
  ProfileCreate,
  ProfileUpdate,
  PolymarketValueResponse,
  ProviderRiskProfile,
  AllRiskResponse,
  RiskConfig,
  RiskConfigUpdate,
  OpportunityInput,
  SelectOpportunityResponse,
  RiskAwareStake,
  ProviderLimit,
  BettingSnapshot,
} from '@/types';
import type { MlHealth } from '@/types/market';

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
  // Boost edge (boosted/original)
  edge_pct: number | null;
  is_positive_ev: boolean | null;
  fair_odds: number | null;
  // LLM enrichment
  llm_title: string | null;
  llm_probability: number | null;
  llm_fair_odds: number | null;
  llm_edge_pct: number | null;
  llm_reasoning: string | null;
  llm_confidence: string | null;
  // Pre-computed Kelly stake
  recommended_stake: number | null;
  kelly_fraction: number | null;
}

export interface SpecialsFilters {
  sports: string[];
  providers: string[];
  categories: string[];
}

export interface LlmHealth {
  status: string;              // ok | error | skipped | unknown
  anthropic_status: string | null;
  last_error: string | null;
  last_success_at: string | null;
  last_run_at: string | null;
  enriched_count: number;
  carried_count: number;
  candidate_count: number;
}

export interface SpecialsResponse {
  specials: SpecialItem[];
  count: number;
  ev_positive_count: number;
  matched_count: number;
  llm_count?: number;
  scraped_at: string | null;
  llm_health?: LlmHealth;
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
  skip_reason: string | null;
  bonus_cleared: boolean;
  min_odds_applied: number;
}

// ============ Settings Types ============

export interface ExtractionProvider {
  provider_id: string;
  name: string;
  enabled: boolean;
}

export interface ExtractionPlatform {
  platform_id: string;
  platform_name: string;
  tier: string;
  providers: ExtractionProvider[];
  sites: string[];
}

export interface ExtractionSettingsResponse {
  platforms: ExtractionPlatform[];
}

const API_BASE = '/api';

export async function getMlHealth(): Promise<MlHealth> {
  const res = await fetch(`${API_BASE}/trading/market/ml/health`);
  return res.json();
}

// Configuration for fetch with retry
const DEFAULT_TIMEOUT_MS = 45000; // 45 seconds — generous for slow PCs under extraction load
const DEFAULT_RETRIES = 1;        // 1 retry only — avoid retry storms when backend is busy
const INITIAL_BACKOFF_MS = 2000;  // 2 seconds — give backend breathing room

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
async function fetchJson<T>(endpoint: string, options?: RequestInit, timeoutMs?: number): Promise<T> {
  return fetchWithRetry<T>(endpoint, options, DEFAULT_RETRIES, timeoutMs ?? DEFAULT_TIMEOUT_MS);
}

export const api = {
  // ============ Providers ============
  async getProviders(): Promise<ProvidersResponse> {
    return fetchJson<ProvidersResponse>('/providers');
  },

  // ============ Bankroll ============
  async getBankroll(): Promise<BankrollInfo> {
    return fetchJson<BankrollInfo>('/bankroll');
  },

  async getBankrollStats(): Promise<BankrollStats> {
    return fetchJson<BankrollStats>('/bankroll/stats');
  },

  async getBankrollStatus(): Promise<{
    profile_id: number;
    profile_name: string;
    bankroll: number;
    bonus_progress: Record<string, import('@/types').BonusProgressEntry>;
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

  async setBalance(
    providerId: string,
    balance: number
  ): Promise<{
    success: boolean;
    provider_id: string;
    old_balance: number;
    new_balance: number;
  }> {
    return fetchJson(`/bankroll/set/${providerId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ balance }),
    });
  },

  async transferFunds(
    fromProviderId: string,
    toProviderId: string,
    amount: number,
    withBonus = false
  ): Promise<{
    success: boolean;
    from_provider_id: string;
    to_provider_id: string;
    amount: number;
    from_new_balance: number;
    to_new_balance: number;
    bonus_claimed: number;
    bonus_status: string | null;
    bonus_type: string | null;
  }> {
    return fetchJson('/bankroll/transfer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        from_provider_id: fromProviderId,
        to_provider_id: toProviderId,
        amount,
        with_bonus: withBonus,
      }),
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
    bonus_type: string | null;
    bonus_limit: number | null;
  }> {
    return fetchJson(`/bankroll/deposit/${providerId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ amount }),
    });
  },

  async claimBonus(providerId: string): Promise<{ success: boolean; provider_id: string; status: string }> {
    return fetchJson(`/bankroll/claim-bonus/${providerId}`, { method: 'POST' });
  },

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

  // ============ Profiles ============
  async getProfiles(): Promise<{ profiles: Profile[]; active: Profile | null }> {
    return fetchJson('/profiles');
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

  // ============ Trading ============

  async getTradingConfig(): Promise<import('@/types/trading').TradingConfig> {
    return fetchJson('/trading/config');
  },

  async getRoutineConfig(): Promise<{ macro_items: string[]; session_items: string[]; psych_threshold: number }> {
    return fetchJson('/trading/routine/config');
  },

  async getTradingAccounts(): Promise<{ accounts: import('@/types/trading').TradingAccount[] }> {
    return fetchJson('/trading/accounts');
  },

  async updateTradingAccount(id: number, data: Record<string, unknown>): Promise<{ success: boolean; account: import('@/types/trading').TradingAccount }> {
    return fetchJson(`/trading/accounts/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  async adjustTradingBalance(id: number, amount: number): Promise<{ success: boolean; balance: number }> {
    return fetchJson(`/trading/accounts/${id}/adjust`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ amount }),
    });
  },

  async resetTradingDaily(id: number): Promise<{ success: boolean }> {
    return fetchJson(`/trading/accounts/${id}/reset-daily`, { method: 'POST' });
  },

  async resetTradingWeekly(id: number): Promise<{ success: boolean }> {
    return fetchJson(`/trading/accounts/${id}/reset-weekly`, { method: 'POST' });
  },

  async getTodayRoutine(): Promise<import('@/types/trading').DailyRoutine> {
    return fetchJson('/trading/routine/today');
  },

  async updateRoutine(date: string, data: Record<string, unknown>): Promise<{ success: boolean; routine: import('@/types/trading').DailyRoutine }> {
    return fetchJson(`/trading/routine/${date}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  async createTrade(data: Record<string, unknown>): Promise<import('@/types/trading').TradeValidation & { success?: boolean; trade_id?: number; error?: string; dry_run?: boolean }> {
    return fetchJson('/trading/trades', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  async getTrades(filters?: {
    account_id?: number;
    instrument?: string;
    setup_type?: string;
    state?: string;
    limit?: number;
  }): Promise<{ trades: import('@/types/trading').Trade[]; count: number }> {
    const params = new URLSearchParams();
    if (filters?.account_id) params.set('account_id', filters.account_id.toString());
    if (filters?.instrument) params.set('instrument', filters.instrument);
    if (filters?.setup_type) params.set('setup_type', filters.setup_type);
    if (filters?.state) params.set('state', filters.state);
    if (filters?.limit) params.set('limit', filters.limit.toString());
    return fetchJson(`/trading/trades?${params}`);
  },

  async transitionTrade(id: number, toState: string, notes?: string): Promise<{ success: boolean; state: string }> {
    return fetchJson(`/trading/trades/${id}/transition`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ to_state: toState, notes }),
    });
  },

  async partialExitTrade(id: number, contracts: number, exitPrice: number, notes?: string): Promise<{ success: boolean; remaining_contracts: number; partial_pnl: number }> {
    return fetchJson(`/trading/trades/${id}/partial-exit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ contracts, exit_price: exitPrice, notes }),
    });
  },

  async moveToBE(id: number): Promise<{ success: boolean; stop_price: number }> {
    return fetchJson(`/trading/trades/${id}/move-to-be`, { method: 'POST' });
  },

  async trailStop(id: number, newStop: number, notes?: string): Promise<{ success: boolean; stop_price: number }> {
    return fetchJson(`/trading/trades/${id}/trail-stop`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ new_stop: newStop, notes }),
    });
  },

  async addPosition(id: number, contracts: number, entryPrice: number, notes?: string): Promise<{ success: boolean; contracts: number; avg_entry: number }> {
    return fetchJson(`/trading/trades/${id}/add-position`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ contracts, entry_price: entryPrice, notes }),
    });
  },

  async closeTrade(id: number, exitPrice: number, commission = 0, notes?: string): Promise<{ success: boolean; realized_pnl: number; r_multiple: number | null }> {
    return fetchJson(`/trading/trades/${id}/close`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ exit_price: exitPrice, commission, notes }),
    });
  },

  async submitTradeReview(id: number, data: Record<string, unknown>): Promise<{ success: boolean }> {
    return fetchJson(`/trading/trades/${id}/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  async getUnreviewedTrades(): Promise<{ trades: import('@/types/trading').Trade[]; count: number }> {
    return fetchJson('/trading/trades/unreviewed');
  },

  async getTradingAnalytics(filters?: {
    account_id?: number;
    instrument?: string;
    setup_type?: string;
  }): Promise<import('@/types/trading').TradingAnalytics> {
    const params = new URLSearchParams();
    if (filters?.account_id) params.set('account_id', filters.account_id.toString());
    if (filters?.instrument) params.set('instrument', filters.instrument);
    if (filters?.setup_type) params.set('setup_type', filters.setup_type);
    return fetchJson(`/trading/analytics?${params}`);
  },

  getTradingExportUrl(filters?: { state?: string; account_id?: number; instrument?: string }): string {
    const params = new URLSearchParams();
    if (filters?.state) params.set('state', filters.state);
    if (filters?.account_id) params.set('account_id', filters.account_id.toString());
    if (filters?.instrument) params.set('instrument', filters.instrument);
    return `${API_BASE}/trading/export/csv?${params}`;
  },

  // ============ Market Data / Scanner ============

  async getCandles(symbol = 'NQ', interval = '5m', date?: string, days = 5): Promise<import('@/types/market').CandlesResponse> {
    const params = new URLSearchParams({ symbol, interval, days: String(days) });
    if (date) params.set('date', date);
    return fetchJson(`/trading/market/candles?${params}`);
  },

  async getDevelopingVwap(symbol = 'NQ', interval = '1m'): Promise<{
    vwap: Array<{ t: number; vwap: number; sd1_u: number; sd1_l: number; sd2_u: number; sd2_l: number; sd3_u: number; sd3_l: number }>;
    symbol: string; count: number;
  }> {
    return fetchJson(`/trading/market/vwap?symbol=${symbol}&interval=${interval}`);
  },

  async getMarketSession(): Promise<import('@/types/market').MarketSession> {
    return fetchJson('/trading/market/session');
  },

  /** Get expanded session with all analytical layers (replaces getMarketSession for dashboard) */
  async getExpandedSession(): Promise<import('@/types/market').ExpandedSession> {
    return fetchJson('/trading/market/session');
  },

  async getVolumeProfile(symbol = 'NQ', timeframe = 'session'): Promise<{
    timeframe: string; poc: number; vah: number; val: number;
    levels: Array<{ price: number; volume: number }>;
  }> {
    return fetchJson(`/trading/market/volume-profile?symbol=${symbol}&timeframe=${timeframe}`);
  },

  async getSessionLevels(symbol = 'NQ', days = 5): Promise<import('@/types/market').SessionLevelsResponse> {
    return fetchJson(`/trading/market/session-levels?symbol=${symbol}&days=${days}`);
  },

  /** Get live indicators — orderflow + ML predictions (replaces getConfirmations) */
  async getIndicators(): Promise<import('@/types/market').IndicatorsResponse> {
    return fetchJson('/trading/market/indicators');
  },

  /** Update VP anchor dates (leg start, macro start) */
  async updateVPAnchors(data: { vp_leg_start?: string; vp_ongoing_macro_start?: string }, symbol = 'NQ'): Promise<{ status: string }> {
    return fetchJson(`/trading/market/context?symbol=${symbol}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  async getMarketSessionByDate(date: string): Promise<import('@/types/market').MarketSession> {
    return fetchJson(`/trading/market/session/${date}`);
  },

  async getMarketSignals(): Promise<{ signals: import('@/types/market').TradingSignal[] }> {
    return fetchJson('/trading/market/signals');
  },

  async triggerMarketScan(threshold?: number): Promise<{ signals: import('@/types/market').TradingSignal[]; count: number }> {
    const params = threshold ? `?threshold=${threshold}` : '';
    return fetchJson(`/trading/market/scan${params}`, { method: 'POST' });
  },

  async triggerMarketCompute(date?: string): Promise<import('@/types/market').MarketSession> {
    const params = date ? `?date=${date}` : '';
    return fetchJson(`/trading/market/compute${params}`, { method: 'POST' });
  },

  async getMarketHistory(limit = 30): Promise<{ sessions: import('@/types/market').MarketSessionSummary[] }> {
    return fetchJson(`/trading/market/history?limit=${limit}`);
  },

  async getMacroSnapshot(): Promise<import('@/types/market').MacroSnapshot> {
    return fetchJson('/trading/market/macro');
  },

  async getConfirmations(): Promise<import('@/types/market').ConfirmationState> {
    return fetchJson('/trading/market/confirmations');
  },

  async getMarketContext(symbol = 'NQ'): Promise<import('@/types/market').MarketContext> {
    const res = await fetch(`${API_BASE}/trading/market/context?symbol=${symbol}`);
    return res.json();
  },

  async updateMarketContext(data: Partial<import('@/types/market').MarketContext>, symbol = 'NQ') {
    const res = await fetch(`${API_BASE}/trading/market/context?symbol=${symbol}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    return res.json();
  },

  async getCotData(limit = 4): Promise<any[]> {
    return fetchJson(`/trading/market/cot?limit=${limit}`);
  },

  async getFootprint(period = 300, limit = 20): Promise<any> {
    return fetchJson(`/trading/market/footprint?period=${period}&limit=${limit}`);
  },

  async getTopOfBook(): Promise<{ bid_price: number; bid_size: number; ask_price: number; ask_size: number; spread: number; ts: string | null }> {
    return fetchJson('/trading/market/book');
  },

  async getMarketLevels(symbol = 'NQ', date?: string): Promise<any[]> {
    const params = date ? `?symbol=${symbol}&date=${date}` : `?symbol=${symbol}`;
    return fetchJson(`/trading/market/levels${params}`);
  },

  async getLiveLevels(symbol = 'NQ'): Promise<{ levels: import('@/types/market').MonitoredLevel[]; price: number | null }> {
    return fetchJson(`/trading/market/levels/live?symbol=${symbol}`);
  },

  async scalePosition(tradeId: number, pct: number = 50): Promise<{ success: boolean; remaining_contracts: number }> {
    return fetchJson(`/trading/trades/${tradeId}/scale?pct=${pct}`, { method: 'POST' });
  },

  async closePosition(tradeId: number): Promise<{ success: boolean }> {
    return fetchJson(`/trading/trades/${tradeId}/close`, { method: 'POST' });
  },

  async updateStop(tradeId: number, newStop: number): Promise<{ success: boolean }> {
    return fetchJson(`/trading/trades/${tradeId}/stop?new_stop=${newStop}`, { method: 'POST' });
  },

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

  async getMirrorStatus(): Promise<{ running: boolean; provider: string | null; status: string; since: string | null }> {
    return fetchJson('/mirror/status');
  },

  async startMirror(provider = 'spelklubben', discovery = false): Promise<{ running: boolean; provider: string; status: string; since: string }> {
    const params = new URLSearchParams({ provider });
    if (discovery) params.set('discovery', 'true');
    return fetchJson(`/mirror/start?${params}`, { method: 'POST' });
  },

  async stopMirror(): Promise<{ running: boolean; provider: string | null; status: string }> {
    return fetchJson('/mirror/stop', { method: 'POST' });
  },

};
