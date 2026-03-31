// dqnConfig.ts — maps each of the 160 DQN observation indices to display properties

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
  { name: 'LEVEL TYPE',  color: '#06b6d4', start: 0,   end: 31  },
  { name: 'ORDERFLOW',   color: '#10b981', start: 31,  end: 46  },
  { name: 'STRUCTURE',   color: '#8b5cf6', start: 46,  end: 84  },
  { name: 'TPO',         color: '#f59e0b', start: 84,  end: 97  },
  { name: 'CANDLES',     color: '#ec4899', start: 97,  end: 112 },
  { name: 'CONFLUENCE',  color: '#14b8a6', start: 112, end: 120 },
  { name: 'MACRO',       color: '#ef4444', start: 120, end: 127 },
  { name: 'SETUP',       color: '#f97316', start: 127, end: 140 },
  { name: 'MICRO',       color: '#22d3ee', start: 140, end: 160 },
];

// Level type names (indices 0-30) — matches LevelType enum order in config.py
const LEVEL_TYPES = [
  // Volume profile — daily
  'daily_poc', 'daily_vah', 'daily_val',
  // Volume profile — weekly
  'weekly_poc', 'weekly_vah', 'weekly_val',
  // Volume profile — monthly
  'monthly_poc', 'monthly_vah', 'monthly_val',
  // VWAP bands
  'vwap', 'vwap_sd1', 'vwap_sd2', 'vwap_sd3',
  // Session
  'pdh', 'pdl', 'tokyo_high', 'tokyo_low', 'nyib_high', 'nyib_low',
  // TPO
  'tpoc', 'tvah', 'tval', 'tibh', 'tibl',
  // Structure
  'naked_poc',
  // Swing levels
  'daily_swing_high', 'daily_swing_low',
  'weekly_swing_high', 'weekly_swing_low',
  'monthly_swing_high', 'monthly_swing_low',
];

// Orderflow feature names (indices 31-45)
const ORDERFLOW = [
  'delta_pct', 'delta_norm', 'cvd_norm', 'cvd_trend',
  'vol_ratio', 'body_ratio', 'spread_ticks', 'pa_ratio',
  'imbal_max', 'stacked_cnt', 'stacked_dir',
  'big_cnt', 'big_net_delta', 'absorption', 'stop_run',
];

// Structure feature names (indices 46-77)
const STRUCTURE = [
  'vwap_sd', 'in_va', 'poc_dist', 'vah_dist', 'val_dist', 'single_prints',
  'ib_range', 'poor_high', 'poor_low',
  'mkt_trend', 'mkt_range', 'mkt_neutral',
  'min_since_rth', 'sess_vol%', 'daily_range%', 'tod_sin', 'tod_cos',
  'sess_rth', 'sess_globex', 'sess_london',
  'ib_break_up', 'ib_break_dn', 'ib_intact',
  // Swing structure
  'swing_trend_d', 'swing_trend_w', 'swing_trend_m',
  'swing_dist_d', 'swing_dist_w', 'swing_dist_m',
  'swing_pos_d', 'swing_pos_w', 'swing_pos_m',
  // BOS / CHoCH flags
  'bos_d', 'bos_w', 'bos_m',
  'choch_d', 'choch_w', 'choch_m',
];

// TPO feature names (indices 84-96)
const TPO = [
  'poc_dist', 'va_width', 'in_va', 'time_at_px',
  'excess_hi', 'excess_lo', 'rotation_f', 'rotation_n',
  'shape_p', 'shape_b', 'shape_d', 'shape_bal', 'reserved',
];

// Candle window feature names (indices 97-111) — 5 candles x 3 features
const CANDLES = [
  'c1 delta', 'c1 vol', 'c1 body',
  'c2 delta', 'c2 vol', 'c2 body',
  'c3 delta', 'c3 vol', 'c3 body',
  'c4 delta', 'c4 vol', 'c4 body',
  'c5 delta', 'c5 vol', 'c5 body',
];

// Confluence feature names (indices 112-119) — now includes FVG/SP overlap
const CONFLUENCE = [
  'levels_near', 'cluster_score', 'dist_higher', 'dist_lower', 'hierarchy',
  'fvg_overlap', 'fvg_width', 'sp_overlap',
];

// Macro feature names (indices 120-130)
const MACRO = [
  'vix', 'vix_chg', 'regime', 'dxy_chg',
  'us10y_chg', 'us2y_chg', 'yield_curve',
  'cot_net', 'cot_chg', 'news_prox', 'news_imp',
];

// Setup detection feature names (indices 127-139)
const SETUP = [
  'poor_extr', 'ib_break', 'spring', 'sfp',
  'rule80', 'fakeout', 'brk_balance', 'dbl_dist',
  'news_dir', 'absorption', 'vwap_sd2', 'gap_logic', 'pbd',
];

// Micro feature names (indices 140-159) — tick-level context at touch
const MICRO = [
  'approach_vel', 'approach_accel', 'net_delta', 'delta_trend',
  'max_trade', 'big_trade%', 'buy_vol%', 'tick_spread',
  'consec_dir', 'reversal_cnt', 'time_compress', 'last5_vel',
  'last5_delta', 'bid_aggress', 'touch_size', 'linearity',
  'vol_surge', 'rsv_0', 'rsv_1', 'rsv_2',
];

// Build the full 160-element array
export const DQN_INPUTS: DQNInputDef[] = [
  ...LEVEL_TYPES.map((label, i) => ({ index: i, label, segment: 'LEVEL TYPE' })),
  ...ORDERFLOW.map((label, i) => ({ index: 31 + i, label, segment: 'ORDERFLOW' })),
  ...STRUCTURE.map((label, i) => ({ index: 46 + i, label, segment: 'STRUCTURE' })),
  ...TPO.map((label, i) => ({ index: 84 + i, label, segment: 'TPO' })),
  ...CANDLES.map((label, i) => ({ index: 97 + i, label, segment: 'CANDLES' })),
  ...CONFLUENCE.map((label, i) => ({ index: 112 + i, label, segment: 'CONFLUENCE' })),
  ...MACRO.map((label, i) => ({ index: 120 + i, label, segment: 'MACRO' })),
  ...SETUP.map((label, i) => ({ index: 127 + i, label, segment: 'SETUP' })),
  ...MICRO.map((label, i) => ({ index: 140 + i, label, segment: 'MICRO' })),
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
