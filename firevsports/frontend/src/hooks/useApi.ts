export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  });
  if (!resp.ok) throw new Error(`API ${resp.status}: ${resp.statusText}`);
  return resp.json();
}

export const api = {
  getPlayBatch: () => apiFetch<any>('/api/opportunities/play/batch', { method: 'POST' }),
  getPendingBets: () => apiFetch<any>('/api/opportunities/play/pending-bets'),
  navigateBet: (body: any) => apiFetch<any>('/mirror/navigate', { method: 'POST', body: JSON.stringify(body) }),
  placeBet: (body: any) => apiFetch<any>('/mirror/place', { method: 'POST', body: JSON.stringify(body) }),
  getMirrorStatus: () => apiFetch<any>('/mirror/status'),
  startMirror: () => apiFetch<any>('/mirror/start', { method: 'POST' }),
  confirmSettlements: (pid: string) => apiFetch<any>('/api/mirror/settlements/confirm-queue', {
    method: 'POST', body: JSON.stringify({ provider_id: pid }),
  }),
  getProviderState: (pid: string) => apiFetch<any>(`/api/mirror/state/${pid}`),
};
