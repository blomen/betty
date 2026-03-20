// dqnConfig.ts — maps each of the 107 DQN observation indices to display properties

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
  { name: 'LEVEL TYPE',  color: '#06b6d4', start: 0,  end: 26 },
  { name: 'ORDERFLOW',   color: '#10b981', start: 26, end: 41 },
  { name: 'STRUCTURE',   color: '#8b5cf6', start: 41, end: 64 },
  { name: 'TPO',         color: '#f59e0b', start: 64, end: 77 },
  { name: 'CANDLES',     color: '#ec4899', start: 77, end: 92 },
  { name: 'CONFLUENCE',  color: '#14b8a6', start: 92, end: 97 },
  { name: 'MACRO',       color: '#ef4444', start: 97, end: 107 },
];

// Level type names (indices 0-25) — matches LevelType enum order in config.py
const LEVEL_TYPES = [
  'poc_session', 'poc_daily', 'poc_weekly', 'poc_monthly', 'poc_macro',
  'vah', 'val', 'vwap', 'vwap_sd1', 'vwap_sd2', 'vwap_sd3',
  'ib_high', 'ib_low', 'pdh', 'pdl',
  'tokyo_hl', 'london_hl', 'globex_hl', 'overnight_hl',
  'weekly_hl', 'monthly_hl',
  'naked_poc', 'single_print', 'fvg', 'order_block', 'swing_point',
];

// Orderflow feature names (indices 26-40)
const ORDERFLOW = [
  'delta_pct', 'delta_norm', 'cvd_norm', 'cvd_trend',
  'vol_ratio', 'body_ratio', 'spread_ticks', 'pa_ratio',
  'imbal_max', 'stacked_cnt', 'stacked_dir',
  'big_cnt', 'big_net_delta', 'absorption', 'stop_run',
];

// Structure feature names (indices 41-63)
const STRUCTURE = [
  'vwap_sd', 'in_va', 'poc_dist', 'vah_dist', 'val_dist', 'single_prints',
  'ib_range', 'poor_high', 'poor_low',
  'mkt_trend', 'mkt_range', 'mkt_neutral',
  'min_since_rth', 'sess_vol%', 'daily_range%', 'tod_sin', 'tod_cos',
  'sess_rth', 'sess_globex', 'sess_london',
  'ib_break_up', 'ib_break_dn', 'ib_intact',
];

// TPO feature names (indices 64-76)
const TPO = [
  'poc_dist', 'va_width', 'in_va', 'time_at_px',
  'excess_hi', 'excess_lo', 'rotation_f', 'rotation_n',
  'shape_p', 'shape_b', 'shape_d', 'shape_bal', 'reserved',
];

// Candle window feature names (indices 77-91) — 5 candles x 3 features
const CANDLES = [
  'c1 delta', 'c1 vol', 'c1 body',
  'c2 delta', 'c2 vol', 'c2 body',
  'c3 delta', 'c3 vol', 'c3 body',
  'c4 delta', 'c4 vol', 'c4 body',
  'c5 delta', 'c5 vol', 'c5 body',
];

// Confluence feature names (indices 92-96)
const CONFLUENCE = [
  'levels_near', 'cluster_score', 'dist_higher', 'dist_lower', 'hierarchy',
];

// Macro feature names (indices 97-106)
const MACRO = [
  'vix', 'vix_chg', 'regime', 'dxy_chg', 'gex',
  'us10y_chg', 'us2y_chg', 'yield_curve', 'news', 'news_sev',
];

// Build the full 107-element array
export const DQN_INPUTS: DQNInputDef[] = [
  ...LEVEL_TYPES.map((label, i) => ({ index: i, label, segment: 'LEVEL TYPE' })),
  ...ORDERFLOW.map((label, i) => ({ index: 26 + i, label, segment: 'ORDERFLOW' })),
  ...STRUCTURE.map((label, i) => ({ index: 41 + i, label, segment: 'STRUCTURE' })),
  ...TPO.map((label, i) => ({ index: 64 + i, label, segment: 'TPO' })),
  ...CANDLES.map((label, i) => ({ index: 77 + i, label, segment: 'CANDLES' })),
  ...CONFLUENCE.map((label, i) => ({ index: 92 + i, label, segment: 'CONFLUENCE' })),
  ...MACRO.map((label, i) => ({ index: 97 + i, label, segment: 'MACRO' })),
];

/** Get segment color for a given segment name */
export function getSegmentColor(segmentName: string): string {
  return DQN_SEGMENTS.find(s => s.name === segmentName)?.color ?? '#52525b';
}

/** Hidden layer sizes (real DQN architecture) */
export const HIDDEN_LAYERS = [128, 128, 64] as const;
export const NUM_ACTIONS = 3;
export const ACTION_NAMES = ['LONG', 'SHORT', 'SKIP'] as const;
export const ACTION_COLORS = ['#10b981', '#ef4444', '#52525b'] as const;
