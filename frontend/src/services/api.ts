import type { ArbitrageOpportunity, ValueBet, Event, Provider } from '@/types';

const API_BASE = '/api';

async function fetchJson<T>(endpoint: string): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`);
  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`);
  }
  return response.json();
}

export const api = {
  async getArbitrage(): Promise<ArbitrageOpportunity[]> {
    try {
      return await fetchJson<ArbitrageOpportunity[]>('/opportunities?type=arbitrage');
    } catch {
      return [];
    }
  },

  async getValueBets(): Promise<ValueBet[]> {
    try {
      return await fetchJson<ValueBet[]>('/opportunities?type=value');
    } catch {
      return [];
    }
  },

  async getEvents(): Promise<Event[]> {
    try {
      return await fetchJson<Event[]>('/events');
    } catch {
      return [];
    }
  },

  async getProviders(): Promise<Provider[]> {
    try {
      return await fetchJson<Provider[]>('/providers');
    } catch {
      return [];
    }
  },

  async runPipeline(providers?: string[]): Promise<{ status: string }> {
    const params = providers ? `?providers=${providers.join(',')}` : '';
    return fetchJson(`/pipeline/run${params}`);
  },

  async getHealth(): Promise<{ status: string; providers: Record<string, boolean> }> {
    return fetchJson('/health');
  },
};
