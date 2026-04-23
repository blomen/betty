// Trading types

export interface TradingAccount {
  id: number;
  name: string;
  account_type: 'intraday' | 'swing' | 'hodl';
  balance: number;
  equity: number;
  realized_pnl: number;
  daily_pnl: number;
  weekly_pnl: number;
  risk_per_trade_pct: number;
  max_daily_loss_pct: number;
  max_weekly_loss_pct: number;
  max_trades_per_day: number;
  stop_after_consecutive_losses: number;
  trades_today: number;
  consecutive_losses: number;
  is_daily_locked: boolean;
  is_weekly_locked: boolean;
}

export interface DailyRoutine {
  id: number;
  date: string;
  macro_notes: Record<string, string> | null;
  overnight_high: number | null;
  overnight_low: number | null;
  key_levels: Array<{ label: string; price: number }> | null;
  prev_value_area: { vah: number; val: number; poc: number } | null;
  bias_text: string | null;
  bias_direction: 'bullish' | 'bearish' | 'neutral' | null;
  bias_confidence: number | null;
  sleep_score: number | null;
  focus_score: number | null;
  emotional_score: number | null;
  psych_average: number | null;
  psych_override: string | null;
  checklist_completion: Record<string, boolean> | null;
  is_complete: boolean;
}

export interface TradeEvent {
  id: number;
  event_type: string;
  from_state: string | null;
  to_state: string | null;
  details: Record<string, unknown> | null;
  notes: string | null;
  timestamp: string | null;
}

export interface TradeReview {
  id: number;
  thesis_recap: string | null;
  followed_rules: boolean | null;
  what_to_improve: string | null;
  grade: number | null;
}

export interface Trade {
  id: number;
  account_id: number;
  account_name: string | null;
  daily_routine_id: number | null;
  instrument: string;
  direction: 'long' | 'short';
  setup_type: string;
  entry_price: number | null;
  stop_price: number | null;
  be_price: number | null;
  targets: Array<{ price: number; contracts?: number }> | null;
  contracts: number;
  risk_amount: number | null;
  rr_ratio: number | null;
  r_multiple: number | null;
  confirmations: Record<string, boolean> | null;
  state: string;
  realized_pnl: number | null;
  commission: number;
  notes: string | null;
  armed_at: string | null;
  triggered_at: string | null;
  opened_at: string | null;
  closed_at: string | null;
  created_at: string | null;
  events: TradeEvent[];
  review: TradeReview | null;
}

export interface InstrumentConfig {
  name: string;
  tick_size: number;
  tick_value: number;
  contract_size: number;
  margin: number;
  default_account: string;
}

export interface SetupConfirmation {
  name: string;
  category: string;
  description: string;
  confirmations: string[];
}

export interface TradingConfig {
  instruments: Record<string, InstrumentConfig>;
  setups: Record<string, SetupConfirmation>;
  daily_routine: {
    macro_items: string[];
    session_items: string[];
    psych_threshold: number;
  };
}

export interface TradeValidation {
  errors: string[];
  warnings: string[];
  sizing: {
    suggested_contracts?: number;
    risk_per_contract?: number;
    total_risk?: number;
    max_risk_dollars?: number;
    rr_ratio?: number | null;
  };
}

export interface SetupStats {
  count: number;
  wins: number;
  win_rate: number;
  total_pnl: number;
  avg_r: number;
  expectancy: number;
}

export interface InstrumentStats {
  count: number;
  wins: number;
  win_rate: number;
  total_pnl: number;
}

export interface DirectionStats {
  count: number;
  wins: number;
  total_pnl: number;
}

export interface EquityCurvePoint {
  trade_id: number;
  closed_at: string | null;
  pnl: number;
  cumulative_pnl: number;
}

export interface TradingAnalytics {
  total: number;
  wins?: number;
  losses?: number;
  breakevens?: number;
  win_rate?: number;
  total_pnl?: number;
  gross_wins?: number;
  gross_losses?: number;
  avg_win?: number;
  avg_loss?: number;
  profit_factor?: number | string;
  expectancy?: number;
  avg_r?: number;
  max_r?: number;
  min_r?: number;
  largest_win?: number;
  largest_loss?: number;
  max_win_streak?: number;
  max_loss_streak?: number;
  current_streak?: number;
  current_streak_direction?: string | null;
  by_setup?: Record<string, SetupStats>;
  by_instrument?: Record<string, InstrumentStats>;
  by_direction?: Record<string, DirectionStats>;
  equity_curve?: EquityCurvePoint[];
  avg_grade?: number;
  rules_followed_pct?: number;
  total_commission?: number;
}
