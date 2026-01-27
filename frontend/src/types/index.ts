export interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: Date;
  isStreaming?: boolean;
}

export interface ChatState {
  messages: Message[];
  isLoading: boolean;
  error: string | null;
}

export interface BettingContext {
  opportunities: Opportunity[];
  events: EventSummary[];
  providers: Provider[];
  bankroll: BankrollInfo;
}

// Opportunities (Arbitrage + Value + Bonus)
export interface Opportunity {
  id: number;
  type: 'arbitrage' | 'value' | 'bonus';
  event_id: string;
  market: string;
  provider1: string;
  provider2: string | null;
  odds1: number;
  odds2: number | null;
  outcome1: string;
  outcome2: string | null;
  profit_pct: number | null;
  edge_pct: number | null;
  detected_at: string;
}

// Events
export interface EventSummary {
  id: string;
  sport: string;
  league: string;
  home_team: string;
  away_team: string;
  start_time: string | null;
  odds_count: number;
}

export interface EventDetail extends EventSummary {
  odds: Record<string, OddsEntry[]>;
}

export interface OddsEntry {
  provider: string;
  outcome: string;
  odds: number;
}

// Providers
export interface Provider {
  id: string;
  name: string;
  url: string | null;
  is_enabled: boolean;
  balance: number;
}

export interface ProvidersResponse {
  providers: Provider[];
  total_balance: number;
}

// Bankroll
export interface BankrollInfo {
  total: number;
  providers: {
    id: string;
    name: string;
    balance: number;
  }[];
}

export interface BankrollStats {
  total_bets: number;
  wins: number;
  losses: number;
  voids: number;
  total_staked: number;
  total_profit: number;
  roi_pct: number;
  win_rate: number;
}

// Bets
export interface Bet {
  id: number;
  event_id: string | null;
  provider: string;
  market: string | null;
  outcome: string | null;
  odds: number;
  stake: number;
  is_bonus: boolean;
  bonus_type: string | null;
  result: 'pending' | 'won' | 'lost' | 'void';
  payout: number;
  profit: number;
  roi_pct: number;
  placed_at: string;
}

// Profile
export interface Profile {
  name: string;
  kelly_fraction: number;
  min_edge_pct: number;
  min_arb_pct: number;
  max_stake_pct: number;
  min_retention_pct: number;
  preferred_counterparts: string[];
  bonus_enabled: boolean;
}

// Stake Calculator
export interface StakeCalculation {
  recommended_stake: number;
  kelly_stake: number;
  max_stake: number;
  bankroll: number;
  reason: string;
}

// Bonus Mode
export interface BonusMatchRequest {
  event_id: string;
  market: string;
  anchor_provider: string;
  anchor_outcome: string;
  anchor_odds: number;
  anchor_stake: number;
  is_free_bet: boolean;
  counterpart_providers?: string[];
}

export interface BonusMatch {
  event_id: string;
  market: string;
  anchor_provider: string;
  anchor_outcome: string;
  anchor_odds: number;
  anchor_stake: number;
  hedge_provider: string;
  hedge_outcome: string;
  hedge_odds: number;
  hedge_stake: number;
  qualifying_loss: number;
  retention_pct: number;
}

// Extraction
export interface ExtractionStatus {
  running: boolean;
  last_run: string | null;
  events: number;
  odds: number;
}

// Metrics
export interface MetricsRun {
  run_id: string;
  started_at: string;
  finished_at: string;
  total_duration_ms: number;
  providers: Record<string, ProviderMetrics>;
}

export interface ProviderMetrics {
  provider_id: string;
  success: boolean;
  error: string | null;
  duration_ms: number;
  events_extracted: number;
  odds_extracted: number;
}

// Circuit Breaker
export interface CircuitBreakerStatus {
  state: 'CLOSED' | 'OPEN' | 'HALF_OPEN';
  failure_count: number;
  success_count: number;
  last_failure_time: string | null;
  last_success_time: string | null;
  opened_at: string | null;
}

// Cache
export interface CacheStats {
  total_entries: number;
  total_hits: number;
  total_misses: number;
  hit_rate: number;
  providers: Record<string, ProviderCacheStats>;
}

export interface ProviderCacheStats {
  entries: number;
  hits: number;
  misses: number;
  hit_rate: number;
}

// Health Check
export interface HealthCheckStatus {
  healthy: boolean;
  response_time_ms: number;
  error: string | null;
  checked_at: string;
}

// Provider Monitor
export interface ProviderHealth {
  health_score: 'EXCELLENT' | 'GOOD' | 'FAIR' | 'POOR' | 'CRITICAL';
  score_value: number;
  is_healthy: boolean;
  has_critical_issues: boolean;
  avg_events_per_run: number;
  avg_response_time_ms: number;
  success_rate: number;
  trend_direction: 'IMPROVING' | 'STABLE' | 'DEGRADING';
  issues: ProviderIssue[];
}

export interface ProviderIssue {
  type: string;
  severity: 'info' | 'warning' | 'critical';
  message: string;
  metric_value: number | null;
}

// Bankroll Exposure
export interface BankrollExposure {
  total_balance: number;
  total_pending: number;
  total_available: number;
  providers: ProviderExposure[];
}

export interface ProviderExposure {
  provider_id: string;
  provider_name: string;
  total_balance: number;
  pending_exposure: number;
  pending_bets_count: number;
  available: number;
}

// Opportunity with Event Details
export interface OpportunityWithEvent extends Opportunity {
  event?: EventSummary;
}

// Bet Placement Form
export interface BetPlacementData {
  opportunity_id?: number;
  event_id?: string;
  provider_id: string;
  market?: string;
  outcome?: string;
  odds: number;
  stake: number;
  is_bonus?: boolean;
  bonus_type?: string;
}
