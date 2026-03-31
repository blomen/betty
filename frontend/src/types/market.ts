/** Market data and scanner types. */

export interface MarketSession {
  date: string;
  symbol: string;

  // Volume profile
  poc?: number;
  vah?: number;
  val?: number;

  // VWAP bands
  vwap?: number;
  vwap_1sd_upper?: number;
  vwap_1sd_lower?: number;
  vwap_2sd_upper?: number;
  vwap_2sd_lower?: number;
  vwap_3sd_upper?: number;
  vwap_3sd_lower?: number;

  // Initial balance
  ib_high?: number;
  ib_low?: number;
  ib_range?: number;

  // Session structural levels
  pdh?: number;
  pdl?: number;
  tokyo_high?: number;
  tokyo_low?: number;
  london_high?: number;
  london_low?: number;

  // Overnight
  overnight_high?: number;
  overnight_low?: number;

  // Previous day
  prev_poc?: number;
  prev_vah?: number;
  prev_val?: number;

  // Delta
  total_delta?: number;
  delta_divergence?: boolean;
  cumulative_delta_last?: number;

  // TPO profile
  tpo_poc?: number;
  tpo_vah?: number;
  tpo_val?: number;
  distribution_type?: string;

  // Macro
  macro?: MacroSnapshot;

  // Classifications
  market_type?: string;
  opening_type?: string;
  poor_high?: boolean;
  poor_low?: boolean;
  single_prints?: [number, number][];

  // Current state
  last_price?: number;
  price_vs_va?: string;
  price_vs_vwap?: string;
  price_vs_ib?: string;

  // Session metrics (Task 23b)
  rotation_factor?: number;
  aspr?: number;
  aspr_percentile?: number;
  value_migration?: 'up' | 'down' | 'neutral';

  // Meta
  status?: string;
  message?: string;
}

export interface ScanCondition {
  name: string;
  score: number;
  weight: number;
  is_auto: boolean;
  detail?: string;
}

export interface TradingSignal {
  id: number;
  setup_type: string;
  setup_name: string;
  setup_category?: string;
  category: string;
  direction: 'long' | 'short';
  score: number;
  conditions: ScanCondition[] | string;
  price_at_signal?: number;
  suggested_entry?: number;
  suggested_stop?: number;
  suggested_target?: number;
  suggested_target_2?: number;
  suggested_target_3?: number;
  rr_tp1?: number;
  level_touched?: string;
  vwap?: number;
  poc?: number;
  triggered_at?: string;
  trade_id?: number;
}

export interface MacroSnapshot {
  vix?: number;
  vix_change_pct?: number;
  dxy?: number;
  dxy_change_pct?: number;
  us10y?: number;
  us10y_change_bps?: number;
  us2y?: number;
  yield_curve_spread?: number;
  regime: string;
  regime_score: number;
  fetched_at?: string;
  news_proximity?: number;
  news_importance?: number;
}

export interface MarketSessionSummary {
  date: string;
  symbol: string;
  poc?: number;
  vah?: number;
  val?: number;
  vwap?: number;
  ib_high?: number;
  ib_low?: number;
  market_type?: string;
  opening_type?: string;
  total_delta?: number;
}

/** @deprecated Use OrderflowIndicators instead */
export interface ConfirmationCard {
  checked: boolean;
  regime?: string;
  vix?: number | null;
  structure?: string;
  deviation_sd?: number | null;
  price_vs_va?: string;
  // Rich orderflow signals (only on orderflow card)
  delta?: number | null;
  delta_aligned?: boolean;
  divergence?: boolean;
  delta_unwind?: boolean;
  cvd?: number | null;
  cvd_trend?: 'rising' | 'falling' | 'flat';
  vsa_absorption?: boolean;
  tick_vol_accelerating?: boolean;
  trapped_traders?: boolean;
  passive_active_ratio?: number;
  big_trades_count?: number;
  big_trades_net_delta?: number;
  stop_run_detected?: boolean;
  // Imbalance stacking (footprint analysis)
  imbalance_ratio_max?: number;
  stacked_imbalance_count?: number;
  stacked_imbalance_direction?: 'buy' | 'sell' | 'neutral';
}

