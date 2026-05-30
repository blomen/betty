export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  });
  if (!resp.ok) {
    // FastAPI puts the structured failure reason in `detail`. Without reading
    // it, every error reaches the UI as "API 502: Bad Gateway" — opaque, and
    // tells the user nothing about why nav/prep/placement failed. Try to read
    // it; fall back to statusText if the body isn't JSON.
    let detail = ''
    try {
      const body = await resp.clone().json()
      if (typeof body?.detail === 'string') detail = body.detail
      else if (body?.detail) detail = JSON.stringify(body.detail)
    } catch {
      try { detail = (await resp.text()).slice(0, 200) } catch { /* give up */ }
    }
    throw new Error(`API ${resp.status}: ${detail || resp.statusText}`);
  }
  return resp.json();
}

export const api = {
  // Play
  getPlayBatch: () => apiFetch<any>('/api/opportunities/play/batch', { method: 'POST' }),
  getPendingBets: () => apiFetch<any>('/api/opportunities/play/pending-bets'),
  settleConfirm: () => apiFetch<any>('/api/opportunities/play/settle-confirm', { method: 'POST' }),
  settleScan: () => apiFetch<any>('/api/opportunities/play/settle-scan'),
  // Arb
  getArbOpps: (providers: string[], counterpartProviders?: string[], limit?: number) => {
    if (!providers.length) return Promise.resolve({ opportunities: [] })
    const params = new URLSearchParams({ providers: providers.join(',') })
    if (counterpartProviders && counterpartProviders.length) {
      params.set('counterpart_providers', counterpartProviders.join(','))
    }
    if (limit) params.set('limit', String(limit))
    return apiFetch<any>(`/api/opportunities/arb-workflow?${params.toString()}`)
  },
  // Bankroll
  getBankrollSummary: () => apiFetch<any>('/api/bankroll'),
  getBankrollStats: () => apiFetch<any>('/api/bankroll/stats'),
  // Bonus / freebet lifecycle. /status and /bonuses already exist server-side;
  // these are the missing shims for the Sports-tab BonusChip. bonus-transition
  // advances freebet phases (start_freebet | trigger_settled | freebet_used);
  // claim-bonus dismisses ("taken on another account").
  getBankrollStatus: () => apiFetch<any>('/api/bankroll/status'),
  getProviderBonuses: () => apiFetch<any>('/api/bankroll/bonuses'),
  bonusTransition: (providerId: string, action: 'start_freebet' | 'trigger_settled' | 'freebet_used') =>
    apiFetch<any>(`/api/bankroll/bonus-transition/${providerId}`, { method: 'POST', body: JSON.stringify({ action }) }),
  claimBonus: (providerId: string) =>
    apiFetch<any>(`/api/bankroll/claim-bonus/${providerId}`, { method: 'POST' }),
  backfillWagering: () => apiFetch<any>('/api/bankroll/backfill-wagering', { method: 'POST' }),
  // Bonusdeposit start: records the deposit + arms the two-phase/single-phase
  // wagering machine (adds the deposit to the tracked balance server-side).
  depositWithBonus: (providerId: string, amount: number) =>
    apiFetch<any>(`/api/bankroll/deposit/${providerId}`, { method: 'POST', body: JSON.stringify({ amount }) }),
  // Bets / Stats
  getOpportunities: () => apiFetch<any>('/api/opportunities'),
  // Mirror (local)
  getBrowserTabs: () => apiFetch<any>('/mirror/browser/tabs'),
  getProviderState: (pid: string) => apiFetch<any>(`/mirror/browser/provider/${pid}`),
  navigateBet: (body: any) => apiFetch<any>('/mirror/navigate', { method: 'POST', body: JSON.stringify(body) }),
  placeBet: (body: any) => apiFetch<any>('/mirror/place', { method: 'POST', body: JSON.stringify(body) }),
  getMirrorStatus: () => apiFetch<any>('/mirror/status'),
  startMirror: () => apiFetch<any>('/mirror/start', { method: 'POST' }),
  openTab: (providerId: string) => apiFetch<any>('/mirror/open-provider-tab', { method: 'POST', body: JSON.stringify({ provider_id: providerId }) }),
  closeAllTabs: () => apiFetch<any>('/mirror/close-all-tabs', { method: 'POST' }),
  // Play loop control
  startPlayLoop: (batch: any[], balances: Record<string, number>, providerIds: string[]) =>
    apiFetch<any>('/mirror/play/start', { method: 'POST', body: JSON.stringify({ batch, balances, provider_ids: providerIds }) }),
  confirmSettlements: (confirmed?: { bet_id: number; result: string; payout: number }[]) =>
    apiFetch<any>('/mirror/play/confirm-settlements', {
      method: 'POST',
      body: JSON.stringify(confirmed ? { confirmed } : {}),
    }),
  placeCurrent: () => apiFetch<any>('/mirror/play/place', { method: 'POST' }),
  skipCurrent: (providerId?: string) =>
    apiFetch<any>('/mirror/play/skip', { method: 'POST', body: JSON.stringify({ provider_id: providerId }) }),
  stopPlayLoop: () => apiFetch<any>('/mirror/play/stop', { method: 'POST' }),
  runProvider: (providerId: string) =>
    apiFetch<any>(`/mirror/play/run/${providerId}`, { method: 'POST' }),
  pauseProvider: (providerId: string) =>
    apiFetch<any>(`/mirror/play/pause/${providerId}`, { method: 'POST' }),
  getPlayStatus: () => apiFetch<any>('/mirror/play/status'),
  // User-picked arb: drive the soft anchor tab to a specific opp (navigate +
  // prep + start slip-odds stream). Bypasses the runner queue.
  navigateOpp: (providerId: string, opp: any) =>
    apiFetch<any>('/mirror/arb/navigate-opp', {
      method: 'POST',
      body: JSON.stringify({ provider_id: providerId, opp }),
    }),
  // Tell the mirror that these legs were already recorded via /api/bets/batch
  // (external placement). Subsequent browser intercepts for the same provider
  // are dropped at the manual-fallback boundary so the same Pinnacle/counter
  // leg can't be inserted twice. Call AFTER a successful /api/bets/batch.
  markExternalPlaced: (legs: Array<{ provider_id: string; event_id?: string; market?: string; outcome?: string }>) =>
    apiFetch<any>('/mirror/bet/external-placed', {
      method: 'POST',
      body: JSON.stringify({ legs }),
    }),
  settleBatch: (batch: { bet_id: number; result: string }[]) =>
    apiFetch<any>('/api/opportunities/play/settle-batch', { method: 'POST', body: JSON.stringify(batch) }),
  // Manual betting controls — used by PlayPage inline buttons (Phase 1 of the
  // soft-automation strip-down). Backend endpoints already exist; these are
  // just the missing /hooks/useApi shims.
  setBalance: (providerId: string, balance: number) =>
    apiFetch<any>(`/api/bankroll/set/${providerId}`, {
      method: 'POST',
      body: JSON.stringify({ balance }),
    }),
  createBet: (data: Record<string, unknown>) =>
    apiFetch<any>('/api/bets', { method: 'POST', body: JSON.stringify(data) }),
  editBet: (betId: number, data: { stake?: number; odds?: number; result?: string; payout?: number }) =>
    apiFetch<any>(`/api/bets/${betId}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteBet: (betId: number) =>
    apiFetch<any>(`/api/bets/${betId}`, { method: 'DELETE' }),
  // Single-bet manual settle. settleBatch is for the auto-detected-then-confirmed
  // flow; this one fires immediately when the user clicks W/L/V on a pending row.
  settleBet: (betId: number, result: 'won' | 'lost' | 'void', payout?: number) =>
    apiFetch<any>('/api/opportunities/play/settle-bet', {
      method: 'POST',
      body: JSON.stringify({ bet_id: betId, result, ...(payout != null ? { payout } : {}) }),
    }),
};
