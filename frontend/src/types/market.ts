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

export interface ConfirmationCard {
  checked: boolean;
  regime?: string;
  vix?: number | null;
  structure?: string;
  deviation_sd?: number | null;
  price_vs_va?: string;
  delta?: number | null;
  divergence?: boolean;
}

export interface ConfirmationState {
  macro: ConfirmationCard;
  span: ConfirmationCard;
  fair_value: ConfirmationCard;
  orderflow: ConfirmationCard;
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
  vp_old_macro_start?: number;
  vp_ongoing_macro_start?: number;
  vp_leg_start?: number;
}
