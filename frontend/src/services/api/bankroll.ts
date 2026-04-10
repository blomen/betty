import type { BankrollInfo, BankrollStats, BankrollExposure, AllocationRecommendation } from '@/types';
import { fetchJson } from './client';

export const bankrollApi = {
  async getBankroll(): Promise<BankrollInfo> {
    return fetchJson<BankrollInfo>('/bankroll');
  },

  async getBankrollStats(): Promise<BankrollStats> {
    return fetchJson<BankrollStats>('/bankroll/stats');
  },

  async getBankrollStatus(): Promise<{
    profile_id: number;
    profile_name: string;
    bankroll: number;
    bonus_progress: Record<string, import('@/types').BonusProgressEntry>;
  }> {
    return fetchJson('/bankroll/status');
  },

  async setAllBalances(
    balance: number,
    providerIds?: string[]
  ): Promise<{
    success: boolean;
    updated_count: number;
    balance_per_provider: number;
    total_balance: number;
  }> {
    return fetchJson('/bankroll/set-all', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        balance,
        provider_ids: providerIds,
      }),
    });
  },

  async allocate(liquidAmount: number): Promise<{
    recommendations: AllocationRecommendation[];
    liquid_amount: number;
  }> {
    return fetchJson('/bankroll/allocate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ liquid_amount: liquidAmount }),
    });
  },

  async getLiquidBalance(): Promise<{ liquid_balance: number }> {
    return fetchJson('/bankroll/liquid');
  },

  async setBalance(
    providerId: string,
    balance: number
  ): Promise<{
    success: boolean;
    provider_id: string;
    old_balance: number;
    new_balance: number;
  }> {
    return fetchJson(`/bankroll/set/${providerId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ balance }),
    });
  },

  async resetAllBalances(): Promise<{
    success: boolean;
    reset_count: number;
    message: string;
  }> {
    return fetchJson('/bankroll/reset-all', { method: 'POST' });
  },

  async getBankrollExposure(): Promise<BankrollExposure> {
    return fetchJson<BankrollExposure>('/bankroll/exposure');
  },

  async depositWithBonus(
    providerId: string,
    amount: number
  ): Promise<{
    success: boolean;
    provider_id: string;
    deposit: number;
    bonus_claimed: number;
    total_added: number;
    old_balance: number;
    new_balance: number;
    bonus_status: string | null;
    bonus_type: string | null;
    bonus_limit: number | null;
  }> {
    return fetchJson(`/bankroll/deposit/${providerId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ amount }),
    });
  },

  async claimBonus(providerId: string): Promise<{ success: boolean; provider_id: string; status: string }> {
    return fetchJson(`/bankroll/claim-bonus/${providerId}`, { method: 'POST' });
  },
};