/** @deprecated Use IndicatorsResponse instead */
export interface ConfirmationState {
  macro: ConfirmationCard;
  span: ConfirmationCard;
  fair_value: ConfirmationCard;
  orderflow: ConfirmationCard;
  // M7 gate classifier predictions
  ml_day_type?: string;
  ml_day_type_confidence?: number;
}

export interface StreamTickEvent {
  type: 'tick';
  ts: string;
  price: number;
  size: number;
  side: 'A' | 'B';
  cvd: number;
  delta_1m: number;
}

export interface StreamBookEvent {
  type: 'book';
  ts: string;
  bid_price: number;
  bid_size: number;
  ask_price: number;
  ask_size: number;
  spread: number;
}

export interface MarketContext {
  symbol: string;
  gates_set: boolean;
  macro_bias?: 'bull' | 'bear' | 'neutral';
  risk_mode?: 'risk_on' | 'risk_off' | 'mixed';
  cycle_phase?: 'early' | 'mid' | 'late' | 'recession';
  structure?: 'uptrend' | 'downtrend' | 'ranging';
  structure_hl?: number;
  structure_lh?: number;
  day_type?: 'trend' | 'normal' | 'normal_variation' | 'neutral' | 'composite';
  vp_current_start?: number;
  vp_ongoing_macro_start?: number;
  vp_leg_start?: number;
}

// === Expanded Dashboard Types ===

/** Swing point structure from detect_swing_points() */
export interface PriceStructure {
  structure: 'uptrend' | 'downtrend' | 'ranging';
  last_hh: number | null;
  last_hl: number | null;
  last_lh: number | null;
  last_ll: number | null;
  swing_high: number | null;
  swing_low: number | null;
}

/** Individual swing point from fractal pivot detection */
export interface SwingLevel {
  price: number;
  timestamp: number;
  type: 'swing_high' | 'swing_low';
  timeframe: 'daily' | 'weekly' | 'monthly';
}

/** Structural event: BOS or CHoCH */
export interface StructureEvent {
  price: number;
  timestamp: number;
  event_type: 'bos_bullish' | 'bos_bearish' | 'choch_bullish' | 'choch_bearish';
  swing_type: 'swing_high' | 'swing_low';
  swing_price: number;
}

/** Per-timeframe swing analysis */
export interface TimeframeSwings {
  timeframe: string;
  structure: 'uptrend' | 'downtrend' | 'reversing_up' | 'reversing_down' | 'ranging';
  swing_highs: SwingLevel[];
  swing_lows: SwingLevel[];
  last_bos: StructureEvent | null;
  last_choch: StructureEvent | null;
  bos_active: boolean;
  choch_active: boolean;
}

/** Multi-timeframe swing structure from compute_multi_tf_swings() */
export interface SwingStructure {
  daily: TimeframeSwings;
  weekly: TimeframeSwings;
  monthly: TimeframeSwings;
  trend_alignment: number;
}

/** Multi-timeframe volume profile data */
export interface VPLevel {
  poc: number;
  vah: number;
  val: number;
  anchor?: string;
}

export interface NakedPOC {
  date: string;
  price: number;
}

export interface ProfilesData {
  session: VPLevel;
  weekly?: VPLevel;
  monthly?: VPLevel;
  developing_poc: number | null;
  developing_poc_direction: 'up' | 'down' | 'flat';
  naked_pocs: NakedPOC[];
}

/** Structural level from MarketLevel table */
export interface StructuralLevel {
  type: string;
  price_low: number;
  price_high: number;
  direction: string | null;
  session?: string;
  is_filled?: boolean;
}

/** Price position relative to key levels */
export interface PricePosition {
  last_price: number | null;
  vs_va: string;
  vs_vwap: string;
  vs_ib: string;
  vwap_deviation_sd?: number;
}

