export interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: Date;
  isStreaming?: boolean;
  isExtraction?: boolean;
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
  prov_home?: string | null;   // Provider's own team name (e.g., "Malmö FF" vs canonical "malmo")
  prov_away?: string | null;
  starts_at?: string;
  // Stake recommendations (for value bets)
  suggested_stake?: number | null;
  final_stake?: number | null;
  kelly_fraction?: number | null;
  skip_reason?: string | null;
  counts_toward_wagering?: boolean;
  bankroll_needed?: number | null;
  bonus_cleared?: boolean | null;
  // Freebet phase info
  bonus_status?: 'trigger_needed' | 'freebet_available' | null;
  bonus_amount?: number | null;
  min_odds_applied?: number | null;
  // Provider-specific IDs for browser navigation
  provider_meta?: Record<string, string | number> | null;
  // Freshness tracking
  odds_updated_at?: string | null;
  provider_last_checked?: string | null;
  // Allocation / cluster play mode
  allocation_score?: number | null;
  allocation_reason?: string | null;
  edge_routing?: 'high_edge_unlimited' | 'grind_ok' | null;
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
  limit_risk?: 'low' | 'medium' | 'high' | 'instant';
  limit_notes?: string | null;
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
  total_deposited: number;
  total_withdrawn: number;
  net_deposited: number;
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
  bet_type?: string | null;  // "value", "dutch", "reverse", "polymarket", "boost"
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
  poly_home?: string | null;   // Polymarket's own team name (e.g., "Bulls" vs canonical "Chicago Bulls")
  poly_away?: string | null;
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
  provider_last_checked?: string | null;
}

export interface PolymarketValueResponse {
  value_bets: PolymarketValueBet[];
  count: number;
  total_scanned: number;
  total_bankroll: number;
}

// Polymarket Rewards
export interface PolyRewardHedge {
  provider: string;
  odds: number;
}

export interface PolymarketRewardMarket {
  event_id: string;
  home_team: string;
  away_team: string;
  display_home?: string | null;
  display_away?: string | null;
  poly_home?: string | null;
  poly_away?: string | null;
  sport: string;
  league: string | null;
  start_time: string | null;
  rewards_daily_rate: number;  // 0.0 — not available from API
  rewards_max_spread: number;  // Max cents from midpoint to earn rewards
  rewards_min_size: number;    // Min shares for eligibility
  competitive: number;         // 0-1 float (lower = less competition = more rewards)
  poly_prices: Record<string, number>;
  pinnacle_fair_odds: Record<string, number>;
  best_hedge_odds: Record<string, PolyRewardHedge>;
  event_slug: string | null;
  polymarket_url: string | null;
}

