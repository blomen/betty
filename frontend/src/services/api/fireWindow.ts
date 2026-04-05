import { fetchJson } from './client';

export interface FireWindowBet {
  bet_id: number;
  provider_id: string;
  event_id: string;
  market: string;
  outcome: string;
  point: number | null;
  odds: number;
  fair_odds: number;
  edge_pct: number;
  stake: number;
  expected_profit: number;
  display_home: string;
  display_away: string;
  sport: string;
  tier: string;
  market_slug: string | null;
  poly_outcome: string | null;
  original_outcome: string | null;
  start_time: string | null;
  // Live data
  live_odds: number | null;
  live_price_cents: number | null;
  live_edge: number | null;
  delta: number;
  category: 'improved' | 'stable' | 'degraded' | 'negative' | 'pending';
  last_updated: string | null;
}

export interface ProviderQueueItem {
  provider_id: string;
  bet_count: number;
  total_stake: number;
  total_ev: number;
  tier: string;
  fired: boolean;
}

export interface LiveState {
  provider_id: string;
  tier: string;
  position: number;
  total_providers: number;
  status: string;
  bets: FireWindowBet[];
  summary: {
    total_bets: number;
    active_bets: number;
    excluded_bets: number;
    total_stake: number;
    total_ev: number;
  };
}

export interface FireResult {
  provider_id: string;
  placed: any[];
  failed: any[];
  excluded: any[];
  summary: { total: number; fired: number; failed: number; excluded: number };
  next_provider: string | null;
}

export const fireWindowApi = {
  open(batch: any[], providerOrder?: string[]) {
    return fetchJson<{ status: string; queue: ProviderQueueItem[]; current_provider: string | null }>(
      '/fire-window/open',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ batch, provider_order: providerOrder }),
      },
    );
  },

  activate(providerId: string) {
    return fetchJson<LiveState>(
      `/fire-window/activate/${providerId}`,
      { method: 'POST' },
      120_000,
    );
  },

  getState() {
    return fetchJson<LiveState>('/fire-window/state');
  },

  fire() {
    return fetchJson<FireResult>(
      '/fire-window/fire',
      { method: 'POST' },
      300_000,
    );
  },

  skip() {
    return fetchJson<{ provider_id: string; status: string; next_provider: string | null }>(
      '/fire-window/skip',
      { method: 'POST' },
    );
  },

  getQueue() {
    return fetchJson<{ status: string; queue: ProviderQueueItem[]; current_provider: string | null }>(
      '/fire-window/queue',
    );
  },

  close() {
    return fetchJson<{ status: string }>(
      '/fire-window/close',
      { method: 'POST' },
    );
  },

  getSummary() {
    return fetchJson<{ status: string; providers: any[]; totals: any }>(
      '/fire-window/summary',
    );
  },

  openTabs() {
    return fetchJson<{ opened: string[]; count: number }>(
      '/fire-window/open-tabs',
      { method: 'POST' },
      60_000,
    );
  },

  getNextBet() {
    return fetchJson<any>('/fire-window/next-bet');
  },

  checkBet(betId: number) {
    return fetchJson<any>(`/fire-window/check-bet/${betId}`, { method: 'POST' }, 30_000);
  },

  placeBet(betId: number) {
    return fetchJson<any>(`/fire-window/place-bet/${betId}`, { method: 'POST' }, 60_000);
  },

  skipBet(betId: number) {
    return fetchJson<any>(`/fire-window/skip-bet/${betId}`, { method: 'POST' });
  },
};
