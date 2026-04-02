// dqnConfig.ts — maps DQN observation indices to display properties
// Synced with backend/src/rl/features/observation.py build_observation() segments

export interface DQNInputDef {
  index: number;
  label: string;
  segment: string;
}

export interface DQNSegment {
  name: string;
  color: string;
  start: number;
  end: number;  // exclusive
}

export const DQN_SEGMENTS: DQNSegment[] = [
  { name: 'LEVEL TYPE',     color: '#06b6d4', start: 0,   end: 31  },
  { name: 'ORDERFLOW',      color: '#10b981', start: 31,  end: 52  },
  { name: 'DOW/SESSION',    color: '#8b5cf6', start: 52,  end: 116 },
  { name: 'TPO',            color: '#f59e0b', start: 116, end: 154 },
  { name: 'CANDLES',        color: '#ec4899', start: 154, end: 169 },
  { name: 'ZONE',           color: '#a3e635', start: 169, end: 173 },
  { name: 'CONFLUENCE',     color: '#14b8a6', start: 173, end: 178 },
  { name: 'MACRO',          color: '#ef4444', start: 178, end: 189 },
  { name: 'EXCHANGE STATS', color: '#38bdf8', start: 189, end: 194 },
  { name: 'SETUP',          color: '#f97316', start: 194, end: 208 },
  { name: 'AMT',            color: '#a78bfa', start: 208, end: 221 },
  { name: 'MICRO',          color: '#22d3ee', start: 221, end: 241 },
  { name: 'APPROACH',       color: '#94a3b8', start: 241, end: 242 },
  { name: 'EXECUTION',      color: '#fb923c', start: 242, end: 249 },
];

// Level type names (indices 0-30) — matches LevelType enum in rl/config.py
const LEVEL_TYPES = [
  'daily_poc', 'daily_vah', 'daily_val',
  'weekly_poc', 'weekly_vah', 'weekly_val',
  'monthly_poc', 'monthly_vah', 'monthly_val',
  'vwap', 'vwap_sd1', 'vwap_sd2', 'vwap_sd3',
  'pdh', 'pdl', 'tokyo_high', 'tokyo_low', 'nyib_high', 'nyib_low',
  'tpoc', 'tvah', 'tval', 'tibh', 'tibl',
  'naked_poc',
  'daily_swing_high', 'daily_swing_low',
  'weekly_swing_high', 'weekly_swing_low',
  'monthly_swing_high', 'monthly_swing_low',
];

// Orderflow feature names (indices 31-51)
const ORDERFLOW = [
  'delta_pct', 'delta_norm', 'cvd_norm', 'cvd_trend',
  'vol_ratio', 'body_ratio', 'spread_ticks', 'pa_ratio',
  'imbal_max', 'stacked_cnt', 'stacked_dir',
  'big_cnt', 'big_net_delta', 'absorption', 'stop_run',
  'delta_accel', 'cvd_divergence', 'vol_trend', 'pa_trend', 'imbal_trend', 'time_weight',
];

// Dow Theory + session context feature names (indices 52-115, 64 features)
const DOW_SESSION = [
  // Session context (0-19)
  'vwap_sd', 'in_va', 'poc_dist', 'vah_dist', 'val_dist', 'va_width',
  'ib_range', 'poor_high', 'poor_low',
  'min_since_rth', 'sess_vol%', 'daily_range%', 'tod_sin', 'tod_cos',
  'sess_rth', 'sess_globex', 'sess_london',
  'ib_break_up', 'ib_break_dn', 'ib_intact',
  // Dow Theory swings (20-59)
  'trend_d', 'trend_w', 'trend_m',
  'dist_sh_d', 'dist_sh_w', 'dist_sh_m',
  'dist_sl_d', 'dist_sl_w', 'dist_sl_m',
  'above_sh_d', 'above_sh_w', 'above_sh_m',
  'below_sl_d', 'below_sl_w', 'below_sl_m',
  'position_d', 'position_w', 'position_m',
  'hh_lh_d', 'hh_lh_w', 'hh_lh_m',
  'hl_ll_d', 'hl_ll_w', 'hl_ll_m',
  'swing_range_d', 'swing_range_w', 'swing_range_m',
  'bos_d', 'bos_w', 'bos_m',
  'choch_d', 'choch_w', 'choch_m',
  'event_dir_d', 'event_dir_w', 'event_dir_m',
  'momentum_d', 'momentum_w', 'momentum_m',
  'trend_alignment',
  // PDH/PDL (60-63)
  'pdh_dist', 'pdl_dist', 'pdh_pdl_pos', 'pdh_pdl_width',
];

// TPO per-session features (indices 116-153) — 3 sessions × ~12 features + global
const TPO = [
  'tky_poc_dist', 'tky_vah_dist', 'tky_val_dist', 'tky_in_va',
  'tky_shape_p', 'tky_shape_b', 'tky_shape_d',
  'tky_ib_range', 'tky_rotation', 'tky_opening_type',
  'tky_poc_migration', 'tky_excess',
  'ldn_poc_dist', 'ldn_vah_dist', 'ldn_val_dist', 'ldn_in_va',
  'ldn_shape_p', 'ldn_shape_b', 'ldn_shape_d',
  'ldn_ib_range', 'ldn_rotation', 'ldn_opening_type',
  'ldn_poc_migration', 'ldn_excess',
  'ny_poc_dist', 'ny_vah_dist', 'ny_val_dist', 'ny_in_va',
  'ny_shape_p', 'ny_shape_b', 'ny_shape_d',
  'ny_ib_range', 'ny_rotation', 'ny_opening_type',
  'ny_poc_migration', 'ny_excess',
  'global_rotation', 'global_poc_migration',
];