export interface PolymarketRewardsResponse {
  rewards: PolymarketRewardMarket[];
  count: number;
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

// Provider Limits
export interface ProviderLimit {
  id: number;
  profile_id: number;
  provider_id: string;
  provider_name: string;
  limit_type: 'stake_limited' | 'market_restricted' | 'odds_restricted' | 'fully_banned';
  limit_level: number;  // 1-5
  detected_at: string | null;
  notes: string | null;
  betting_snapshot: BettingSnapshot | null;
  created_at: string | null;
}

export interface BettingSnapshot {
  total_bets: number;
  total_stake: number;
  total_profit: number;
  win_rate: number | null;
  roi_pct: number | null;
  avg_clv_pct: number | null;
  avg_odds: number | null;
  account_age_days: number | null;
  first_bet_date: string | null;
  last_bet_date: string | null;
  sport_breakdown: Record<string, number>;
  bet_type_breakdown: Record<string, number>;
  market_breakdown: Record<string, number>;
  bonus_bets: number;
}

// Cluster Play Mode
export interface ClusterInfo {
  id: string;
  label: string;
  members: string[];
  canonical: string;
  total_balance: number;
  playable_count: number;
}

export interface ClusterProviderStatus {
  provider_id: string;
  balance: number;
  wagering_remaining: number;
  wagering_progress_pct: number;
  bonus_status: string | null;
  bonus_amount: number;
  min_odds: number;
  daily_bets: number;
  daily_cap: number;
  limit_type: string | null;
  limit_level: number | null;
  allocation_score: number;
  is_limited: boolean;
}

export interface ClusterSummary {
  cluster: string;
  providers: ClusterProviderStatus[];
  total_balance: number;
  total_wagering_remaining: number;
}

export interface PlaySibling {
  provider_id: string;
  balance: number;
  lifecycle: 'available' | 'deposited' | 'wagering' | 'freebet' | 'playing' | 'limited' | 'dormant';
  bonus_status: string | null;
  trigger_mode: 'single' | 'cumulative';
  wagering_remaining: number;
  wagering_progress_pct: number;
  min_odds: number | null;
  bonus_amount: number;
  limit_level: number | null;
  expires_at: string | null;
  days_remaining: number | null;
}

export interface PlayCluster {
  id: string;
  label: string;
  canonical: string;
  active_siblings: PlaySibling[];
  available_siblings: PlaySibling[];
  recommended_siblings: PlaySibling[];
  dormant_siblings: PlaySibling[];
  total_balance: number;
  playable_count: number;
  unique_opps: number;
  urgency: number;
  needs_deposit: boolean;
}

export interface PlaySession {
  clusters: PlayCluster[];
  total_bankroll: number;
  min_stake: number;
}

export interface BatchBet {
  rank: number;
  tier: 'polymarket' | 'pinnacle' | 'soft';
  provider_id: string;
  event_id: string;
  market: string;
  outcome: string;
  point: number | null;
  odds: number;
  fair_odds: number;
  edge_pct: number;
  stake: number;
  expected_profit: number;
  is_bonus: boolean;
  bonus_type: string | null;
  display_home: string;
  display_away: string;
  sport: string;
  league: string;
  start_time: string | null;
  detected_at: string | null;
  odds_age_minutes: number | null;
  lifecycle: string | null;
  cluster: string | null;
  funded: boolean;
  skip_reason: string | null;
  wagering_pct?: number | null;
}

export interface BatchSummary {
  total_bets: number;
  total_stake: number;
  total_expected_profit: number;
  polymarket_bets: number;
  polymarket_ev: number;
  pinnacle_bets: number;
  pinnacle_ev: number;
  soft_bets: number;
  soft_ev: number;
  usdc_rate?: number;
  tier_breakdown?: Record<string, { count: number; stake: number; ev: number }>;
}

export interface ProviderBalanceStatus {
  provider_id: string;
  cluster: string | null;
  balance: number;
  allocated: number;
  remaining: number;
  lifecycle: string;
  excess?: number;
  shortfall?: number;
  missed_bets: number;
  missed_ev: number;
  wagering_total: number;
  wagering_remaining: number;
  days_remaining: number | null;
  trigger_mode: string;
  bonus_amount: number;
}

export interface CapitalAction {
  type: 'deposit' | 'withdraw';
  provider_id: string;
  cluster: string;
  amount: number;
  target_balance: number;
  unlocks: number;
  avg_edge: number;
  expected_ev: number;
  currency: 'SEK' | 'USDC';
  priority: number;
  priority_label: string;
}

export interface CapitalPlan {
  total_deployed: number;
  withdrawable: number;
  actions: CapitalAction[];
}

export interface WageringProjection {
  provider_id: string;
  cluster: string;
  wagering_total: number;
  wagering_remaining: number;
  batch_stake: number;
  projected_remaining: number;
  days_remaining: number | null;
}

export interface BatchResult {
  batch: BatchBet[];
  summary: BatchSummary;
  balance_status: ProviderBalanceStatus[];
  missed_opportunities: {
    total_bets: number;
    total_ev: number;
    reason: string;
  };
  capital_plan: CapitalPlan;
  wagering_projections: WageringProjection[];
}

export interface PendingBet {
  id: number;
  event_name: string;
  market: string;
  outcome: string;
  odds: number;
  stake: number;
  currency: string;
  placed_at: string | null;
}

export interface PendingProvider {
  provider_id: string;
  pending_count: number;
  total_stake: number;
  bets: PendingBet[];
}

export interface PendingBetsResponse {
  providers: PendingProvider[];
  total_pending: number;
  total_stake: number;
}

export interface SettleBetResult {
  bet_id: number;
  result: string;
  payout: number;
  settled_at: string;
}

// Play page: cluster batch (same shape as BatchResult)
export type ClusterBatchResult = BatchResult;
export type ClusterBet = BatchBet;

// Capital allocation step
export interface SiblingAssignment {
  provider_id: string;
  cluster: string;
  bets_assigned: number;
  capital_needed: number;
  current_balance: number;
  currency: 'SEK' | 'USDC';
  lifecycle: string;
  bonus_badge: string | null;
}

export interface AllocationResult {
  sibling_plan: SiblingAssignment[];
  allocated_batch: BatchBet[];
  wagering_projections: WageringProjection[];
}
