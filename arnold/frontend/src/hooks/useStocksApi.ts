const API_BASE = '/stocks/api'

async function fetchJson<T>(endpoint: string): Promise<T> {
  const res = await fetch(`${API_BASE}${endpoint}`)
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`)
  return res.json()
}

export const api = {
  getCandles(interval = '5m', days = 3, date?: string) {
    const params = new URLSearchParams({ interval, days: String(days) })
    if (date) params.set('date', date)
    return fetchJson<import('@/types/stocks').CandlesResponse>(`/candles?${params}`)
  },

  getSession() {
    return fetchJson<import('@/types/stocks').ExpandedSession>('/session')
  },

  getSessionLevels(days = 5) {
    return fetchJson<import('@/types/stocks').SessionLevelsResponse>(`/session-levels?days=${days}`)
  },

  getVP(tf: string, date?: string) {
    const params = date ? `?date=${date}` : ''
    return fetchJson<import('@/types/stocks').VPData>(`/vp/${tf}${params}`)
  },

  getVWAP(interval = '5m') {
    return fetchJson<import('@/types/stocks').VWAPResponse>(`/vwap?interval=${interval}`)
  },

  getSessionTPO() {
    return fetchJson<import('@/types/stocks').SessionTPOResponse>('/session-tpo')
  },

  getState() {
    return fetchJson<{
      ticks: Array<{ p: number; s: number; t: number; d: string }>
      signals: import('@/types/stocks').Signal[]
      quote: import('@/types/stocks').Quote | null
      zones: import('@/types/stocks').Zone[]
      account: import('@/types/stocks').Account
      positions: import('@/types/stocks').Position[]
      stats: { tick_count: number; signal_count: number; trade_count: number; session_start: number | null; relay_connected: boolean; stream_running: boolean }
    }>('/state')
  },

  getTrades() {
    return fetchJson<{ trades?: import('@/types/stocks').Trade[] }>('/trades')
  },

  getAccountInfo() {
    return fetchJson<import('@/types/stocks').Account>('/account-info')
  },

  getBrokerTrades(days = 30) {
    return fetchJson<{ trades: import('@/types/stocks').BrokerTrade[] }>(`/broker-trades?days=${days}`)
  },

  getModelStatus() {
    return fetchJson<import('@/types/stocks').ModelStatus>('/model-status')
  },

  getOrders() {
    return fetchJson<{ orders: import('@/types/stocks').Order[] }>('/orders')
  },

  flatten() {
    return fetch(`${API_BASE}/flatten`, { method: 'POST' }).then(r => r.json())
  },

  cancelOrder(orderId: number) {
    return fetch(`${API_BASE}/cancel-order/${orderId}`, { method: 'POST' }).then(r => r.json())
  },

  getLevels(date?: string) {
    const params = date ? `?date=${date}` : ''
    return fetchJson<import('@/types/stocks').LevelEntry[]>(`/levels${params}`)
  },

  getLevelsReplay(date?: string) {
    const params = date ? `?date=${date}` : ''
    return fetchJson<import('@/types/stocks').LevelsReplayResponse>(`/levels/replay${params}`)
  },
}
