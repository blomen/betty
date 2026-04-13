// dqnConfig.ts — maps DQN observation indices to display properties
// Synced with backend/src/rl/features/observation.py build_observation() segments
// Zone mode: 276 dims total

export interface DQNSegment {
  name: string
  color: string
  start: number
  end: number // exclusive
  features: string[]
}

export const DQN_SEGMENTS: DQNSegment[] = [
  {
    name: 'LEVEL TYPE', color: '#06b6d4', start: 0, end: 31,
    features: [
      'daily_poc', 'daily_vah', 'daily_val',
      'weekly_poc', 'weekly_vah', 'weekly_val',
      'monthly_poc', 'monthly_vah', 'monthly_val',
      'vwap', 'vwap_sd1', 'vwap_sd2', 'vwap_sd3',
      'pdh', 'pdl',
      'tokyo_high', 'tokyo_low', 'nyib_high', 'nyib_low',
      'tpoc', 'tvah', 'tval', 'tibh', 'tibl',
      'naked_poc',
      'daily_swing_high', 'daily_swing_low',
      'weekly_swing_high', 'weekly_swing_low',
      'monthly_swing_high', 'monthly_swing_low',
    ],
  },
  {
    name: 'ORDERFLOW', color: '#10b981', start: 31, end: 52,
    features: [
      'delta_pct', 'delta', 'cvd', 'cvd_trend',
      'volume_ratio', 'body_ratio', 'spread_ticks',
      'passive_active_ratio', 'imbalance_density',
      'stacked_imbalance_count', 'stacked_direction',
      'big_trades_count', 'big_trades_net_delta',
      'vsa_absorption', 'stop_run_detected',
      'delta_acceleration', 'absorption_strength',
      'initiative_momentum', 'volume_climax',
      'delta_divergence', 'flow_shift',
    ],
  },
  {
    name: 'STRUCTURE', color: '#8b5cf6', start: 52, end: 116,
    features: [
      // VWAP (1)
      'price_vs_vwap_sd',
      // Volume Profile (5)
      'price_in_va', 'dist_to_poc', 'dist_to_vah', 'dist_to_val', 'va_width',
      // IB Range (3)
      'ib_range', 'poor_high', 'poor_low',
      // Session Context (11)
      'timing', 'session_volume_pct', 'daily_range_pct',
      'minute_sin', 'minute_cos',
      'session_rth', 'session_globex', 'session_london',
      'ib_broken_up', 'ib_broken_down', 'ib_broken_none',
      // Dow Theory Daily (13)
      'trend_d', 'dist_sh_d', 'dist_sl_d', 'above_sh_d', 'below_sl_d',
      'position_d', 'hh_lh_d', 'hl_ll_d', 'swing_range_d',
      'bos_d', 'choch_d', 'last_dir_d', 'momentum_d',
      // Dow Theory Weekly (13)
      'trend_w', 'dist_sh_w', 'dist_sl_w', 'above_sh_w', 'below_sl_w',
      'position_w', 'hh_lh_w', 'hl_ll_w', 'swing_range_w',
      'bos_w', 'choch_w', 'last_dir_w', 'momentum_w',
      // Dow Theory Monthly (13)
      'trend_m', 'dist_sh_m', 'dist_sl_m', 'above_sh_m', 'below_sl_m',
      'position_m', 'hh_lh_m', 'hl_ll_m', 'swing_range_m',
      'bos_m', 'choch_m', 'last_dir_m', 'momentum_m',
      // Trend alignment (1)
      'trend_alignment',
      // PDH/PDL (4)
      'dist_pdh', 'dist_pdl', 'position_pdh_pdl', 'pdh_pdl_range',
    ],
  },
  {
    name: 'TPO', color: '#f59e0b', start: 116, end: 154,
    features: [
      // Tokyo (12)
      'tky_vs_poc', 'tky_vs_vah', 'tky_vs_val', 'tky_shape',
      'tky_ib_range', 'tky_vs_ib_mid', 'tky_poor',
      'tky_va_pos', 'tky_rotation', 'tky_open_type', 'tky_open_dir', 'tky_excess',
      // London (12)
      'ldn_vs_poc', 'ldn_vs_vah', 'ldn_vs_val', 'ldn_shape',
      'ldn_ib_range', 'ldn_vs_ib_mid', 'ldn_poor',
      'ldn_va_pos', 'ldn_rotation', 'ldn_open_type', 'ldn_open_dir', 'ldn_excess',
      // NY (12)
      'ny_vs_poc', 'ny_vs_vah', 'ny_vs_val', 'ny_shape',
      'ny_ib_range', 'ny_vs_ib_mid', 'ny_poor',
      'ny_va_pos', 'ny_rotation', 'ny_open_type', 'ny_open_dir', 'ny_excess',
      // POC migration (2)
      'poc_mig_tky_ldn', 'poc_mig_ldn_ny',
    ],
  },
  {
    name: 'CANDLES', color: '#ec4899', start: 154, end: 169,
    features: [
      'c1_delta', 'c1_vol', 'c1_body',
      'c2_delta', 'c2_vol', 'c2_body',
      'c3_delta', 'c3_vol', 'c3_body',
      'c4_delta', 'c4_vol', 'c4_body',
      'c5_delta', 'c5_vol', 'c5_body',
    ],
  },
  {
    name: 'ZONE', color: '#a3e635', start: 169, end: 173,
    features: ['width_norm', 'count_norm', 'hierarchy', 'session_relevance'],
  },
  {
    name: 'CONFLUENCE', color: '#14b8a6', start: 173, end: 178,
    features: [
      'nearest_higher_zone', 'nearest_lower_zone',
      'fvg_overlap', 'fvg_width', 'single_print_overlap',
    ],
  },
  {
    name: 'MACRO', color: '#ef4444', start: 178, end: 189,
    features: [
      'vix', 'vix_change', 'regime_score',
      'dxy_change', 'us10y_change', 'us2y_change', 'yield_spread',
      'cot_net', 'cot_change',
      'news_proximity', 'news_importance',
    ],
  },
  {
    name: 'EXCHANGE', color: '#38bdf8', start: 189, end: 194,
    features: ['oi', 'oi_change', 'settlement_dist', 'cleared_vol', 'block_vol_ratio'],
  },
  {
    name: 'SETUP', color: '#f97316', start: 194, end: 208,
    features: [
      'poor_extreme', 'ib_break', 'spring', 'sfp',
      'rule_80', 'fakeout', 'break_from_balance',
      'double_distribution', 'news_directional', 'absorption',
      'vwap_sd2_reversal', 'gap_logic', 'pbd', 'squeeze',
    ],
  },
  {
    name: 'AMT', color: '#a78bfa', start: 208, end: 228,
    features: [
      // Day type one-hot (6)
      'dt_non_trend', 'dt_normal', 'dt_neutral',
      'dt_normal_var', 'dt_trend', 'dt_double_dist',
      // Opening type one-hot (4)
      'ot_od', 'ot_otd', 'ot_orr', 'ot_oa',
      // Scalar (3)
      'range_ext', 'va_overlap', 'value_migration',
      // Static enrichment (7)
      'ib_percentile', 'overnight_gap', 'open_vs_prior_poc',
      'composite_va_overlap', 'prior_poor_high', 'prior_poor_low',
      'prior_excess_quality',
    ],
  },
  {
    name: 'AMT DYN', color: '#c084fc', start: 228, end: 248,
    features: [
      'ib_ext_up', 'ib_ext_down', 'ib_max_ext', 'ib_ext_net',
      'dev_day_type', 'day_type_conf',
      'responsive_ratio', 'initiative_ratio',
      'va_accept_high', 'va_reject_high', 'va_accept_low', 'va_reject_low',
      'poc_mig_speed', 'va_width_exp_rate',
      'balance_duration', 'balance_width',
      'single_print_prox', 'excess_high', 'excess_low', 'otf_activity',
    ],
  },
  {
    name: 'MICRO', color: '#22d3ee', start: 248, end: 268,
    features: [
      'approach_vel', 'approach_accel', 'net_delta', 'delta_trend',
      'max_trade_size', 'big_trade_ratio', 'buy_vol_ratio', 'tick_spread',
      'consec_dir', 'reversal_count', 'time_compress', 'last5_vel',
      'last5_delta', 'bid_aggression', 'size_at_touch',
      'approach_linear', 'vol_surge', 'price_vs_mid', 'big_trade_skew', 'last5_accel',
    ],
  },
  {
    name: 'APPROACH', color: '#94a3b8', start: 268, end: 269,
    features: ['direction'],
  },
  {
    name: 'EXECUTION', color: '#fb923c', start: 269, end: 276,
    features: [
      'follow_through', 'follow_strength',
      'responsive_auction', 'initiative_auction',
      'session_atr', 'vol_anomaly', 'time_in_session',
    ],
  },
]

/** Hidden layer sizes (Dueling DQN: 256->256->128->64) */
export const HIDDEN_LAYERS = [256, 256, 128, 64] as const
export const NUM_ACTIONS = 3
export const ACTION_NAMES = ['CONT', 'REV', 'SKIP'] as const
export const ACTION_COLORS = ['#10b981', '#ef4444', '#52525b'] as const
