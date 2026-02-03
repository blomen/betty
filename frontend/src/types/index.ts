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
  detected_at: string;
  // Event details
  sport?: string;
  league?: string;
  home_team?: string;
  away_team?: string;
  starts_at?: string;
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

// Extraction
export interface ExtractionStatus {
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
  providers: Record<string, ProviderProgress>;
}

export interface ProviderProgress {
  status: 'pending' | 'running' | 'completed' | 'failed';
  events: number;
  odds: number;
  duration_seconds: number;
  error: string | null;
  sports_completed: number;
  sports_total: number;
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
}

export interface PolymarketMatchedResponse {
  events: PolymarketMatchedEvent[];
  count: number;
}

// Bonus Arbitrage Types
export interface BonusArb {
  event_id: string;
  market: string;
  outcome: string;
  anchor_provider: string;
  anchor_odds: number;
  fair_odds: number;
  edge_pct: number;
  home_team: string | null;
  away_team: string | null;
  sport: string | null;
  // Kelly-based stake suggestions
  suggested_stake: number;
  kelly_stake: number;
  max_stake: number;
}

export interface BonusScanResponse {
  opportunities: BonusArb[];
  count: number;
  anchor_provider: string;
  total_bankroll: number;
  anchor_balance: number;
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
  opportunities?: BonusArb[];
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

// Full Arbitrage with all legs
export interface ArbitrageLeg {
  outcome: string;
  provider: string;
  odds: number;
  stake: number;
  return: number;
}

export interface FullArbitrage {
  event_id: string;
  market: string;
  profit_pct: number;
  home_team: string | null;
  away_team: string | null;
  sport: string | null;
  start_time: string | null;
  legs: ArbitrageLeg[];
}

export interface ArbitrageScanResponse {
  opportunities: FullArbitrage[];
  count: number;
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

// Generic Dropdown Workflow Types (used by extract, arb, value, bets commands)
export type DropdownWorkflowType = 'idle' | 'extract' | 'arb' | 'value' | 'bets';

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
  // Extract workflow
  selectedProviders?: string[];
  // Arb/Value workflow
  opportunities?: OpportunityWithEvent[];
  fullArbs?: FullArbitrage[];  // Full arbitrage with all legs
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
  selected?: boolean;  // For multi-select (extract)
  type: 'provider' | 'opportunity' | 'stake' | 'action';
}