// Candle window feature names (indices 154-168) — 5 candles × 3 features
const CANDLES = [
  'c1_delta', 'c1_vol', 'c1_body',
  'c2_delta', 'c2_vol', 'c2_body',
  'c3_delta', 'c3_vol', 'c3_body',
  'c4_delta', 'c4_vol', 'c4_body',
  'c5_delta', 'c5_vol', 'c5_body',
];

// Zone features (indices 169-172)
const ZONE = [
  'zone_width', 'zone_members', 'zone_hierarchy', 'zone_session_age',
];

// Confluence feature names (indices 173-177)
const CONFLUENCE = [
  'levels_near', 'cluster_score', 'dist_higher', 'dist_lower', 'hierarchy',
];

// Macro feature names (indices 178-188)
const MACRO = [
  'vix', 'vix_chg', 'regime', 'dxy_chg',
  'us10y_chg', 'us2y_chg', 'yield_curve',
  'cot_net', 'cot_chg', 'news_prox', 'news_imp',
];

// Exchange stats feature names (indices 189-193)
const EXCHANGE_STATS = [
  'oi_norm', 'oi_change', 'settlement_dist', 'cleared_vol', 'block_ratio',
];

// Setup detection feature names (indices 194-207)
const SETUP = [
  'poor_extr', 'ib_break', 'spring', 'sfp',
  'rule80', 'fakeout', 'brk_balance', 'dbl_dist',
  'news_dir', 'absorption', 'vwap_sd2', 'gap_logic', 'pbd', 'rsv_setup',
];

// AMT features (indices 208-220)
const AMT = [
  'day_trend', 'day_normal', 'day_neutral', 'day_range',
  'open_drive', 'open_test', 'open_reject', 'open_auction',
  'range_ext_up', 'range_ext_dn',
  'va_overlap', 'value_migration', 'globex_hl_ratio',
];

// Micro feature names (indices 221-240)
const MICRO = [
  'approach_vel', 'approach_accel', 'net_delta', 'delta_trend',
  'max_trade', 'big_trade%', 'buy_vol%', 'tick_spread',
  'consec_dir', 'reversal_cnt', 'time_compress', 'last5_vel',
  'last5_delta', 'bid_aggress', 'touch_size', 'linearity',
  'vol_surge', 'rsv_0', 'rsv_1', 'rsv_2',
];

// Approach direction (index 241)
const APPROACH = ['approach_dir'];

// Execution context (indices 242-248)
const EXECUTION = [
  'auction_quality', 'ib_time_pct', 'time_at_level', 'retest_count',
  'prior_touch_result', 'session_momentum', 'tick_velocity',
];

// Build the full observation array
export const DQN_INPUTS: DQNInputDef[] = [
  ...LEVEL_TYPES.map((label, i) => ({ index: i, label, segment: 'LEVEL TYPE' })),
  ...ORDERFLOW.map((label, i) => ({ index: 31 + i, label, segment: 'ORDERFLOW' })),
  ...DOW_SESSION.map((label, i) => ({ index: 52 + i, label, segment: 'DOW/SESSION' })),
  ...TPO.map((label, i) => ({ index: 116 + i, label, segment: 'TPO' })),
  ...CANDLES.map((label, i) => ({ index: 154 + i, label, segment: 'CANDLES' })),
  ...ZONE.map((label, i) => ({ index: 169 + i, label, segment: 'ZONE' })),
  ...CONFLUENCE.map((label, i) => ({ index: 173 + i, label, segment: 'CONFLUENCE' })),
  ...MACRO.map((label, i) => ({ index: 178 + i, label, segment: 'MACRO' })),
  ...EXCHANGE_STATS.map((label, i) => ({ index: 189 + i, label, segment: 'EXCHANGE STATS' })),
  ...SETUP.map((label, i) => ({ index: 194 + i, label, segment: 'SETUP' })),
  ...AMT.map((label, i) => ({ index: 208 + i, label, segment: 'AMT' })),
  ...MICRO.map((label, i) => ({ index: 221 + i, label, segment: 'MICRO' })),
  ...APPROACH.map((label, i) => ({ index: 241 + i, label, segment: 'APPROACH' })),
  ...EXECUTION.map((label, i) => ({ index: 242 + i, label, segment: 'EXECUTION' })),
];

/** Get segment color for a given segment name */
export function getSegmentColor(segmentName: string): string {
  return DQN_SEGMENTS.find(s => s.name === segmentName)?.color ?? '#52525b';
}

/** Hidden layer sizes (real Dueling DQN architecture: 256→256→128→64) */
export const HIDDEN_LAYERS = [256, 256, 128, 64] as const;
export const NUM_ACTIONS = 3;
export const ACTION_NAMES = ['CONT', 'REV', 'SKIP'] as const;
export const ACTION_COLORS = ['#10b981', '#ef4444', '#52525b'] as const;
