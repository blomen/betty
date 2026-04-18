export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  });
  if (!resp.ok) throw new Error(`API ${resp.status}: ${resp.statusText}`);
  return resp.json();
}

export const api = {
  // Play
  getPlayBatch: () => apiFetch<any>('/api/opportunities/play/batch', { method: 'POST' }),
  getPendingBets: () => apiFetch<any>('/api/opportunities/play/pending-bets'),
  settleConfirm: () => apiFetch<any>('/api/opportunities/play/settle-confirm', { method: 'POST' }),
  settleScan: () => apiFetch<any>('/api/opportunities/play/settle-scan'),
  // Dutch
  getDutchOpportunities: () => apiFetch<any>('/api/opportunities/dutch-workflow'),
  getArbOpps: (providers: string[]) => {
    if (!providers.length) return Promise.resolve({ opportunities: [] })
    const qs = encodeURIComponent(providers.join(','))
    return apiFetch<any>(`/api/opportunities/dutch-workflow?providers=${qs}`)
  },
  // Bankroll
  getBankrollSummary: () => apiFetch<any>('/api/bankroll'),
  getBankrollStats: () => apiFetch<any>('/api/bankroll/stats'),
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
  getPlayStatus: () => apiFetch<any>('/mirror/play/status'),
  settleBatch: (batch: { bet_id: number; result: string }[]) =>
    apiFetch<any>('/api/opportunities/play/settle-batch', { method: 'POST', body: JSON.stringify(batch) }),
};
