export interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: Date;
  isStreaming?: boolean;
  isExtraction?: boolean;
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
  fair_odds: number | null;
  point?: number | null;
  detected_at: string;
  // Event details
  sport?: string;
  league?: string;
  home_team?: string;
  away_team?: string;
  starts_at?: string;
  // Stake recommendations (for value bets)
  suggested_stake?: number | null;
  final_stake?: number | null;
  kelly_fraction?: number | null;
  skip_reason?: string | null;
  bonus_cleared?: boolean | null;
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
  point?: number;
}

// Providers
export interface ProviderBonus {
  type: 'freebet' | 'bonusdeposit';
  amount: number;
}

export interface Provider {
  id: string;
  name: string;
  url: string | null;
  is_enabled: boolean;
  balance: number;
  bonus?: ProviderBonus | null;
  bonus_status?: 'available' | 'in_progress' | 'completed' | null;
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
  id: number;
  name: string;
  bankroll: number;
  currency: string;
  kelly_fraction: number;
  min_edge_pct: number;
  min_arb_pct: number;
  max_stake_pct: number;
  min_retention_pct: number;
  preferred_counterparts: string[];
  bonus_enabled: boolean;
  is_active: boolean;
  created_at: string | null;
}

export interface ProfileCreate {
  name: string;
  bankroll?: number;
  currency?: string;
  kelly_fraction?: number;
  min_edge_pct?: number;
  min_arb_pct?: number;
  max_stake_pct?: number;
}

