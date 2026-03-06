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
  type: 'arbitrage' | 'value' | 'bonus' | 'dutch' | 'reverse' | 'reverse_value';
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
  display_home?: string | null;
  display_away?: string | null;
  starts_at?: string;
  // Stake recommendations (for value bets)
  suggested_stake?: number | null;
  final_stake?: number | null;
  kelly_fraction?: number | null;
  skip_reason?: string | null;
  bankroll_needed?: number | null;
  bonus_cleared?: boolean | null;
  // Freebet phase info
  bonus_status?: 'trigger_needed' | 'freebet_available' | null;
  bonus_amount?: number | null;
  min_odds_applied?: number | null;
  // Provider-specific IDs for browser navigation
  provider_meta?: Record<string, string | number> | null;
}

// Events
export interface EventSummary {
  id: string;
  sport: string;
  league: string;
  home_team: string;
  away_team: string;
  display_home?: string | null;
  display_away?: string | null;
  start_time: string | null;
  odds_count: number;
}

export interface EventDetail extends EventSummary {
  odds: Record<string, OddsEntry[]>;
}

export interface LiveEvent {
  id: string;
  sport: string;
  league: string;
  home_team: string;
  away_team: string;
  display_home?: string | null;
  display_away?: string | null;
  start_time: string | null;
  home_score: number | null;
  away_score: number | null;
  match_status: 'live' | 'finished';
  match_minute: number | null;
  match_period: string | null;
  stats: Record<string, unknown> | null;
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
  min_odds?: number;
  wagering_multiplier?: number;
}

export interface Provider {
  id: string;
  name: string;
  url: string | null;
  site_url?: string | null;
  is_enabled: boolean;
  balance: number;
  bonus?: ProviderBonus | null;
  bonus_status?: 'available' | 'trigger_needed' | 'freebet_available' | 'in_progress' | 'completed' | 'claimed' | null;
}

export interface ProvidersResponse {
  providers: Provider[];
  total_balance: number;
}

// Bonus tracking
export interface BonusPrognosis {
  remaining: number;
  bets_per_week: number;
  avg_stake: number;
  est_weeks: number | null;
  weekly_wagering: number;
  // Total context (all providers)
  total_bets_per_week: number;
  total_avg_stake: number;
  total_weekly_wagering: number;
  bankroll: number;
  // Required pace from deadline
  required_weekly_wagering: number;
}

export interface BonusProgressEntry {
  status: 'available' | 'trigger_needed' | 'freebet_available' | 'in_progress' | 'completed' | 'claimed';
  bonus_type: 'freebet' | 'bonusdeposit' | null;
  bonus_amount: number;
  wagering_requirement: number;
  wagered_amount: number;
  min_odds: number;
  progress_pct: number;
  is_cleared: boolean;
  claimed_at: string | null;
  expires_at: string | null;
  days_remaining: number | null;
  action_needed: string;
  prognosis: BonusPrognosis | null;
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
  bet_profit: number;
  freebet_profit: number;
  bonus_profit: number;
  roi_pct: number;
  win_rate: number;
  avg_clv: number;
  clv_positive_pct: number;
  clv_count: number;
}

// Bets
export interface Bet {
  id: number;
  event_id: string | null;
  provider: string;
  market: string | null;
  outcome: string | null;
  odds: number;
  point?: number | null;
  stake: number;
  currency: string;  // "SEK" or "USD" — determines stake/payout/profit units
  is_bonus: boolean;
  bonus_type: string | null;
  result: 'pending' | 'won' | 'lost' | 'void';
  payout: number;
  profit: number;
  roi_pct: number;
  placed_at: string;
  home_team?: string | null;
  away_team?: string | null;
  display_home?: string | null;
  display_away?: string | null;
  sport?: string | null;
  league?: string | null;
  clv_pct?: number | null;
  closing_odds?: number | null;
  start_time?: string | null;
  edge_pct?: number | null;
  fair_odds?: number | null;
  selection_probability?: number | null;
  placed_edge_pct?: number | null;
  fair_odds_at_placement?: number | null;
  current_odds?: number | null;
  settlement_source?: string | null;
  home_score?: number | null;
  away_score?: number | null;
  match_status?: string | null;
  match_minute?: number | null;
  match_period?: string | number | null;
  provider_site_url?: string | null;
  boost_title?: string | null;
  predicted_result?: string | null;
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
  color: string;
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
  color?: string;
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
  color?: string;
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
  total_free: number;
  total_locked: number;
  providers: ProviderExposure[];
}

export interface ProviderWagering {
  status: string;
  wagered: number;
  requirement: number;
  progress_pct: number;
  remaining: number;
  min_odds: number;
  days_remaining: number | null;
  expires_at: string | null;
}

export interface ProviderExposure {
  provider_id: string;
  provider_name: string;
  total_balance: number;
  balance_sek?: number;
  currency?: string;
  exchange_rate_sek?: number;
  pending_exposure: number;
  pending_bets_count: number;
  available: number;
  platform: string;
  is_locked: boolean;
  wagering?: ProviderWagering | null;
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
  display_home?: string | null;
  display_away?: string | null;
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
  display_home?: string | null;
  display_away?: string | null;
  sport: string;
  league: string | null;
  start_time: string | null;
  // Polymarket-native fields
  price_cents?: number;        // Price per share in cents (e.g., 34 = 34¢)
  fair_price_cents?: number;   // Fair price in cents from Pinnacle
  exchange_rate_sek?: number;  // USDC → SEK rate
  // Stake recommendations
  suggested_stake?: number | null;  // SEK
  final_stake?: number | null;      // SEK
  final_stake_usdc?: number | null; // USDC
  shares?: number | null;           // Number of shares
  payout_usdc?: number | null;      // Payout if win ($1 per share)
  kelly_fraction?: number | null;
  skip_reason?: string | null;
  bankroll_needed?: number | null;
  bonus_cleared?: boolean | null;
  // Navigation — event slug for deep links to polymarket.com/event/{slug}
  event_slug?: string | null;
  provider_meta?: Record<string, string | number> | null;
  updated_at?: string | null;
}

export interface PolymarketValueResponse {
  value_bets: PolymarketValueBet[];
  count: number;
  total_scanned: number;
  total_bankroll: number;
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

export interface PolyMyBet {
  id: number;
  event_id: string | null;
  market: string | null;
  outcome: string | null;
  odds: number;
  stake_sek: number;
  stake_usdc: number;
  result: 'pending' | 'won' | 'lost' | 'void';
  payout_sek: number;
  payout_usdc: number;
  profit_sek: number;
  profit_usdc: number;
  placed_at: string | null;
  edge_pct: number | null;
  fair_odds: number | null;
  settlement_source: string | null;
  home_team: string | null;
  away_team: string | null;
  sport: string | null;
  start_time: string | null;
}

export interface PolyMyBetsStats {
  total_bets: number;
  pending: number;
  wins: number;
  losses: number;
  voids: number;
  win_rate: number;
  total_staked_sek: number;
  total_staked_usdc: number;
  total_profit_sek: number;
  total_profit_usdc: number;
  roi_pct: number;
  avg_edge: number;
}

export interface PolyMyBetsResponse {
  bets: PolyMyBet[];
  count: number;
  stats: PolyMyBetsStats;
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

