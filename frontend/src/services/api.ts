import type { ArbitrageOpportunity, ValueBet, Event, Provider, Profile, ProfileCreate, ProfileUpdate } from '@/types';

const API_BASE = '/api';

async function fetchJson<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`, options);
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `API error: ${response.status}`);
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

  // Profile API
  async getProfiles(): Promise<{ profiles: Profile[]; active: Profile | null }> {
    return fetchJson('/profiles');
  },

  async getActiveProfile(): Promise<Profile> {
    return fetchJson('/profiles/active');
  },

  async createProfile(data: ProfileCreate): Promise<{ success: boolean; profile: Profile }> {
    return fetchJson('/profiles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  async updateProfile(id: number, data: ProfileUpdate): Promise<{ success: boolean; profile: Profile }> {
    return fetchJson(`/profiles/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  async activateProfile(id: number): Promise<{ success: boolean; profile: Profile }> {
    return fetchJson(`/profiles/${id}/activate`, {
      method: 'POST',
    });
  },

  async deleteProfile(id: number): Promise<{ success: boolean }> {
    return fetchJson(`/profiles/${id}`, {
      method: 'DELETE',
    });
  },
};
