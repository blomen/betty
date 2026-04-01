import { fetchJson, API_BASE } from './client';

export const tradingApi = {
  // ============ Trading ============

  async getTradingConfig(): Promise<import('@/types/trading').TradingConfig> {
    return fetchJson('/trading/config');
  },

  async getRoutineConfig(): Promise<{ macro_items: string[]; session_items: string[]; psych_threshold: number }> {
    return fetchJson('/trading/routine/config');
  },

  async getTradingAccounts(): Promise<{ accounts: import('@/types/trading').TradingAccount[] }> {
    return fetchJson('/trading/accounts');
  },

  async updateTradingAccount(id: number, data: Record<string, unknown>): Promise<{ success: boolean; account: import('@/types/trading').TradingAccount }> {
    return fetchJson(`/trading/accounts/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  async adjustTradingBalance(id: number, amount: number): Promise<{ success: boolean; balance: number }> {
    return fetchJson(`/trading/accounts/${id}/adjust`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ amount }),
    });
  },

  async resetTradingDaily(id: number): Promise<{ success: boolean }> {
    return fetchJson(`/trading/accounts/${id}/reset-daily`, { method: 'POST' });
  },

  async resetTradingWeekly(id: number): Promise<{ success: boolean }> {
    return fetchJson(`/trading/accounts/${id}/reset-weekly`, { method: 'POST' });
  },

  async getTodayRoutine(): Promise<import('@/types/trading').DailyRoutine> {
    return fetchJson('/trading/routine/today');
  },

  async updateRoutine(date: string, data: Record<string, unknown>): Promise<{ success: boolean; routine: import('@/types/trading').DailyRoutine }> {
    return fetchJson(`/trading/routine/${date}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  async createTrade(data: Record<string, unknown>): Promise<import('@/types/trading').TradeValidation & { success?: boolean; trade_id?: number; error?: string; dry_run?: boolean }> {
    return fetchJson('/trading/trades', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  async getTrades(filters?: {
    account_id?: number;
    instrument?: string;
    setup_type?: string;
    state?: string;
    limit?: number;
  }): Promise<{ trades: import('@/types/trading').Trade[]; count: number }> {
    const params = new URLSearchParams();
    if (filters?.account_id) params.set('account_id', filters.account_id.toString());
    if (filters?.instrument) params.set('instrument', filters.instrument);
    if (filters?.setup_type) params.set('setup_type', filters.setup_type);
    if (filters?.state) params.set('state', filters.state);
    if (filters?.limit) params.set('limit', filters.limit.toString());
    return fetchJson(`/trading/trades?${params}`);
  },

  async transitionTrade(id: number, toState: string, notes?: string): Promise<{ success: boolean; state: string }> {
    return fetchJson(`/trading/trades/${id}/transition`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ to_state: toState, notes }),
    });
  },

  async partialExitTrade(id: number, contracts: number, exitPrice: number, notes?: string): Promise<{ success: boolean; remaining_contracts: number; partial_pnl: number }> {
    return fetchJson(`/trading/trades/${id}/partial-exit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ contracts, exit_price: exitPrice, notes }),
    });
  },

  async moveToBE(id: number): Promise<{ success: boolean; stop_price: number }> {
    return fetchJson(`/trading/trades/${id}/move-to-be`, { method: 'POST' });
  },

  async trailStop(id: number, newStop: number, notes?: string): Promise<{ success: boolean; stop_price: number }> {
    return fetchJson(`/trading/trades/${id}/trail-stop`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ new_stop: newStop, notes }),
    });
  },

  async addPosition(id: number, contracts: number, entryPrice: number, notes?: string): Promise<{ success: boolean; contracts: number; avg_entry: number }> {
    return fetchJson(`/trading/trades/${id}/add-position`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ contracts, entry_price: entryPrice, notes }),
    });
  },

  async closeTrade(id: number, exitPrice: number, commission = 0, notes?: string): Promise<{ success: boolean; realized_pnl: number; r_multiple: number | null }> {
    return fetchJson(`/trading/trades/${id}/close`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ exit_price: exitPrice, commission, notes }),
    });
  },

  async submitTradeReview(id: number, data: Record<string, unknown>): Promise<{ success: boolean }> {
    return fetchJson(`/trading/trades/${id}/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  async getUnreviewedTrades(): Promise<{ trades: import('@/types/trading').Trade[]; count: number }> {
    return fetchJson('/trading/trades/unreviewed');
  },

  async getTradingAnalytics(filters?: {
    account_id?: number;
    instrument?: string;
    setup_type?: string;
  }): Promise<import('@/types/trading').TradingAnalytics> {
    const params = new URLSearchParams();
    if (filters?.account_id) params.set('account_id', filters.account_id.toString());
    if (filters?.instrument) params.set('instrument', filters.instrument);
    if (filters?.setup_type) params.set('setup_type', filters.setup_type);
    return fetchJson(`/trading/analytics?${params}`);
  },

  getTradingExportUrl(filters?: { state?: string; account_id?: number; instrument?: string }): string {
    const params = new URLSearchParams();
    if (filters?.state) params.set('state', filters.state);
    if (filters?.account_id) params.set('account_id', filters.account_id.toString());
    if (filters?.instrument) params.set('instrument', filters.instrument);
    return `${API_BASE}/trading/export/csv?${params}`;
  },

  // ============ Market Data / Scanner ============

  async getCandles(symbol = 'NQ', interval = '5m', date?: string, days = 5): Promise<import('@/types/market').CandlesResponse> {
    const params = new URLSearchParams({ symbol, interval, days: String(days) });
    if (date) params.set('date', date);
    return fetchJson(`/trading/market/candles?${params}`);
  },

  async getDevelopingVwap(symbol = 'NQ', interval = '1m'): Promise<{
    vwap: Array<{ t: number; vwap: number; sd1_u: number; sd1_l: number; sd2_u: number; sd2_l: number; sd3_u: number; sd3_l: number }>;
    symbol: string; count: number;
  }> {
    return fetchJson(`/trading/market/vwap?symbol=${symbol}&interval=${interval}`);
  },

  async getMarketSession(): Promise<import('@/types/market').MarketSession> {
    return fetchJson('/trading/market/session');
  },

  /** Get expanded session with all analytical layers (replaces getMarketSession for dashboard) */
  async getExpandedSession(): Promise<import('@/types/market').ExpandedSession> {
    return fetchJson('/trading/market/session');
  },

  async getVolumeProfile(symbol = 'NQ', timeframe = 'session'): Promise<{
    timeframe: string; poc: number; vah: number; val: number;
    levels: Array<{ price: number; volume: number }>;
  }> {
    return fetchJson(`/trading/market/volume-profile?symbol=${symbol}&timeframe=${timeframe}`);
  },

  async getSessionLevels(symbol = 'NQ', days = 5): Promise<import('@/types/market').SessionLevelsResponse> {
    return fetchJson(`/trading/market/session-levels?symbol=${symbol}&days=${days}`);
  },

  /** Get live indicators — orderflow + ML predictions (replaces getConfirmations) */
  async getIndicators(): Promise<import('@/types/market').IndicatorsResponse> {
    return fetchJson('/trading/market/indicators');
  },

  /** Update VP anchor dates (leg start, macro start) */
  async updateVPAnchors(data: { vp_leg_start?: string; vp_ongoing_macro_start?: string }, symbol = 'NQ'): Promise<{ status: string }> {
    return fetchJson(`/trading/market/context?symbol=${symbol}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  async getMarketSessionByDate(date: string): Promise<import('@/types/market').MarketSession> {
    return fetchJson(`/trading/market/session/${date}`);
  },

  async getMarketSignals(): Promise<{ signals: import('@/types/market').TradingSignal[] }> {
    return fetchJson('/trading/market/signals');
  },

  async triggerMarketScan(threshold?: number): Promise<{ signals: import('@/types/market').TradingSignal[]; count: number }> {
    const params = threshold ? `?threshold=${threshold}` : '';
    return fetchJson(`/trading/market/scan${params}`, { method: 'POST' });
  },

  async triggerMarketCompute(date?: string): Promise<import('@/types/market').MarketSession> {
    const params = date ? `?date=${date}` : '';
    return fetchJson(`/trading/market/compute${params}`, { method: 'POST' });
  },

  async getMarketHistory(limit = 30): Promise<{ sessions: import('@/types/market').MarketSessionSummary[] }> {
    return fetchJson(`/trading/market/history?limit=${limit}`);
  },

  async getMacroSnapshot(): Promise<import('@/types/market').MacroSnapshot> {
    return fetchJson('/trading/market/macro');
  },

  async getConfirmations(): Promise<import('@/types/market').ConfirmationState> {
    return fetchJson('/trading/market/confirmations');
  },

  async getMarketContext(symbol = 'NQ'): Promise<import('@/types/market').MarketContext> {
    const res = await fetch(`${API_BASE}/trading/market/context?symbol=${symbol}`);
    return res.json();
  },

  async updateMarketContext(data: Partial<import('@/types/market').MarketContext>, symbol = 'NQ') {
    const res = await fetch(`${API_BASE}/trading/market/context?symbol=${symbol}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    return res.json();
  },

  async getCotData(limit = 4): Promise<any[]> {
    return fetchJson(`/trading/market/cot?limit=${limit}`);
  },

  async getCotSummary(): Promise<{ cot_net_position: number | null; cot_change_1w: number | null }> {
    return fetchJson('/trading/market/cot/summary');
  },

  async getFootprint(period = 300, limit = 20): Promise<any> {
    return fetchJson(`/trading/market/footprint?period=${period}&limit=${limit}`);
  },

  async getTopOfBook(): Promise<{ bid_price: number; bid_size: number; ask_price: number; ask_size: number; spread: number; ts: string | null }> {
    return fetchJson('/trading/market/book');
  },

  async getTpoLive(symbol = 'NQ'): Promise<import('@/types/market').TPOLiveProfile> {
    return fetchJson(`/trading/market/tpo/live?symbol=${symbol}`);
  },

  async getSessionTPO(symbol = 'NQ'): Promise<import('@/types/market').SessionTPOResponse> {
    return fetchJson(`/trading/market/tpo/sessions?symbol=${symbol}`);
  },

  async getTpoHistory(symbol = 'NQ', days = 30): Promise<{
    sessions: import('@/types/market').TPOLiveProfile[];
    symbol: string;
    count: number;
  }> {
    return fetchJson(`/trading/market/tpo?symbol=${symbol}&days=${days}`);
  },

  async getMarketLevels(symbol = 'NQ', date?: string): Promise<any[]> {
    const params = date ? `?symbol=${symbol}&date=${date}` : `?symbol=${symbol}`;
    return fetchJson(`/trading/market/levels${params}`);
  },

  async getLiveLevels(symbol = 'NQ'): Promise<{ levels: import('@/types/market').MonitoredLevel[]; price: number | null }> {
    return fetchJson(`/trading/market/levels/live?symbol=${symbol}`);
  },

  async scalePosition(tradeId: number, pct: number = 50): Promise<{ success: boolean; remaining_contracts: number }> {
    return fetchJson(`/trading/trades/${tradeId}/scale?pct=${pct}`, { method: 'POST' });
  },

  async closePosition(tradeId: number): Promise<{ success: boolean }> {
    return fetchJson(`/trading/trades/${tradeId}/close`, { method: 'POST' });
  },

  async updateStop(tradeId: number, newStop: number): Promise<{ success: boolean }> {
    return fetchJson(`/trading/trades/${tradeId}/stop?new_stop=${newStop}`, { method: 'POST' });
  },
};
