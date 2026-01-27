export interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: Date;
  isStreaming?: boolean;
}

export interface ChatState {
  messages: Message[];
  isLoading: boolean;
  error: string | null;
}

export interface BettingContext {
  arbitrage: ArbitrageOpportunity[];
  valueBets: ValueBet[];
  events: Event[];
  providers: Provider[];
}

export interface ArbitrageOpportunity {
  id: string;
  event: string;
  sport: string;
  profit_pct: number;
  legs: ArbLeg[];
  created_at: string;
}

export interface ArbLeg {
  provider: string;
  outcome: string;
  odds: number;
  stake_pct: number;
}

export interface ValueBet {
  id: string;
  event: string;
  sport: string;
  provider: string;
  outcome: string;
  odds: number;
  fair_odds: number;
  edge_pct: number;
  kelly_stake: number;
  created_at: string;
}

export interface Event {
  id: string;
  canonical_id: string;
  sport: string;
  home_team: string;
  away_team: string;
  start_time: string;
  odds: OddsEntry[];
}

export interface OddsEntry {
  provider: string;
  market: string;
  outcome: string;
  odds: number;
  updated_at: string;
}

export interface Provider {
  id: string;
  name: string;
  balance: number;
  active: boolean;
}

export interface Profile {
  id: number;
  name: string;
  bankroll: number;
  currency: string;
  kelly_fraction: number;
  min_edge_pct: number;
  min_arb_pct: number;
  max_stake_pct: number;
  is_active: boolean;
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
}

export interface ProfileUpdate {
  name?: string;
  bankroll?: number;
  currency?: string;
  kelly_fraction?: number;
  min_edge_pct?: number;
  min_arb_pct?: number;
  max_stake_pct?: number;
}
