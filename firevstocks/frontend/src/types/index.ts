export interface CandleData {
  t: number
  o: number
  h: number
  l: number
  c: number
  v: number
}

export interface CandlesResponse {
  candles: CandleData[]
  symbol: string
  interval: string
}

export interface SessionLevelDay {
  date: string
  pdh: number | null
  pdl: number | null
  ib_high: number | null
  ib_low: number | null
  tokyo_high: number | null
  tokyo_low: number | null
  london_high: number | null
  london_low: number | null
  ny_high: number | null
  ny_low: number | null
  tokyo_start: number
  tokyo_end: number
  london_start: number
  london_end: number
  ib_start: number
  ib_end: number
  ny_start: number
  ny_end: number
  day_start: number
  day_end: number
}

export interface SwingPivot {
  price: number
  tf: string      // "daily" | "weekly"
  type: string    // "high" | "low"
  rank: number    // 0 = most recent
}

export interface SessionLevelsResponse {
  days: SessionLevelDay[]
  symbol: string
  swings?: SwingPivot[]
}

export interface VPData {
  levels: Array<{ price: number; volume: number }>
  poc: number
  vah: number
  val: number
  timeframe: string
}

export interface VWAPPoint {
  t: number
  vwap: number
  sd1_u: number
  sd1_l: number
  sd2_u: number
  sd2_l: number
  sd3_u: number
  sd3_l: number
}

export interface VWAPResponse {
  vwap?: VWAPPoint[]
  vwap_days?: VWAPPoint[][]
  symbol: string
  count: number
}

export interface SessionTPOData {
  letters: Record<string, string[]>
  tpo_counts: Record<string, number>
  poc: number
  vah: number
  val: number
  ib_high: number
  ib_low: number
  ib_valid: boolean
  shape: string
  opening_type: string
  opening_direction: string
  poor_high: boolean
  poor_low: boolean
  upper_excess: number
  lower_excess: number
  session_high: number
  session_low: number
  rotation_factor: number
}

export interface SessionTPOResponse {
  date: string
  sessions: {
    tokyo: SessionTPOData | null
    london: SessionTPOData | null
    ny: SessionTPOData | null
  }
  poc_migration_tokyo_london: number
  poc_migration_london_ny: number
}

export interface ExpandedSession {
  session: {
    vwap?: number
    poc?: number
    vah?: number
    val?: number
    ib_high?: number
    ib_low?: number
    last_price?: number
  }
  macro: {
    cot_net_position?: number | null
    cot_change_1w?: number | null
  }
  profiles: {
    session: { poc: number; vah: number; val: number }
    weekly?: { poc: number; vah: number; val: number }
    monthly?: { poc: number; vah: number; val: number }
  }
  price_position: { last_price: number | null }
}

export interface Signal {
  action: string
  confidence: number
  cont_p?: number
  rev_p?: number
  stop_ticks?: number
  zone?: string
  specialist?: string
  price?: number
  features?: number[]
  model_type?: string
  ts?: number
}

export interface DQNConnection {
  from_idx: number
  to_idx: number
  strength: number
  sign: number
}

export interface DQNInferenceEvent {
  type: 'dqn_inference'
  trigger: 'approaching' | 'touched' | 'zone_entry'
  price: number
  zone_center?: number
  zone_members?: number
  zone_hierarchy?: number
  inputs: number[]
  activations: {
    layer1: number[]
    layer2: number[]
    layer3: number[]
    layer4: number[]
  }
  connections: {
    input_l1: DQNConnection[]
    l1_l2: DQNConnection[]
    l2_l3: DQNConnection[]
    l3_l4: DQNConnection[]
    l4_output: DQNConnection[]
  }
  q_values: number[]
  action: string
  confidence?: number
  cont_p?: number
  rev_p?: number
  cont_ev?: number
  rev_ev?: number
  stop_ticks?: number
  sizing_signal?: number
  model_type?: string
  level?: string
  level_price?: number
  timestamp: number
}

export interface Zone {
  price: number
  members: number
  name?: string
}

export interface Fill {
  side: string
  price: number
  size: number
  ts: number
}

export interface ExitEvent {
  price: number
  was_stop?: boolean
  ts: number
}

export interface Quote {
  bid: number
  ask: number
  bid_size: number
  ask_size: number
}

export interface Position {
  side: string | number
  size: number
  price: number
  contractId?: string
}

export interface Account {
  id?: number
  balance?: number
  buyingPower?: number
  canTrade?: boolean
  [key: string]: unknown
}

export interface Trade {
  id: number
  accountId: number
  contractId: string
  side: number
  size: number
  price: number
  timestamp: string
  [key: string]: unknown
}

export interface BrokerTrade {
  id: number
  ts: string
  session_date: string
  symbol: string
  side: string
  size: number
  entry_price: number
  stop_price: number | null
  exit_price: number | null
  pnl_dollars: number | null
  pnl_r: number | null
  signal_action: string | null
  signal_confidence: number | null
  signal_zone: number | null
  closed_at: string | null
}

export interface ModelStatus {
  relay_connected: boolean
  stream_running: boolean
  trade_count: number
  signal_count: number
  session_start: number | null
  halted?: boolean
  halt_reason?: string
  session_pnl?: number
  peak_equity?: number
  trailing_dd?: number
  consecutive_stops?: number
  is_flat?: boolean
  position_side?: string | null
  position_size?: number
  entry_price?: number
  stop_price?: number
}

export interface Order {
  orderId?: number
  id?: number
  action?: string
  type?: string
  size?: number
  price?: number
  stopPrice?: number
  limitPrice?: number
  status?: string
  [key: string]: unknown
}

export interface LevelEntry {
  level_type: string
  price_low: number
  price_high: number
  direction: string
  session: string
  is_filled: boolean
}

export interface LevelsReplayResponse {
  date: string
  ticks_count: number
  episodes_count: number
  active_levels: Array<{
    name: string
    price: number
    type: string
  }>
  fvgs: Array<{ low: number; high: number; direction: string }>
  order_blocks: Array<{ low: number; high: number; direction: string }>
  session_levels: Record<string, number | null>
  vwap: { vwap: number | null; sd1_upper: number | null; sd1_lower: number | null; sd2_upper: number | null; sd2_lower: number | null } | null
  volume_profile: { poc: number | null; vah: number | null; val: number | null } | null
  error?: string
}
