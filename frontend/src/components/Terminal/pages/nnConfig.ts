// nnConfig.ts — defines every feature node in the neural network

export type NNColor = 'green' | 'red' | 'amber' | 'dim';

export interface NNFeatureDef {
  key: string;          // feature dict key or special key like 'book.bid_size'
  label: string;        // short display label
  group: string;        // group name for vertical clustering
  range: [number, number]; // [min, max] for visual normalization to [0,1]
  colorFn: (v: number) => NNColor; // bullish/bearish coloring
}

// Color helpers
const posneg = (v: number): NNColor => v > 0 ? 'green' : v < 0 ? 'red' : 'dim';
const high = (v: number): NNColor => v > 0.5 ? 'amber' : 'dim';
const bool = (v: number): NNColor => v ? 'amber' : 'dim';

export const NN_FEATURES: NNFeatureDef[] = [
  // BOOK
  { key: 'book.bid_size', label: 'BID', group: 'BOOK', range: [0, 2000], colorFn: (v) => v > 500 ? 'green' : 'dim' },
  { key: 'book.ask_size', label: 'ASK', group: 'BOOK', range: [0, 2000], colorFn: (v) => v > 500 ? 'red' : 'dim' },
  { key: 'book.spread', label: 'SPREAD', group: 'BOOK', range: [0, 2], colorFn: (v) => v > 0.75 ? 'amber' : 'dim' },
  { key: 'passive_active_ratio', label: 'PA RATIO', group: 'BOOK', range: [0, 4], colorFn: (v) => v > 2 ? 'amber' : 'dim' },

  // ORDERFLOW
  { key: 'delta', label: 'DELTA', group: 'FLOW', range: [-50000, 50000], colorFn: posneg },
  { key: 'cvd', label: 'CVD', group: 'FLOW', range: [-50000, 50000], colorFn: posneg },
  { key: 'vsa_absorption', label: 'ABSORB', group: 'FLOW', range: [0, 1], colorFn: bool },
  { key: 'stacked_imbalance_count', label: 'IMBAL', group: 'FLOW', range: [0, 5], colorFn: high },
  { key: 'big_trades_count', label: 'BIG', group: 'FLOW', range: [0, 10], colorFn: high },
  { key: 'trapped_traders', label: 'TRAPPED', group: 'FLOW', range: [0, 1], colorFn: bool },
  { key: 'stop_run_detected', label: 'STOP RUN', group: 'FLOW', range: [0, 1], colorFn: bool },

  // TEMPORAL
  { key: 'delta_slope_5m', label: 'Δ SLP 5M', group: 'TEMPORAL', range: [-100, 100], colorFn: posneg },
  { key: 'delta_slope_10m', label: 'Δ SLP 10M', group: 'TEMPORAL', range: [-100, 100], colorFn: posneg },
  { key: 'cvd_acceleration', label: 'CVD ACCEL', group: 'TEMPORAL', range: [-2, 2], colorFn: posneg },
  { key: 'volume_roc_5m', label: 'VOL ROC', group: 'TEMPORAL', range: [-5, 5], colorFn: posneg },
  { key: 'price_velocity', label: 'PX VEL', group: 'TEMPORAL', range: [-5, 5], colorFn: posneg },

  // CANDLE
  { key: 'last_candle_body_ratio', label: 'BODY', group: 'CANDLE', range: [0, 1], colorFn: high },
  { key: 'last_candle_delta', label: 'LAST Δ', group: 'CANDLE', range: [-5000, 5000], colorFn: posneg },

  // SESSION
  { key: 'market_type', label: 'MKT TYPE', group: 'SESSION', range: [0, 4], colorFn: high },
  { key: 'opening_type', label: 'OPEN TYPE', group: 'SESSION', range: [0, 4], colorFn: high },
  { key: 'ib_range', label: 'IB RANGE', group: 'SESSION', range: [0, 100], colorFn: high },

  // MACRO
  { key: 'vix_level', label: 'VIX', group: 'MACRO', range: [10, 80], colorFn: (v) => v > 25 ? 'amber' : 'dim' },
  { key: 'regime_score', label: 'REG SCORE', group: 'MACRO', range: [0, 1], colorFn: high },

  // LEVEL
  { key: 'level_strength', label: 'STRENGTH', group: 'LEVEL', range: [0, 1], colorFn: (v) => v > 0.5 ? 'green' : 'dim' },
  { key: 'level_confluence', label: 'CONFLNCE', group: 'LEVEL', range: [0, 5], colorFn: (v) => v >= 2 ? 'green' : 'dim' },
  { key: 'delta_aligned', label: 'Δ ALIGN', group: 'LEVEL', range: [0, 1], colorFn: (v) => v ? 'green' : 'red' },

  // APPROACH VOLUME
  { key: 'approach_vol_slope', label: 'VOL SLOPE', group: 'APPROACH', range: [-2, 2], colorFn: posneg },
  { key: 'approach_vol_ratio', label: 'VOL INTO', group: 'APPROACH', range: [0, 3], colorFn: high },
  { key: 'approach_delta_slope', label: 'Δ INTO', group: 'APPROACH', range: [-2, 2], colorFn: posneg },
];

/** Group labels in display order */
export const NN_GROUPS = ['BOOK', 'FLOW', 'TEMPORAL', 'CANDLE', 'SESSION', 'MACRO', 'LEVEL', 'APPROACH'];

/** Normalize a raw value to [0, 1] given a feature's range */
export function normalizeValue(value: number, range: [number, number]): number {
  const [min, max] = range;
  return Math.max(0, Math.min(1, (Math.abs(value) - Math.abs(min)) / (Math.abs(max) - Math.abs(min))));
}

/** Format a raw value for display next to the node */
export function formatValue(value: number | string | boolean | null | undefined): string {
  if (value == null) return '--';
  if (typeof value === 'boolean') return value ? 'YES' : '--';
  if (typeof value === 'string') return value;
  if (Math.abs(value) >= 1000) return `${value > 0 ? '+' : ''}${(value / 1000).toFixed(1)}k`;
  if (Math.abs(value) >= 1) return `${value > 0 ? '+' : ''}${value.toFixed(0)}`;
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}`;
}