/** Expanded session response (replaces old MarketSession for dashboard) */
export interface ExpandedSession {
  session: MarketSession;
  macro: MacroSnapshot & {
    cot_net_position?: number | null;
    cot_change_1w?: number | null;
    gex?: number | null;
    put_call_ratio?: number | null;
    es_nq_ratio_change?: number | null;
  };
  structure: PriceStructure;
  swing_structure?: SwingStructure;
  profiles: ProfilesData;
  levels: StructuralLevel[];
  price_position: PricePosition;
  ml_day_type: string | null;
  ml_day_type_confidence: number | null;
}

/** Orderflow indicator data (replaces ConfirmationCard for orderflow) */
export interface OrderflowIndicators {
  delta: number | null;
  delta_aligned: boolean;
  delta_divergence: boolean;
  delta_unwind: boolean;
  cvd: number | null;
  cvd_trend: 'rising' | 'falling' | 'flat';
  vsa_absorption: boolean;
  tick_vol_accelerating: boolean;
  trapped_traders: boolean;
  passive_active_ratio: number | null;
  big_trades_count: number;
  big_trades_net_delta: number;
  stop_run_detected: boolean;
  imbalance_ratio_max: number | null;
  stacked_imbalance_count: number;
  stacked_imbalance_direction: 'buy' | 'sell' | 'neutral';
}

/** Indicators response (replaces ConfirmationState) */
export interface IndicatorsResponse {
  orderflow: OrderflowIndicators;
  ml_day_type: string | null;
  ml_day_type_confidence: number | null;
}

