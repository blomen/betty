import type { BankrollInfo, BankrollStats, BankrollExposure } from '@/types';
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

  async adjustBalance(
    providerId: string,
    amount: number
  ): Promise<{
    success: boolean;
    provider_id: string;
    old_balance: number;
    adjustment: number;
    new_balance: number;
  }> {
    return fetchJson(`/bankroll/adjust/${providerId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ amount }),
    });
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

  async transferFunds(
    fromProviderId: string,
    toProviderId: string,
    amount: number,
    withBonus = false
  ): Promise<{
    success: boolean;
    from_provider_id: string;
    to_provider_id: string;
    amount: number;
    from_new_balance: number;
    to_new_balance: number;
    bonus_claimed: number;
    bonus_status: string | null;
    bonus_type: string | null;
  }> {
    return fetchJson('/bankroll/transfer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        from_provider_id: fromProviderId,
        to_provider_id: toProviderId,
        amount,
        with_bonus: withBonus,
      }),
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