export interface ProfileUpdate {
  name?: string;
  bankroll?: number;
  currency?: string;
  kelly_fraction?: number;
  min_edge_pct?: number;
  min_arb_pct?: number;
  max_stake_pct?: number;
  min_retention_pct?: number;
  preferred_counterparts?: string[];
  bonus_enabled?: boolean;
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

// Metrics - Detailed
export interface SportMetrics {
  duration_seconds: number;
  events_processed: number;
  events_new: number;
  odds_processed: number;
  odds_new: number;
  success: boolean;
  error: string | null;
}

export interface DetailedProviderMetrics {
  duration_seconds: number;
  total_events: number;
  total_events_new: number;
  total_odds: number;
  total_odds_new: number;
  sports_attempted: number;
  sports_succeeded: number;
  sports_failed: number;
  success_rate: number;
  retries: number;
  cache_hits: number;
  rate_limit_hits: number;
  success: boolean;
  error: string | null;
  sports: Record<string, SportMetrics>;
}

export interface DetailedMetricsRun {
  run_id: string;
  start_time: number;
  end_time: number | null;
  duration_seconds: number;
  total_events: number;
  total_odds: number;
  providers_attempted: number;
  providers_succeeded: number;
  providers_failed: number;
  overall_success_rate: number;
  total_retries: number;
  total_cache_hits: number;
  polymarket: {
    events: number;
    odds: number;
  };
  providers: Record<string, DetailedProviderMetrics>;
}

// Metrics - Simple (legacy)
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

// Polymarket Matched Events
export interface PolymarketOddsEntry {
  outcome: string;
  odds: number;
}

export interface PolymarketEdge {
  outcome: string;
  provider: string;
  edge_pct: number;
  provider_odds: number;
  polymarket_odds: number;
}

export interface PolymarketPinnacleEdge {
  outcome: string;
  polymarket_odds: number;
  pinnacle_odds: number;
  edge_pct: number;
}

export interface PolymarketMatchedEvent {
  id: string;
  sport: string;
  league: string | null;
  home_team: string;
  away_team: string;
  start_time: string | null;
  polymarket_odds: PolymarketOddsEntry[];
  other_providers: Record<string, PolymarketOddsEntry[]>;
  edges: PolymarketEdge[];
  best_edge: number;
  polymarket_edges: PolymarketPinnacleEdge[];
}

export interface PolymarketMatchedResponse {
  events: PolymarketMatchedEvent[];
  count: number;
}

// Polymarket Value Bets
export interface PolymarketValueBet {
  event_id: string;
  market: string;
  outcome: string;
  polymarket_odds: number;
  fair_odds: number;
  fair_probability: number;
  edge_pct: number;
  point: number | null;
  home_team: string;
  away_team: string;
  sport: string;
  league: string | null;
  start_time: string | null;
  // Stake recommendations (from profile/bankroll)
  suggested_stake?: number | null;
  final_stake?: number | null;
  kelly_fraction?: number | null;
  skip_reason?: string | null;
  bonus_cleared?: boolean | null;
}

export interface PolymarketValueResponse {
  value_bets: PolymarketValueBet[];
  count: number;
  total_scanned: number;
}

// Polymarket Stats
export interface PolymarketSportStat {
  sport: string;
  count: number;
}

export interface PolymarketStats {
  total_odds: number;
  total_events: number;
  matched_events: number;
  match_rate: number;
  normalization_rate: number;
  sports: PolymarketSportStat[];
}

// Bonus Arbitrage Types (True Arb with Hedges)
export interface BonusArbLeg {
  outcome: string;
  provider: string;
  odds: number;
  stake: number;
  return: number;
  is_anchor: boolean;
  bonus_type: string | null;
  bonus_amount: number | null;
}

export interface BonusArbOpportunity {
  event_id: string;
  market: string;
  profit_pct: number;
  profit_amount: number;
  home_team: string | null;
  away_team: string | null;
  sport: string | null;
  start_time: string | null;
  anchor_outcome: string;
  legs: BonusArbLeg[];
  // Quality classification: "verified" (normal) or "suspect" (needs validation)
  quality?: 'verified' | 'suspect';
}

export interface BonusArbResponse {
  opportunities: BonusArbOpportunity[];
  count: number;
  anchor_provider: string;
  anchor_bonus: { type: string; amount: number };
  anchor_balance: number;
  total_bankroll: number;
  valid_counterparts: string[];
}

export type BonusWorkflowStep =
  | 'idle'
  | 'select-provider'
  | 'select-opportunity'
  | 'select-stake'
  | 'manual-stake'
  | 'confirm';

export interface BonusWorkflowState {
  step: BonusWorkflowStep;
  anchorProvider?: string;
  opportunities?: BonusArbOpportunity[];
  selectedOpp?: number;
  suggestedStake?: number;
  totalBankroll?: number;
  anchorBalance?: number;
}

export interface BonusDropdownOption {
  id: string | number;
  label: string;
  sublabel?: string;
  type: 'provider' | 'opportunity' | 'stake' | 'action';
}

// Bankroll Workflow Types
export type BankrollWorkflowStep =
  | 'idle'
  | 'select-action'      // Deposit, Withdraw, Settings, Reset
  | 'select-provider'    // For deposit/withdraw
  | 'enter-amount'       // Manual input
  | 'select-setting'     // Kelly fraction, max stake, etc.
  | 'select-value'       // Setting values
  | 'confirm-reset';     // Type RESET

export interface BankrollWorkflowState {
  step: BankrollWorkflowStep;
  action?: 'deposit' | 'withdraw' | 'settings' | 'reset';
  selectedProvider?: string;
  amount?: number;
  selectedSetting?: string;
}

export interface BankrollOption {
  id: string | number;
  label: string;
  sublabel?: string;
  type: 'action' | 'provider' | 'setting' | 'value';
}

// Generic Dropdown Workflow Types (used by value, bets commands)
export type DropdownWorkflowType = 'idle' | 'value' | 'bets';

export type DropdownWorkflowStep =
  | 'idle'
  | 'select-provider'
  | 'select-opportunity'
  | 'select-stake'
  | 'manual-stake'
  | 'select-bet'
  | 'settle-bet'
  | 'select-event'
  | 'select-event-outcome'
  | 'confirm';

// Event with pending bets grouped together
export interface EventWithBets {
  event_id: string;
  home_team: string;
  away_team: string;
  sport?: string;
  bets: Bet[];
  total_stake: number;
}

export interface DropdownWorkflowState {
  type: DropdownWorkflowType;
  step: DropdownWorkflowStep;
  // Value workflow
  opportunities?: OpportunityWithEvent[];
  selectedOpp?: number;
  suggestedStake?: number;
  selectedProvider?: string;  // Provider for bet placement
  // Bets workflow
  bets?: Bet[];
  selectedBet?: number;
  eventsWithBets?: EventWithBets[];
  selectedEventId?: string;
}

export interface DropdownOption {
  id: string | number;
  label: string;
  sublabel?: string;
  selected?: boolean;
  type: 'provider' | 'opportunity' | 'stake' | 'action';
}

// ============ Risk Management Types ============

export interface RiskFeatures {
  stake_entropy: number;
  market_diversity: number;
  timing_regularity: number;
  outcome_correlation: number;
  bonus_usage_ratio: number;
  clv_score: number;
  win_rate_deviation: number;
  bets_analyzed: number;
  calculation_window_days: number;
  calculated_at: string;
}

export interface ProviderRiskProfile {
  provider_id: string;
  risk_score: number;
  risk_level: 'low' | 'medium' | 'high' | 'critical';
  features: RiskFeatures;
  recommendations: string[];
  is_on_cooldown: boolean;
  cooldown_until: string | null;
  cooldown_reason: string | null;
  brier_score: number | null;
}

export interface RiskSummary {
  total_providers: number;
  low_risk: number;
  medium_risk: number;
  high_risk: number;
  critical_risk: number;
  on_cooldown: number;
  avg_risk_score: number;
}

export interface AllRiskResponse {
  providers: Record<string, ProviderRiskProfile>;
  summary: RiskSummary;
}

export interface RiskConfig {
  lambda_coefficient: number;
  stake_noise_pct: number;
  softmax_temperature: number;
  weight_stake_entropy: number;
  weight_market_diversity: number;
  weight_timing_regularity: number;
  weight_outcome_correlation: number;
  weight_bonus_usage: number;
  weight_clv: number;
  weight_win_rate: number;
  threshold_low: number;
  threshold_medium: number;
  threshold_high: number;
  rolling_window_days: number;
  cooldown_trigger_score: number;
  cooldown_duration_hours: number;
}

export interface RiskConfigUpdate {
  lambda_coefficient?: number;
  stake_noise_pct?: number;
  softmax_temperature?: number;
  weight_stake_entropy?: number;
  weight_market_diversity?: number;
  weight_timing_regularity?: number;
  weight_outcome_correlation?: number;
  weight_bonus_usage?: number;
  weight_clv?: number;
  weight_win_rate?: number;
  threshold_low?: number;
  threshold_medium?: number;
  threshold_high?: number;
  rolling_window_days?: number;
  cooldown_trigger_score?: number;
  cooldown_duration_hours?: number;
}

export interface OpportunityInput {
  event_id: string;
  provider_id: string;
  outcome: string;
  odds: number;
  fair_odds: number;
}

export interface RankedOpportunity {
  event_id: string;
  provider_id: string;
  outcome: string;
  odds: number;
  fair_odds: number;
  expected_value: number;
  edge_pct: number;
  risk_score: number;
  risk_penalty: number;
  utility: number;
  base_stake: number;
  risk_adjusted_stake: number;
  stake_multiplier: number;
  selection_probability: number;
  rank: number;
}

export interface SelectOpportunityResponse {
  selected: RankedOpportunity | null;
  all_ranked: RankedOpportunity[];
  selection_entropy: number;
}

export interface RiskAwareStake {
  base_stake: number;
  risk_adjusted_stake: number;
  final_stake: number;
  max_stake: number;
  risk_score: number;
  risk_level: 'low' | 'medium' | 'high' | 'critical' | 'unknown';
  expected_value: number;
  risk_penalty: number;
  utility: number;
  noise_applied: number;
  noise_pct: number;
  provider_balance: number;
  reason: string;
  skip_reason: string | null;
}

export interface StakeNoiseResult {
  original_stake: number;
  final_stake: number;
  noise_applied: number;
  noise_pct: number;
  was_rounded: boolean;
  reason: string;
}

