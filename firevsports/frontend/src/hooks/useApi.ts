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
  // Bankroll
  getBankrollSummary: () => apiFetch<any>('/api/bankroll'),
  getBankrollStats: () => apiFetch<any>('/api/bankroll/stats'),
  // Bets / Stats
  getOpportunities: () => apiFetch<any>('/api/opportunities'),
  // Mirror (local)
  navigateBet: (body: any) => apiFetch<any>('/mirror/navigate', { method: 'POST', body: JSON.stringify(body) }),
  placeBet: (body: any) => apiFetch<any>('/mirror/place', { method: 'POST', body: JSON.stringify(body) }),
  getMirrorStatus: () => apiFetch<any>('/mirror/status'),
  startMirror: () => apiFetch<any>('/mirror/start', { method: 'POST' }),
};