/** Single OHLCV candle for chart rendering */
export interface CandleData {
  t: number;  // Unix epoch seconds
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

/** Response from GET /api/trading/market/candles */
export interface CandlesResponse {
  candles: CandleData[];
  symbol: string;
  interval: string;
  date: string;
}

/** Per-day session levels with CET epoch boundaries for time-scoped chart drawing */
export interface SessionLevelDay {
  date: string;
  pdh: number | null;
  pdl: number | null;
  pdh_time: number | null;
  pdl_time: number | null;
  ib_high: number | null;
  ib_low: number | null;
  tokyo_high: number | null;
  tokyo_low: number | null;
  london_high: number | null;
  london_low: number | null;
  ny_high: number | null;
  ny_low: number | null;
  tokyo_start: number;
  tokyo_end: number;
  london_start: number;
  london_end: number;
  ib_start: number;
  ib_end: number;
  ny_start: number;
  ny_end: number;
  day_start: number;
  day_end: number;
  // Swing levels (most recent confirmed pivot per timeframe)
  daily_swing_high: number | null;
  daily_swing_low: number | null;
  weekly_swing_high: number | null;
  weekly_swing_low: number | null;
  monthly_swing_high: number | null;
  monthly_swing_low: number | null;
}

export interface SessionLevelsResponse {
  days: SessionLevelDay[];
  symbol: string;
}

// --- Level Battle Station types ---

export type LevelStatusType = 'watching' | 'approaching' | 'at_level' | 'triggered' | 'rejected';

export interface MonitoredLevel {
  name: string;
  price: number;
  category: 'session' | 'band' | 'prior' | 'structure' | 'overnight';
  status: LevelStatusType;
  distance_ticks: number;
  cluster: string[];
}

export interface OrderflowSnapshot {
  long: OrderflowIndicators;
  short: OrderflowIndicators;
}

export interface LevelTouchedEvent {
  type: 'level_touched';
  level: string;
  level_price: number;
  category: string;
  price: number;
  confluence: string[];
  orderflow: OrderflowSnapshot;
}

export interface LevelApproachingEvent {
  type: 'level_approaching';
  level: string;
  level_price: number;
  category: string;
  price: number;
  distance_ticks: number;
}

export interface OrderflowUpdateEvent {
  type: 'orderflow_update';
  price: number;
  ts: number;
  orderflow: OrderflowSnapshot;
}

export interface LevelRejectedEvent {
  type: 'level_rejected';
  level: string;
  level_price: number;
}

export interface BattleScreenData {
  level: string;
  level_price: number;
  category: string;
  price: number;
  confluence: string[];
  orderflow: OrderflowSnapshot;
  structure: ExpandedSession['session'] | null;
  ml: {
    day_type: string | null;
    day_type_confidence: number | null;
  } | null;
  macro: ExpandedSession['macro'] | null;
  suggested_entry: number;
  suggested_stop: number;
  targets: { name: string; price: number }[];
}

export interface MlPrediction {
  level: string;
  predicted: string;
  raw_predicted?: string;
  confidence: number;
  probabilities: Record<string, number>;
  top_features?: Array<{ name: string; contribution: number }>;
  timestamp: number;
}

export interface PositionRow {
  trade_id: number;
  instrument: string;
  direction: 'long' | 'short';
  entry_price: number;
  current_size: number;
  original_size: number;
  current_price: number;
  pnl_points: number;
  pnl_dollars: number;
  stop_price: number;
  targets: { name: string; price: number; hit: boolean }[];
  next_target: { name: string; price: number } | null;
  status: 'running' | 'at_target' | 'stopped' | 'closed';
}

export interface MlFeatureSnapshot {
  level: string;
  features: Record<string, number | string | boolean | null>;
  timestamp: number;
}

export interface TPOLiveProfile {
  date?: string;
  poc: number;
  vah: number;
  val: number;
  ib_high: number;
  ib_low: number;
  rotation_factor: number;
  profile_shape: string;
  opening_type: string;
  opening_direction: string;
  upper_excess: number;
  lower_excess: number;
  session_high: number;
  session_low: number;
  tpo_counts: Record<string, number>;
  single_prints: number[];
  letters: Record<string, string[]>;
}

export interface SessionTPOData {
  letters: Record<string, string[]>;  // price → [A, B, C, ...]
  tpo_counts: Record<string, number>; // price → count
  poc: number;
  vah: number;
  val: number;
  ib_high: number;
  ib_low: number;
  ib_valid: boolean;
  shape: string;
  opening_type: string;
  opening_direction: string;
  poor_high: boolean;
  poor_low: boolean;
  upper_excess: number;
  lower_excess: number;
  session_high: number;
  session_low: number;
  rotation_factor: number;
}

export interface SessionTPOResponse {
  date: string;
  sessions: {
    tokyo: SessionTPOData | null;
    london: SessionTPOData | null;
    ny: SessionTPOData | null;
  };
  poc_migration_tokyo_london: number;
  poc_migration_london_ny: number;
}

export interface MlHealth {
  model_loaded: boolean;
  version: number | null;
  training_data_count: number;
  validation_score: number | null;
  baseline_metric: number | null;
  class_distribution: Record<string, number>;
  recent_accuracy: { last_50?: number; sample_count?: number };
  top_features: Array<{ name: string; importance: number }>;
  use_fallback: boolean;
  trained_at: string | null;
}

// --- DQN Live Inference ---

export interface DQNConnection {
  from_idx: number;
  to_idx: number;
  strength: number;
  sign: 1 | -1;
}

export interface DQNInferenceEvent {
  type: 'dqn_inference';
  trigger: 'approaching' | 'touched';
  level: string;
  level_price: number;
  inputs: number[];           // 160
  activations: {
    layer1: number[];         // 256
    layer2: number[];         // 256
    layer3: number[];         // 128
    layer4: number[];         // 64
  };
  q_values: number[];         // 3: [CONTINUATION, REVERSAL, SKIP]
  action: 'CONTINUATION' | 'REVERSAL' | 'SKIP';
  connections: {
    input_l1: DQNConnection[];
    l1_l2: DQNConnection[];
    l2_l3: DQNConnection[];
    l3_l4: DQNConnection[];
    l4_output: DQNConnection[];
    l3_output?: DQNConnection[];  // legacy compat
  };
  timestamp: number;
}
