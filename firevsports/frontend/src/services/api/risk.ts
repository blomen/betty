import type {
  ProviderRiskProfile,
  AllRiskResponse,
  RiskConfig,
  RiskConfigUpdate,
  OpportunityInput,
  SelectOpportunityResponse,
  RiskAwareStake,
} from '@/types';
import { fetchJson } from './client';

export const riskApi = {
  async getProviderRisk(providerId: string): Promise<ProviderRiskProfile> {
    return fetchJson<ProviderRiskProfile>(`/risk/provider/${providerId}`);
  },

  async getAllRiskProfiles(): Promise<AllRiskResponse> {
    return fetchJson<AllRiskResponse>('/risk/all');
  },

  async getRiskConfig(): Promise<RiskConfig> {
    return fetchJson<RiskConfig>('/risk/config');
  },

  async updateRiskConfig(config: RiskConfigUpdate): Promise<RiskConfig> {
    return fetchJson<RiskConfig>('/risk/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    });
  },

  async selectOpportunity(
    opportunities: OpportunityInput[],
    stake: number,
    options?: { temperature?: number; deterministic?: boolean }
  ): Promise<SelectOpportunityResponse> {
    return fetchJson<SelectOpportunityResponse>('/risk/select', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        opportunities,
        stake,
        temperature: options?.temperature,
        deterministic: options?.deterministic ?? false,
      }),
    });
  },

  async setProviderCooldown(
    providerId: string,
    durationHours: number,
    reason?: string
  ): Promise<{ success: boolean; provider_id: string; cooldown_until: string; reason: string }> {
    return fetchJson(`/risk/cooldown/${providerId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        duration_hours: durationHours,
        reason,
      }),
    });
  },

  async clearProviderCooldown(
    providerId: string
  ): Promise<{ success: boolean; provider_id: string; message: string }> {
    return fetchJson(`/risk/cooldown/${providerId}`, {
      method: 'DELETE',
    });
  },

  async calculateRiskAwareStake(
    odds: number,
    fairOdds: number,
    providerId: string,
    force = false
  ): Promise<RiskAwareStake> {
    const params = new URLSearchParams();
    params.set('odds', odds.toString());
    params.set('fair_odds', fairOdds.toString());
    params.set('provider_id', providerId);
    params.set('force', force.toString());
    return fetchJson<RiskAwareStake>(`/risk/calculate-stake?${params}`, {
      method: 'POST',
    });
  },
};
